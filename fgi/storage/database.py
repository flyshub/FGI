import sqlite3
from pathlib import Path
from typing import Optional
import pandas as pd
from fgi.config.settings import DB_PATH


class Database:
    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def path(self) -> Path:
        """数据库文件路径（公开只读接口）。"""
        return self._path

    def connect(self):
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        return self

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def init_schema(self):
        if self._conn is None:
            raise RuntimeError("Database not connected")
        self._conn.executescript("""
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
                V1 REAL, V2 REAL,
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

    def upsert_raw_data(self, date: str, indicator: str, value: float):
        if self._conn is None:
            raise RuntimeError("Database not connected")
        self._conn.execute("""
            INSERT INTO raw_data (date, indicator, value)
            VALUES (?, ?, ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                value = excluded.value,
                update_time = CURRENT_TIMESTAMP
        """, (date, indicator, value))

    def upsert_raw_data_batch(self, df: pd.DataFrame, indicator: str):
        if self._conn is None:
            raise RuntimeError("Database not connected")
        records = [(row["date"], indicator, row["value"]) for _, row in df.iterrows()]
        self._conn.executemany("""
            INSERT INTO raw_data (date, indicator, value)
            VALUES (?, ?, ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                value = excluded.value,
                update_time = CURRENT_TIMESTAMP
        """, records)

    def get_raw_data(self, indicator: str, start_date: str, end_date: str) -> pd.DataFrame:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        query = """
            SELECT date, value FROM raw_data
            WHERE indicator = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        return pd.read_sql_query(query, self._conn, params=(indicator, start_date, end_date))

    def upsert_score(self, date: str, scores: dict):
        if self._conn is None:
            raise RuntimeError("Database not connected")
        scores = dict(scores)
        scores.pop("FGI_legacy", None)  # FGI_legacy 保持 NULL（回滚字段，由版本切换流程写）
        if "FGI_current" not in scores and scores.get("FGI_final") is not None:
            scores["FGI_current"] = scores["FGI_final"]
        fields = list(scores.keys())
        values = [scores[f] for f in fields]
        placeholders = ", ".join(["?"] * len(fields))
        field_names = ", ".join(fields)
        update_clause = ", ".join([f"{f} = excluded.{f}" for f in fields])

        self._conn.execute(f"""
            INSERT INTO scores_daily (date, {field_names})
            VALUES (?, {placeholders})
            ON CONFLICT (date) DO UPDATE SET {update_clause}
        """, [date] + values)

    def get_scores(self, start_date: str, end_date: str) -> pd.DataFrame:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        query = """
            SELECT * FROM scores_daily
            WHERE date >= ? AND date <= ?
            ORDER BY date
        """
        return pd.read_sql_query(query, self._conn, params=(start_date, end_date))

    def upsert_status(self, date: str, indicator: str, status: str, source: str = "", error: str = ""):
        if self._conn is None:
            raise RuntimeError("Database not connected")
        indicator = indicator.lower()  # 统一小写，避免大小写双写
        self._conn.execute("""
            INSERT INTO daily_status (date, indicator, status, source, error)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                status = excluded.status,
                source = excluded.source,
                error = excluded.error
        """, (date, indicator, status, source, error or ""))

    def get_status(self, date: str) -> pd.DataFrame:
        query = """
            SELECT * FROM daily_status WHERE date = ? ORDER BY indicator
        """
        return pd.read_sql_query(query, self._conn, params=(date,))

    def get_latest_score_date(self) -> Optional[str]:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        cursor = self._conn.execute("SELECT MAX(date) FROM scores_daily")
        row = cursor.fetchone()
        return row[0] if row else None

    def get_missing_dates(self, indicator: str, start_date: str, end_date: str,
                          trading_days: Optional[list] = None) -> list:
        """trading_days 传入真实交易日历；缺省回退 m3_close 已有日期，再回退工作日。"""
        query = """
            SELECT date FROM raw_data
            WHERE indicator = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        df = pd.read_sql_query(query, self._conn, params=(indicator, start_date, end_date))
        if trading_days is None:
            m3 = self.get_raw_data("m3_close", start_date, end_date)
            trading_days = m3["date"].tolist() if not m3.empty else None
        if trading_days is None:
            all_dates = [d.strftime("%Y-%m-%d") for d in pd.date_range(start=start_date, end=end_date, freq="B")]
        else:
            all_dates = [str(d) for d in trading_days]
        existing = set(df["date"].tolist())
        return [d for d in all_dates if d not in existing]

    def commit(self):
        self._conn.commit()
