from __future__ import annotations

import pandas as pd
from typing import Optional
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class S3Calculator:
    """V3.8: 涨停封单量 (formerly S4) - levistock/AKShare zt_daily_summary"""

    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "s4_zt_daily",
            "fetch_zt_daily_summary",
            start_date,
            end_date
        )

    def calculate_zt_ratio(self, df: pd.DataFrame) -> pd.DataFrame:
        # 统一单位为亿元（与 raw_data 的 s3_seal_fund 一致）
        df["zt_ratio"] = pd.to_numeric(df["seal_fund_sum"], errors="coerce") / 1e8
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.Series:
        df["percentile"] = rolling_percentile(df["zt_ratio"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def _try_fetch_from_source(self, start_date: str, end_date: str) -> pd.DataFrame | None:
        result = self.fetch_data(start_date, end_date)
        if result.status == DataSourceStatus.HEALTHY and result.data is not None and not result.data.empty:
            for _, row in result.data.iterrows():
                self._db.upsert_raw_data(str(row["date"]), "s3_seal_fund", float(row["seal_fund_sum"]) / 1e8)
            self._db.commit()
            df = result.data
            df["zt_ratio"] = pd.to_numeric(df["seal_fund_sum"], errors="coerce") / 1e8
            df = df[df["date"] >= start_date].copy()
            return df
        return None

    def run(self, date: str, lookback_days: Optional[int] = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        db_data = self._db.get_raw_data("s3_seal_fund", start_date, end_date)

        today_in_db = not db_data.empty and date in db_data["date"].values
        if not today_in_db:
            result = self.fetch_data(date, date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                for _, row in result.data.iterrows():
                    self._db.upsert_raw_data(str(row["date"]), "s3_seal_fund", float(row["seal_fund_sum"]) / 1e8)
                self._db.commit()
                db_data = self._db.get_raw_data("s3_seal_fund", start_date, end_date)

        if db_data.empty:
            df = self._try_fetch_from_source(start_date, end_date)
            if df is None:
                self._db.upsert_status(date, "s3", "missing", "database", "No data collected")
                return {"s3": None, "status": "missing"}
        else:
            df = pd.DataFrame({
                "date": db_data["date"],
                "zt_ratio": db_data["value"],
            })
            df = df[df["date"] >= start_date].copy()
            if len(df) < 252:
                full_df = self._try_fetch_from_source(start_date, end_date)
                if full_df is not None:
                    df = full_df
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "s3", "missing", "database", "No data for date")
            return {"s3": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "s3", "missing", "database", "Insufficient data")
            return {"s3": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "s3_zt_ratio", today["zt_ratio"].iloc[0])
        self._db.upsert_raw_data(date, "s3_percentile", percentile)
        self._db.upsert_score(date, {"S3": score})
        self._db.upsert_status(date, "s3", "normal", "database")

        return {"s3": score, "status": "normal", "percentile": percentile}
