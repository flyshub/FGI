import pandas as pd

from fgi.calculator.common import mark_success, missing, save_score
from fgi.calculator.status import StatusRecorder
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.config.settings import PERCENTILE_WINDOW_YEARS
from fgi.indicators.registry import raw_keys
from fgi.storage.database import Database

K = raw_keys("f3")


class F3Calculator:
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

    def _fetch_index(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "f3_index",
            "fetch_index_daily",
            "sh000001",
            start_date,
            end_date,
        )

    def run(self, date: str, lookback_days: int = None) -> dict:
        self._status.begin(date)
        if lookback_days is None:
            lookback_days = self._window + 60

        start_date = (pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)).strftime(
            "%Y-%m-%d"
        )

        result = self._fetch_index(start_date, date)
        if result.status != DataSourceStatus.HEALTHY or result.data is None or result.data.empty:
            return missing(self._status, "f3", result.error or "No data", result.source)

        df = result.data.copy()
        df["price_change"] = df["close"].diff()
        df["flow_magnitude"] = df["price_change"] * df["volume"]

        df = df[["date", "flow_magnitude"]].dropna()
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)

        df["percentile"] = rolling_percentile(df["flow_magnitude"], window=self._window)

        today = df[df["date"] == pd.Timestamp(date)]
        if today.empty:
            return missing(self._status, "f3", "No data for date", "proxy")

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            return missing(self._status, "f3", "Insufficient data", "proxy")

        score = percentile * 100

        # store proxy raw data for reproducibility
        last = result.data.iloc[-1]
        raw_date = (
            last["date"]
            if isinstance(last["date"], str)
            else str(last["date"].strftime("%Y-%m-%d"))
        )
        if not pd.isna(last.get("close")):
            self._db.upsert_raw_data(raw_date, K["close"], float(last["close"]))
        if not pd.isna(last.get("volume")):
            self._db.upsert_raw_data(raw_date, K["volume"], float(last["volume"]))
        save_score(self._db, date, "f3", score, {K["percentile"]: percentile})
        mark_success(self._status, "f3", "proxy", fetched_freshly=True)

        return {"f3": score, "status": "normal", "percentile": percentile}
