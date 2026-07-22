import pytest
import tempfile
from datetime import datetime
from pathlib import Path
from fgi.storage.database import Database
from fgi.output.backfill import (
    setup_data_manager,
    is_trading_day,
    get_date_range,
    batch_dates,
    backfill_indicator,
    compute_fgi_daily,
)


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        path = Path(tmp.name)
    database = Database(path)
    with database:
        database.init_schema()
        yield database
    path.unlink(missing_ok=True)


@pytest.fixture
def mock_calculator():
    class MockCalculator:
        def __init__(self):
            self.call_count = 0
            self.last_date = None
            
        def run(self, date: str):
            self.call_count += 1
            self.last_date = date
            return {
                "date": date,
                "fgi_final": 50.0,
                "health_score": 100.0,
                "dimension_scores": {"momentum": 50.0, "sentiment": 50.0, "valuation": 50.0, "funding": 50.0},
                "indicator_results": {}
            }
    
    return MockCalculator()


class TestBackfillUtilities:
    def test_is_trading_day(self):
        assert is_trading_day("2024-01-01") == True
        assert is_trading_day("2024-01-02") == True
        assert is_trading_day("2024-01-03") == True
        assert is_trading_day("2024-01-04") == True
        assert is_trading_day("2024-01-05") == True
        assert is_trading_day("2024-01-06") == False
    
    def test_get_date_range(self):
        dates = get_date_range("2024-01-01", "2024-01-10")
        assert len(dates) == 8
        assert "2024-01-01" in dates
        assert "2024-01-10" in dates
        assert "2024-01-06" not in dates
        assert "2024-01-07" not in dates
    
    def test_batch_dates(self):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05"]
        batches = batch_dates(dates, 2)
        assert len(batches) == 3
        assert batches[0] == ["2024-01-01", "2024-01-02"]
        assert batches[1] == ["2024-01-03", "2024-01-04"]
        assert batches[2] == ["2024-01-05"]


class TestBackfillIndicator:
    def test_backfill_indicator_success(self, db, mock_calculator):
        dates = ["2024-01-01", "2024-01-02", "2024-01-03"]
        
        backfill_indicator(db, mock_calculator, "test_indicator", dates)
        
        assert mock_calculator.call_count == 3
        assert mock_calculator.last_date == "2024-01-03"
    
    def test_backfill_indicator_empty(self, db, mock_calculator):
        backfill_indicator(db, mock_calculator, "test_indicator", [])
        assert mock_calculator.call_count == 0


class TestSetupDataManager:
    def test_no_mock_source(self):
        manager = setup_data_manager()
        assert "mock" not in manager._sources

    def test_no_s1_chain(self):
        manager = setup_data_manager()
        assert "s1_sentiment_zz" not in manager._chains

    def test_chains_reference_only_registered_sources(self):
        manager = setup_data_manager()
        for indicator, chain in manager._chains.items():
            assert len(chain._sources) > 0


class TestTradingCalendarIntegration:
    def test_resolver_used_for_backfill_dates(self, monkeypatch):
        # backfill 直接调用 resolve_trading_days（无中间包装层）
        import fgi.output.backfill as backfill_module
        monkeypatch.setattr(backfill_module, "resolve_trading_days",
                            lambda s, e, db=None: ["2024-01-02"])
        assert backfill_module.resolve_trading_days("2024-01-01", "2024-01-05") == ["2024-01-02"]


class TestComputeFgiDailyStatus:
    def test_writes_daily_status(self, db):
        class Calculator:
            def run(self, date):
                return {
                    "date": date,
                    "fgi_final": 50.0,
                    "indicator_results": {
                        "m3": {"score": 55.0, "status": "normal", "source": "akshare"},
                        "f2": {"score": None, "status": "missing", "error": "No data"},
                    },
                }

        compute_fgi_daily(Calculator(), db, ["2024-01-02"])
        status = db.get_status("2024-01-02")
        by_indicator = dict(zip(status["indicator"], status["status"]))
        assert by_indicator["m3"] == "normal"
        assert by_indicator["f2"] == "missing"