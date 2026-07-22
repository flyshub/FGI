import pandas as pd
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
    for chain in [
        "m1_zt_stats", "m2_sentiment", "m3_index", "m4_cyb_volume",
        "s3_sentiment", "s4_zt_daily", "v1_pe", "v1_bond",
        "f1_margin", "f1_market_cap", "f2_fund_position",
        "f3_industry_flow", "f3_index",
    ]:
        manager.configure_chain(chain, ["mock"])
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

    def test_dimension_score_zero_counts(self, calculator):
        """0 分是有效分（历史最低值），不得被 or 链当作缺失"""
        indicator_results = {
            "M1": {"M1": 0.0, "status": "normal"},
            "M2": {"score": None, "status": "missing"},
            "M3": {"score": None, "status": "missing"},
            "M4": {"score": None, "status": "missing"},
        }
        score = calculator.calculate_dimension_score(indicator_results, "momentum")
        assert score == 0.0

    def test_dimension_all_missing_returns_none(self, calculator):
        """维度全部指标缺失 → None，由 calculate_fgi 跨维重归一化（不再默认 50）"""
        indicator_results = {
            "M1": {"score": None, "status": "missing"},
            "M2": {"score": None, "status": "missing"},
        }
        assert calculator.calculate_dimension_score(indicator_results, "momentum") is None

    def test_forward_fill_within_5_trading_days(self, calculator, db):
        """缺失值用最近 5 个交易日内最后有效得分填充，状态 degraded；
        填充值不落库 scores_daily，degraded 状态落 daily_status"""
        db.upsert_score("2024-01-08", {"M2": 66.0})
        db.upsert_score("2024-01-09", {"M1": 50.0})
        db.upsert_score("2024-01-10", {"M1": 51.0})
        db.commit()
        results = {"M2": {"score": None, "status": "missing"}}
        calculator._apply_forward_fill(results, "2024-01-10")
        assert results["M2"]["score"] == 66.0
        assert results["M2"]["status"] == "degraded"
        # 填充值不得写回 scores_daily（否则次日 elapsed 重置，5 日上限失效）
        persisted = db.get_scores("2024-01-10", "2024-01-10")
        assert pd.isna(persisted.iloc[0]["M2"])
        # degraded 状态仍照常写 daily_status
        status = db.get_status("2024-01-10")
        m2_status = status[status["indicator"] == "m2"].iloc[0]
        assert m2_status["status"] == "degraded"
        assert m2_status["source"] == "forward_fill"

    def test_forward_fill_beyond_5_trading_days_stays_missing(self, calculator, db):
        db.upsert_score("2024-01-01", {"M2": 70.0})
        for d in ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04",
                  "2024-01-05", "2024-01-08", "2024-01-09", "2024-01-10"]:
            db.upsert_score(d, {"M1": 50.0})
        db.commit()
        results = {"M2": {"score": None, "status": "missing"}}
        calculator._apply_forward_fill(results, "2024-01-10")
        assert results["M2"]["score"] is None
        assert results["M2"]["status"] == "missing"

    def test_forward_fill_elapsed_accumulates_across_days(self, calculator, db):
        """连续多日缺失：elapsed 必须跨日累计，第 1~5 个交易日填充、第 6 日起 missing。
        回归测试：旧实现把填充值写回 scores_daily，导致次日 elapsed 重置为 1，
        连续缺失超过 5 日仍无限填充。"""
        # 01-01 为最后真实得分；01-02 ~ 01-09 连续缺失（01-06/07 为周末）
        db.upsert_score("2024-01-01", {"M2": 70.0})
        missing_days = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05",
                        "2024-01-08", "2024-01-09"]
        for d in ["2024-01-01"] + missing_days:
            db.upsert_score(d, {"M1": 50.0})  # 模拟当日其他指标正常，产生交易日行
        db.commit()

        # 第 1~5 个缺失交易日：填充
        for i, d in enumerate(missing_days[:5], start=1):
            results = {"M2": {"score": None, "status": "missing"}}
            calculator._apply_forward_fill(results, d)
            assert results["M2"]["score"] == 70.0, f"day {i} ({d}) should be filled"
            assert results["M2"]["status"] == "degraded"

        # 第 6 个缺失交易日：超过 MISSING_DAY_LIMIT，必须保持 missing
        results = {"M2": {"score": None, "status": "missing"}}
        calculator._apply_forward_fill(results, missing_days[5])
        assert results["M2"]["score"] is None
        assert results["M2"]["status"] == "missing"

        # scores_daily 中 M2 自始至终只有 01-01 的真实得分
        persisted = db.get_scores("2024-01-01", "2024-01-09")
        assert persisted["M2"].dropna().tolist() == [70.0]

    def test_calculate_health(self, calculator):
        indicator_results = {
            "M1": {"status": "normal"},
            "M2": {"status": "normal"},
            "M3": {"status": "missing"},
        }
        health = calculator.calculate_health(indicator_results)
        assert abs(health - 83.33) < 0.01

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
