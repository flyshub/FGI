"""PushPlus (pushplus.plus) push notification for daily FGI reports.

Thin HTTP transport layer. Data aggregation lives in data_service.py,
Markdown/HTML rendering lives in renderer.py.
"""
from __future__ import annotations

import os
import logging
import requests
from datetime import datetime
from typing import Optional

from fgi.config.settings import DB_PATH
from fgi.storage.database import Database
from fgi.output.data_service import (
    get_prev_scores, get_fgi_percentile, get_most_changed_indicators,
    get_zone_context_card,
)
from fgi.output.renderer import (
    INDICATOR_NAMES, build_fgi_markdown,
)

logger = logging.getLogger(__name__)


def _post(token: str, title: str, content: str) -> bool:
    """Send to a single PushPlus token. Returns True on success."""
    if not token:
        return False

    payload = {
        "token": token,
        "title": title,
        "content": content,
        "template": "markdown",
    }

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
    """Push to all configured subscribers. Returns True if at least one succeeded."""
    tokens = []
    primary = os.getenv("FGI_PUSHPLUS_TOKEN", "")
    if primary:
        tokens.append(primary)
    extra = os.getenv("FGI_PUSHPLUS_TOKENS", "")
    if extra:
        tokens.extend(t for t in extra.split(",") if t.strip())

    if not tokens:
        logger.info("no PushPlus tokens configured, skipping push")
        return False

    ok = False
    for t in tokens:
        if _post(t.strip(), title, content):
            ok = True
    return ok


def send_fgi_report(fgi_raw: float, dimension_scores: dict, indicator_results: dict,
                    health: float, *, date_str: str | None = None,
                    decision_matrix: Optional[dict] = None) -> bool:
    """Send FGI daily report via PushPlus.

    Returns True on success, False otherwise.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")
    ts = datetime.now().strftime("%H:%M:%S")

    # Data retrieval (internal DB connection)
    with Database(DB_PATH) as db:
        prev_scores = get_prev_scores(db, date_str)
        fgi_percentile_result = get_fgi_percentile(db, fgi_raw)
        movers = get_most_changed_indicators(db, indicator_results, date_str, INDICATOR_NAMES)
        signal_card = get_zone_context_card(db, fgi_raw)

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
