"""TencentSource 契约测试：解析真实返回形状、按日期裁剪、失败降级不抛异常。

不联网：session.get 用假响应替换。
"""

import pandas as pd
import pytest

from fgi.collector.base import DataSourceStatus
from fgi.collector.tencent_source import TencentSource

ROWS = [
    ["2024-01-08", "2900.0", "2910.0", "2920.0", "2890.0", "100000"],
    ["2024-01-09", "2910.0", "2920.0", "2930.0", "2900.0", "110000"],
    ["2024-01-10", "2920.0", "2930.0", "2940.0", "2910.0", "120000"],
]


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _payload(rows, key):
    # 腾讯返回的 kline 每行是 [date, open, close, high, low, volume]
    return {"data": {key: {"qday": rows}}}


def _responding_session(source, monkeypatch, rows=ROWS, key_from_request=True):
    """让假响应按请求里的代码构造 data 键，模拟真实返回。"""

    def fake_get(url, params=None, timeout=None):
        code = params["param"].split(",")[0]
        return _Resp(_payload(rows, code))

    monkeypatch.setattr(source._session, "get", fake_get)


@pytest.fixture
def source():
    return TencentSource()


class TestFetchDaily:
    def test_parses_kline_and_filters_range(self, source, monkeypatch):
        _responding_session(source, monkeypatch)
        result = source.fetch_daily("000001", "2024-01-09", "2024-01-10")

        assert result.status == DataSourceStatus.HEALTHY
        assert list(result.data["date"]) == ["2024-01-09", "2024-01-10"]
        # 数值列必须是 numeric，否则下游 rolling_percentile 会静默产出 NaN
        assert result.data["close"].dtype.kind == "f"
        assert result.data["close"].iloc[-1] == pytest.approx(2930.0)

    def test_symbol_prefix_is_normalised(self, source, monkeypatch):
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen["param"] = params["param"]
            code = params["param"].split(",")[0]
            return _Resp(_payload(ROWS, code))

        monkeypatch.setattr(source._session, "get", fake_get)
        source.fetch_daily("600000", "2024-01-08", "2024-01-10")
        assert seen["param"].startswith("sh600000")

        source.fetch_daily("sz000001", "2024-01-08", "2024-01-10")
        assert seen["param"].startswith("sz000001")

    def test_falls_back_to_day_key(self, source, monkeypatch):
        # 部分标的使用 "day" 而不是 "qday"
        def fake_get(url, params=None, timeout=None):
            code = params["param"].split(",")[0]
            return _Resp({"data": {code: {"day": ROWS}}})

        monkeypatch.setattr(source._session, "get", fake_get)
        result = source.fetch_daily("000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.HEALTHY
        assert len(result.data) == 3

    def test_missing_symbol_is_failed(self, source, monkeypatch):
        monkeypatch.setattr(source._session, "get", lambda *a, **k: _Resp({"data": {}}))
        result = source.fetch_daily("000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED
        assert result.data is None

    def test_empty_kline_is_failed(self, source, monkeypatch):
        _responding_session(source, monkeypatch, rows=[])
        result = source.fetch_daily("000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED

    def test_network_error_degrades_instead_of_raising(self, source, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("blocked")

        monkeypatch.setattr(source._session, "get", boom)
        result = source.fetch_daily("000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED
        assert "blocked" in result.error

    def test_range_without_overlap_returns_empty_frame(self, source, monkeypatch):
        _responding_session(source, monkeypatch)
        result = source.fetch_daily("000001", "2024-02-01", "2024-02-10")
        assert result.status == DataSourceStatus.HEALTHY
        assert result.data.empty


class TestHealthCheck:
    def test_ok(self, source, monkeypatch):
        monkeypatch.setattr(source._session, "get", lambda *a, **k: _Resp({}))
        assert source.health_check() == DataSourceStatus.HEALTHY

    def test_failure(self, source, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("down")

        monkeypatch.setattr(source._session, "get", boom)
        assert source.health_check() == DataSourceStatus.FAILED


def test_index_daily_shares_implementation(source, monkeypatch):
    _responding_session(source, monkeypatch)
    index = source.fetch_index_daily("sh000001", "2024-01-09", "2024-01-10")
    daily = source.fetch_daily("000001", "2024-01-09", "2024-01-10")
    pd.testing.assert_frame_equal(index.data, daily.data)
