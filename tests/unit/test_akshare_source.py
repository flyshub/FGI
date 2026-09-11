"""AKShareSource 单元测试：实例级缓存行为 + fetch_cyb_daily 换手率 + fetch_pe_data 日频插值。
通过 sys.modules 注入假 akshare，不依赖真实包与网络。"""

import sys
import types
from unittest.mock import patch

import pandas as pd
import pytest

import fgi.collector.akshare_source as aks
from fgi.collector.akshare_source import AKShareSource
from fgi.collector.base import DataSourceResult, DataSourceStatus


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
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "volume": [1e8] * len(dates),
        }
    )


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
    return pd.DataFrame(
        {
            "日期": dates.strftime("%Y-%m-%d"),
            "主力净流入-净额": [-1e10 + i * 1e8 for i in range(120)],
        }
    )


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
    return pd.DataFrame(
        {
            "date": dates.strftime("%Y-%m-%d"),
            "open": [20.0] * 100,
            "high": [22.0] * 100,
            "low": [18.0] * 100,
            "close": [19.0 + i * 0.05 for i in range(100)],  # 渐变避免退化
        }
    )


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


# ---------------------------------------------------------------------------
# fetch_pe_data 日频插值测试
# ---------------------------------------------------------------------------


def _pe_monthly_df():
    """模拟 ak.stock_index_pe_lg(symbol='沪深300') 返回格式：月末PE。"""
    dates = pd.to_datetime(
        ["2024-06-28", "2024-07-31", "2024-08-30", "2024-09-30", "2024-10-31"]
    )
    return pd.DataFrame(
        {
            "日期": dates,
            "滚动市盈率": [12.5, 13.0, 12.8, 13.2, 12.9],
        }
    )


def _index_daily_df():
    """模拟 ak.stock_zh_index_daily(symbol='sh000300') 返回格式：日频 OHLCV。"""
    # 2024-07-01 ~ 2024-10-31 (覆盖 4 个月末 PE 锚点)
    dates = pd.bdate_range("2024-07-01", "2024-10-31")
    # 模拟价格渐变：7/1=4500 → 10/31=4400
    n = len(dates)
    close = [4500.0 + i * (4400.0 - 4500.0) / (n - 1) for i in range(n)]
    return pd.DataFrame(
        {
            "date": dates,
            "open": close,
            "high": [c + 10 for c in close],
            "low": [c - 10 for c in close],
            "close": close,
            "volume": [1e9] * n,
        }
    )


class TestFetchPEDataInterpolation:
    """V1 日频插值：月末PE锚点精度 + 非月末插值公式 + 边界/降级。"""

    def test_monthend_anchor_matches_original(self, fake_ak, fast_retry):
        """月末PE锚点值应与原始月频数据一致。"""
        fake_ak.stock_index_pe_lg = lambda symbol: _pe_monthly_df()
        fake_ak.stock_zh_index_daily = lambda symbol: _index_daily_df()
        src = AKShareSource()
        result = src.fetch_pe_data("2024-07-01", "2024-10-31")
        assert result.status == DataSourceStatus.HEALTHY
        df = result.data
        # 月末日期应精确匹配原始 PE
        jul31 = df[df["date"] == "2024-07-31"]["滚动市盈率"].iloc[0]
        aug30 = df[df["date"] == "2024-08-30"]["滚动市盈率"].iloc[0]
        sep30 = df[df["date"] == "2024-09-30"]["滚动市盈率"].iloc[0]
        oct31 = df[df["date"] == "2024-10-31"]["滚动市盈率"].iloc[0]
        assert abs(jul31 - 13.0) < 0.01
        assert abs(aug30 - 12.8) < 0.01
        assert abs(sep30 - 13.2) < 0.01
        assert abs(oct31 - 12.9) < 0.01

    def test_interpolation_formula(self, fake_ak, fast_retry):
        """非月末交易日应符合公式：daily_pe = me_pe × (daily_close / me_close)。"""
        fake_ak.stock_index_pe_lg = lambda symbol: _pe_monthly_df()
        fake_ak.stock_zh_index_daily = lambda symbol: _index_daily_df()
        src = AKShareSource()
        result = src.fetch_pe_data("2024-07-01", "2024-10-31")
        df = result.data
        idx_df = _index_daily_df()
        idx_df["date"] = pd.to_datetime(idx_df["date"]).dt.strftime("%Y-%m-%d")

        # 验证 2024-08-05（非月末）的插值
        row = df[df["date"] == "2024-08-05"]
        assert len(row) == 1
        daily_pe = row["滚动市盈率"].iloc[0]
        # 最近月末锚点：2024-07-31 (PE=13.0)
        me_pe = 13.0
        daily_close = idx_df[idx_df["date"] == "2024-08-05"]["close"].iloc[0]
        me_close = idx_df[idx_df["date"] == "2024-07-31"]["close"].iloc[0]
        expected = me_pe * daily_close / me_close
        assert abs(daily_pe - expected) < 0.01

    def test_daily_count_matches_trading_days(self, fake_ak, fast_retry):
        """输出行数应等于请求范围内的交易日数。"""
        fake_ak.stock_index_pe_lg = lambda symbol: _pe_monthly_df()
        fake_ak.stock_zh_index_daily = lambda symbol: _index_daily_df()
        src = AKShareSource()
        result = src.fetch_pe_data("2024-08-01", "2024-08-31")
        assert result.status == DataSourceStatus.HEALTHY
        # 2024-08-01 ~ 2024-08-31 的交易日数
        expected_count = len(
            pd.bdate_range("2024-08-01", "2024-08-31")
        )
        assert len(result.data) == expected_count

    def test_no_index_data_fallback_to_monthly(self, fake_ak, fast_retry):
        """fetch_index_daily 失败时应降级返回月频数据。"""
        fake_ak.stock_index_pe_lg = lambda symbol: _pe_monthly_df()
        # stock_zh_index_daily 返回空 → index 失败
        fake_ak.stock_zh_index_daily = lambda symbol: pd.DataFrame()
        src = AKShareSource()
        result = src.fetch_pe_data("2024-07-01", "2024-10-31")
        assert result.status == DataSourceStatus.HEALTHY
        # 降级返回月频，行数 = 4 (7/31, 8/30, 9/30, 10/31)
        assert len(result.data) == 4

    def test_no_pe_in_range_failed(self, fake_ak, fast_retry):
        """请求范围内无PE数据时应返回 FAILED。"""
        fake_ak.stock_index_pe_lg = lambda symbol: _pe_monthly_df()
        fake_ak.stock_zh_index_daily = lambda symbol: _index_daily_df()
        src = AKShareSource()
        result = src.fetch_pe_data("2023-01-01", "2023-01-31")
        assert result.status == DataSourceStatus.FAILED

    def test_empty_pe_source_failed(self, fake_ak, fast_retry):
        """PE 源数据为空时应返回 FAILED。"""
        fake_ak.stock_index_pe_lg = lambda symbol: pd.DataFrame()
        fake_ak.stock_zh_index_daily = lambda symbol: _index_daily_df()
        src = AKShareSource()
        result = src.fetch_pe_data("2024-07-01", "2024-10-31")
        assert result.status == DataSourceStatus.FAILED
