import pytest
import tempfile
import pandas as pd
from pathlib import Path
from fgi.calculator.valuation.v1 import V1Calculator
from fgi.calculator.valuation.v2 import V2Calculator
from fgi.collector.fallback import DataSourceManager
from fgi.collector.mock_source import MockSource
from fgi.storage.database import Database


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
    manager.configure_chain("v1_index", ["mock"])
    manager.configure_chain("v2_index", ["mock"])
    return manager


@pytest.fixture
def v1_calculator(data_manager, db):
    return V1Calculator(data_manager, db)


@pytest.fixture
def v2_calculator(data_manager, db):
    return V2Calculator(data_manager, db)


class TestV1Calculator:
    def test_calculate_deviation(self, v1_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100 + i * 0.1 for i in range(100)]
        })
        result = v1_calculator.calculate_deviation(df)
        assert "deviation" in result.columns
        assert "ma20" in result.columns
        assert result["deviation"].iloc[70] is not None

    def test_calculate_score(self, v1_calculator):
        score = v1_calculator.calculate_score(0.5)
        assert score == 50.0

        score = v1_calculator.calculate_score(0.0)
        assert score == 0.0

        score = v1_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, v1_calculator, db):
        result = v1_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["v1"] is not None
        assert 0 <= result["v1"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["V1"] == result["v1"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestV2Calculator:
    def test_calculate_deviation(self, v2_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100 + i * 0.1 for i in range(100)]
        })
        result = v2_calculator.calculate_deviation(df)
        assert "deviation" in result.columns
        assert "ma20" in result.columns
        assert result["deviation"].iloc[70] is not None

    def test_calculate_score(self, v2_calculator):
        score = v2_calculator.calculate_score(0.5)
        assert score == 50.0

        score = v2_calculator.calculate_score(0.0)
        assert score == 0.0

        score = v2_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, v2_calculator, db):
        result = v2_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["v2"] is not None
        assert 0 <= result["v2"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["V2"] == result["v2"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"