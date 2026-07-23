import sqlite3
from pathlib import Path
from typing import Optional
import pandas as pd
from fgi.config.settings import DB_PATH


class Database:
    # NOTE: connection 字段刻意下划线前缀 + 完整名 _connection。
    # 调用方应通过本类的公共方法访问数据；任何对 `_conn` 或 `_connection`
    # 的外部访问将被 dev 守卫 (scripts/check_no_external_conn.sh) 拒绝。
    def __init__(self, db_path: Optional[Path] = None):
        self._path = db_path or DB_PATH
        self._connection: Optional[sqlite3.Connection] = None

    @property
    def path(self) -> Path:
        """数据库文件路径（公开只读接口）。"""
        return self._path

    def connect(self):
        self._connection = sqlite3.connect(str(self._path))
        self._connection.execute("PRAGMA journal_mode=WAL")
        return self

    def close(self):
        if self._connection:
            self._connection.close()
            self._connection = None

    def __enter__(self):
        return self.connect()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def init_schema(self):
        if self._connection is None:
            raise RuntimeError("Database not connected")
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
        if self._connection is None:
            raise RuntimeError("Database not connected")
        self._connection.execute("""
            INSERT INTO raw_data (date, indicator, value)
            VALUES (?, ?, ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                value = excluded.value,
                update_time = CURRENT_TIMESTAMP
        """, (date, indicator, value))

    def upsert_raw_data_batch(self, df: pd.DataFrame, indicator: str):
        if self._connection is None:
            raise RuntimeError("Database not connected")
        records = [(row["date"], indicator, row["value"]) for _, row in df.iterrows()]
        self._connection.executemany("""
            INSERT INTO raw_data (date, indicator, value)
            VALUES (?, ?, ?)
            ON CONFLICT (date, indicator) DO UPDATE SET
                value = excluded.value,
                update_time = CURRENT_TIMESTAMP
        """, records)

    def get_raw_data(self, indicator: str, start_date: str, end_date: str) -> pd.DataFrame:
        if self._connection is None:
            raise RuntimeError("Database not connected")
        query = """
            SELECT date, value FROM raw_data
            WHERE indicator = ? AND date >= ? AND date <= ?
            ORDER BY date
        """
        return pd.read_sql_query(query, self._connection, params=(indicator, start_date, end_date))

    def upsert_score(self, date: str, scores: dict):
        if self._connection is None:
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

        self._connection.execute(f"""
            INSERT INTO scores_daily (date, {field_names})
            VALUES (?, {placeholders})
            ON CONFLICT (date) DO UPDATE SET {update_clause}
        """, [date] + values)

    def get_scores(self, start_date: str, end_date: str) -> pd.DataFrame:
        if self._connection is None:
            raise RuntimeError("Database not connected")
        query = """
            SELECT * FROM scores_daily
            WHERE date >= ? AND date <= ?
            ORDER BY date
        """
        return pd.read_sql_query(query, self._connection, params=(start_date, end_date))

    def upsert_status(self, date: str, indicator: str, status: str, source: str = "", error: str = ""):
        if self._connection is None:
            raise RuntimeError("Database not connected")
        indicator = indicator.lower()  # 统一小写，避免大小写双写
        self._connection.execute("""
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
        return pd.read_sql_query(query, self._connection, params=(date,))

    def get_latest_score_date(self) -> Optional[str]:
        if self._connection is None:
            raise RuntimeError("Database not connected")
        cursor = self._connection.execute("SELECT MAX(date) FROM scores_daily")
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
        df = pd.read_sql_query(query, self._connection, params=(indicator, start_date, end_date))
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
        self._connection.commit()

    # --- 扩展公共查询接口（避免外部直接访问 _connection） ---

    def count_rows(self, table: str, where: str = "") -> int:
        """通用行数查询。table 限 'raw_data' / 'scores_daily' / 'daily_status'。

        where 是可选的 SQL 片段，会被原样拼到 WHERE 后（参数化由调用方在片段内处理）。
        为了防止 SQL 注入，table 必须命中白名单。
        """
        if self._connection is None:
            raise RuntimeError("Database not connected")
        if table not in ("raw_data", "scores_daily", "daily_status"):
            raise ValueError(f"unknown table: {table}")
        sql = f"SELECT COUNT(*) FROM {table}"
        if where:
            sql += f" WHERE {where}"
        return self._connection.execute(sql).fetchone()[0]

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
            raise ValueError(f"clear_table_range supports scores_daily/daily_status only, got: {table}")
        cur = self._connection.execute(
            f"DELETE FROM {table} WHERE date BETWEEN ? AND ?",
            (start_date, end_date),
        )
        return cur.rowcount or 0


    def update_score_field(self, date: str, field: str, value):
        """更新 scores_daily 单个字段。field 必须是 scores_daily 的合法列名。"""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        # 字段白名单（与 init_schema 同步）
        allowed = {
            "M1", "M2", "M3", "M4", "S1", "S2", "S3", "V1", "V2", "F1", "F2", "F3",
            "FGI_raw", "FGI_final", "FGI_legacy", "FGI_current", "health_score",
        }
        if field not in allowed:
            raise ValueError(f"unknown score field: {field}")
        self._connection.execute(
            f"UPDATE scores_daily SET {field} = ? WHERE date = ?",
            (value, date),
        )

    def get_indicator_status(self, date: str) -> list:
        """返回 [(indicator, status), ...]，按 indicator 升序。"""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        return self._connection.execute(
            "SELECT indicator, status FROM daily_status WHERE date = ? ORDER BY indicator",
            (date,),
        ).fetchall()

    def get_latest_raw_date(self, indicator: str, on_or_before: str) -> Optional[str]:
        """返回 <= on_or_before 的最大 raw_data.date（无则 None）。

        用于 forward-fill 溯源：从最后得分日回溯到该指标的真实写入日。
        """
        if self._connection is None:
            raise RuntimeError("Database not connected")
        row = self._connection.execute(
            "SELECT date FROM raw_data WHERE indicator = ? AND date <= ? ORDER BY date DESC LIMIT 1",
            (indicator, on_or_before),
        ).fetchone()
        return row[0] if row else None

    def get_raw_date_range(self, indicator: str) -> Optional[tuple]:
        """返回 raw_data 中某 indicator 的 (min_date, max_date)；无数据返回 None。"""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        row = self._connection.execute(
            "SELECT MIN(date), MAX(date) FROM raw_data WHERE indicator = ?",
            (indicator,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return row[0], row[1]

    def delete_raw_data(self, indicator: str) -> int:
        """删除 raw_data 中某 indicator 的所有行，返回删除行数。"""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        cur = self._connection.execute(
            "DELETE FROM raw_data WHERE indicator = ?", (indicator,)
        )
        return cur.rowcount

    def get_raw_value_stats(self, indicator: str) -> Optional[tuple]:
        """返回 raw_data 中某 indicator 的 (min_value, max_value, avg_value)；无数据返回 None。"""
        if self._connection is None:
            raise RuntimeError("Database not connected")
        row = self._connection.execute(
            "SELECT MIN(value), MAX(value), AVG(value) FROM raw_data WHERE indicator = ?",
            (indicator,),
        ).fetchone()
        if not row or row[0] is None:
            return None
        return row[0], row[1], row[2]
