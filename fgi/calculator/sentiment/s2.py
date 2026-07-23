import pandas as pd
from typing import Optional
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class S2Calculator:
    """V3.8: 股吧热度 (formerly S3) - zzshare market_hot_sentiment p_close"""

    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "s2_sentiment",
            "fetch_market_hot_sentiment",
            start_date,
            end_date
        )

    def calculate_heat(self, df: pd.DataFrame) -> pd.DataFrame:
        # 热度缺失保持 NaN，由后续流程判 missing/degraded，不填充满分值
        df["heat"] = pd.to_numeric(df["p_close"], errors="coerce")
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.Series:
        df["percentile"] = rolling_percentile(df["heat"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def run(self, date: str, lookback_days: Optional[int] = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        result = self.fetch_data(start_date, end_date)
        if result.status != DataSourceStatus.HEALTHY:
            self._db.upsert_status(date, "s2", "missing", result.source, result.error or "")
            return {"s2": None, "status": "missing"}

        df = result.data
        if df is None:
            self._db.upsert_status(date, "s2", "missing", result.source, "No data")
            return {"s2": None, "status": "missing"}

        df = self.calculate_heat(df)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "s2", "missing", result.source, "No data for date")
            return {"s2": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "s2", "missing", result.source, "Insufficient data")
            return {"s2": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "s2_heat", today["heat"].iloc[0])
        self._db.upsert_raw_data(date, "s2_percentile", percentile)
        self._db.upsert_score(date, {"S2": score})
        self._db.upsert_status(date, "s2", "normal", result.source)

        return {"s2": score, "status": "normal", "percentile": percentile}
