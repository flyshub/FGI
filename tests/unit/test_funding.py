import pytest
import tempfile
from pathlib import Path
from fgi.calculator.funding.f1 import F1Calculator
from fgi.calculator.funding.f2 import F2Calculator
from fgi.calculator.funding.f3 import F3Calculator
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
    manager.configure_chain("f1_margin", ["mock"])
    manager.configure_chain("f2_northbound", ["mock"])
    manager.configure_chain("f3_index", ["mock"])
    return manager


@pytest.fixture
def f1_calculator(data_manager, db):
    return F1Calculator(data_manager, db)


@pytest.fixture
def f2_calculator(data_manager, db):
    return F2Calculator(data_manager, db)


@pytest.fixture
def f3_calculator(data_manager, db):
    return F3Calculator(data_manager, db)


class TestF1Calculator:
    def test_calculate_margin_growth(self, f1_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "融资余额": [1000000.0] * 100
        })
        result = f1_calculator.calculate_margin_growth(df)
        assert "margin_growth" in result.columns
        assert "margin_balance" in result.columns

    def test_calculate_score(self, f1_calculator):
        score = f1_calculator.calculate_score(0.5)
        assert score == 50.0

        score = f1_calculator.calculate_score(0.0)
        assert score == 0.0

        score = f1_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, f1_calculator, db):
        result = f1_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["f1"] is not None
        assert 0 <= result["f1"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["F1"] == result["f1"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestF2Calculator:
    def test_calculate_northbound_ratio(self, f2_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "net_buy": [1000000.0] * 100
        })
        result = f2_calculator.calculate_northbound_ratio(df)
        assert "northbound_ratio" in result.columns
        assert "northbound_amount" in result.columns

    def test_calculate_score(self, f2_calculator):
        score = f2_calculator.calculate_score(0.5)
        assert score == 50.0

        score = f2_calculator.calculate_score(0.0)
        assert score == 0.0

        score = f2_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, f2_calculator, db):
        result = f2_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["f2"] is not None
        assert 0 <= result["f2"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["F2"] == result["f2"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestF3Calculator:
    def test_calculate_large_single_inflow(self, f3_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100.0] * 100,
            "volume": [1000000] * 100
        })
        result = f3_calculator.calculate_large_single_inflow(df)
        assert "large_single_ratio" in result.columns
        assert "large_single_inflow" in result.columns

    def test_calculate_score(self, f3_calculator):
        score = f3_calculator.calculate_score(0.5)
        assert score == 50.0

        score = f3_calculator.calculate_score(0.0)
        assert score == 0.0

        score = f3_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, f3_calculator, db):
        result = f3_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["f3"] is not None
        assert 0 <= result["f3"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["F3"] == result["f3"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"