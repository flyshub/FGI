"""PushPlus (pushplus.plus) push notification for daily FGI reports."""
from __future__ import annotations

import os
import sqlite3
import logging
import requests
from datetime import datetime, timedelta
from typing import Optional

from fgi.common.utils import extract_indicator_score
from fgi.config.settings import DB_PATH, HEALTHY_THRESHOLD

logger = logging.getLogger(__name__)


INDICATOR_NAMES = {
    "M1": "涨停板家数", "M2": "散户意愿", "M3": "偏离60日均线", "M4": "创业板成交活跃度",
    "S2": "股吧热度", "S3": "涨停封单量",
    "V1": "沪深300风险溢价", "V2": "ΔERP Z-score", "V4": "期权隐含波动率",
    "F1": "融资余额占比", "F2": "基金股票仓位", "F3": "主力资金板块偏好",
}

DIMENSION_NAMES = {
    "momentum": "动量", "sentiment": "情绪",
    "valuation": "估值", "volatility": "波动率", "funding": "资金",
}

# 维度 → 指标映射，控制排版顺序
DIMENSION_INDICATORS = {
    "momentum": ["M1", "M2", "M3", "M4"],
    "sentiment": ["S2", "S3"],
    "valuation": ["V1", "V2"],
    "volatility": ["V4"],
    "funding":  ["F1", "F2", "F3"],
}

_DIM_COLORS = {
    "momentum": "#E8F4FD",
    "sentiment": "#FDE8E8",
    "valuation": "#E8F5E9",
    "volatility": "#F3E8FD",
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


_CHANGE_DEFS = {
    "主力资金板块偏好": "主力资金在行业板块间净流入的集中度百分位。读数高=集中布局；读数低=分散或收缩。",
    "创业板成交活跃度": "创业板成交量占全市场成交量的滚动百分位。读数高=资金集中于创业板；读数低=流出。",
    "ΔERP Z-score": "ERP的Z-score，衡量股债性价比偏离历史均值程度。正值=股票性价比偏强。",
    "期权隐含波动率": "50ETF期权隐含波动率（QVIX，中国版VIX）的5年滚动百分位反向得分。高VIX=恐慌；低VIX=平静。",
}

_SUBSTITUTE_DESC = {
    "F3": "用上证指数当日涨跌幅 × 成交量估算主力资金净流向，替代 AKShare stock_market_fund_flow 的真实资金流数据",
}

_INDICATOR_DIM = {}
for _dim, _inds in DIMENSION_INDICATORS.items():
    for _name in _inds:
        _INDICATOR_DIM[_name] = DIMENSION_NAMES[_dim]


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
        return f"→ {prev_fgi:.1f} → {fgi:.1f}（持平）"
    arrow = "🔼" if delta > 0 else "🔽"
    return f"{arrow} {delta:+.1f}（昨日: {prev_fgi:.1f} · 今日: {fgi:.1f}）"


def _score_bar(score: float, width: int = 8) -> str:
    """mini bar chart for score (0-100)."""
    filled = max(1, round(score / 100 * width))
    return "█" * filled + "░" * (width - filled)


def _fgi_percentile(fgi: float) -> tuple[str, str]:
    """return (human-friendly label, short note for extreme)."""
    try:
        db = sqlite3.connect(str(DB_PATH))
        below = db.execute(
            "SELECT COUNT(*) FROM scores_daily WHERE FGI_final IS NOT NULL AND FGI_final < ?",
            (fgi,)
        ).fetchone()[0]
        total = db.execute(
            "SELECT COUNT(*) FROM scores_daily WHERE FGI_final IS NOT NULL"
        ).fetchone()[0]
        db.close()
        if total == 0:
            return "无历史数据", ""
        pct = below / total * 100
        if pct <= 10:
            return f"低于历史上 {100-pct:.0f}% 的日子（极低）", "⚠️ 处于历史极低区间"
        if pct <= 25:
            return f"低于历史上 {100-pct:.0f}% 的日子（偏低）", ""
        if pct <= 40:
            return f"位于历史中下区域（{pct:.0f}%分位）", ""
        if pct <= 60:
            return f"位于历史中部（{pct:.0f}%分位）", ""
        if pct <= 75:
            return f"位于历史中上区域（{pct:.0f}%分位）", ""
        if pct <= 90:
            return f"高于历史上 {pct:.0f}% 的日子（偏高）", ""
        return f"高于历史上 {pct:.0f}% 的日子（极高）", "⚠️ 处于历史极高区间"
    except Exception:
        return "暂无历史参考", ""


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


def _fgi_header(fgi: float, health: float, date_str: str,
                indicator_results: dict | None = None) -> str:
    level = _fgi_level(fgi)
    bar = _score_bar(fgi, 20)
    pos, extreme_note = _fgi_percentile(fgi)
    trend = _fgi_trend(fgi, date_str)

    issues = []
    if indicator_results:
        for name, r in indicator_results.items():
            st = r.get("status") if isinstance(r, dict) else None
            if st == "degraded":
                issues.append(f"{INDICATOR_NAMES.get(name, name)} 前向填充")
            elif st == "missing":
                issues.append(f"{INDICATOR_NAMES.get(name, name)} 缺失")
            elif st == "substituted":
                issues.append(f"{INDICATOR_NAMES.get(name, name)} 替代估算")
    health_label = f"**{health:.0f}** / 100"
    if issues:
        health_label += "（" + " · ".join(issues) + "）"
    if health < HEALTHY_THRESHOLD:
        health_label += " ⚠️ 数据质量异常，仅供参考"

    rows = [
        f"| 当前情绪 | **{level}** |",
    ]
    if trend:
        rows.append(f"| 趋势 | {trend} |")
    rows += [
        f"| 数据健康度 | {health_label} |",
        f"| 历史位置 | {pos} |",
    ]
    if extreme_note:
        rows.append(f"| 注意 | {extreme_note} |")

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


def _decision_matrix_section(dm: dict) -> str:
    """决策矩阵块：3×3 网格 + 软性建议。"""
    fgi = dm.get("fgi")
    sent = dm.get("sentiment_tier", "")
    val = dm.get("valuation_tier", "")
    val_pct = dm.get("valuation_pct")
    pe_pct = dm.get("pe_pct")
    pb_pct = dm.get("pb_pct")
    quadrant = dm.get("quadrant", "")
    advice = dm.get("advice", "")

    fgi_str = f"{fgi:.1f}" if fgi is not None else "—"
    val_pct_str = f"{val_pct*100:.0f}%" if val_pct is not None else "—"
    pe_pct_str = f"{pe_pct*100:.0f}%" if pe_pct is not None else "—"
    pb_pct_str = f"{pb_pct*100:.0f}%" if pb_pct is not None else "—"

    # 简单 emoji 选择
    emoji = {
        "强烈关注": "🟢", "关注": "🔵", "中性": "⚪",
        "观望": "🟡", "谨慎": "🟠", "强烈谨慎": "🔴",
    }.get(quadrant, "❓")

    # 3x3 矩阵，当前象限高亮
    def cell(s: str, v: str, hl: bool) -> str:
        bg = "#FFE082" if hl else "#Fff"
        s_str = f"<strong>{s}</strong>" if hl else s
        v_str = f"<strong>{v}</strong>" if hl else v
        return f'<td style="padding:6px 10px;border:1px solid #e0e0e0;background:{bg};text-align:center;color:#222">{s_str}<br><span style="font-size:0.85em;color:#444">{v_str}</span></td>'

    cur_sent = sent
    cur_val = val
    sents_raw = ["恐惧", "中性", "贪婪"]
    sents_display = ["恐惧(<35)", "中性(35-65)", "贪婪(>65)"]
    vals = ["低估", "合理", "高估"]
    html = ['<table style="width:100%">']
    # 表头
    html.append('<tr style="background:#ececec"><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#222;font-weight:700">情绪＼估值<sup>沪深300</sup></th>'
                '<th style="padding:6px 10px;border:1px solid #e0e0e0;color:#222;font-weight:700">低估(&lt;25%)</th>'
                '<th style="padding:6px 10px;border:1px solid #e0e0e0;color:#222;font-weight:700">合理(25-75%)</th>'
                '<th style="padding:6px 10px;border:1px solid #e0e0e0;color:#222;font-weight:700">高估(&gt;75%)</th></tr>')
    qmap = {
        ("恐惧", "低估"): "强烈关注", ("恐惧", "合理"): "关注", ("恐惧", "高估"): "观望",
        ("中性", "低估"): "关注", ("中性", "合理"): "中性", ("中性", "高估"): "谨慎",
        ("贪婪", "低估"): "观望", ("贪婪", "合理"): "谨慎", ("贪婪", "高估"): "强烈谨慎",
    }
    for si, s in enumerate(sents_raw):
        cells = [f'<td style="padding:6px 10px;border:1px solid #e0e0e0;background:#ececec;font-weight:700;color:#222">{sents_display[si]}</td>']
        for v in vals:
            q = qmap[(s, v)]
            cells.append(cell(q, "", s == cur_sent and v == cur_val))
        html.append("<tr>" + "".join(cells) + "</tr>")
    html.append("</table>")

    lines = [
        "### 🎯 情绪-估值决策矩阵",
        "",
        "\n".join(html),
        "",
        f"- 当前象限：{emoji} **{quadrant}**（情绪 {sent} · 估值 {val}）",
        f"- 情绪 FGI：{fgi_str}",
        f"- 估值分位（沪深300）：{val_pct_str}（PE {pe_pct_str} · PB {pb_pct_str}）",
        f"- 建议：{advice}",
        "",
        "<sub>※ 决策矩阵为情绪-估值辅助工具，软性建议不构成投资指令</sub>",
    ]
    return "\n".join(lines)


def _build_fgi_markdown(fgi_raw: float, dimension_scores: dict, indicator_results: dict,
                        health: float, date_str: str,
                        decision_matrix: Optional[dict] = None) -> str:
    parts = [_fgi_header(fgi_raw, health, date_str, indicator_results), "", "---", ""]

    # --- 决策矩阵 ---
    if decision_matrix:
        parts.append(_decision_matrix_section(decision_matrix))
        parts.append("")
        parts.append("---")
        parts.append("")

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

    # --- 极端信号 + 说明 ---
    extreme_high = []
    extreme_low = []
    for name, label in INDICATOR_NAMES.items():
        s = extract_indicator_score(indicator_results.get(name, {}), name)
        if s is not None:
            if s >= 85:
                extreme_high.append((name, label, s))
            elif s <= 15:
                extreme_low.append((name, label, s))

    if extreme_high or extreme_low:
        parts.append("")
        parts.append("### ⚡ 极端信号")
        parts.append("")
        if extreme_high:
            parts.append("🔴 **极度贪婪（≥85）**: " + " · ".join(f"{l}（{s:.0f}）" for _, l, s in extreme_high))
        if extreme_low:
            parts.append("🟢 **极度恐惧（≤15）**: " + " · ".join(f"{l}（{s:.0f}）" for _, l, s in extreme_low))
        parts.append("")
        parts.append("**说明：** " + "；".join([
            "、".join(f"{l}" for _, l, _ in extreme_high) + "高于 85 分阈值，属于" + "、".join(sorted(set(_INDICATOR_DIM[n] for n, _, _ in extreme_high))) + "历史高位区间" if extreme_high else "",
            "、".join(f"{l}" for _, l, _ in extreme_low) + "低于 15 分阈值，属于" + "、".join(sorted(set(_INDICATOR_DIM[n] for n, _, _ in extreme_low))) + "历史低位区间" if extreme_low else "",
        ]))

    # --- 最大变动 ---
    movers = _most_changed_indicators(indicator_results, date_str)
    if movers:
        parts.append("")
        parts.append("### 📈 最大变动")
        parts.append("")
        mhtml = ['<table style="width:100%">', '<tr style="background:#ececec"><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">指标</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">变动</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">昨日→今日</th><th style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-weight:700">口径</th></tr>']
        for diff, _name, label, delta, yesterday, today in movers:
            arrow = "🔼" if delta > 0 else "🔽"
            defn = _CHANGE_DEFS.get(label, "")
            mhtml.append(f'<tr style="background:#fff"><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#333">{label}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;color:#222;font-weight:600;white-space:nowrap">{arrow} {diff:.0f}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;text-align:center;color:#333;white-space:nowrap">{yesterday:.0f}→{today:.0f}</td><td style="padding:6px 10px;border:1px solid #e0e0e0;color:#555;font-size:0.9em">{defn}</td></tr>')
        mhtml.append("</table>")
        parts.append("\n".join(mhtml))

    # --- 当日总结 ---
    level = _fgi_level(fgi_raw)
    pos_label, _ = _fgi_percentile(fgi_raw)

    dim_avgs = {}
    for dim in DIMENSION_INDICATORS:
        vals = [extract_indicator_score(indicator_results.get(n, {}), n) for n in DIMENSION_INDICATORS[dim]]
        vals_clean = [v for v in vals if v is not None]
        dim_avgs[dim] = sum(vals_clean) / len(vals_clean) if vals_clean else None

    parts.append("")
    parts.append("### 📝 当日总结")
    parts.append("")
    dim_line = " · ".join(f"{DIMENSION_NAMES[d]} {dim_avgs[d]:.0f}" for d in DIMENSION_INDICATORS if dim_avgs[d] is not None)
    parts.append(f"- FGI {fgi_raw:.1f}（{level}），{pos_label}")
    parts.append(f"- 维度：{dim_line}")

    movers = _most_changed_indicators(indicator_results, date_str)
    if movers:
        mover_str = " · ".join(f"{_name} {'🔼' if d>0 else '🔽'}{abs(d):.0f}" for _, _name, _, d, _, _ in movers)
        parts.append(f"- 最大变动：{mover_str}")

    parts.append(f"- 极端指标：🔴极度贪婪 {len(extreme_high)}个 · 🟢极度恐惧 {len(extreme_low)}个")

    degraded_inds = [(n, indicator_results.get(n, {})) for n in INDICATOR_NAMES]
    degraded = [(INDICATOR_NAMES[n], r.get("source_date", "")) for n, r in degraded_inds if r.get("status") == "degraded"]
    if degraded:
        for name, sd in degraded:
            parts.append(f"- 前向填充：{name}（源数据至 {sd}）")

    substituted = [(INDICATOR_NAMES[n], n) for n, r in degraded_inds if r.get("status") == "substituted"]
    if substituted:
        for name, code in substituted:
            desc = _SUBSTITUTE_DESC.get(code, "代理估算")
            parts.append(f"- 替代指标：{name}：{desc}")

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
                    health: float, *, date_str: str | None = None,
                    decision_matrix: Optional[dict] = None) -> bool:
    """Send FGI daily report via PushPlus.

    Returns True on success, False otherwise.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H:%M:%S")

    content = _build_fgi_markdown(fgi_raw, dimension_scores, indicator_results, health, date_str,
                                  decision_matrix=decision_matrix)
    content += f"\n\n---\n`{date_str} {ts}`"

    return _post(f"📊 A股恐贪指数 · {date_str} {ts}", content)


def send_alert(title: str, content: str) -> bool:
    """Send an alert message via PushPlus. Returns True on success."""
    return _post(title, content)
