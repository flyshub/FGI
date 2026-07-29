"""SQLite connection manager with domain repository accessors (#105).

Connection lifecycle and schema live here. Domain-specific queries are in
ScoreRepository, RawDataRepository, and StatusRepository (repositories.py),
accessible as db.scores / db.raw_data / db.status properties.
"""

from __future__ import annotations

from pathlib import Path

from fgi.config.settings import DB_PATH


class Database:
    # NOTE: connection 字段刻意下划线前缀 + 完整名 _connection。
    # 调用方应通过本类的公共方法访问数据；任何对 `_conn` 或 `_connection`
    # 的外部访问将被 dev 守卫 (scripts/check_no_external_conn.sh) 拒绝。
    def __init__(self, db_path: Path | None = None):
        self._path = db_path or DB_PATH
        self._connection = None
        self._scores = None
        self._raw_data = None
        self._status = None

    @property
    def path(self) -> Path:
        """数据库文件路径（公开只读接口）。"""
        return self._path

    @property
    def connection(self):
        """Raw sqlite3 connection — prefer repositories over direct access."""
        return self._connection

    # ── Domain repository accessors ──────────────────────────
    # Lazy-init: first access after connect() creates the repository.
    # These are the primary query interface going forward.

    @property
    def scores(self):
        """ScoreRepository — scores_daily CRUD and aggregates."""
        if self._scores is None:
            from fgi.storage.repositories import ScoreRepository

            self._scores = ScoreRepository(self._connection)  # type: ignore[arg-type]
        return self._scores

    @property
    def raw_data(self):
        """RawDataRepository — raw_data CRUD and diagnostics."""
        if self._raw_data is None:
            from fgi.storage.repositories import RawDataRepository

            self._raw_data = RawDataRepository(self._connection)  # type: ignore[arg-type]
        return self._raw_data

    @property
    def status(self):
        """StatusRepository — daily_status CRUD and queries."""
        if self._status is None:
            from fgi.storage.repositories import StatusRepository

            self._status = StatusRepository(self._connection)  # type: ignore[arg-type]
        return self._status

    # ── Backward-compatible delegation ───────────────────────
    # Existing callers use db.upsert_raw_data(...) etc. These thin
    # wrappers delegate to the repository so 23 caller files don't
    # need updating in this commit. New code should use db.raw_data.*
    # directly.

    def upsert_raw_data(self, date, indicator, value):
        return self.raw_data.upsert_raw_data(date, indicator, value)

    def upsert_raw_data_batch(self, df, indicator):
        return self.raw_data.upsert_raw_data_batch(df, indicator)

    def get_raw_data(self, indicator, start_date, end_date):
        return self.raw_data.get_raw_data(indicator, start_date, end_date)

    def delete_raw_data(self, indicator):
        return self.raw_data.delete_raw_data(indicator)

    def get_latest_raw_date(self, indicator, on_or_before):
        return self.raw_data.get_latest_raw_date(indicator, on_or_before)

    def get_raw_date_range(self, indicator):
        return self.raw_data.get_raw_date_range(indicator)

    def get_raw_value_stats(self, indicator):
        return self.raw_data.get_raw_value_stats(indicator)

    def count_raw_data_by_indicator(self, indicator):
        return self.raw_data.count_raw_data_by_indicator(indicator)

    def get_missing_dates(self, indicator, start_date, end_date, trading_days=None):
        return self.raw_data.get_missing_dates(indicator, start_date, end_date, trading_days)

    def upsert_score(self, date, scores):
        return self.scores.upsert_score(date, scores)

    def update_score_field(self, date, field, value):
        return self.scores.update_score_field(date, field, value)

    def get_scores(self, start_date, end_date):
        return self.scores.get_scores(start_date, end_date)

    def get_score_on_date(self, date):
        return self.scores.get_score_on_date(date)

    def get_latest_score_date(self):
        return self.scores.get_latest_score_date()

    def count_scores_with_data(self):
        return self.scores.count_scores_with_data()

    def count_scores_below(self, fgi):
        return self.scores.count_scores_below(fgi)

    def upsert_status(self, date, indicator, status, source="", error=""):
        return self.status.upsert_status(date, indicator, status, source, error)

    def upsert_status_keep_source(self, date, indicator, status, error=""):
        return self.status.upsert_status_keep_source(date, indicator, status, error)

    def get_status(self, date):
        return self.status.get_status(date)

    def get_indicator_status(self, date):
        return self.status.get_indicator_status(date)

    def get_degraded_dates(self, start_date, end_date):
        return self.status.get_degraded_dates(start_date, end_date)

    # ── Connection lifecycle ──────────────────────────────────

    def connect(self):
        import sqlite3

        self._connection = sqlite3.connect(str(self._path))
        self._connection.execute("PRAGMA journal_mode=WAL")
        return self

    def close(self):
        if self._connection:
            self._connection.close()
            self._connection = None
        self._scores = None
        self._raw_data = None
        self._status = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        if self._connection is None:
            return
        if exc_type is not None:
            if self._connection.in_transaction:
                self._connection.rollback()
        else:
            self._connection.commit()
        self.close()
        return False  # propagate any exception

    def commit(self):
        if self._connection is not None:
            self._connection.commit()

    # ── Schema management ────────────────────────────────────

    def init_schema(self):
        if self._connection is None:
            raise RuntimeError("Database not connected")
        # NOTE: S1 and S4 columns on scores_daily are deprecated since V3.8
        # (indicator set reduced). Columns kept to avoid schema migration risk.
        self._connection.executescript("""
            CREATE TABLE IF NOT EXISTS raw_data (
                date TEXT,
                indicator TEXT,
                value REAL,
                update_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (date, indicator)
            );

            CREATE TABLE IF NOT EXISTS scores_daily (
                date TEXT PRIMARY KEY,
                M1 REAL, M2 REAL, M3 REAL, M4 REAL,
                S1 REAL, S2 REAL, S3 REAL,
                V1 REAL, V2 REAL, V4 REAL,
                F1 REAL, F2 REAL, F3 REAL,
                FGI_raw REAL, FGI_final REAL,
                FGI_legacy REAL, FGI_current REAL,
                health_score REAL
            );

            CREATE TABLE IF NOT EXISTS daily_status (
                date TEXT,
                indicator TEXT,
                status TEXT,
                source TEXT,
                error TEXT,
                PRIMARY KEY (date, indicator)
            );
        """)

        self._migrate_scores_daily_columns()
        self._cleanup_deprecated_raw_data()

    def _cleanup_deprecated_raw_data(self):
        """Remove deprecated indicators from raw_data (one-time cleanup)."""
        if self._connection is None:
            return
        # F3 switched to proxy (price×volume) — f3_industry_net_flow is dead data (#77)
        self._connection.execute("DELETE FROM raw_data WHERE indicator = 'f3_industry_net_flow'")

    def _migrate_scores_daily_columns(self):
        """Add any scores_daily columns missing from older DB schemas."""
        if self._connection is None:
            return
        existing = {
            r[1] for r in self._connection.execute("PRAGMA table_info(scores_daily)").fetchall()
        }
        defined = {
            "M1",
            "M2",
            "M3",
            "M4",
            "S1",
            "S2",
            "S3",
            "V1",
            "V2",
            "V4",
            "F1",
            "F2",
            "F3",
            "FGI_raw",
            "FGI_final",
            "FGI_legacy",
            "FGI_current",
            "health_score",
        }
        for col in defined - existing:
            self._connection.execute(f"ALTER TABLE scores_daily ADD COLUMN {col} REAL")

    # ── Generic table utilities ──────────────────────────────

    def count_rows(self, table: str) -> int:
        """全表行数。table 限 'raw_data' / 'scores_daily' / 'daily_status'。"""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        if table not in ("raw_data", "scores_daily", "daily_status"):
            raise ValueError(f"unknown table: {table}")
        return self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    def clear_table(self, table: str):
        """清空指定表数据。table 限 'scores_daily' / 'daily_status' / 'raw_data'。"""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        if table not in ("raw_data", "scores_daily", "daily_status"):
            raise ValueError(f"unknown table: {table}")
        self._connection.execute(f"DELETE FROM {table}")

    def clear_table_range(self, table: str, start_date: str, end_date: str) -> int:
        """范围删除指定表的日期分区。返回删除行数。

        table 限 'scores_daily' / 'daily_status'（无 raw_data，raw_data 用 PK 复合键）。
        用于 recompute 时只清指定范围而非整表，保留历史数据完整性。
        """
        if self._connection is None:
            raise RuntimeError("Database not connected")
        if table not in ("scores_daily", "daily_status"):
            raise ValueError(
                f"clear_table_range supports scores_daily/daily_status only, got: {table}"
            )
        cur = self._connection.execute(
            f"DELETE FROM {table} WHERE date BETWEEN ? AND ?",
            (start_date, end_date),
        )
        return cur.rowcount or 0
