from __future__ import annotations

import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class M1Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "m1_zt_stats",
            "fetch_zt_daily_summary",
            start_date,
            end_date
        )

    def calculate_zt_count(self, df: pd.DataFrame) -> pd.DataFrame:
        df["zt_count"] = df["limit_up_count"]
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["zt_count"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def _try_fetch_from_source(self, start_date: str, end_date: str, target_date: str) -> pd.DataFrame | None:
        result = self.fetch_data(start_date, end_date)
        if result.status == DataSourceStatus.HEALTHY and result.data is not None and not result.data.empty:
            for _, row in result.data.iterrows():
                self._db.upsert_raw_data(str(row["date"]), "m1_zt_count", float(row["limit_up_count"]))
            self._db.commit()
            df = result.data
            df["zt_count"] = df["limit_up_count"]
            df = df[df["date"] >= start_date].copy()
            return df
        return None

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        db_data = self._db.get_raw_data("m1_zt_count", start_date, end_date)

        today_in_db = not db_data.empty and date in db_data["date"].values
        if not today_in_db:
            result = self.fetch_data(date, date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                for _, row in result.data.iterrows():
                    self._db.upsert_raw_data(str(row["date"]), "m1_zt_count", float(row["limit_up_count"]))
                self._db.commit()
                db_data = self._db.get_raw_data("m1_zt_count", start_date, end_date)

        if db_data.empty:
            df = self._try_fetch_from_source(start_date, end_date, date)
            if df is None:
                self._db.upsert_status(date, "m1", "missing", "database", "No data collected")
                return {"m1": None, "status": "missing"}
        else:
            df = pd.DataFrame({
                "date": db_data["date"],
                "zt_count": db_data["value"],
            })
            df = df[df["date"] >= start_date].copy()
            if len(df) < 252:
                full_df = self._try_fetch_from_source(start_date, end_date, date)
                if full_df is not None:
                    df = full_df
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "m1", "missing", "database", "No data for date")
            return {"m1": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "m1", "missing", "database", "Insufficient data")
            return {"m1": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "m1_zt_count", today["zt_count"].iloc[0])
        self._db.upsert_raw_data(date, "m1_percentile", percentile)
        self._db.upsert_score(date, {"M1": score})
        self._db.upsert_status(date, "m1", "normal", "database")

        return {"m1": score, "status": "normal", "percentile": percentile}