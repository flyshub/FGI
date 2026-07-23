from datetime import datetime
import pandas as pd
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import PERCENTILE_WINDOW_YEARS


class F3Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def _fetch_index(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "f3_index", "fetch_index_daily", "sh000001", start_date, end_date,
        )

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        start_date = (pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)).strftime("%Y-%m-%d")

        result = self._fetch_index(start_date, date)
        if result.status != DataSourceStatus.HEALTHY or result.data is None or result.data.empty:
            self._db.upsert_status(date, "f3", "missing", result.source, result.error or "No data")
            return {"f3": None, "status": "missing"}

        df = result.data.copy()
        df["price_change"] = df["close"].diff()
        df["flow_magnitude"] = df["price_change"] * df["volume"]

        df = df[["date", "flow_magnitude"]].dropna()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        df["percentile"] = rolling_percentile(df["flow_magnitude"], window=self._window)

        today = df[df["date"] == pd.Timestamp(date)]
        if today.empty:
            self._db.upsert_status(date, "f3", "missing", "proxy", "No data for date")
            return {"f3": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "f3", "missing", "proxy", "Insufficient data")
            return {"f3": None, "status": "missing"}

        score = percentile * 100

        # store proxy raw data for reproducibility
        last = result.data.iloc[-1]
        raw_date = last["date"] if isinstance(last["date"], str) else str(last["date"].strftime("%Y-%m-%d"))
        if not pd.isna(last.get("close")):
            self._db.upsert_raw_data(raw_date, "f3_proxy_close", float(last["close"]))
        if not pd.isna(last.get("volume")):
            self._db.upsert_raw_data(raw_date, "f3_proxy_volume", float(last["volume"]))
        self._db.upsert_raw_data(date, "f3_percentile", percentile)
        self._db.upsert_score(date, {"F3": score})
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self._db.upsert_status(date, "f3", "normal", "proxy", f"fetched_at={ts}")

        return {"f3": score, "status": "normal", "percentile": percentile}