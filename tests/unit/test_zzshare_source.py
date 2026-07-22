"""ZZShareSource 单元测试：字段缺失=NaN、DEGRADED 状态、health_check 相对日期、
_cached_range 全区间一次拉取+本地切片。注入假 zzshare，不依赖真实包与网络。"""
import sys
import types

import pandas as pd
import pytest

import fgi.collector.zzshare_source as zzs
from fgi.collector.zzshare_source import ZZShareSource
from fgi.collector.base import DataSourceStatus


class FakeDataApi:
    def __init__(self, open_data=None, hot_data=None):
        self._open_data = open_data if open_data is not None else []
        self._hot_data = hot_data if hot_data is not None else []
        self.open_calls = []
        self.hot_calls = []

    def open_sentiment_data(self, date1=None, date2=None):
        self.open_calls.append((date1, date2))
        return self._open_data

    def market_hot_sentiment(self, date1=None, date2=None):
        self.hot_calls.append((date1, date2))
        return self._hot_data


@pytest.fixture
def source_factory(monkeypatch):
    monkeypatch.setattr(zzs, "_retry", lambda fn, **kwargs: fn())

    def _make(open_data=None, hot_data=None):
        api = FakeDataApi(open_data=open_data, hot_data=hot_data)
        zzshare_mod = types.ModuleType("zzshare")
        client_mod = types.ModuleType("zzshare.client")
        client_mod.DataApi = lambda: api
        zzshare_mod.client = client_mod
        monkeypatch.setitem(sys.modules, "zzshare", zzshare_mod)
        monkeypatch.setitem(sys.modules, "zzshare.client", client_mod)
        src = ZZShareSource()
        return src, api

    return _make


def _open_item(date, up=3000, down=1500, uplimit=50, downlimit=5):
    return {"date1": date, "up_num": up, "down_num": down,
            "uplimit_num": uplimit, "downlimit_num": downlimit}


class TestFetchOpenSentiment:
    def test_complete_data_healthy(self, source_factory):
        src, _ = source_factory(open_data=[
            _open_item("2024-01-02"), _open_item("2024-01-03")])
        result = src.fetch_open_sentiment("2024-01-01", "2024-01-31")
        assert result.status == DataSourceStatus.HEALTHY
        assert len(result.data) == 2
        assert result.data["up_num"].iloc[0] == 3000

    def test_missing_field_is_nan_and_degraded(self, source_factory):
        item = _open_item("2024-01-02")
        del item["down_num"]
        src, _ = source_factory(open_data=[item])
        result = src.fetch_open_sentiment("2024-01-01", "2024-01-31")
        assert result.status == DataSourceStatus.DEGRADED
        assert "missing" in result.error
        # 缺失字段是 NaN，绝不写 0 假值
        assert pd.isna(result.data["down_num"].iloc[0])
        assert (result.data["down_num"] != 0).all()

    def test_no_data_failed(self, source_factory):
        src, _ = source_factory(open_data=[])
        result = src.fetch_open_sentiment("2024-01-01", "2024-01-31")
        assert result.status == DataSourceStatus.FAILED

    def test_cached_range_slices_without_refetch(self, source_factory):
        src, api = source_factory(open_data=[
            _open_item("2024-01-02"), _open_item("2024-01-03")])
        src.fetch_open_sentiment("2024-01-01", "2024-01-10")
        result = src.fetch_open_sentiment("2024-01-02", "2024-01-03")
        assert result.status == DataSourceStatus.HEALTHY
        assert len(api.open_calls) == 1
        assert len(result.data) == 2


class TestFetchMarketHotSentiment:
    def test_missing_pclose_is_nan_and_degraded(self, source_factory):
        src, _ = source_factory(hot_data=[
            {"date": "2024-01-02", "p_close": 45.5},
            {"date": "2024-01-03"},  # p_close 缺失
        ])
        result = src.fetch_market_hot_sentiment("2024-01-01", "2024-01-31")
        assert result.status == DataSourceStatus.DEGRADED
        assert pd.isna(result.data["p_close"].iloc[1])
        # 绝不 fillna(100)
        assert (result.data["p_close"] != 100).all()

    def test_complete_data_healthy(self, source_factory):
        src, _ = source_factory(hot_data=[{"date": "2024-01-02", "p_close": 45.5}])
        result = src.fetch_market_hot_sentiment("2024-01-01", "2024-01-31")
        assert result.status == DataSourceStatus.HEALTHY
        assert result.data["p_close"].iloc[0] == 45.5


class TestHealthCheck:
    def test_uses_relative_dates(self, source_factory):
        src, api = source_factory(open_data=[_open_item("2024-01-02")])
        assert src.health_check() == DataSourceStatus.HEALTHY
        assert len(api.open_calls) == 1
        date1, date2 = api.open_calls[0]
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        week_ago = (pd.Timestamp.now() - pd.Timedelta(days=7)).strftime("%Y-%m-%d")
        assert date2 == today
        assert date1 == week_ago

    def test_empty_data_failed(self, source_factory):
        src, _ = source_factory(open_data=[])
        assert src.health_check() == DataSourceStatus.FAILED
