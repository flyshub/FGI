"""FuyaoSource 契约测试：thscode 映射、5 年钳位、区间裁剪、错误码降级。

不联网：session.get 用假响应替换。
"""

from datetime import date, datetime, timedelta

import pytest

from fgi.collector.base import DataSourceStatus
from fgi.collector.chains import configure_manager
from fgi.collector.fallback import DataSourceManager
from fgi.collector.fuyao_source import (
    MAX_HISTORY_DAYS,
    TZ,
    FuyaoSource,
    _to_ms,
    to_thscode,
)
from fgi.collector.mock_source import MockSource

BARS = [
    {"date_ms": _to_ms("2024-01-08"), "close_price": 2900.0, "volume": 100000},
    {"date_ms": _to_ms("2024-01-09"), "close_price": 2910.0, "volume": 110000},
    {"date_ms": _to_ms("2024-01-10"), "close_price": 2920.0, "volume": 120000},
]


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _ok(items):
    return _Resp({"code": 0, "message": "ok", "data": {"item": items}})


@pytest.fixture
def source(monkeypatch):
    return FuyaoSource(api_key="test-key")


class TestThscode:
    @pytest.mark.parametrize(
        ("symbol", "expected"),
        [
            ("sh000001", "000001.SH"),
            ("sz399006", "399006.SZ"),
            ("SH000300", "000300.SH"),
            ("000001.SH", "000001.SH"),
            ("600000", "600000.SH"),
            ("000001", "000001.SZ"),
        ],
    )
    def test_mapping(self, symbol, expected):
        assert to_thscode(symbol) == expected


class TestFetchIndexDaily:
    def test_parses_bars_and_filters_range(self, source, monkeypatch):
        monkeypatch.setattr(source._session, "get", lambda *a, **k: _ok(BARS))
        result = source.fetch_index_daily("sh000001", "2024-01-09", "2024-01-10")

        assert result.status == DataSourceStatus.HEALTHY
        assert result.source == "fuyao"
        assert list(result.data["date"]) == ["2024-01-09", "2024-01-10"]
        # 数值列必须是 numeric，否则下游 rolling_percentile 会静默产出 NaN
        assert result.data["close"].dtype.kind == "f"
        assert result.data["close"].iloc[-1] == pytest.approx(2920.0)

    def test_start_is_clamped_to_five_years(self, source, monkeypatch):
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen.update(params)
            return _ok(BARS)

        monkeypatch.setattr(source._session, "get", fake_get)
        source.fetch_index_daily("sh000001", "2015-01-01", "2024-01-10")

        limit = datetime.now(TZ).date() - timedelta(days=MAX_HISTORY_DAYS)
        assert seen["start"] >= _to_ms(limit.isoformat())
        assert seen["start"] > _to_ms("2015-01-01")

    def test_end_is_clamped_to_today(self, source, monkeypatch):
        seen = {}

        def fake_get(url, params=None, timeout=None):
            seen.update(params)
            return _ok(BARS)

        monkeypatch.setattr(source._session, "get", fake_get)
        source.fetch_index_daily("sh000001", "2024-01-01", "2099-01-01")

        assert seen["end"] <= int(datetime.now(TZ).timestamp() * 1000)

    def test_stale_last_bar_is_degraded(self, source, monkeypatch):
        monkeypatch.setattr(source._session, "get", lambda *a, **k: _ok(BARS))
        result = source.fetch_index_daily("sh000001", "2024-01-08", "2024-01-12")
        assert result.status == DataSourceStatus.DEGRADED
        assert "2024-01-10" in result.error

    def test_empty_items_is_failed(self, source, monkeypatch):
        monkeypatch.setattr(source._session, "get", lambda *a, **k: _ok([]))
        result = source.fetch_index_daily("sh000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED
        assert result.data is None

    def test_business_error_degrades_instead_of_raising(self, source, monkeypatch):
        monkeypatch.setattr(
            source._session,
            "get",
            lambda *a, **k: _Resp({"code": 1003, "message": "查询区间需落在近 5 年"}),
        )
        result = source.fetch_index_daily("sh000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED
        assert "1003" in result.error

    def test_network_error_degrades_instead_of_raising(self, source, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("blocked")

        monkeypatch.setattr(source._session, "get", boom)
        result = source.fetch_index_daily("sh000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.FAILED
        assert "blocked" in result.error

    def test_rate_limit_is_retried(self, source, monkeypatch):
        monkeypatch.setattr("fgi.collector.fuyao_source.time.sleep", lambda *_: None)
        calls = []

        def fake_get(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                return _Resp({"code": 4001, "message": "rate limited"})
            return _ok(BARS)

        monkeypatch.setattr(source._session, "get", fake_get)
        result = source.fetch_index_daily("sh000001", "2024-01-08", "2024-01-10")
        assert result.status == DataSourceStatus.HEALTHY
        assert len(calls) == 2


class TestFetchCybDaily:
    def test_returns_volume_only(self, source, monkeypatch):
        monkeypatch.setattr(source._session, "get", lambda *a, **k: _ok(BARS))
        result = source.fetch_cyb_daily("2024-01-08", "2024-01-10")

        assert result.status == DataSourceStatus.HEALTHY
        assert list(result.data.columns) == ["date", "volume"]
        assert result.data["volume"].iloc[-1] == pytest.approx(120000)


class TestHealthCheck:
    def test_ok(self, source, monkeypatch):
        monkeypatch.setattr(source._session, "get", lambda *a, **k: _ok([]))
        assert source.health_check() == DataSourceStatus.HEALTHY

    def test_failure(self, source, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("down")

        monkeypatch.setattr(source._session, "get", boom)
        assert source.health_check() == DataSourceStatus.FAILED


def test_missing_key_raises():
    with pytest.raises(ValueError):
        FuyaoSource(api_key="")


class TestChainPreference:
    """ADR-0005：生产路径把 fuyao 提到链首，回填路径完全不启用它。"""

    def _manager(self):
        manager = DataSourceManager()
        manager.register_source("akshare", MockSource("akshare"))
        manager.register_source("fuyao", MockSource("fuyao"))
        return manager

    def test_primary_lifts_fuyao(self):
        manager = self._manager()
        configure_manager(manager, extra_fallbacks=["fuyao"], primary="fuyao")

        for chain in ("m3_index", "m4_cyb_volume", "f3_index"):
            assert manager._chains[chain]._sources == [
                manager._sources["fuyao"],
                manager._sources["akshare"],
            ]

    def test_backfill_never_uses_fuyao(self):
        manager = self._manager()
        configure_manager(manager)

        for chain in ("m3_index", "m4_cyb_volume", "f3_index"):
            assert manager._chains[chain]._sources == [manager._sources["akshare"]]


def test_date_roundtrip():
    assert datetime.fromtimestamp(_to_ms("2024-01-10") / 1000, TZ).date() == date(2024, 1, 10)
