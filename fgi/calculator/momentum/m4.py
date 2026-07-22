import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile, zscore
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class M4Calculator:
    """V3.8: M4 = 创业板换手率(%) 60 日 Z-score 的滚动百分位。
    优先读 raw_data 的 m4_turnover（collector 写入），不足时从数据源拉取并落库。"""

    ZSCORE_WINDOW = 60

    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "m4_cyb_turnover",
            "fetch_cyb_daily",
            start_date,
            end_date
        )

    def calculate_turnover_zscore(self, df: pd.DataFrame) -> pd.DataFrame:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        df["turnover_zscore"] = zscore(
            df["turnover_rate"], window=self.ZSCORE_WINDOW, min_periods=self.ZSCORE_WINDOW
        )
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["turnover_zscore"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def _persist_source_data(self, result: DataSourceResult):
        for _, row in result.data.iterrows():
            d = row["date"]
            d = d.strftime("%Y-%m-%d") if hasattr(d, "strftime") else str(d)
            self._db.upsert_raw_data(d, "m4_turnover", float(row["turnover_rate"]))
        self._db.commit()

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + self.ZSCORE_WINDOW + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        db_data = self._db.get_raw_data("m4_turnover", start_date, end_date)

        today_in_db = not db_data.empty and date in db_data["date"].values
        if not today_in_db:
            recent_start = pd.Timestamp(date) - pd.Timedelta(days=30)
            result = self.fetch_data(recent_start.strftime("%Y-%m-%d"), date)
            if result.status == DataSourceStatus.HEALTHY and result.data is not None:
                self._persist_source_data(result)
                db_data = self._db.get_raw_data("m4_turnover", start_date, end_date)

        if db_data.empty:
            result = self.fetch_data(start_date, end_date)
            if result.status != DataSourceStatus.HEALTHY or result.data is None or result.data.empty:
                self._db.upsert_status(date, "m4", "missing", result.source or "", result.error or "No data collected")
                return {"m4": None, "status": "missing"}
            self._persist_source_data(result)
            db_data = self._db.get_raw_data("m4_turnover", start_date, end_date)

        df = pd.DataFrame({
            "date": db_data["date"],
            "turnover_rate": db_data["value"],
        })
        df = self.calculate_turnover_zscore(df)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "m4", "missing", "database", "No data for date")
            return {"m4": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "m4", "missing", "database", "Insufficient data")
            return {"m4": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "m4_zscore", today["turnover_zscore"].iloc[0])
        self._db.upsert_raw_data(date, "m4_percentile", percentile)
        self._db.upsert_score(date, {"M4": score})
        self._db.upsert_status(date, "m4", "normal", "database")

        return {"m4": score, "status": "normal", "percentile": percentile}
