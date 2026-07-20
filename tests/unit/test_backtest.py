import pytest
import tempfile
from pathlib import Path
from fgi.output.backtest import BacktestEngine
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
def engine(db):
    return BacktestEngine(db)


class TestBacktestEngine:
    def test_get_score_series(self, engine, db):
        db.upsert_score("2024-01-01", {"FGI_final": 50.0})
        db.upsert_score("2024-01-02", {"FGI_final": 55.0})
        db.commit()

        scores = engine.get_score_series("2024-01-01", "2024-01-02")
        assert len(scores) == 2

    def test_calculate_ic(self, engine, db):
        for i in range(30):
            db.upsert_score(f"2024-01-{i+1:02d}", {"FGI_final": 50.0 + i})
        db.commit()

        scores = engine.get_score_series("2024-01-01", "2024-01-30")
        result = engine.calculate_ic(scores, forward_days=5)
        assert "ic_mean" in result
        assert "ic_std" in result
        assert "icir" in result

    def test_layer_backtest(self, engine, db):
        for i in range(100):
            db.upsert_score(f"2024-01-{i+1:02d}", {"FGI_final": 50.0 + i % 50})
        db.commit()

        scores = engine.get_score_series("2024-01-01", "2024-04-09")
        result = engine.layer_backtest(scores, n_layers=5, holding_days=5)
        assert "layer_returns" in result
        assert len(result["layer_returns"]) == 5

    def test_strategy_simulation(self, engine, db):
        for i in range(100):
            db.upsert_score(f"2024-01-{i+1:02d}", {"FGI_final": 50.0 + i % 50})
        db.commit()

        scores = engine.get_score_series("2024-01-01", "2024-04-09")
        result = engine.strategy_simulation(scores, holding_days=5)
        assert "total_return" in result
        assert "win_rate" in result
        assert "sharpe" in result

    def test_run_full_backtest(self, engine, db):
        for i in range(100):
            db.upsert_score(f"2024-01-{i+1:02d}", {"FGI_final": 50.0 + i % 50})
        db.commit()

        result = engine.run_full_backtest("2024-01-01", "2024-04-09")
        assert "ic_analysis" in result
        assert "layer_backtest" in result
        assert "strategy_simulation" in result
        assert "data_points" in result

    def test_run_full_backtest_empty(self, engine):
        result = engine.run_full_backtest("2024-01-01", "2024-01-01")
        assert "error" in result
