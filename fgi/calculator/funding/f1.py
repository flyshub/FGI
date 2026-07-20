import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class F1Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "f1_margin",
            "fetch_margin_data",
            start_date,
            end_date
        )

    def calculate_margin_growth(self, df: pd.DataFrame) -> pd.Series:
        df["margin_balance"] = pd.to_numeric(df["融资余额"], errors="coerce")
        df["margin_growth"] = df["margin_balance"].pct_change()
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.Series:
        df["percentile"] = rolling_percentile(df["margin_growth"], window=self._window)
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
            self._db.upsert_status(date, "f1", "missing", result.source, result.error)
            return {"f1": None, "status": "missing"}

        df = result.data
        df = self.calculate_margin_growth(df)
        df = self.calculate_percentile(df)

        if df.empty:
            self._db.upsert_status(date, "f1", "missing", result.source, "No data for date")
            return {"f1": None, "status": "missing"}

        latest = df.iloc[-1]
        if pd.isna(latest["percentile"]):
            self._db.upsert_status(date, "f1", "missing", result.source, "Insufficient data")
            return {"f1": None, "status": "missing"}

        score = self.calculate_score(latest["percentile"])

        self._db.upsert_raw_data(date, "f1_margin_growth", latest["margin_growth"])
        self._db.upsert_raw_data(date, "f1_percentile", latest["percentile"])
        self._db.upsert_score(date, {"F1": score})
        self._db.upsert_status(date, "f1", "normal", result.source)

        return {"f1": score, "status": "normal", "percentile": latest["percentile"]}