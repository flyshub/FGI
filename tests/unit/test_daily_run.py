import pytest
from fgi.output import daily_run
from fgi.output.daily_run import is_trading_day, setup_data_manager


class TestTradingDay:
    def test_calendar_trading_day(self):
        days = ["2024-01-02", "2024-01-03"]
        assert is_trading_day("2024-01-02", trading_days=days) is True

    def test_calendar_holiday_weekday(self):
        # 2024-01-01 是周一但元旦休市：真实日历里不存在则跳过
        days = ["2024-01-02", "2024-01-03"]
        assert is_trading_day("2024-01-01", trading_days=days) is False

    def test_calendar_weekend(self):
        days = ["2024-01-02"]
        assert is_trading_day("2024-01-06", trading_days=days) is False

    def test_weekday_fallback_when_calendar_unavailable(self, monkeypatch):
        monkeypatch.setattr(daily_run.TradingCalendar, "load", lambda self: None)
        assert is_trading_day("2024-01-01") is True
        assert is_trading_day("2024-01-06") is False
        assert is_trading_day("2024-01-07") is False

    def test_main_skips_non_trading_day(self, monkeypatch, capsys):
        monkeypatch.setattr(daily_run, "is_trading_day", lambda d: False)
        monkeypatch.setattr("sys.argv", ["daily_run", "--date", "2024-01-06"])
        daily_run.main()
        assert "not a trading day" in capsys.readouterr().out


class TestSetupDataManager:
    def test_setup(self):
        manager = setup_data_manager()
        assert manager is not None
        assert len(manager._sources) > 0

    def test_no_s1_chain(self):
        manager = setup_data_manager()
        assert "s1_sentiment_zz" not in manager._chains
        assert "s1_index" not in manager._chains

    def test_no_mock_source(self):
        manager = setup_data_manager()
        assert "mock" not in manager._sources
