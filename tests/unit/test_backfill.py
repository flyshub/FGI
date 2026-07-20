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
    backfill_indicator
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