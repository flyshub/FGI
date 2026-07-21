import time
import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus


def _retry(fn, retries=3, delay=2):
    last_err = None
    for i in range(retries):
        try:
            return fn()
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(delay * (2 ** i))
    raise last_err


class ZZShareSource(DataSource):
    def __init__(self):
        self._api = None

    def _get_api(self):
        if self._api is None:
            from zzshare.client import DataApi
            self._api = DataApi()
        return self._api

    def fetch_open_sentiment(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            api = self._get_api()
            data = _retry(lambda: api.open_sentiment_data(date1=start_date, date2=end_date))
            if not data or not isinstance(data, list) or len(data) == 0:
                return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "No sentiment data")
            records = []
            for item in data:
                records.append({
                    "date": item["date1"],
                    "up_num": int(item.get("up_num", 0)),
                    "down_num": int(item.get("down_num", 0)),
                    "uplimit_num": int(item.get("uplimit_num", 0)),
                    "downlimit_num": int(item.get("downlimit_num", 0)),
                })
            result_df = pd.DataFrame(records)
            mask = (result_df["date"] >= start_date) & (result_df["date"] <= end_date)
            result_df = result_df.loc[mask].copy()
            if result_df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "No sentiment data in range")
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "zzshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", str(e))

    def fetch_market_hot_sentiment(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            api = self._get_api()
            data = _retry(
                lambda: api.market_hot_sentiment(date1=start_date, date2=end_date),
                retries=3, delay=2
            )
            if not data or not isinstance(data, list) or len(data) == 0:
                return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "No hot sentiment data")
            records = []
            for item in data:
                records.append({
                    "date": pd.Timestamp(item["date"]).strftime("%Y-%m-%d"),
                    "p_close": float(item.get("p_close", 100.0)),
                })
            result_df = pd.DataFrame(records)
            mask = (result_df["date"] >= start_date) & (result_df["date"] <= end_date)
            result_df = result_df.loc[mask].copy()
            if result_df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "No hot sentiment data in range")
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "zzshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", str(e))

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "Not supported")

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "Not supported")

    def health_check(self) -> DataSourceStatus:
        try:
            api = self._get_api()
            data = api.open_sentiment_data(date1="2026-07-20", date2="2026-07-21")
            if data and len(data) > 0:
                return DataSourceStatus.HEALTHY
            return DataSourceStatus.FAILED
        except Exception:
            return DataSourceStatus.FAILED
