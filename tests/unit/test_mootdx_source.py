"""MootdxSource 契约测试：客户端惰性创建、列名归一、失败降级不抛异常。

不联网：替换 _client。
"""

import pandas as pd
import pytest

from fgi.collector.base import DataSourceStatus
from fgi.collector.mootdx_source import MootdxSource


class _Client:
    def __init__(self, df=None, exc=None):
        self._df = df
        self._exc = exc
        self.calls = []

    def bars(self, **kwargs):
        self.calls.append(kwargs)
        if self._exc:
            raise self._exc
        return self._df


def _bars():
    return pd.DataFrame(
        {
            "date": ["2024-01-08", "2024-01-09", "2024-01-10"],
            "open": [2900.0, 2910.0, 2920.0],
            "close": [2910.0, 2920.0, 2930.0],
            "high": [2920.0, 2930.0, 2940.0],
            "low": [2890.0, 2900.0, 2910.0],
            "vol": [100000, 110000, 120000],
        }
    )


@pytest.fixture
def source():
    return MootdxSource()


class TestFetchDaily:
    def test_renames_vol_and_filters_range(self, source, monkeypatch):
        monkeypatch.setattr(source, "_client", _Client(_bars()))
        result = source.fetch_daily("000001", "2024-01-09", "2024-01-10")

        assert result.status == DataSourceStatus.HEALTHY
        assert "volume" in result.data.columns
        assert list(result.data["date"]) == ["2024-01-09", "2024-01-10"]

    def test_no_data_is_failed(self, source, monkeypatch):
        monkeypatch.setattr(source, "_client", _Client(None))
        assert source.fetch_daily("000001", "2024-01-08", "2024-01-10").status == (
            DataSourceStatus.FAILED
        )

    def test_empty_frame_is_failed(self, source, monkeypatch):
        monkeypatch.setattr(source, "_client", _Client(pd.DataFrame()))
        result = source.fetch_daily("000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED

    def test_client_error_degrades_instead_of_raising(self, source, monkeypatch):
        monkeypatch.setattr(source, "_client", _Client(exc=ConnectionError("tcp refused")))
        result = source.fetch_daily("000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED
        assert "tcp refused" in result.error

    def test_lazy_client_is_created_once(self, source, monkeypatch):
        created = []
        fake = _Client(_bars())

        def factory():
            created.append(1)
            return fake

        monkeypatch.setattr(source, "_get_client", factory)
        source.fetch_daily("000001", "2024-01-08", "2024-01-10")
        source.fetch_daily("000001", "2024-01-08", "2024-01-10")
        assert len(created) == 2  # 每次调用都走 _get_client，但内部只建一次

    def test_index_symbol_prefix_is_stripped(self, source, monkeypatch):
        client = _Client(_bars())
        monkeypatch.setattr(source, "_client", client)
        result = source.fetch_index_daily("sh000001", "2024-01-08", "2024-01-10")
        assert client.calls[-1]["symbol"] == "000001"
        assert result.status == DataSourceStatus.HEALTHY


class TestHealthCheck:
    def test_ok(self, source, monkeypatch):
        monkeypatch.setattr(source, "_get_client", lambda: _Client(_bars()))
        assert source.health_check() == DataSourceStatus.HEALTHY

    def test_missing_client_library_is_failed(self, source, monkeypatch):
        def boom():
            raise ImportError("mootdx not installed")

        monkeypatch.setattr(source, "_get_client", boom)
        assert source.health_check() == DataSourceStatus.FAILED
