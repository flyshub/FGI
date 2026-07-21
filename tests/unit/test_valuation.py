import pytest
import tempfile
import pandas as pd
import numpy as np
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
    manager.configure_chain("v1_pe", ["mock"])
    manager.configure_chain("v1_bond", ["mock"])
    manager.configure_chain("v2_index", ["mock"])
    return manager


@pytest.fixture
def v1_calculator(data_manager, db):
    return V1Calculator(data_manager, db)


@pytest.fixture
def v2_calculator(data_manager, db):
    return V2Calculator(data_manager, db)


class TestV1Calculator:
    """V3.8: ERP = 1/PE - 10yr bond yield, reverse direction"""

    def test_calculate_erp(self, v1_calculator):
        pe_df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "滚动市盈率": [12.0] * 100,
        })
        bond_df = pd.DataFrame({
            "date": pd.date_range("2024-01-01", periods=100).strftime("%Y-%m-%d"),
            "yield_10y": [2.80] * 100,
        })
        result = v1_calculator.calculate_erp(pe_df, bond_df)
        assert "erp" in result.columns
        assert "earnings_yield" in result.columns
        assert result["erp"].iloc[0] == pytest.approx(1.0 / 12.0 - 0.028, abs=0.01)

    def test_calculate_score_reverse(self, v1_calculator):
        assert v1_calculator.calculate_score(0.5) == 50.0
        assert v1_calculator.calculate_score(0.0) == 100.0
        assert v1_calculator.calculate_score(1.0) == 0.0

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
    """V3.8: ΔERP Z-score (250-day, negative sigmoid). Reads v1_erp from DB."""

    def test_calculate_derp_zscore(self, v2_calculator):
        np.random.seed(42)
        base = 0.06
        erp_series = pd.Series([base + np.random.normal(0, 0.002) for _ in range(300)], dtype=float)
        scores = v2_calculator.calculate_derp_zscore(erp_series)
        assert len(scores) == 300
        assert 40 < scores.iloc[-1] < 60

    def test_calculate_score(self, v2_calculator):
        score = v2_calculator.calculate_score(50.0)
        assert score == 50.0

    def test_run_with_db_data(self, v2_calculator, db):
        np.random.seed(42)
        dates = pd.date_range("2024-01-01", periods=400).strftime("%Y-%m-%d")
        for i, d in enumerate(dates):
            db.upsert_raw_data(d, "v1_erp", 0.06 + np.random.normal(0, 0.002))
        db.commit()

        result = v2_calculator.run("2024-10-15", lookback_days=300)
        assert result["status"] == "normal"
        assert result["v2"] is not None
        assert 0 <= result["v2"] <= 100
