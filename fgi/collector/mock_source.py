import pandas as pd
from fgi.collector.base import DataSource, DataSourceResult, DataSourceStatus


class MockSource(DataSource):
    def __init__(self, name: str = "mock", healthy: bool = True):
        self._name = name
        self._healthy = healthy

    def fetch_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "open": [100.0] * len(dates),
            "close": [100.0] * len(dates),
            "high": [100.0] * len(dates),
            "low": [100.0] * len(dates),
            "volume": [1000000] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_index_daily(self, symbol: str, start_date: str, end_date: str) -> DataSourceResult:
        return self.fetch_daily(symbol, start_date, end_date)

    def fetch_zt_pool(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "symbol": ["000001"] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_js_weibo(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "bullish_count": [100] * len(dates),
            "bearish_count": [50] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_cyb_daily(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "turnover_rate": [0.5] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_margin_data(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "融资余额": [1000000.0 + i * 100 for i in range(len(dates))],
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_northbound_data(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "net_buy": [500000.0 + i * 100 for i in range(len(dates))],
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_market_overview(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "bullish_count": [3000] * len(dates),
            "bearish_count": [1800] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_option_volume(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "call_volume": [100000] * len(dates),
            "put_volume": [80000] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_zt_stats(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "limit_up_count": [50] * len(dates),
            "limit_down_count": [10] * len(dates),
            "limit_up_ratio": [0.05] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_zt_daily_summary(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "limit_up_count": [50] * len(dates),
            "seal_fund_sum": [1000000000.0] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_sentiment_data(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "rise_num": [3000] * len(dates),
            "fall_num": [1800] * len(dates),
            "flat_num": [200] * len(dates),
            "up_num": [100] * len(dates),
            "down_num": [50] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_pe_data(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "滚动市盈率": [12.0 + i * 0.01 for i in range(len(dates))],
            "静态市盈率": [15.0] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def fetch_open_sentiment(self, start_date: str, end_date: str) -> DataSourceResult:
        if not self._healthy:
            return DataSourceResult(None, DataSourceStatus.FAILED, self._name, "Mock failure")
        dates = pd.date_range(start=start_date, end=end_date, freq="B")
        df = pd.DataFrame({
            "date": dates.strftime("%Y-%m-%d"),
            "up_num": [3044] * len(dates),
            "down_num": [2165] * len(dates),
            "uplimit_num": [97] * len(dates),
            "downlimit_num": [20] * len(dates),
        })
        return DataSourceResult(df, DataSourceStatus.HEALTHY, self._name)

    def health_check(self) -> DataSourceStatus:
        return DataSourceStatus.HEALTHY if self._healthy else DataSourceStatus.FAILED
