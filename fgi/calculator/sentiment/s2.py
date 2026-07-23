import numpy as np
import pandas as pd
from typing import Optional
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile, zscore
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class S2Calculator:
    """V3.8.5 (#45): 股吧热度 p_close 的 log+Z-score 滚动百分位。

    原 V3.8 直接对 p_close 做 5 年滚动百分位，但 p_close 有强长期上升趋势
    （散户数量逐年增长），导致 percentile 退化为 0.99 中位（无区分度）。
    改用 log + 60 日 Z-score + 5 年百分位（与 M4 同处理），让分布健康。
    """

    ZSCORE_WINDOW = 60

    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "s2_sentiment",
            "fetch_market_hot_sentiment",
            start_date,
            end_date
        )

    def calculate_heat(self, df: pd.DataFrame) -> pd.DataFrame:
        # 热度缺失保持 NaN，由后续流程判 missing/degraded，不填充满分值
        df["heat"] = pd.to_numeric(df["p_close"], errors="coerce")
        return df

    def calculate_zscore(self, df: pd.DataFrame) -> pd.DataFrame:
        # 过滤非正值（log 要求 >0），再做 log + Z-score
        df["log_heat"] = np.log(df["heat"].where(df["heat"] > 0))
        df["heat_zscore"] = zscore(
            df["log_heat"], window=self.ZSCORE_WINDOW, min_periods=self.ZSCORE_WINDOW
        )
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["heat_zscore"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def run(self, date: str, lookback_days: Optional[int] = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + self.ZSCORE_WINDOW + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        result = self.fetch_data(start_date, end_date)
        if result.status != DataSourceStatus.HEALTHY:
            self._db.upsert_status(date, "s2", "missing", result.source, result.error or "")
            return {"s2": None, "status": "missing"}

        df = result.data
        if df is None:
            self._db.upsert_status(date, "s2", "missing", result.source, "No data")
            return {"s2": None, "status": "missing"}

        df = self.calculate_heat(df)
        df = self.calculate_zscore(df)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "s2", "missing", result.source, "No data for date")
            return {"s2": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "s2", "missing", result.source, "Insufficient data")
            return {"s2": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "s2_heat", today["heat"].iloc[0])
        self._db.upsert_raw_data(date, "s2_zscore", today["heat_zscore"].iloc[0])
        self._db.upsert_raw_data(date, "s2_percentile", percentile)
        self._db.upsert_score(date, {"S2": score})
        self._db.upsert_status(date, "s2", "normal", result.source)

        return {"s2": score, "status": "normal", "percentile": percentile}
