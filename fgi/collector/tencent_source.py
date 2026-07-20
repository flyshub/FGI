import requests
import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus


class TencentSource(DataSource):
    def __init__(self):
        self._session = requests.Session()

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        try:
            if symbol.startswith("sh") or symbol.startswith("sz"):
                code = symbol
            else:
                code = f"sh{symbol}" if symbol.startswith("6") else f"sz{symbol}"

            url = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            params = {"param": f"{code},day,,,{1500},qfq"}
            resp = self._session.get(url, params=params, timeout=10)
            data = resp.json()

            if code not in data.get("data", {}):
                return DataSourceResult(None, DataSourceStatus.FAILED, "tencent", "No data")

            kline = data["data"][code].get("qday", data["data"][code].get("day", []))
            if not kline:
                return DataSourceResult(None, DataSourceStatus.FAILED, "tencent", "No data")

            df = pd.DataFrame(kline, columns=["date", "open", "close", "high", "low", "volume"])
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
            for col in ["open", "close", "high", "low", "volume"]:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            mask = (df["date"] >= start_date) & (df["date"] <= end_date)
            df = df.loc[mask].copy()
            return DataSourceResult(df, DataSourceStatus.HEALTHY, "tencent")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "tencent", str(e))

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        return self.fetch_daily(symbol, start_date, end_date)

    def health_check(self) -> DataSourceStatus:
        try:
            resp = self._session.get("http://qt.gtimg.cn/q=sh000001", timeout=5)
            if resp.status_code == 200:
                return DataSourceStatus.HEALTHY
            return DataSourceStatus.FAILED
        except Exception:
            return DataSourceStatus.FAILED
