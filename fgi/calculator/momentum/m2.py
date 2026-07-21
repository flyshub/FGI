import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class M2Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "m2_sentiment",
            "fetch_open_sentiment",
            start_date,
            end_date
        )

    def calculate_sentiment_ratio(self, df: pd.DataFrame) -> pd.DataFrame:
        df["bullish_ratio"] = df["up_num"] / (df["up_num"] + df["down_num"])
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["bullish_ratio"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        result = self.fetch_data(start_date, end_date)
        if result.status != DataSourceStatus.HEALTHY:
            self._db.upsert_status(date, "m2", "missing", result.source or "", result.error or "")
            return {"m2": None, "status": "missing"}

        df = result.data
        df = self.calculate_sentiment_ratio(df)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "m2", "missing", result.source or "", "No data for date")
            return {"m2": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "m2", "missing", result.source or "", "Insufficient data")
            return {"m2": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "m2_bullish_ratio", today["bullish_ratio"].iloc[0])
        self._db.upsert_raw_data(date, "m2_percentile", percentile)
        self._db.upsert_score(date, {"M2": score})
        self._db.upsert_status(date, "m2", "normal", result.source or "")

        return {"m2": score, "status": "normal", "percentile": percentile}