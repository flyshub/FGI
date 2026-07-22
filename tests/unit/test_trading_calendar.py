"""TradingCalendar / resolve_trading_days 单元测试。
注入假 akshare，不依赖真实包与网络。"""
import sys
import types

import pandas as pd
import pytest

from fgi.collector.trading_calendar import TradingCalendar, resolve_trading_days


def _install_fake_akshare(monkeypatch, days=None, raises=False):
    fake = types.SimpleNamespace()
    if raises:
        def _boom():
            raise RuntimeError("network down")
        fake.tool_trade_date_hist_sina = _boom
    else:
        fake.tool_trade_date_hist_sina = lambda: pd.DataFrame({"trade_date": days or []})
    monkeypatch.setitem(sys.modules, "akshare", fake)
    return fake


SAMPLE_DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]


class TestTradingCalendar:
    def test_load_from_akshare_and_writes_disk(self, monkeypatch, tmp_path):
        _install_fake_akshare(monkeypatch, SAMPLE_DAYS)
        cal = TradingCalendar(cache_dir=tmp_path)
        days = cal.load()
        assert days == SAMPLE_DAYS
        assert (tmp_path / "trade_calendar.csv").exists()

    def test_disk_fallback_when_akshare_fails(self, monkeypatch, tmp_path):
        pd.DataFrame({"trade_date": SAMPLE_DAYS}).to_csv(
            tmp_path / "trade_calendar.csv", index=False)
        _install_fake_akshare(monkeypatch, raises=True)
        cal = TradingCalendar(cache_dir=tmp_path)
        assert cal.load() == SAMPLE_DAYS

    def test_none_when_nothing_available(self, monkeypatch, tmp_path):
        _install_fake_akshare(monkeypatch, raises=True)
        cal = TradingCalendar(cache_dir=tmp_path)
        assert cal.load() is None

    def test_memory_cache_single_fetch(self, monkeypatch, tmp_path):
        fake = _install_fake_akshare(monkeypatch, SAMPLE_DAYS)
        calls = []
        original = fake.tool_trade_date_hist_sina
        fake.tool_trade_date_hist_sina = lambda: calls.append(1) or original()
        cal = TradingCalendar(cache_dir=tmp_path)
        cal.load()
        cal.load()
        assert len(calls) == 1

    def test_trading_days_filters_range(self, monkeypatch, tmp_path):
        _install_fake_akshare(monkeypatch, SAMPLE_DAYS)
        cal = TradingCalendar(cache_dir=tmp_path)
        days = cal.trading_days("2024-01-03", "2024-01-05")
        assert days == ["2024-01-03", "2024-01-04", "2024-01-05"]


class _StubCalendar:
    def __init__(self, days):
        self._days = days

    def trading_days(self, start_date, end_date):
        return self._days


class _StubDb:
    def __init__(self, m3_dates):
        self._m3_dates = m3_dates

    def get_raw_data(self, indicator, start_date, end_date):
        if indicator == "m3_close" and self._m3_dates:
            return pd.DataFrame({"date": self._m3_dates, "value": [1.0] * len(self._m3_dates)})
        return pd.DataFrame(columns=["date", "value"])


class TestResolveTradingDays:
    def test_calendar_first(self):
        days = resolve_trading_days("2024-01-01", "2024-01-05",
                                    calendar=_StubCalendar(["2024-01-02"]))
        assert days == ["2024-01-02"]

    def test_fallback_m3_close(self):
        days = resolve_trading_days("2024-01-01", "2024-01-05",
                                    db=_StubDb(["2024-01-02", "2024-01-04"]),
                                    calendar=_StubCalendar(None))
        assert days == ["2024-01-02", "2024-01-04"]

    def test_fallback_business_days(self):
        days = resolve_trading_days("2024-01-05", "2024-01-08",
                                    db=_StubDb(None),
                                    calendar=_StubCalendar(None))
        assert days == ["2024-01-05", "2024-01-08"]

    def test_empty_calendar_falls_through(self):
        # 日历在区间内为空（如区间超出日历覆盖）时继续回退
        days = resolve_trading_days("2024-01-05", "2024-01-05",
                                    db=_StubDb(None),
                                    calendar=_StubCalendar([]))
        assert days == ["2024-01-05"]
