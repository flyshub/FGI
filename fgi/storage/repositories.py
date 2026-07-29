"""Domain-specific repository classes extracted from Database (#105).

Each repository owns a coherent set of SQL queries against a single domain,
receiving a sqlite3.Connection from the caller. Database remains the thin
connection manager; repositories are the query layer.
"""

from __future__ import annotations

import sqlite3

import pandas as pd


class ScoreRepository:
    """scores_daily CRUD and aggregates."""

    # Field whitelist — keep in sync with init_schema
    ALLOWED_FIELDS = frozenset(
        {
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
            "FGI_current",
            "health_score",
        }
    )

    def __init__(self, conn: sqlite3.Connection):
        self._handle = conn

    # ── write ────────────────────────────────────────────────

    def upsert_score(self, date: str, scores: dict) -> None:
        scores = dict(scores)
        scores.pop("FGI_legacy", None)
        if "FGI_current" not in scores and scores.get("FGI_final") is not None:
            scores["FGI_current"] = scores["FGI_final"]

        unknown = set(scores.keys()) - self.ALLOWED_FIELDS
        if unknown:
            raise ValueError(f"unknown score field(s): {sorted(unknown)}")

        fields = list(scores.keys())
        values = [scores[f] for f in fields]
        placeholders = ", ".join(["?"] * len(fields))
        field_names = ", ".join(fields)
        update_clause = ", ".join([f"{f} = excluded.{f}" for f in fields])

        self._handle.execute(
            f"""
            INSERT INTO scores_daily (date, {field_names})
            VALUES (?, {placeholders})
            ON CONFLICT (date) DO UPDATE SET {update_clause}
        """,
            [date] + values,
        )

    def update_score_field(self, date: str, field: str, value) -> None:
        if field not in self.ALLOWED_FIELDS:
            raise ValueError(f"unknown score field: {field}")
        self._handle.execute(
            f"UPDATE scores_daily SET {field} = ? WHERE date = ?",
            (value, date),
        )

    # ── read ─────────────────────────────────────────────────

    def get_scores(self, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM scores_daily WHERE date >= ? AND date <= ? ORDER BY date",
            self._handle,
            params=[start_date, end_date],
        )

    def get_score_on_date(self, date: str) -> dict | None:
        cur = self._handle.execute("SELECT * FROM scores_daily WHERE date = ?", (date,))
        row = cur.fetchone()
        if not row:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row, strict=False))

    def get_latest_score_date(self) -> str | None:
        row = self._handle.execute("SELECT MAX(date) FROM scores_daily").fetchone()
        return row[0] if row else None

    def count_scores_with_data(self) -> int:
        return self._handle.execute(
            "SELECT COUNT(*) FROM scores_daily WHERE FGI_final IS NOT NULL"
        ).fetchone()[0]

    def count_scores_below(self, fgi: float) -> int:
        return self._handle.execute(
            "SELECT COUNT(*) FROM scores_daily WHERE FGI_final IS NOT NULL AND FGI_final < ?",
            (fgi,),
        ).fetchone()[0]


class RawDataRepository:
    """raw_data CRUD and diagnostics."""

    def __init__(self, conn: sqlite3.Connection):
        self._handle = conn

    # ── write ────────────────────────────────────────────────

    def upsert_raw_data(self, date: str, indicator: str, value: float) -> None:
        self._handle.execute(
            """
            INSERT INTO raw_data (date, indicator, value)
            VALUES (?, ?, ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                value = excluded.value,
                update_time = CURRENT_TIMESTAMP
        """,
            (date, indicator, value),
        )

    def upsert_raw_data_batch(self, df: pd.DataFrame, indicator: str) -> None:
        records = [(row["date"], indicator, row["value"]) for _, row in df.iterrows()]
        self._handle.executemany(
            """
            INSERT INTO raw_data (date, indicator, value)
            VALUES (?, ?, ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                value = excluded.value,
                update_time = CURRENT_TIMESTAMP
        """,
            records,
        )

    def delete_raw_data(self, indicator: str) -> int:
        cur = self._handle.execute("DELETE FROM raw_data WHERE indicator = ?", (indicator,))
        return cur.rowcount

    # ── read ─────────────────────────────────────────────────

    def get_raw_data(self, indicator: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT date, value FROM raw_data "
            "WHERE indicator = ? AND date >= ? AND date <= ? ORDER BY date",
            self._handle,
            params=[indicator, start_date, end_date],
        )

    def get_latest_raw_date(self, indicator: str, on_or_before: str) -> str | None:
        row = self._handle.execute(
            "SELECT date FROM raw_data WHERE indicator = ? AND date <= ? "
            "ORDER BY date DESC LIMIT 1",
            (indicator, on_or_before),
        ).fetchone()
        return row[0] if row else None

    def get_raw_date_range(self, indicator: str) -> tuple | None:
        row = self._handle.execute(
            "SELECT MIN(date), MAX(date) FROM raw_data WHERE indicator = ?",
            (indicator,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return row[0], row[1]

    def get_raw_value_stats(self, indicator: str) -> tuple | None:
        row = self._handle.execute(
            "SELECT MIN(value), MAX(value), AVG(value) FROM raw_data WHERE indicator = ?",
            (indicator,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return row[0], row[1], row[2]

    def get_missing_dates(
        self,
        indicator: str,
        start_date: str,
        end_date: str,
        trading_days: list | None = None,
    ) -> list:
        """返回 [start_date, end_date] 范围内缺少的日期列表。

        trading_days 传入真实交易日历；缺省回退 m3_close 已有日期，再回退工作日。
        """
        query = """
            SELECT date FROM raw_data
            WHERE indicator = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        df = pd.read_sql_query(query, self._handle, params=[indicator, start_date, end_date])
        if trading_days is None:
            m3 = self.get_raw_data("m3_close", start_date, end_date)
            trading_days = m3["date"].tolist() if not m3.empty else None
        if trading_days is None:
            all_dates = [
                d.strftime("%Y-%m-%d")
                for d in pd.date_range(start=start_date, end=end_date, freq="B")
            ]
        else:
            all_dates = [str(d) for d in trading_days]
        existing = set(df["date"].tolist())
        return [d for d in all_dates if d not in existing]

    def count_raw_data_by_indicator(self, indicator: str) -> int:
        return self._handle.execute(
            "SELECT COUNT(*) FROM raw_data WHERE indicator = ?", (indicator,)
        ).fetchone()[0]

    def count_rows(self) -> int:
        return self._handle.execute("SELECT COUNT(*) FROM raw_data").fetchone()[0]


class StatusRepository:
    """daily_status CRUD and queries."""

    def __init__(self, conn: sqlite3.Connection):
        self._handle = conn

    # ── write ────────────────────────────────────────────────

    def upsert_status(
        self,
        date: str,
        indicator: str,
        status: str,
        source: str = "",
        error: str = "",
    ) -> None:
        indicator = indicator.lower()
        self._handle.execute(
            """
            INSERT INTO daily_status (date, indicator, status, source, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                status = excluded.status,
                source = excluded.source,
                error = excluded.error
        """,
            (date, indicator, status, source, error or ""),
        )

    def upsert_status_keep_source(
        self,
        date: str,
        indicator: str,
        status: str,
        error: str = "",
    ) -> None:
        """Same as upsert_status but preserves existing source field (#51)."""
        indicator = indicator.lower()
        self._handle.execute(
            """
            INSERT INTO daily_status (date, indicator, status, source, error)
            VALUES (?, ?, ?, COALESCE(
                (SELECT source FROM daily_status WHERE date = ? AND indicator = ?), ''
            ), ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                status = excluded.status,
                error = CASE WHEN excluded.error != '' THEN excluded.error
                             ELSE daily_status.error END
        """,
            (date, indicator, status, date, indicator, error or ""),
        )

    # ── read ─────────────────────────────────────────────────

    def get_status(self, date: str) -> pd.DataFrame:
        return pd.read_sql_query(
            "SELECT * FROM daily_status WHERE date = ? ORDER BY indicator",
            self._handle,
            params=[date],
        )

    def get_indicator_status(self, date: str) -> list:
        return self._handle.execute(
            "SELECT indicator, status FROM daily_status WHERE date = ? ORDER BY indicator",
            (date,),
        ).fetchall()

    def get_degraded_dates(self, start_date: str, end_date: str) -> list:
        return self._handle.execute(
            "SELECT date, indicator, status, source, error FROM daily_status "
            "WHERE date >= ? AND date <= ? AND status = 'degraded' ORDER BY date, indicator",
            (start_date, end_date),
        ).fetchall()
