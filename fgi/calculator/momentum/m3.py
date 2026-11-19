import pandas as pd

from fgi.calculator.common import lookback_start, mark_success, missing, save_score
from fgi.calculator.status import StatusRecorder
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.config.settings import PERCENTILE_WINDOW_YEARS
from fgi.indicators.registry import raw_keys
from fgi.storage.database import Database

K = raw_keys("m3")


class M3Calculator:
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

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "m3_index", "fetch_index_daily", "sh000001", start_date, end_date
        )

    def calculate_deviation(self, df: pd.DataFrame) -> pd.DataFrame:
        df["ma60"] = df["close"].rolling(window=60).mean()
        df["deviation"] = (df["close"] - df["ma60"]) / df["ma60"]
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.Series:
        df["percentile"] = rolling_percentile(df["deviation"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def run(self, date: str, lookback_days: int = None) -> dict:
        self._status.begin(date)
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = lookback_start(date, lookback_days)

        result = self.fetch_data(start_date, end_date)
        if result.status != DataSourceStatus.HEALTHY:
            return missing(self._status, "m3", result.error, result.source)

        df = result.data
        df = self.calculate_deviation(df)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            return missing(self._status, "m3", "No data for date", result.source)

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            return missing(self._status, "m3", "Insufficient data", result.source)

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, K["deviation"], today["deviation"].iloc[0])
        save_score(
            self._db,
            date,
            "m3",
            score,
            {K["close"]: today["close"].iloc[0], K["percentile"]: percentile},
        )
        mark_success(self._status, "m3", result.source, fetched_freshly=True)

        return {"m3": score, "status": "normal", "percentile": percentile}
