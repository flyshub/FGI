import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class F2Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "f2_northbound",
            "fetch_northbound_data",
            start_date,
            end_date
        )

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["northbound_amount"] = pd.to_numeric(df["net_buy"], errors="coerce")
        df["percentile"] = rolling_percentile(df["northbound_amount"], window=self._window)
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
            self._db.upsert_status(date, "f2", "missing", result.source, result.error)
            return {"f2": None, "status": "missing"}

        df = result.data
        df = self.calculate_percentile(df)

        if df.empty:
            self._db.upsert_status(date, "f2", "missing", result.source, "No data for date")
            return {"f2": None, "status": "missing"}

        latest = df.iloc[-1]
        if pd.isna(latest["percentile"]):
            self._db.upsert_status(date, "f2", "missing", result.source, "Insufficient data")
            return {"f2": None, "status": "missing"}

        score = self.calculate_score(latest["percentile"])

        self._db.upsert_raw_data(date, "f2_northbound_amount", latest["northbound_amount"])
        self._db.upsert_raw_data(date, "f2_percentile", latest["percentile"])
        self._db.upsert_score(date, {"F2": score})
        self._db.upsert_status(date, "f2", "normal", result.source)

        return {"f2": score, "status": "normal", "percentile": latest["percentile"]}