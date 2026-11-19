import pandas as pd

from fgi.calculator.common import lookback_start, mark_success, missing, save_score
from fgi.calculator.status import StatusRecorder
from fgi.collector.base import DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.config.settings import PERCENTILE_WINDOW_YEARS
from fgi.storage.database import Database


class F1Calculator:
    """V3.8.3: 融资余额占比 = 沪深合计融资余额 / 全A总市值 (月度前向填充)."""

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

    def fetch_margin_data(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch("f1_margin", "fetch_margin_data", start_date, end_date)

    def fetch_market_cap(self, start_date: str, end_date: str) -> DataSourceResult:
        return self._data_manager.fetch("f1_market_cap", "fetch_market_cap", start_date, end_date)

    def calculate_margin_ratio(self, margin_df: pd.DataFrame, cap_df: pd.DataFrame) -> pd.DataFrame:
        margin_df = margin_df.copy()
        raw_date = margin_df["date"].astype(str).str.strip()
        if "-" not in raw_date.iloc[0]:
            margin_df["date"] = pd.to_datetime(raw_date, errors="coerce").dt.strftime("%Y-%m-%d")
        else:
            margin_df["date"] = raw_date
        margin_df = margin_df.sort_values("date").reset_index(drop=True)
        margin_df["margin_balance"] = pd.to_numeric(margin_df["融资余额"], errors="coerce")
        cap_df = cap_df.copy().sort_values("date").reset_index(drop=True)
        cap_df["market_cap"] = pd.to_numeric(cap_df["market_cap"], errors="coerce")
        merged = pd.merge(margin_df[["date", "margin_balance"]], cap_df, on="date", how="left")
        merged["market_cap"] = merged["market_cap"].replace(0, pd.NA).ffill()
        merged = merged.dropna(subset=["market_cap"])
        merged["margin_ratio"] = merged["margin_balance"] / (merged["market_cap"] * 1e8)
        return merged

    def calculate_percentile(self, df: pd.DataFrame) -> pd.Series:
        df["percentile"] = rolling_percentile(df["margin_ratio"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def run(self, date: str, lookback_days: int = None) -> dict:
        self._status.begin(date)
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = lookback_start(date, lookback_days)

        margin_result = self.fetch_margin_data(start_date, end_date)
        if margin_result.status != DataSourceStatus.HEALTHY:
            return missing(self._status, "f1", margin_result.error, margin_result.source)

        cap_result = self.fetch_market_cap(start_date, end_date)
        if cap_result.status != DataSourceStatus.HEALTHY:
            return missing(self._status, "f1", cap_result.error, cap_result.source)

        df = self.calculate_margin_ratio(margin_result.data, cap_result.data)
        for _, row in df.iterrows():
            self._db.upsert_raw_data(
                str(row["date"]), "f1_margin_balance", float(row["margin_balance"])
            )
        self._db.commit()
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        # T+1 延迟统一由 _apply_forward_fill 处理（不在 calculator 层 fallback）
        if today.empty:
            return missing(self._status, "f1", "No data for date", "akshare")

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            return missing(self._status, "f1", "Insufficient data", "akshare")

        score = self.calculate_score(percentile)

        # 写入原始数据（margin_balance/market_cap）支持 offline recompute 重构
        self._db.upsert_raw_data(date, "f1_margin_balance", today["margin_balance"].iloc[0])
        save_score(
            self._db,
            date,
            "f1",
            score,
            {
                "f1_market_cap": today["market_cap"].iloc[0],
                "f1_margin_ratio": today["margin_ratio"].iloc[0],
                "f1_percentile": percentile,
            },
        )
        mark_success(self._status, "f1", "akshare", fetched_freshly=True)

        return {"f1": score, "status": "normal", "percentile": percentile}
