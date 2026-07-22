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
    def __init__(self, cache_ttl: float = 6 * 3600):
        self._api = None
        self._cache_ttl = cache_ttl
        self._raw_cache = {}

    def _get_api(self):
        if self._api is None:
            from zzshare.client import DataApi
            self._api = DataApi()
        return self._api

    def _cached_range(self, kind: str, fetch_fn, start_date: str, end_date: str):
        """全区间一次拉取 + 本地切片：请求区间被缓存区间覆盖时直接复用。
        拉取时把结束日放宽到当天，避免回填逐日前进时重复请求。"""
        now = time.time()
        entry = self._raw_cache.get(kind)
        if entry is not None and now - entry[0] < self._cache_ttl \
                and entry[1] <= start_date and end_date <= entry[2]:
            return entry[3]
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        fetch_end = max(end_date, today)
        data = fetch_fn(start_date, fetch_end)
        if data:
            self._raw_cache[kind] = (now, start_date, fetch_end, data)
        return data

    def fetch_open_sentiment(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            api = self._get_api()
            data = self._cached_range(
                "open_sentiment",
                lambda s, e: _retry(lambda: api.open_sentiment_data(date1=s, date2=e)),
                start_date, end_date)
            if not data or not isinstance(data, list) or len(data) == 0:
                return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "No sentiment data")
            # 字段缺失记 NaN（绝不写 0 等假值），有缺失则整体标记 DEGRADED
            records = []
            missing = 0
            for item in data:
                row = {"date": item["date1"]}
                for col in ("up_num", "down_num", "uplimit_num", "downlimit_num"):
                    val = pd.to_numeric(item.get(col), errors="coerce")
                    if pd.isna(val):
                        missing += 1
                    row[col] = val
                records.append(row)
            result_df = pd.DataFrame(records)
            mask = (result_df["date"] >= start_date) & (result_df["date"] <= end_date)
            result_df = result_df.loc[mask].copy()
            if result_df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "No sentiment data in range")
            if missing > 0:
                return DataSourceResult(result_df, DataSourceStatus.DEGRADED, "zzshare",
                                        f"{missing} fields missing")
            return DataSourceResult(result_df, DataSourceStatus.HEALTHY, "zzshare")
        except Exception as e:
            return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", str(e))

    def fetch_market_hot_sentiment(self, start_date: str, end_date: str) -> DataSourceResult:
        try:
            api = self._get_api()
            data = self._cached_range(
                "hot_sentiment",
                lambda s, e: _retry(lambda: api.market_hot_sentiment(date1=s, date2=e),
                                    retries=3, delay=2),
                start_date, end_date)
            if not data or not isinstance(data, list) or len(data) == 0:
                return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "No hot sentiment data")
            # p_close 缺失记 NaN（绝不 fillna(100) 写假值），有缺失则标记 DEGRADED
            records = []
            missing = 0
            for item in data:
                val = pd.to_numeric(item.get("p_close"), errors="coerce")
                if pd.isna(val):
                    missing += 1
                records.append({
                    "date": pd.Timestamp(item["date"]).strftime("%Y-%m-%d"),
                    "p_close": val,
                })
            result_df = pd.DataFrame(records)
            mask = (result_df["date"] >= start_date) & (result_df["date"] <= end_date)
            result_df = result_df.loc[mask].copy()
            if result_df.empty:
                return DataSourceResult(None, DataSourceStatus.FAILED, "zzshare", "No hot sentiment data in range")
            if missing > 0:
                return DataSourceResult(result_df, DataSourceStatus.DEGRADED, "zzshare",
                                        f"{missing} fields missing")
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
            end = pd.Timestamp.now().strftime("%Y-%m-%d")
            start = (pd.Timestamp.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
            data = api.open_sentiment_data(date1=start, date2=end)
            if data and len(data) > 0:
                return DataSourceStatus.HEALTHY
            return DataSourceStatus.FAILED
        except Exception:
            return DataSourceStatus.FAILED
