"""AKShareSource 单元测试：实例级缓存行为 + fetch_cyb_daily 换手率。
通过 sys.modules 注入假 akshare，不依赖真实包与网络。"""
import sys
import types

import pandas as pd
import pytest

import fgi.collector.akshare_source as aks
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.base import DataSourceStatus


@pytest.fixture
def fast_retry(monkeypatch):
    """_retry 改为不重试不睡眠，加速失败路径。"""
    monkeypatch.setattr(aks, "_retry", lambda fn, **kwargs: fn())


@pytest.fixture
def fake_ak(monkeypatch):
    fake = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "akshare", fake)
    return fake


class TestInstanceCache:
    def test_cache_hit_no_refetch(self):
        src = AKShareSource()
        calls = []
        result1 = src._cached("k", lambda: calls.append(1) or "v")
        result2 = src._cached("k", lambda: calls.append(1) or "v")
        assert result1 == "v"
        assert result2 == "v"
        assert len(calls) == 1

    def test_ttl_expiry_refetches(self):
        src = AKShareSource(cache_ttl=0)
        calls = []
        src._cached("k", lambda: calls.append(1) or "v")
        src._cached("k", lambda: calls.append(1) or "v")
        assert len(calls) == 2

    def test_max_entries_evicts_oldest(self):
        src = AKShareSource(cache_max=2)
        src._cache["k1"] = (1.0, "v1")
        src._cache["k2"] = (2.0, "v2")
        src._cached("k3", lambda: "v3")
        assert "k1" not in src._cache
        assert "k2" in src._cache
        assert "k3" in src._cache

    def test_none_result_not_cached(self):
        src = AKShareSource()
        calls = []
        assert src._cached("k", lambda: calls.append(1) or None) is None
        assert "k" not in src._cache
        assert src._cached("k", lambda: calls.append(1) or "v") == "v"
        assert len(calls) == 2

    def test_empty_result_not_cached(self):
        src = AKShareSource()
        calls = []
        empty = pd.DataFrame()
        result = src._cached("k", lambda: calls.append(1) or empty)
        assert result.empty
        assert "k" not in src._cache
        src._cached("k", lambda: calls.append(1) or empty)
        assert len(calls) == 2

    def test_no_cross_instance_pollution(self):
        src1 = AKShareSource()
        src2 = AKShareSource()
        src1._cached("k", lambda: "v1")
        assert src2._cached("k", lambda: "v2") == "v2"


def _cyb_hist_df():
    dates = pd.date_range("2024-01-01", "2024-01-10", freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "volume": [1e8] * len(dates),
    })


class TestFetchCybDaily:
    def test_uses_volume_column(self, fake_ak, fast_retry):
        fake_ak.stock_zh_index_daily = lambda **kwargs: _cyb_hist_df()
        src = AKShareSource()
        result = src.fetch_cyb_daily("2024-01-02", "2024-01-05")
        assert result.status == DataSourceStatus.HEALTHY
        df = result.data
        assert list(df.columns) == ["date", "volume"]
        assert df["date"].min() >= "2024-01-02"
        assert df["date"].max() <= "2024-01-05"
        # 成交量量级 > 1
        assert df["volume"].min() > 1e6
    def test_full_range_cached_and_sliced(self, fake_ak, fast_retry):
        calls = []
        fake_ak.stock_zh_index_daily = lambda **kwargs: calls.append(kwargs) or _cyb_hist_df()
        src = AKShareSource()
        r1 = src.fetch_cyb_daily("2024-01-02", "2024-01-03")
        r2 = src.fetch_cyb_daily("2024-01-04", "2024-01-05")
        assert r1.status == DataSourceStatus.HEALTHY
        assert r2.status == DataSourceStatus.HEALTHY
        assert len(calls) == 1
        # 一次拉取全区间（不随请求区间变化）
        assert calls[0]["symbol"] == "sz399006"

    def test_no_data_failed(self, fake_ak, fast_retry):
        fake_ak.index_zh_a_hist = lambda **kwargs: pd.DataFrame()
        src = AKShareSource()
        result = src.fetch_cyb_daily("2024-01-02", "2024-01-05")
        assert result.status == DataSourceStatus.FAILED

    def test_no_data_in_range_failed(self, fake_ak, fast_retry):
        fake_ak.index_zh_a_hist = lambda **kwargs: _cyb_hist_df()
        src = AKShareSource()
        result = src.fetch_cyb_daily("2025-01-01", "2025-01-10")
        assert result.status == DataSourceStatus.FAILED


def _market_fund_flow_df():
    """模拟 ak.stock_market_fund_flow() 返回格式：120 天历史主力净流入。"""
    dates = pd.date_range("2024-01-01", periods=120, freq="B")
    return pd.DataFrame({
        "日期": dates.strftime("%Y-%m-%d"),
        "主力净流入-净额": [-1e10 + i * 1e8 for i in range(120)],
    })


class TestFetchIndustryFundFlow:
    """Issue #42: fetch_industry_fund_flow 必须使用 stock_market_fund_flow（历史接口），
    不再使用 stock_fund_flow_industry(symbol='即时')（实时接口）。"""

    def test_uses_historical_endpoint_returns_full_range(self, fake_ak, fast_retry):
        """fetch_industry_fund_flow 应返回 120 天历史数据，而非单一当日快照。"""
        fake_ak.stock_market_fund_flow = lambda **kwargs: _market_fund_flow_df()
        src = AKShareSource()
        result = src.fetch_industry_fund_flow("2024-01-01", "2024-06-30")
        assert result.status == DataSourceStatus.HEALTHY
        df = result.data
        # 必须返回多日历史，而非只有一行当日快照
        assert len(df) == 120
        assert list(df.columns) == ["date", "net_flow"]
        assert df["date"].min() == "2024-01-01"
        assert df["date"].max() >= "2024-06-01"
        # net_flow 应为数值类型
        assert df["net_flow"].dtype.kind in "iuf"

    def test_no_data_failed(self, fake_ak, fast_retry):
        fake_ak.stock_market_fund_flow = lambda **kwargs: pd.DataFrame()
        src = AKShareSource()
        result = src.fetch_industry_fund_flow("2024-01-01", "2024-06-30")
        assert result.status == DataSourceStatus.FAILED

    def test_filters_by_date_range(self, fake_ak, fast_retry):
        """请求窗口外的数据应被过滤。"""
        fake_ak.stock_market_fund_flow = lambda **kwargs: _market_fund_flow_df()
        src = AKShareSource()
        result = src.fetch_industry_fund_flow("2024-01-15", "2024-01-25")
        assert result.status == DataSourceStatus.HEALTHY
        df = result.data
        assert df["date"].min() >= "2024-01-15"
        assert df["date"].max() <= "2024-01-25"


def _qvix_df():
    """模拟 ak.index_option_50etf_qvix() 返回格式：date + OHLC。"""
    dates = pd.date_range("2024-01-01", periods=100, freq="B")
    return pd.DataFrame({
        "date": dates.strftime("%Y-%m-%d"),
        "open": [20.0] * 100,
        "high": [22.0] * 100,
        "low": [18.0] * 100,
        "close": [19.0 + i * 0.05 for i in range(100)],  # 渐变避免退化
    })


class TestFetchQvix:
    """V4 (QVIX 中国版 VIX): fetch_qvix 必须返回 50ETF 期权隐含波动率历史。"""

    def test_returns_close_column_full_range(self, fake_ak, fast_retry):
        fake_ak.index_option_50etf_qvix = lambda: _qvix_df()
        src = AKShareSource()
        result = src.fetch_qvix("2024-01-02", "2024-01-31")
        assert result.status == DataSourceStatus.HEALTHY
        df = result.data
        assert list(df.columns) == ["date", "close"]
        assert df["date"].min() >= "2024-01-02"
        assert df["date"].max() <= "2024-01-31"
        # close 数值类型
        assert df["close"].dtype.kind in "iuf"

    def test_no_data_failed(self, fake_ak, fast_retry):
        fake_ak.index_option_50etf_qvix = lambda: pd.DataFrame()
        src = AKShareSource()
        result = src.fetch_qvix("2024-01-02", "2024-01-31")
        assert result.status == DataSourceStatus.FAILED

    def test_full_range_cached_and_sliced(self, fake_ak, fast_retry):
        calls = []
        fake_ak.index_option_50etf_qvix = lambda: calls.append(1) or _qvix_df()
        src = AKShareSource()
        r1 = src.fetch_qvix("2024-01-02", "2024-01-05")
        r2 = src.fetch_qvix("2024-01-08", "2024-01-10")
        assert r1.status == DataSourceStatus.HEALTHY
        assert r2.status == DataSourceStatus.HEALTHY
        # 一次拉取全量后切片
        assert len(calls) == 1
