import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class S1Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "s1_sentiment",
            "fetch_sentiment_data",
            start_date,
            end_date
        )

    def calculate_rise_fall_ratio(self, df: pd.DataFrame) -> pd.DataFrame:
        df["rise_fall_ratio"] = (df["rise_num"] - df["fall_num"]) / (df["rise_num"] + df["fall_num"])
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.Series:
        df["percentile"] = rolling_percentile(df["rise_fall_ratio"], window=self._window)
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
            self._db.upsert_status(date, "s1", "missing", result.source, result.error or "")
            return {"s1": None, "status": "missing"}

        df = result.data
        df = self.calculate_rise_fall_ratio(df)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "s1", "missing", result.source, "No data for date")
            return {"s1": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "s1", "missing", result.source, "Insufficient data")
            return {"s1": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "s1_rise_fall_ratio", today["rise_fall_ratio"].iloc[0])
        self._db.upsert_raw_data(date, "s1_percentile", percentile)
        self._db.upsert_score(date, {"S1": score})
        self._db.upsert_status(date, "s1", "normal", result.source)

        return {"s1": score, "status": "normal", "percentile": percentile}