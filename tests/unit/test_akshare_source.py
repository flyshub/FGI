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
        "日期": dates.strftime("%Y-%m-%d"),
        "开盘": [1800.0] * len(dates),
        "收盘": [1810.0] * len(dates),
        "成交量": [1e8] * len(dates),
        "换手率": [3.5 + i * 0.1 for i in range(len(dates))],
    })


class TestFetchCybDaily:
    def test_uses_turnover_column(self, fake_ak, fast_retry):
        fake_ak.index_zh_a_hist = lambda **kwargs: _cyb_hist_df()
        src = AKShareSource()
        result = src.fetch_cyb_daily("2024-01-02", "2024-01-05")
        assert result.status == DataSourceStatus.HEALTHY
        df = result.data
        assert list(df.columns) == ["date", "turnover_rate"]
        assert df["date"].min() >= "2024-01-02"
        assert df["date"].max() <= "2024-01-05"
        # 换手率单位 %，不是成交量
        assert df["turnover_rate"].max() < 10.0

    def test_full_range_cached_and_sliced(self, fake_ak, fast_retry):
        calls = []
        fake_ak.index_zh_a_hist = lambda **kwargs: calls.append(kwargs) or _cyb_hist_df()
        src = AKShareSource()
        r1 = src.fetch_cyb_daily("2024-01-02", "2024-01-03")
        r2 = src.fetch_cyb_daily("2024-01-04", "2024-01-05")
        assert r1.status == DataSourceStatus.HEALTHY
        assert r2.status == DataSourceStatus.HEALTHY
        assert len(calls) == 1
        # 一次拉取全区间（不随请求区间变化）
        assert calls[0]["start_date"] == "19900101"

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
