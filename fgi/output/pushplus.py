"""PushPlus (pushplus.plus) push notification for daily FGI reports."""
import os
import logging
import requests
from datetime import datetime

logger = logging.getLogger(__name__)


def _build_fgi_markdown(fgi_raw: float, dimension_scores: dict, indicator_results: dict,
                        health: float, date_str: str) -> str:
    """Build FGI daily report as markdown."""
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
        lines.append(f"| {dim} | {score:.1f} |")

    lines.append("")
    highlights = []
    for name, r in sorted(indicator_results.items()):
        s = r.get("score") or r.get(name.lower())
        st = r.get("status", "?")
        if st == "normal" and s is not None:
            if s >= 85:
                highlights.append(f"{name} {s:.0f} ")
            elif s <= 15:
                highlights.append(f"{name} {s:.0f} ")
    if highlights:
        lines.append("> " + " | ".join(highlights))

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
