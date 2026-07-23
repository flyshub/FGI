from datetime import datetime
import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class V1Calculator:
    """V3.8: 沪深300风险溢价 (ERP) = 1/PE - 10年国债收益率"""

    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_pe_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "v1_pe",
            "fetch_pe_data",
            start_date,
            end_date
        )

    def fetch_bond_yield(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch(
            "v1_bond",
            "fetch_bond_yield",
            start_date,
            end_date
        )

    def calculate_erp(self, pe_df: pd.DataFrame, bond_df: pd.DataFrame) -> pd.DataFrame:
        pe_df = pe_df.copy()
        pe_df["pe_ttm"] = pd.to_numeric(pe_df["滚动市盈率"], errors="coerce")
        pe_df["earnings_yield"] = 1.0 / pe_df["pe_ttm"]
        merged = pd.merge(pe_df, bond_df, on="date", how="inner")
        merged["erp"] = merged["earnings_yield"] - merged["yield_10y"] / 100.0
        return merged

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["erp"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return (1.0 - percentile) * 100

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        pe_result = self.fetch_pe_data(start_date, end_date)
        if pe_result.status != DataSourceStatus.HEALTHY:
            self._db.upsert_status(date, "v1", "missing", pe_result.source, pe_result.error)
            return {"v1": None, "status": "missing"}

        bond_result = self.fetch_bond_yield(start_date, end_date)
        if bond_result.status != DataSourceStatus.HEALTHY:
            self._db.upsert_status(date, "v1", "missing", bond_result.source, bond_result.error)
            return {"v1": None, "status": "missing"}

        df = self.calculate_erp(pe_result.data, bond_result.data)

        # 回写完整 ERP 历史序列（幂等 upsert），供 V2 的 250 日 ΔERP 窗口使用
        erp_history = pd.DataFrame({
            "date": df["date"].astype(str),
            "value": df["erp"],
        }).dropna()
        self._db.upsert_raw_data_batch(erp_history, "v1_erp")
        self._db.commit()

        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "v1", "missing", "akshare", "No data for date")
            return {"v1": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "v1", "missing", "akshare", "Insufficient data")
            return {"v1": None, "status": "missing"}

        score = self.calculate_score(percentile)

        self._db.upsert_raw_data(date, "v1_percentile", percentile)
        self._db.upsert_score(date, {"V1": score})
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self._db.upsert_status(date, "v1", "normal", "akshare", f"fetched_at={ts}")

        return {"v1": score, "status": "normal", "percentile": percentile}
