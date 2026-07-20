import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus


class AKShareSource(DataSource):
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            import akshare as ak
            self._client = ak
        return self._client

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = ak.stock_zh_index_daily(symbol=f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}")
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = ak.stock_zh_index_daily(symbol=symbol)
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_margin_data(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = ak.stock_margin_sse(start_date=start_date.replace("-", ""),
                                      end_date=end_date.replace("-", ""))
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df = df.rename(columns={"信用交易日期": "date"})
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d").dt.strftime("%Y-%m-%d")
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def fetch_northbound_data(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            ak = self._get_client()
            df = ak.stock_hsgt_hist_em(symbol="沪股通")
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", "No data")
            df = df.rename(columns={"日期": "date", "当日成交净买额": "net_buy"})
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "akshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "akshare", str(e))

    def health_check(self) -> DataSourceStatus:
        try:
            ak = self._get_client()
            ak.stock_zh_index_daily(symbol="sh000001")
            return DataSourceStatus.HEALTHY
        except Exception:
            return DataSourceStatus.FAILED
