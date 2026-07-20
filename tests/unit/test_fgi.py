import pytest
import tempfile
from pathlib import Path
from fgi.calculator.fgi import FGICalculator, INDICATOR_WEIGHTS, DIMENSION_WEIGHTS
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
    for indicator in INDICATOR_WEIGHTS:
        manager.configure_chain(f"{indicator}_source", ["mock"])
    return manager


@pytest.fixture
def calculator(data_manager, db):
    return FGICalculator(data_manager, db)


class TestFGICalculator:
    def test_calculate_dimension_score(self, calculator):
        indicator_results = {
            "M1": {"M1": 50.0, "status": "normal"},
            "M2": {"M2": 60.0, "status": "normal"},
            "M3": {"M3": 70.0, "status": "normal"},
            "M4": {"M4": 80.0, "status": "normal"},
        }
        score = calculator.calculate_dimension_score(indicator_results, "momentum")
        expected = 50 * 0.25 + 60 * 0.25 + 70 * 0.25 + 80 * 0.25
        assert abs(score - expected) < 0.01

    def test_calculate_dimension_score_partial(self, calculator):
        indicator_results = {
            "M1": {"M1": 50.0, "status": "normal"},
            "M2": {"score": None, "status": "missing"},
            "M3": {"M3": 70.0, "status": "normal"},
            "M4": {"M4": 80.0, "status": "normal"},
        }
        score = calculator.calculate_dimension_score(indicator_results, "momentum")
        expected = (50 * 0.25 + 70 * 0.25 + 80 * 0.25) / (0.25 + 0.25 + 0.25)
        assert abs(score - expected) < 0.01

    def test_calculate_health(self, calculator):
        indicator_results = {
            "M1": {"status": "normal"},
            "M2": {"status": "normal"},
            "M3": {"status": "missing"},
        }
        health = calculator.calculate_health(indicator_results)
        assert abs(health - 66.67) < 0.01

    def test_calculate_health_all_normal(self, calculator):
        indicator_results = {
            "M1": {"status": "normal"},
            "M2": {"status": "normal"},
        }
        health = calculator.calculate_health(indicator_results)
        assert health == 100.0

    def test_run(self, calculator, db):
        result = calculator.run("2024-01-10")
        assert "fgi_raw" in result
        assert "fgi_final" in result
        assert "health_score" in result
        assert "dimension_scores" in result
        assert 0 <= result["fgi_final"] <= 100
        assert 0 <= result["health_score"] <= 100

        scores = db.get_scores("2024-01-10", "2024-01-10")
        assert len(scores) == 1
        assert scores.iloc[0]["FGI_final"] == result["fgi_final"]
