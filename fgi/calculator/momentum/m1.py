from __future__ import annotations

import pandas as pd

from fgi.calculator.common import lookback_start, mark_success, missing, save_score
from fgi.calculator.status import StatusRecorder
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.config.settings import PERCENTILE_WINDOW_YEARS
from fgi.storage.database import Database


class M1Calculator:
    def __init__(
        self,
        data_manager: DataSourceManager,
        db: Database,
        status: StatusRecorder | None = None,
    ):
        self._data_manager = data_manager
        self._db = db
        self._status = status if status is not None else StatusRecorder(db)
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "m1_zt_stats", "fetch_zt_daily_summary", start_date, end_date
        )

    def calculate_zt_count(self, df: pd.DataFrame) -> pd.DataFrame:
        df["zt_count"] = df["limit_up_count"]
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["zt_count"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def _try_fetch_from_source(
        self, start_date: str, end_date: str, target_date: str
    ) -> pd.DataFrame | None:
        result = self.fetch_data(start_date, end_date)
        if (
            result.status == DataSourceStatus.HEALTHY
            and result.data is not None
            and not result.data.empty
        ):
            for _, row in result.data.iterrows():
                self._db.upsert_raw_data(
                    str(row["date"]), "m1_zt_count", float(row["limit_up_count"])
                )
            self._db.commit()
            df = result.data
            df["zt_count"] = df["limit_up_count"]
            df = df[df["date"] >= start_date].copy()
            return df
        return None

    def run(self, date: str, lookback_days: int = None) -> dict:
        self._status.begin(date)
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = lookback_start(date, lookback_days)

        db_data = self._db.get_raw_data("m1_zt_count", start_date, end_date)

        fetched_freshly = False
        # today_in_db 必须同时满足 date 存在 AND value 非 NULL。
        # 否则历史 NULL 行（数据中断或清理后的污染行）会欺骗 calc 跳过当天 fetch。
        if not db_data.empty and date in db_data["date"].values:
            today_val = db_data[db_data["date"] == date]["value"].iloc[0]
            today_in_db = pd.notna(today_val) and today_val > 0
        else:
            today_in_db = False
        if not today_in_db:
            recent_start = (pd.Timestamp(date) - pd.Timedelta(days=30)).strftime("%Y-%m-%d")
            result = self.fetch_data(recent_start, date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                fetched_freshly = True
                for _, row in result.data.iterrows():
                    self._db.upsert_raw_data(
                        str(row["date"]), "m1_zt_count", float(row["limit_up_count"])
                    )
                self._db.commit()
                db_data = self._db.get_raw_data("m1_zt_count", start_date, end_date)

        if db_data.empty:
            df = self._try_fetch_from_source(start_date, end_date, date)
            if df is None:
                return missing(self._status, "m1", "No data collected", "database")
        else:
            df = pd.DataFrame(
                {
                    "date": db_data["date"],
                    "zt_count": db_data["value"],
                }
            )
            df = df[df["date"] >= start_date].copy()
            if len(df) < 252:
                full_df = self._try_fetch_from_source(start_date, end_date, date)
                if full_df is not None:
                    df = full_df
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            return missing(self._status, "m1", "No data for date", "database")

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            return missing(self._status, "m1", "Insufficient data", "database")

        raw_value = today["zt_count"].iloc[0]
        if pd.isna(raw_value):
            return missing(self._status, "m1", "Raw value is NaN", "database")

        score = self.calculate_score(percentile)

        save_score(
            self._db,
            date,
            "m1",
            score,
            {"m1_zt_count": today["zt_count"].iloc[0], "m1_percentile": percentile},
        )
        mark_success(self._status, "m1", "database", fetched_freshly)

        return {"m1": score, "status": "normal", "percentile": percentile}
