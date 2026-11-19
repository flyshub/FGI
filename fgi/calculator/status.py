"""daily_status 的唯一写入路径。

指标 calculator 不直接写库：它把「这条数据是什么状态、来源是什么」交给
StatusRecorder，由 recorder 统一写入。这样「谁拥有 daily_status」只有一处答案，
也不需要 upsert_status_keep_source 这种为绕开重复写入而生的特例接口。

两种模式：
- 直写（默认）：begin(date) 后 record() 立刻落库。用于单独构造某个 calculator
  的场合（单测、脚本），行为与重构前完全一致。
- 缓冲（buffered=True）：由 FGICalculator 持有一份，跑完一天再 flush，
  保证「一天的所有状态」是一次性写入。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fgi.storage.database import Database


class StatusRecorder:
    def __init__(self, db: Database, buffered: bool = False):
        self._db = db
        self._buffered = buffered
        self._date: str | None = None
        self._rows: list[tuple[str, str, str | None, str | None]] = []

    def begin(self, date: str) -> None:
        """告诉 recorder 当前处理的是哪一天（record 需要它来落库）。"""
        self._date = date

    def record(
        self,
        indicator: str,
        status: str,
        source: str | None = None,
        note: str | None = None,
    ) -> None:
        """登记一条状态。

        source=None 表示「保留库里已有的来源」——只有确实不知道来源时才这么用，
        例如数据本来就在本地库里（DB-first）。
        note 同理：None 表示保留已有说明。
        """
        if self._buffered:
            self._rows.append((indicator, status, source, note))
            return
        if self._date is None:
            raise RuntimeError("StatusRecorder.begin(date) must be called before record()")
        self._db.upsert_status(self._date, indicator, status, source, note)

    def flush(self, date: str) -> None:
        """把本日登记的状态写入 daily_status，并清空缓冲。"""
        if not self._buffered or not self._rows:
            return
        for indicator, status, source, note in self._rows:
            self._db.upsert_status(date, indicator, status, source, note)
        self._db.commit()
        self._rows.clear()

    def pending(self) -> int:
        return len(self._rows)
