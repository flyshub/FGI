import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class M3Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "m3_index",
            "fetch_index_daily",
            "sh000001",
            start_date,
            end_date
        )

    def calculate_deviation(self, df: pd.DataFrame) -> pd.Series:
        df["ma60"] = df["close"].rolling(window=60).mean()
        df["deviation"] = (df["close"] - df["ma60"]) / df["ma60"]
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.Series:
        df["percentile"] = rolling_percentile(df["deviation"], window=self._window)
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
            self._db.upsert_status(date, "m3", "missing", result.source, result.error)
            return {"m3": None, "status": "missing"}

        df = result.data
        df = self.calculate_deviation(df)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "m3", "missing", result.source, "No data for date")
            return {"m3": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "m3", "missing", result.source, "Insufficient data")
            return {"m3": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "m3_deviation", today["deviation"].iloc[0])
        self._db.upsert_raw_data(date, "m3_percentile", percentile)
        self._db.upsert_score(date, {"M3": score})
        self._db.upsert_status(date, "m3", "normal", result.source)

        return {"m3": score, "status": "normal", "percentile": percentile}
