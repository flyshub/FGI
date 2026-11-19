"""指标计算器共用的记账逻辑。

12 个指标的**数据逻辑**差异很大（周频升采样、零值守卫、双序列 ERP、z-score…），
但「算不出来怎么记、算出来怎么落库」是同一套约定。这里只收后者，避免每次改约定
都要动 12 个文件——同时不假装能把差异也统一掉（见 docs/audits 的 P2-1 说明）。
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from fgi.calculator.status import StatusRecorder
from fgi.storage.database import Database


def lookback_start(date: str, lookback_days: int) -> str:
    """百分位窗口的起始日：按自然日多留 1.5 倍（覆盖周末与假期）。"""
    return (pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)).strftime("%Y-%m-%d")


def missing(
    recorder: StatusRecorder,
    key: str,
    reason: str | None = None,
    source: str | None = "database",
) -> dict:
    """统一的「这个指标今天算不出来」返回形状。"""
    recorder.record(key, "missing", source, reason or "")
    return {key: None, "status": "missing"}


def save_score(
    db: Database,
    date: str,
    key: str,
    score: float,
    raw: dict[str, float] | None = None,
) -> None:
    """落 raw_data 与 scores_daily。

    raw 的键必须来自 `registry.raw_keys(<indicator>)`——由
    test_registry.py::test_calculator_sources_contain_no_raw_key_literals 守住，
    不要在调用点写字面量。
    """
    for raw_key, value in (raw or {}).items():
        db.upsert_raw_data(date, raw_key, float(value))
    db.upsert_score(date, {key.upper(): score})


def mark_success(
    recorder: StatusRecorder,
    key: str,
    source: str,
    fetched_freshly: bool,
    status: str = "normal",
    extra_note: str | None = None,
) -> None:
    """成功路径的状态。

    刚从上游取到时记下 fetched_at（可追溯数据是哪天取的）；复用库里的数据时
    来源保持不变（source=None），只在需要时补充说明。
    """
    if fetched_freshly:
        note = f"fetched_at={datetime.now().strftime('%Y-%m-%dT%H:%M:%S')}"
        if extra_note:
            note += f"; {extra_note}"
        recorder.record(key, status, source, note)
    elif extra_note:
        recorder.record(key, status, None, extra_note)
    else:
        recorder.record(key, status, None, None)
