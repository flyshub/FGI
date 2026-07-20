import pytest
import tempfile
from pathlib import Path
from fgi.calculator.momentum.m3 import M3Calculator
from fgi.collector.fallback import DataSourceManager
from fgi.collector.mock_source import MockSource
from fgi.storage.database import Database
import pandas as pd


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
def data_manager():
    manager = DataSourceManager()
    mock = MockSource("mock", healthy=True)
    manager.register_source("mock", mock)
    manager.configure_chain("m3_index", ["mock"])
    return manager


@pytest.fixture
def calculator(data_manager, db):
    return M3Calculator(data_manager, db)


class TestM3Calculator:
    def test_calculate_deviation(self, calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100 + i * 0.1 for i in range(100)]
        })
        result = calculator.calculate_deviation(df)
        assert "deviation" in result.columns
        assert "ma60" in result.columns
        assert result["deviation"].iloc[70] is not None

    def test_calculate_score(self, calculator):
        score = calculator.calculate_score(0.5)
        assert score == 50.0

        score = calculator.calculate_score(0.0)
        assert score == 0.0

        score = calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, calculator, db):
        result = calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["m3"] is not None
        assert 0 <= result["m3"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["M3"] == result["m3"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"
