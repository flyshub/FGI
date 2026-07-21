import pytest
import tempfile
from pathlib import Path
from fgi.calculator.momentum.m1 import M1Calculator
from fgi.calculator.momentum.m2 import M2Calculator
from fgi.calculator.momentum.m4 import M4Calculator
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
    # 为每个指标配置 chain
    manager.configure_chain("m1_zt_stats", ["mock"])
    manager.configure_chain("m2_sentiment", ["mock"])
    manager.configure_chain("m4_cyb_turnover", ["mock"])
    return manager


@pytest.fixture
def m1_calculator(data_manager, db):
    return M1Calculator(data_manager, db)


@pytest.fixture
def m2_calculator(data_manager, db):
    return M2Calculator(data_manager, db)


@pytest.fixture
def m4_calculator(data_manager, db):
    return M4Calculator(data_manager, db)


class TestM1Calculator:
    def test_calculate_zt_count(self, m1_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "limit_up_count": [10] * 100
        })
        result = m1_calculator.calculate_zt_count(df)
        assert "zt_count" in result.columns
        assert result["zt_count"].iloc[0] == 10

    def test_calculate_score(self, m1_calculator):
        score = m1_calculator.calculate_score(0.5)
        assert score == 50.0

        score = m1_calculator.calculate_score(0.0)
        assert score == 0.0

        score = m1_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, m1_calculator, db):
        result = m1_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["m1"] is not None
        assert 0 <= result["m1"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["M1"] == result["m1"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestM2Calculator:
    def test_calculate_sentiment_ratio(self, m2_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "up_num": [100] * 100,
            "down_num": [50] * 100
        })
        result = m2_calculator.calculate_sentiment_ratio(df)
        assert "bullish_ratio" in result.columns
        assert result["bullish_ratio"].iloc[0] == 100 / (100 + 50)

    def test_calculate_score(self, m2_calculator):
        score = m2_calculator.calculate_score(0.5)
        assert score == 50.0

        score = m2_calculator.calculate_score(0.0)
        assert score == 0.0

        score = m2_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, m2_calculator, db):
        result = m2_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["m2"] is not None
        assert 0 <= result["m2"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["M2"] == result["m2"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestM4Calculator:
    def test_calculate_turnover_deviation(self, m4_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "turnover_rate": [0.5] * 100
        })
        result = m4_calculator.calculate_turnover_deviation(df)
        assert "deviation" in result.columns
        assert "ma20_turnover" in result.columns
        assert result["deviation"].iloc[0] == 0.0

    def test_calculate_score(self, m4_calculator):
        score = m4_calculator.calculate_score(0.5)
        assert score == 50.0

        score = m4_calculator.calculate_score(0.0)
        assert score == 0.0

        score = m4_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, m4_calculator, db):
        result = m4_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["m4"] is not None
        assert 0 <= result["m4"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["M4"] == result["m4"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"