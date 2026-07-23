"""PushPlus (pushplus.plus) push notification for daily FGI reports."""
from __future__ import annotations

import os
import sqlite3
import logging
import requests
from datetime import datetime, timedelta

from fgi.common.utils import extract_indicator_score
from fgi.config.settings import DB_PATH, HEALTHY_THRESHOLD

logger = logging.getLogger(__name__)


INDICATOR_NAMES = {
    "M1": "涨停板家数", "M2": "散户意愿", "M3": "偏离60日均线", "M4": "创业板成交活跃度",
    "S2": "股吧热度", "S3": "涨停封单量",
    "V1": "沪深300风险溢价", "V2": "ΔERP Z-score",
    "F1": "融资余额占比", "F2": "基金股票仓位", "F3": "主力资金板块偏好",
}

DIMENSION_NAMES = {
    "momentum": "动量", "sentiment": "情绪",
    "valuation": "估值", "funding": "资金",
}

# 维度 → 指标映射，控制排版顺序
DIMENSION_INDICATORS = {
    "momentum": ["M1", "M2", "M3", "M4"],
    "sentiment": ["S2", "S3"],
    "valuation": ["V1", "V2"],
    "funding":  ["F1", "F2", "F3"],
}

_DIM_COLORS = {
    "momentum": "#E8F4FD",
    "sentiment": "#FDE8E8",
    "valuation": "#E8F5E9",
    "funding": "#FFF8E1",
}

STATUS_LABELS = {
    "normal": "",          # 不展示，默认就是好
    "degraded": "⚠️",       # 数据降级（2+天延迟）
    "missing":  "❌",       # 数据缺失
}

FGI_LEVELS = [
    (15, "极度恐惧"), (35, "恐惧"), (65, "中性"), (85, "贪婪"),
]


_CHANGE_IPS = {
    "主力资金板块偏好": "板块资金偏好转强，短期资金面改善",
    "创业板成交活跃度": "量能骤降至历史极低位，资金从成长股大幅撤退",
    "ΔERP Z-score": "风险偏好温和回暖，债券性价比相对下降",
}


def _data_cell(source_date: str, status: str) -> str:
    """Format data column with optional annotation for filled/proxied data."""
    if not source_date:
        return ""
    note = ""
    if status == "degraded":
        note = '<span style="color:#999;font-size:0.85em">（前向填充）</span>'
    elif status == "substituted":
        note = '<span style="color:#999;font-size:0.85em">（替代指标）</span>'
    return f'{source_date}{note}'


def _fgi_level(fgi: float) -> str:
    for threshold, label in FGI_LEVELS:
        if fgi < threshold:
            return label
    return "极度贪婪"


def _get_prev_scores(date_str: str) -> dict | None:
    try:
        db = sqlite3.connect(str(DB_PATH))
        prev = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        cursor = db.execute("SELECT * FROM scores_daily WHERE date = ?", (prev,))
        row = cursor.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cursor.description]
        return dict(zip(cols, row))
    except Exception:
        return None


def _fgi_trend(fgi: float, date_str: str) -> str:
    prev = _get_prev_scores(date_str)
    prev_fgi = prev.get("FGI_final") if prev else None
    if prev_fgi is None:
        return ""
    delta = fgi - prev_fgi
    if abs(delta) < 0.5:
        return f"→ 持平（{prev_fgi:.1f} → {fgi:.1f}）"
    arrow = "🔼" if delta > 0 else "🔽"
    dir_label = "贪婪" if delta > 0 else "恐惧"
    return f"{arrow} {delta:+.1f}（{dir_label}加深）"


def _score_bar(score: float, width: int = 8) -> str:
    """mini bar chart for score (0-100)."""
    filled = max(1, round(score / 100 * width))
    return "█" * filled + "░" * (width - filled)


def _fgi_percentile(fgi: float) -> str:
    """return a human-friendly historical-position label."""
    try:
        db = sqlite3.connect(str(DB_PATH))
        # Use a single query that counts "below" vs total from scores_daily
        below = db.execute(
            "SELECT COUNT(*) FROM scores_daily WHERE FGI_final IS NOT NULL AND FGI_final < ?",
            (fgi,)
        ).fetchone()[0]
        total = db.execute(
            "SELECT COUNT(*) FROM scores_daily WHERE FGI_final IS NOT NULL"
        ).fetchone()[0]
        db.close()
        if total == 0:
            return "无历史数据"
        pct = below / total * 100
        if pct <= 10:
            return f"低于历史上 {100-pct:.0f}% 的日子（极低）"
        if pct <= 25:
            return f"低于历史上 {100-pct:.0f}% 的日子（偏低）"
        if pct <= 40:
            return f"位于历史中下区域（{pct:.0f}%分位）"
        if pct <= 60:
            return f"位于历史中部（{pct:.0f}%分位）"
        if pct <= 75:
            return f"位于历史中上区域（{pct:.0f}%分位）"
        if pct <= 90:
            return f"高于历史上 {pct:.0f}% 的日子（偏高）"
        return f"高于历史上 {pct:.0f}% 的日子（极高）"
    except Exception:
        return "暂无历史参考"


def _most_changed_indicators(indicator_results: dict, date_str: str) -> list:
    prev = _get_prev_scores(date_str)
    if not prev:
        return []
    changes = []
    for name, label in INDICATOR_NAMES.items():
        today = extract_indicator_score(indicator_results.get(name, {}), name)
        yesterday = prev.get(name)
        if today is not None and yesterday is not None:
            changes.append((abs(today - yesterday), name, label, today - yesterday, yesterday, today))
    changes.sort(reverse=True)
    # ponytail: top 3, add when scrolling matters
    return [c for c in changes[:3] if c[0] >= 5]


def _fgi_header(fgi: float, health: float, date_str: str) -> str:
    """Build the FGI hero section with gauge, bar, health, and historical context.

    When health_score < 60, append a "数据质量异常，仅供参考" warning per spec §质量监控.
    """
    level = _fgi_level(fgi)
    bar = _score_bar(fgi, 20)
    pos = _fgi_percentile(fgi)
    trend = _fgi_trend(fgi, date_str)
    health_label = f"**{health:.0f}** / 100"
    if health < HEALTHY_THRESHOLD:
        health_label += " ⚠️ 数据质量异常，仅供参考"

    rows = [
        f"| 当前情绪 | **{level}** |",
    ]
    if trend:
        rows.append(f"| 趋势 | {trend} |")
    rows += [
        f"| 健康度 | {health_label} |",
        f"| 历史位置 | {pos} |",
    ]

    return "\n".join([
        f"## 📊 A股恐贪指数 · {date_str}",
        "",
        f"### FGI: {fgi:.1f}",
        "",
        f"`{bar} `",
        "",
        f"| 项目 | 值 |",
        f"|------|----|",
        *rows,
    ])


def _build_fgi_markdown(fgi_raw: float, dimension_scores: dict, indicator_results: dict,
                        health: float, date_str: str) -> str:
    parts = [_fgi_header(fgi_raw, health, date_str), "", "---", ""]

    # --- 指标明细 (HTML table with colored rows) ---
    parts.append("### 🔍 各维度指标明细")
    parts.append("")

    html = ['<table style="width:100%">', '<tr style="background:#ececec"><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">维度</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">名称</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">得分</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">数据</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">状态</th></tr>']
    for dim, ilist in DIMENSION_INDICATORS.items():
        bg = f' style="background:{_DIM_COLORS[dim]}"'
        dim_label = DIMENSION_NAMES[dim]
        for i, name in enumerate(ilist):
            r = indicator_results.get(name, {})
            score = extract_indicator_score(r, name)
            s_str = f"{score:.0f}" if score is not None else '<span style="color:#999">—</span>'
            status = r.get("status", "?")
            src_date = r.get("source_date") or date_str
            tag = STATUS_LABELS.get(status, "")
            dim_cell = f"<b>{dim_label}</b>" if i == 0 else ""
            data_cell = _data_cell(src_date, status)
            html.append(f'<tr{bg}><td style="padding:6px 10px;border:1px solid #e0e0e0;font-weight:700;color:#222">{dim_cell}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#333">{INDICATOR_NAMES[name]}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;font-weight:600;color:#222;white-space:nowrap">{s_str}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-size:0.9em">{data_cell}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center">{tag}</td></tr>')
    html.append("</table>")
    parts.append("\n".join(html))

    # --- 维度汇总 ---
    parts.append("")
    parts.append("### 📐 维度汇总")
    parts.append("")
    dhtml = ['<table style="width:100%">', '<tr style="background:#ececec"><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">维度</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">得分</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">权重</th></tr>']
    for dim in DIMENSION_INDICATORS:
        bg = f' style="background:{_DIM_COLORS[dim]}"'
        score = dimension_scores.get(dim)
        s_str = f"{score:.1f}" if score is not None else '<span style="color:#999">—</span>'
        dhtml.append(f'<tr{bg}><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#333;font-weight:700">{DIMENSION_NAMES[dim]}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;font-weight:600;color:#222;white-space:nowrap">{s_str}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;color:#333">25%</td></tr>')
    dhtml.append("</table>")
    parts.append("\n".join(dhtml))

    # --- 极端信号 + 解读 ---
    extreme_high = []
    extreme_low = []
    for name, label in INDICATOR_NAMES.items():
        s = extract_indicator_score(indicator_results.get(name, {}), name)
        if s is not None:
            if s >= 85:
                extreme_high.append((label, s))
            elif s <= 15:
                extreme_low.append((label, s))

    if extreme_high or extreme_low:
        parts.append("")
        parts.append("### ⚡ 极端信号")
        parts.append("")
        if extreme_high:
            parts.append("🔴 **极度贪婪**: " + " · ".join(f"{n}（{s:.0f}）" for n, s in extreme_high))
        if extreme_low:
            parts.append("🟢 **极度恐惧**: " + " · ".join(f"{n}（{s:.0f}）" for n, s in extreme_low))
        if extreme_high and extreme_low:
            parts.append("")
            parts.append('两组指标严重背离：资金面持续贪婪而价格指标普遍恐惧。历史上\u201c聪明钱贪婪 + 价格恐惧\u201d组合通常意味着回调接近尾声、短期反转概率上升。')

    # --- 最大变动 ---
    movers = _most_changed_indicators(indicator_results, date_str)
    if movers:
        parts.append("")
        parts.append("### 📈 最大变动")
        parts.append("")
        parts.append("| 指标 | 变动 | 昨日 → 今日 | 解读 |")
        parts.append("|------|------|-------------|------|")
        for diff, _name, label, delta, yesterday, today in movers:
            arrow = "🔼" if delta > 0 else "🔽"
            interp = _CHANGE_IPS.get(label, "")
            parts.append(f"| {label} | {arrow} {diff:.0f} | {yesterday:.0f} → {today:.0f} | {interp} |")

    return "\n".join(parts)


def _post(title: str, content: str) -> bool:
    """Common PushPlus send: token lookup → payload → post → 200 check. Returns True on success."""
    token = os.getenv("FGI_PUSHPLUS_TOKEN", "")
    if not token:
        logger.info("FGI_PUSHPLUS_TOKEN not configured, skipping push")
        return False

    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
    }

    try:
        resp = requests.post("http://www.pushplus.plus/send", json=payload, timeout=10)
        if resp.status_code == 200 and resp.json().get("code") == 200:
            logger.info(f"PushPlus sent: {title}")
            return True
        logger.error(f"PushPlus error: {resp.text}")
        return False
    except Exception as e:
        logger.error(f"PushPlus push failed: {e}")
        return False


def send_fgi_report(fgi_raw: float, dimension_scores: dict, indicator_results: dict,
                    health: float, *, date_str: str | None = None) -> bool:
    """Send FGI daily report via PushPlus.

    Returns True on success, False otherwise.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H:%M:%S")

    content = _build_fgi_markdown(fgi_raw, dimension_scores, indicator_results, health, date_str)
    content += f"\n\n---\n`{date_str} {ts}`"

    return _post(f"📊 A股恐贪指数 · {date_str} {ts}", content)


def send_alert(title: str, content: str) -> bool:
    """Send an alert message via PushPlus. Returns True on success."""
    return _post(title, content)
