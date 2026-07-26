"""Data aggregation service for FGI push notification.

All functions accept a Database instance and return structured data.
No rendering, no HTTP — pure data retrieval only.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fgi.common.utils import extract_indicator_score
from fgi.output.signal_report import render_zone_context_card
from fgi.storage.database import Database


def get_prev_scores(db: Database, date_str: str) -> dict | None:
    """获取前一日 scores_daily 数据。"""
    try:
        prev = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        return db.get_score_on_date(prev)
    except Exception:
        return None


def get_fgi_percentile(db: Database, fgi: float) -> tuple[str, str]:
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


def get_most_changed_indicators(
    db: Database, indicator_results: dict, date_str: str,
    indicator_names: dict,
) -> list:
    """返回当日变动最大的前 3 个指标（变动 >= 5 分）。"""
    prev = get_prev_scores(db, date_str)
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


def get_zone_context_card(db: Database, fgi: float) -> str:
    """返回 FGI 所在区间历史信号参考卡片。"""
    return render_zone_context_card(fgi, db)
