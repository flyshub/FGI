import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus


class MootdxSource(DataSource):
    def __init__(self):
        self._client = None

    def _get_client(self):
        if self._client is None:
            from mootdx.quotes import Quotes
            self._client = Quotes.factory(market='std', multithread=True, heartbeat=True)
        return self._client

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            client = self._get_client()
            df = client.bars(symbol=symbol, frequency=9, offset=800, adjust='qfq')
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "mootdx", "No data")
            df = df.rename(columns={"date": "date", "close": "close", "open": "open",
                                     "high": "high", "low": "low", "vol": "volume"})
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "mootdx")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "mootdx", str(e))

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            client = self._get_client()
            if symbol.startswith("sh"):
                code = symbol[2:]
            elif symbol.startswith("sz"):
                code = symbol[2:]
            else:
                code = symbol
            df = client.bars(symbol=code, frequency=9, offset=800)
            if df is None or df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "mootdx", "No data")
            df = df.rename(columns={"date": "date", "close": "close"})
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "mootdx")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "mootdx", str(e))

    def health_check(self) -> DataSourceStatus:
        try:
            self._get_client()
            return DataSourceStatus.HEALTHY
        except Exception:
            return DataSourceStatus.FAILED
