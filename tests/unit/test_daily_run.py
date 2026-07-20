import pytest
from fgi.output.daily_run import is_trading_day, setup_data_manager


class TestTradingDay:
    def test_weekday(self):
        assert is_trading_day("2024-01-01") is True

    def test_weekend(self):
        assert is_trading_day("2024-01-06") is False

    def test_sunday(self):
        assert is_trading_day("2024-01-07") is False


class TestSetupDataManager:
    def test_setup(self):
        manager = setup_data_manager()
        assert manager is not None
        assert len(manager._sources) > 0
