import pytest
import tempfile
from pathlib import Path
from fgi.calculator.sentiment.s1 import S1Calculator
from fgi.calculator.sentiment.s2 import S2Calculator
from fgi.calculator.sentiment.s3 import S3Calculator
from fgi.calculator.sentiment.s4 import S4Calculator
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
    manager.configure_chain("s1_index", ["mock"])
    manager.configure_chain("s2_index", ["mock"])
    manager.configure_chain("s3_index", ["mock"])
    manager.configure_chain("s4_index", ["mock"])
    return manager


@pytest.fixture
def s1_calculator(data_manager, db):
    return S1Calculator(data_manager, db)


@pytest.fixture
def s2_calculator(data_manager, db):
    return S2Calculator(data_manager, db)


@pytest.fixture
def s3_calculator(data_manager, db):
    return S3Calculator(data_manager, db)


@pytest.fixture
def s4_calculator(data_manager, db):
    return S4Calculator(data_manager, db)


class TestS1Calculator:
    def test_calculate_rise_fall_ratio(self, s1_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100 + i * 0.1 for i in range(100)]
        })
        result = s1_calculator.calculate_rise_fall_ratio(df)
        assert "rise_fall_ratio" in result.columns
        assert "rise_count" in result.columns
        assert "fall_count" in result.columns
        assert result["rise_fall_ratio"].iloc[0] == 0.0

    def test_calculate_score(self, s1_calculator):
        score = s1_calculator.calculate_score(0.5)
        assert score == 50.0

        score = s1_calculator.calculate_score(0.0)
        assert score == 0.0

        score = s1_calculator.calculate_score(1.0)
        assert score == 100.0

    def test_run_with_mock_data(self, s1_calculator, db):
        result = s1_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["s1"] is not None
        assert 0 <= result["s1"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["S1"] == result["s1"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestS2Calculator:
    def test_calculate_sentiment(self, s2_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100 + i * 0.1 for i in range(100)]
        })
        result = s2_calculator.calculate_sentiment(df)
        assert "sentiment" in result.columns
        assert result["sentiment"].iloc[0] == 0.5

    def test_calculate_score(self, s2_calculator):
        score = s2_calculator.calculate_score(0.5)
        assert score == 50.0

    def test_run_with_mock_data(self, s2_calculator, db):
        result = s2_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["s2"] is not None
        assert 0 <= result["s2"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["S2"] == result["s2"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestS3Calculator:
    def test_calculate_volume(self, s3_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100 + i * 0.1 for i in range(100)]
        })
        result = s3_calculator.calculate_volume(df)
        assert "volume" in result.columns
        assert result["volume"].iloc[0] == 1000000

    def test_calculate_score(self, s3_calculator):
        score = s3_calculator.calculate_score(0.5)
        assert score == 50.0

    def test_run_with_mock_data(self, s3_calculator, db):
        result = s3_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["s3"] is not None
        assert 0 <= result["s3"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["S3"] == result["s3"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"


class TestS4Calculator:
    def test_calculate_zt_ratio(self, s4_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "close": [100 + i * 0.1 for i in range(100)]
        })
        result = s4_calculator.calculate_zt_ratio(df)
        assert "zt_ratio" in result.columns
        assert "zt_volume" in result.columns
        assert "volume" in result.columns
        assert result["zt_ratio"].iloc[0] == 0.1

    def test_calculate_score(self, s4_calculator):
        score = s4_calculator.calculate_score(0.5)
        assert score == 50.0

    def test_run_with_mock_data(self, s4_calculator, db):
        result = s4_calculator.run("2024-01-10", lookback_days=300)
        assert result["status"] == "normal"
        assert result["s4"] is not None
        assert 0 <= result["s4"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["S4"] == result["s4"]

        status = db.get_status("2024-01-10")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "normal"