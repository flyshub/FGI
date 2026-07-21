import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus
from fgi.collector.fallback import DataSourceManager
from fgi.common.utils import rolling_percentile
from fgi.storage.database import Database
from fgi.config.settings import LOOKBACK_YEARS, PERCENTILE_WINDOW_YEARS


class F3Calculator:
    def __init__(self, data_manager: DataSourceManager, db: Database):
        self._data_manager = data_manager
        self._db = db
        self._window = PERCENTILE_WINDOW_YEARS * 252

    def fetch_industry_fund_flow(self, date: str) -> DataSourceResult:
        """Fetch 行业资金流汇总 (real-time daily data)."""
        return self._data_manager.fetch(
            "f3_industry_flow",
            "fetch_industry_fund_flow",
            date,
            date,
        )

    def fetch_index_proxy(self, start_date: str, end_date: str) -> DataSourceResult:
        """Fetch 上证指数 as proxy for historical data."""
        return self._data_manager.fetch(
            "f3_index",
            "fetch_index_daily",
            "sh000001",
            start_date,
            end_date,
        )

    def calculate_flow_proxy(self, df: pd.DataFrame) -> pd.DataFrame:
        """Calculate proxy: close * volume as rough money flow."""
        df["price_change"] = df["close"].diff()
        df["flow_proxy"] = df["price_change"] * df["volume"]
        df["flow_magnitude"] = df["flow_proxy"].abs()
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["flow_magnitude"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        # Track real industry fund flow (accumulate for future use)
        real_flow_result = self.fetch_industry_fund_flow(date)
        if real_flow_result.status == DataSourceStatus.HEALTHY and \
           real_flow_result.data is not None and not real_flow_result.data.empty:
            net_flow = real_flow_result.data["net_flow"].iloc[0]
            self._db.upsert_raw_data(date, "f3_industry_net_flow", float(net_flow))
            self._db.commit()

        # Check if we have enough accumulated real data
        real_data = self._db.get_raw_data("f3_industry_net_flow", start_date, end_date)
        use_real_data = len(real_data) >= self._window

        source = "proxy"
        if use_real_data:
            df = pd.DataFrame({
                "date": real_data["date"],
                "flow_magnitude": real_data["value"].abs(),
            })
            source = "real"
        else:
            proxy_result = self.fetch_index_proxy(start_date, end_date)
            if proxy_result.status != DataSourceStatus.HEALTHY:
                self._db.upsert_status(date, "f3", "missing",
                                       proxy_result.source, proxy_result.error or "Unknown error")
                return {"f3": None, "status": "missing"}
            df = proxy_result.data
            df = self.calculate_flow_proxy(df)

        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "f3", "missing", source, "No data for date")
            return {"f3": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "f3", "missing", source, "Insufficient data")
            return {"f3": None, "status": "missing"}

        score = self.calculate_score(percentile)
        source = "real" if use_real_data else "proxy"

        self._db.upsert_raw_data(date, "f3_percentile", percentile)
        self._db.upsert_score(date, {"F3": score})
        self._db.upsert_status(date, "f3", "normal", source)

        return {"f3": score, "status": "normal", "percentile": percentile}