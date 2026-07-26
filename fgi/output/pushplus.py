"""PushPlus (pushplus.plus) push notification for daily FGI reports.

Thin HTTP transport layer. Markdown/HTML rendering lives in renderer.py.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

import requests

from fgi.common.utils import extract_indicator_score
from fgi.output.renderer import (
    INDICATOR_NAMES,
    build_fgi_markdown,
)
from fgi.output.signal_report import render_zone_context_card
from fgi.storage.database import Database  # noqa: F811 — type hints only

logger = logging.getLogger(__name__)


def _post(token: str, title: str, content: str, to: str | None = None) -> bool:
    """Send to a single PushPlus token (optionally to friend tokens). Returns True on success."""
    if not token:
        return False

    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
    }
    if to:
        payload["to"] = to

    try:
        resp = requests.post("https://www.pushplus.plus/send", json=payload, timeout=10)
        if resp.status_code == 200 and resp.json().get("code") == 200:
            logger.info(f"PushPlus sent: {title}")
            return True
        logger.error(f"PushPlus error: {resp.text}")
        return False
    except Exception as e:
        logger.error(f"PushPlus push failed: {e}")
        return False


def _broadcast(title: str, content: str) -> bool:
    """Push to primary account and all friend subscribers via friend-message API.

    Sends two API calls when friends are configured:
    1. To self (without to parameter) — guarantees the main account receives it
    2. To friends (with to parameter) — optional, only if FGI_PUSHPLUS_FRIENDS set
    Returns True if at least one succeeded.
    """
    primary = os.getenv("FGI_PUSHPLUS_TOKEN", "")
    if not primary:
        logger.info("no PushPlus token configured, skipping push")
        return False

    # 1. Always send to self first
    ok = _post(primary, title, content)

    # 2. Optionally send to friends via to parameter
    friends = os.getenv("FGI_PUSHPLUS_FRIENDS", "")
    if friends:
        if _post(primary, title, content, to=friends):
            ok = True
        else:
            logger.warning("PushPlus friend push failed, but self push may have succeeded")

    return ok


def _get_prev_scores(db: Database, date_str: str) -> dict | None:
    """获取前一日 scores_daily 数据。"""
    try:
        prev = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        return db.get_score_on_date(prev)
    except Exception:
        return None


def _get_fgi_percentile(db: Database, fgi: float) -> tuple[str, str]:
    """返回 (human-friendly label, short note for extreme)."""
    try:
        below = db.count_scores_below(fgi)
        total = db.count_scores_with_data()
        if total == 0:
            return "无历史数据", ""
        pct = below / total * 100
        tiers = [
            (10,  f"低于历史上 {100-pct:.0f}% 的日子（极低）", "⚠️ 处于历史极低区间"),
            (25,  f"低于历史上 {100-pct:.0f}% 的日子（偏低）",  ""),
            (40,  f"位于历史中下区域（{pct:.0f}%分位）",        ""),
            (60,  f"位于历史中部（{pct:.0f}%分位）",            ""),
            (75,  f"位于历史中上区域（{pct:.0f}%分位）",        ""),
            (90,  f"高于历史上 {pct:.0f}% 的日子（偏高）",      ""),
            (100, f"高于历史上 {pct:.0f}% 的日子（极高）",      "⚠️ 处于历史极高区间"),
        ]
        for limit, label, note in tiers:
            if pct <= limit:
                return label, note
        return "暂无历史参考", ""
    except Exception:
        return "暂无历史参考", ""


def _get_most_changed_indicators(
    db: Database, indicator_results: dict, date_str: str,
    indicator_names: dict,
) -> list:
    """返回当日变动最大的前 3 个指标（变动 >= 5 分）。"""
    prev = _get_prev_scores(db, date_str)
    if not prev:
        return []
    changes = []
    for name, label in indicator_names.items():
        today = extract_indicator_score(indicator_results.get(name, {}), name)
        yesterday = prev.get(name)
        if today is not None and yesterday is not None:
            changes.append((abs(today - yesterday), name, label, today - yesterday, yesterday, today))
    changes.sort(reverse=True)
    return [c for c in changes[:3] if c[0] >= 5]


def _get_zone_context_card(db: Database, fgi: float) -> str:
    """返回 FGI 所在区间历史信号参考卡片。"""
    return render_zone_context_card(fgi, db)


def send_fgi_report(db: Database, fgi_raw: float, dimension_scores: dict,
                    indicator_results: dict, health: float, *,
                    date_str: str | None = None,
                    decision_matrix: dict | None = None) -> bool:
    """Send FGI daily report via PushPlus.

    Returns True on success, False otherwise.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H:%M:%S")

    # Data retrieval
    prev_scores = _get_prev_scores(db, date_str)
    fgi_percentile_result = _get_fgi_percentile(db, fgi_raw)
    movers = _get_most_changed_indicators(db, indicator_results, date_str, INDICATOR_NAMES)
    signal_card = _get_zone_context_card(db, fgi_raw)

    # Pure rendering
    content = build_fgi_markdown(
        fgi_raw, dimension_scores, indicator_results, health, date_str,
        prev_scores=prev_scores,
        fgi_percentile_result=fgi_percentile_result,
        movers=movers,
        decision_matrix=decision_matrix,
        signal_card=signal_card,
    )
    content += f"\n\n---\n`{date_str} {ts}`"

    # HTTP transport
    return _broadcast(f"📊 A股恐贪指数 · {date_str} {ts}", content)


def send_alert(title: str, content: str) -> bool:
    """Send an alert message via PushPlus. Returns True on success."""
    return _broadcast(title, content)
