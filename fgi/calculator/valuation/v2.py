from datetime import datetime

import numpy as np
import pandas as pd

from fgi.calculator.common import lookback_start, missing
from fgi.calculator.status import StatusRecorder
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import zscore
from fgi.storage.database import Database


class V2Calculator:
    """V3.8: ΔERP Z-score (250-day window, negative sigmoid). Derived from V1 ERP."""

    def __init__(
        self,
        data_manager: DataSourceManager,
        db: Database,
        status: StatusRecorder | None = None,
    ):
        self._data_manager = data_manager
        self._db = db
        self._status = status if status is not None else StatusRecorder(db)
        self._window = 250

    def calculate_derp_zscore(self, erp_series: pd.Series) -> pd.Series:
        delta = erp_series.diff()
        z = zscore(delta, window=self._window, min_periods=self._window)
        scores = 100.0 / (1.0 + np.exp(z))
        return scores

    def calculate_score(self, score: float) -> float:
        return score

    def run(self, date: str, lookback_days: int = None) -> dict:
        self._status.begin(date)
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = lookback_start(date, lookback_days)

        raw = self._db.get_raw_data("v1_erp", start_date, end_date)
        if raw.empty:
            return missing(self._status, "v2", "No ERP data available", "database")

        erp_df = pd.DataFrame(
            {
                "date": raw["date"],
                "erp": raw["value"],
            }
        ).sort_values("date")

        scores = self.calculate_derp_zscore(erp_df["erp"])
        erp_df["score"] = scores

        today = erp_df[erp_df["date"] == date]
        if today.empty:
            return missing(self._status, "v2", "No data for date", "database")

        score = today["score"].iloc[0]
        if pd.isna(score):
            return missing(self._status, "v2", "Insufficient data", "database")

        self._db.upsert_raw_data(date, "v2_score", float(score))
        self._db.upsert_raw_data(date, "v2_sigmoid_fraction", float(score) / 100.0)
        self._db.upsert_score(date, {"V2": float(score)})
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self._status.record("v2", "normal", "database", f"calculated_at={ts}")

        return {"v2": float(score), "status": "normal"}
