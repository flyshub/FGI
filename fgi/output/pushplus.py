"""PushPlus (pushplus.plus) push notification for daily FGI reports."""
from __future__ import annotations

import os
import sqlite3
import logging
import requests
from datetime import datetime

from fgi.common.utils import extract_indicator_score
from fgi.config.settings import DB_PATH, HEALTHY_THRESHOLD

logger = logging.getLogger(__name__)


INDICATOR_NAMES = {
    "M1": "涨停家数", "M2": "散户意愿", "M3": "均线偏离", "M4": "创业量能",
    "S2": "股吧热度", "S3": "涨停封单",
    "V1": "沪深300·ERP", "V2": "ΔERP",
    "F1": "融资占比", "F2": "基金仓位", "F3": "主力资金",
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

STATUS_LABELS = {
    "normal": "",          # 不展示，默认就是好
    "degraded": "⚠️",       # 数据降级（2+天延迟）
    "missing":  "❌",       # 数据缺失
}

FGI_LEVELS = [
    (15, "极度恐惧"), (35, "恐惧"), (65, "中性"), (85, "贪婪"),
]


def _fgi_level(fgi: float) -> str:
    for threshold, label in FGI_LEVELS:
        if fgi < threshold:
            return label
    return "极度贪婪"


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


def _fgi_header(fgi: float, health: float, date_str: str) -> str:
    """Build the FGI hero section with gauge, bar, health, and historical context.

    When health_score < 60, append a "数据质量异常，仅供参考" warning per spec §质量监控.
    """
    level = _fgi_level(fgi)
    bar = _score_bar(fgi, 20)
    pos = _fgi_percentile(fgi)
    health_label = f"**{health:.0f}** / 100"
    if health < HEALTHY_THRESHOLD:
        health_label += " ⚠️ 数据质量异常，仅供参考"

    return "\n".join([
        f"## 📊 A股恐贪指数 · {date_str}",
        "",
        f"### FGI: {fgi:.1f}",
        "",
        f"`{bar} `",
        "",
        f"| 项目 | 值 |",
        f"|------|----|",
        f"| 当前情绪 | **{level}** |",
        f"| 健康度 | {health_label} |",
        f"| 历史位置 | {pos} |",
    ])


def _build_fgi_markdown(fgi_raw: float, dimension_scores: dict, indicator_results: dict,
                        health: float, date_str: str) -> str:
    lines = [
        _fgi_header(fgi_raw, health, date_str),
        "",
        "---",
        "",
        "### 🔍 各维度指标明细",
        "",
        "| 维度 | 名称 | 得分 | 数据 | 状态 |",
        "|------|------|------|------|------|",
    ]

    for dim, indicator_list in DIMENSION_INDICATORS.items():
        dim_label = DIMENSION_NAMES.get(dim, dim)
        first = True
        for name in indicator_list:
            r = indicator_results.get(name, {})
            score = extract_indicator_score(r, name)
            source_date = r.get("source_date")
            status = r.get("status", "?")

            s_str = f"{score:.0f}" if score is not None else "—"
            d_str = source_date if source_date else date_str
            tag = STATUS_LABELS.get(status, "")

            if first:
                lines.append(f"| **{dim_label}** | {INDICATOR_NAMES.get(name, name)} | {s_str} | {d_str} | {tag} |")
                first = False
            else:
                lines.append(f"| | {INDICATOR_NAMES.get(name, name)} | {s_str} | {d_str} | {tag} |")

    # 维度汇总
    lines.append("")
    lines.append("### 📐 维度汇总")
    lines.append("")
    lines.append("| 维度 | 得分 | 权重 |")
    lines.append("|------|------|------|")
    for dim, score in dimension_scores.items():
        label = DIMENSION_NAMES.get(dim, dim)
        s_str = f"{score:.1f}" if score is not None else "—"
        lines.append(f"| {label} | {s_str} | 25% |")

    # 极端指标提醒
    extreme_high = []
    extreme_low = []
    for name in INDICATOR_NAMES:
        r = indicator_results.get(name, {})
        s = extract_indicator_score(r, name)
        if s is not None:
            label = INDICATOR_NAMES.get(name, name)
            if s >= 85:
                extreme_high.append(f"{label} ({s:.0f})")
            elif s <= 15:
                extreme_low.append(f"{label} ({s:.0f})")

    if extreme_high or extreme_low:
        lines.append("")
        lines.append("### ⚡ 极端信号")
        lines.append("")
        if extreme_high:
            lines.append(f"🔴 **极度贪婪**: {' · '.join(extreme_high)}")
        if extreme_low:
            lines.append(f"🟢 **极度恐惧**: {' · '.join(extreme_low)}")

    return "\n".join(lines)


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
