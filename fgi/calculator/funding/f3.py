from datetime import datetime
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
        """Fetch 主力净流入 (120 天历史数据)."""
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
        """Calculate proxy: price_change * volume as signed money flow.
        大幅上涨日 → 大正值（贪婪）；大幅下跌日 → 大负值（恐慌）。"""
        df["price_change"] = df["close"].diff()
        df["flow_proxy"] = df["price_change"] * df["volume"]
        df["flow_magnitude"] = df["flow_proxy"]  # 保留符号：净流入越大 → 高分
        return df

    def calculate_percentile(self, df: pd.DataFrame) -> pd.DataFrame:
        df["percentile"] = rolling_percentile(df["flow_magnitude"], window=self._window)
        return df

    def calculate_score(self, percentile: float) -> float:
        return percentile * 100

    def splice_real_proxy(self, proxy_df: pd.DataFrame, real_data: pd.DataFrame) -> pd.DataFrame:
        """方案 2.4: 真实行业资金流与 proxy 历史拼接——有真实数据的日期用
        真实净流入（带符号，大幅净流出=恐慌），其余日期用 proxy 幅度。"""
        df = proxy_df[["date", "flow_magnitude"]].copy()
        if real_data is not None and not real_data.empty:
            real_dates = set(real_data["date"])
            df = df[~df["date"].isin(real_dates)]
            real_df = pd.DataFrame({
                "date": real_data["date"],
                "flow_magnitude": real_data["value"],
            })
            df = pd.concat([df, real_df], ignore_index=True)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date").reset_index(drop=True)
        df["date"] = df["date"].dt.strftime("%Y-%m-%d")
        return df

    def run(self, date: str, lookback_days: int = None) -> dict:
        if lookback_days is None:
            lookback_days = self._window + 60

        end_date = date
        start_date = pd.Timestamp(date) - pd.Timedelta(days=lookback_days * 1.5)
        start_date = start_date.strftime("%Y-%m-%d")

        # Track real industry fund flow (accumulate daily)
        real_flow_result = self.fetch_industry_fund_flow(date)
        if real_flow_result.status == DataSourceStatus.HEALTHY and \
           real_flow_result.data is not None and not real_flow_result.data.empty:
            net_flow = real_flow_result.data["net_flow"].iloc[0]
            self._db.upsert_raw_data(date, "f3_industry_net_flow", float(net_flow))
            self._db.commit()

        real_data = self._db.get_raw_data("f3_industry_net_flow", start_date, end_date)

        proxy_result = self.fetch_index_proxy(start_date, end_date)
        if proxy_result.status == DataSourceStatus.HEALTHY and proxy_result.data is not None:
            proxy_df = self.calculate_flow_proxy(proxy_result.data)
            # 写入最后一行 proxy raw（用其自身的日期，避免节假日数据错配）
            last = proxy_result.data.iloc[-1]
            raw_date = last["date"] if isinstance(last["date"], str) else str(last["date"].strftime("%Y-%m-%d"))
            if not pd.isna(last.get("close")):
                self._db.upsert_raw_data(raw_date, "f3_proxy_close", float(last["close"]))
            if not pd.isna(last.get("volume")):
                self._db.upsert_raw_data(raw_date, "f3_proxy_volume", float(last["volume"]))
        elif real_data is not None and not real_data.empty:
            proxy_df = pd.DataFrame({"date": [], "flow_magnitude": []})
        else:
            self._db.upsert_status(date, "f3", "missing",
                                   proxy_result.source, proxy_result.error or "Unknown error")
            return {"f3": None, "status": "missing"}

        df = self.splice_real_proxy(proxy_df, real_data)
        df = self.calculate_percentile(df)

        today = df[df["date"] == date]
        if today.empty:
            self._db.upsert_status(date, "f3", "missing", "splice", "No data for date")
            return {"f3": None, "status": "missing"}

        percentile = today["percentile"].iloc[0]
        if pd.isna(percentile):
            self._db.upsert_status(date, "f3", "missing", "splice", "Insufficient data")
            return {"f3": None, "status": "missing"}

        score = self.calculate_score(percentile)
        today_is_real = not real_data.empty and date in real_data["date"].values
        source = "real" if today_is_real else "proxy"
        status = "normal" if today_is_real else "substituted"

        self._db.upsert_raw_data(date, "f3_percentile", percentile)
        self._db.upsert_score(date, {"F3": score})
        ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self._db.upsert_status(date, "f3", status, source, f"fetched_at={ts}")

        return {"f3": score, "status": status, "percentile": percentile}