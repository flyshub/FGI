import pandas as pd
import numpy as np
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import zscore
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class V2Calculator:
    """V3.8: ΔERP Z-score (250-day window, negative sigmoid). Derived from V1 ERP."""

    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = 250

    def calculate_derp_zscore(self, erp_series: pd.Series) -> pd.Series:
        delta = erp_series.diff()
        z = zscore(delta, window=self._window, min_periods=self._window)
        scores = 100.0 / (1.0 + np.exp(z))
        return scores

    def calculate_score(self, score: float) -> float:
        return score

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        raw = self._db.get_raw_data("v1_erp", start_date, end_date)
        if raw.empty:
            self._db.upsert_status(date, "v2", "missing", "database", "No ERP data available")
            return {"v2": None, "status": "missing"}

        erp_df = pd.DataFrame({
            "date": raw["date"],
            "erp": raw["value"],
        }).sort_values("date")

        scores = self.calculate_derp_zscore(erp_df["erp"])
        erp_df["score"] = scores

        today = erp_df[erp_df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "v2", "missing", "database", "No data for date")
            return {"v2": None, "status": "missing"}

        score = today["score"].iloc[0]
        if pd.isna(score):
            self._db.upsert_status(date, "v2", "missing", "database", "Insufficient data")
            return {"v2": None, "status": "missing"}

        self._db.upsert_raw_data(date, "v2_score", float(score))
        self._db.upsert_raw_data(date, "v2_percentile", float(score) / 100.0)
        self._db.upsert_score(date, {"V2": float(score)})
        self._db.upsert_status(date, "v2", "normal", "database")

        return {"v2": float(score), "status": "normal"}
