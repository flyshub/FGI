"""PushPlus (pushplus.plus) push notification for daily FGI reports."""
import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


INDICATOR_NAMES = {
    "M1": "涨停家数", "M2": "散户意愿", "M3": "均线偏离", "M4": "创业换手",
    "S1": "涨跌比", "S2": "股吧热度", "S3": "涨停封单",
    "V1": "沪深300·ERP", "V2": "ΔERP",
    "F1": "融资占比", "F2": "基金仓位", "F3": "主力资金",
}

DIMENSION_NAMES = {
    "momentum": "动量", "sentiment": "情绪",
    "valuation": "估值", "funding": "资金",
}


def _build_fgi_markdown(fgi_raw: float, dimension_scores: dict, indicator_results: dict,
                        health: float, date_str: str) -> str:
    level = "极度恐惧" if fgi_raw < 15 else "恐惧" if fgi_raw < 35 else \
            "中性" if fgi_raw <= 65 else "贪婪" if fgi_raw <= 85 else "极度贪婪"

    lines = [
        f"##  A股恐贪指数 · {date_str}",
        "",
        f"**FGI: {fgi_raw:.1f}**（{level}）| 健康度: {health:.0f}",
        "",
        "| 维度 | 得分 |",
        "|------|------|",
    ]
    for dim, score in dimension_scores.items():
        label = DIMENSION_NAMES.get(dim, dim)
        lines.append(f"| {label} | {score:.1f} |")

    extreme_high = []
    extreme_low = []
    for name, r in sorted(indicator_results.items()):
        s = r.get("score")
        if s is None:
            s = r.get(name.lower())
        st = r.get("status", "?")
        if st == "normal" and s is not None:
            label = INDICATOR_NAMES.get(name, name)
            if s >= 85:
                extreme_high.append(f"{label}{s:.0f}")
            elif s <= 15:
                extreme_low.append(f"{label}{s:.0f}")

    if extreme_high:
        lines.append("")
        lines.append("极度贪婪 " + " · ".join(extreme_high))
    if extreme_low:
        lines.append("")
        lines.append("极度恐惧 " + " · ".join(extreme_low))

    return "\n".join(lines)


def send_fgi_report(fgi_raw: float, dimension_scores: dict, indicator_results: dict,
                    health: float, *, date_str: str = None) -> bool:
    """Send FGI daily report via PushPlus.

    Returns True on success, False otherwise.
    """
    token = os.getenv("FGI_PUSHPLUS_TOKEN", "")
    if not token:
        logger.info("FGI_PUSHPLUS_TOKEN not configured, skipping push")
        return False

    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    content = _build_fgi_markdown(fgi_raw, dimension_scores, indicator_results, health, date_str)
    payload = {
        "token": token,
        "title": f" A股恐贪指数 · {date_str}",
        "content": content,
        "template": "markdown",
    }

    try:
        resp = requests.post("http://www.pushplus.plus/send", json=payload, timeout=10)
        if resp.status_code == 200 and resp.json().get("code") == 200:
            logger.info(f"PushPlus sent: FGI={fgi_raw:.1f}")
            return True
        logger.error(f"PushPlus error: {resp.text}")
        return False
    except Exception as e:
        logger.error(f"PushPlus push failed: {e}")
        return False
