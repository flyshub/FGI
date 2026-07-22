import pytest
import tempfile
from pathlib import Path
from fgi.calculator.sentiment.s2 import S2Calculator
from fgi.calculator.sentiment.s3 import S3Calculator
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
    manager.configure_chain("m2_sentiment", ["mock"])
    manager.configure_chain("s3_sentiment", ["mock"])
    manager.configure_chain("s4_zt_daily", ["mock"])
    return manager


@pytest.fixture
def s2_calculator(data_manager, db):
    return S2Calculator(data_manager, db)


@pytest.fixture
def s3_calculator(data_manager, db):
    return S3Calculator(data_manager, db)


class TestS2Calculator:
    """V3.8: 股吧热度 (formerly S3) - zzshare market_hot_sentiment"""

    def test_calculate_heat(self, s2_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "p_close": [5000.0 + i * 10 for i in range(100)]
        })
        result = s2_calculator.calculate_heat(df)
        assert "heat" in result.columns
        assert result["heat"].iloc[0] == 5000.0

    def test_calculate_heat_missing_stays_nan(self, s2_calculator):
        """缺失热度不再填充 100（满分贪婪），保持 NaN 走 missing/degraded"""
        df = pd.DataFrame({"p_close": ["bad_value", "100"]})
        result = s2_calculator.calculate_heat(df)
        assert pd.isna(result["heat"].iloc[0])
        assert result["heat"].iloc[1] == 100.0

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
    """V3.8: 涨停封单量 (formerly S4) - levistock/AKShare zt_daily_summary"""

    def test_calculate_zt_ratio(self, s3_calculator):
        df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "seal_fund_sum": [1000000000.0] * 100,
        })
        result = s3_calculator.calculate_zt_ratio(df)
        assert "zt_ratio" in result.columns
        # 统一单位为亿元（与 raw_data 的 s3_seal_fund 一致）
        assert result["zt_ratio"].iloc[0] == 10.0

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
