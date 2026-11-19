import tempfile
from pathlib import Path

import pandas as pd
import pytest

from fgi.calculator.fgi import FGICalculator
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
        "m1_zt_stats",
        "m2_market_overview",
        "m3_index",
        "m4_cyb_volume",
        "s2_sentiment",
        "s3_zt_daily",
        "v1_pe",
        "v1_bond",
        "f1_margin",
        "f1_market_cap",
        "f2_fund_position",
        "f3_industry_flow",
        "f3_index",
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
        """elapsed=1（T+1 延迟）填充并标记 normal；elapsed>=2 填充并标记 degraded；
        填充值不落库 scores_daily。"""
        db.upsert_score("2024-01-08", {"M2": 66.0})
        db.upsert_score("2024-01-09", {"M1": 50.0})
        db.upsert_score("2024-01-10", {"M1": 51.0})
        db.commit()
        results = {"M2": {"score": None, "status": "missing"}}
        # 01-08 → 01-10 = elapsed=2 (01-08 missed 01-09, 01-10)
        calculator._apply_forward_fill(results, "2024-01-10")
        assert results["M2"]["score"] == 66.0
        assert results["M2"]["status"] == "degraded"
        # 填充值不得写回 scores_daily（否则次日 elapsed 重置，上限失效）
        persisted = db.get_scores("2024-01-10", "2024-01-10")
        assert pd.isna(persisted.iloc[0]["M2"])
        # degraded 状态仍照常写 daily_status
        status = db.get_status("2024-01-10")
        m2_status = status[status["indicator"] == "m2"].iloc[0]
        assert m2_status["status"] == "degraded"
        assert m2_status["source"] == "forward_fill"

    def test_forward_fill_elapsed_1_is_forward_fill(self, calculator, db):
        """elapsed=1 是 T+1 延迟，但数据仍是沿用的旧值——不能标成 normal。"""
        db.upsert_score("2024-01-09", {"M2": 66.0})
        db.upsert_score("2024-01-10", {"M1": 51.0})
        db.commit()
        results = {"M2": {"score": None, "status": "missing"}}
        # 01-09 → 01-10 = elapsed=1 (正常 T+1 延迟)
        calculator._apply_forward_fill(results, "2024-01-10")
        assert results["M2"]["score"] == 66.0
        assert results["M2"]["status"] == "forward_fill"
        status = db.get_status("2024-01-10")
        m2_status = status[status["indicator"] == "m2"].iloc[0]
        assert m2_status["status"] == "forward_fill"
        assert m2_status["source"] == "forward_fill"

    def test_forward_fill_beyond_10_trading_days_stays_missing(self, calculator, db):
        db.upsert_score("2024-01-01", {"M2": 70.0})
        # 2024-01-01 (Mon) + 15 trading days after → 01-22 (Mon)
        # elapsed > MISSING_DAY_LIMIT=10 → 应保持 missing
        for d in [
            "2024-01-01",
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
            "2024-01-09",
            "2024-01-10",
            "2024-01-11",
            "2024-01-12",
            "2024-01-15",
            "2024-01-16",
            "2024-01-17",
            "2024-01-18",
            "2024-01-19",
            "2024-01-22",
        ]:
            db.upsert_score(d, {"M1": 50.0})
        db.commit()
        results = {"M2": {"score": None, "status": "missing"}}
        calculator._apply_forward_fill(results, "2024-01-22")
        assert results["M2"]["score"] is None
        assert results["M2"]["status"] == "missing"

    def test_forward_fill_elapsed_accumulates_across_days(self, calculator, db):
        """连续多日缺失：elapsed 必须跨日累计，elapsed=1→normal，2~10→degraded，
        第 11 日起→missing。"""
        # 01-01 为最后真实得分；01-02 ~ 01-17 连续缺失（01-06/07/13/14 为周末）
        db.upsert_score("2024-01-01", {"M2": 70.0})
        missing_days = [
            "2024-01-02",
            "2024-01-03",
            "2024-01-04",
            "2024-01-05",
            "2024-01-08",
            "2024-01-09",
            "2024-01-10",
            "2024-01-11",
            "2024-01-12",
            "2024-01-15",
            "2024-01-16",
            "2024-01-17",
        ]
        for d in ["2024-01-01"] + missing_days:
            db.upsert_score(d, {"M1": 50.0})  # 模拟当日其他指标正常，产生交易日行
        db.commit()

        # 第 1 个缺失交易日：T+1 延迟 → forward_fill（沿用旧值，不是当日实测）
        results = {"M2": {"score": None, "status": "missing"}}
        calculator._apply_forward_fill(results, missing_days[0])
        assert results["M2"]["score"] == 70.0, f"day 1 ({missing_days[0]}) should be filled"
        assert results["M2"]["status"] == "forward_fill"

        # 第 2~10 个缺失交易日：degraded
        for i, d in enumerate(missing_days[1:10], start=2):
            results = {"M2": {"score": None, "status": "missing"}}
            calculator._apply_forward_fill(results, d)
            assert results["M2"]["score"] == 70.0, f"day {i} ({d}) should be filled"
            assert results["M2"]["status"] == "degraded"

        # 第 11 个缺失交易日：超过 MISSING_DAY_LIMIT=10，必须保持 missing
        results = {"M2": {"score": None, "status": "missing"}}
        calculator._apply_forward_fill(results, missing_days[10])
        assert results["M2"]["score"] is None
        assert results["M2"]["status"] == "missing"

        # scores_daily 中 M2 自始至终只有 01-01 的真实得分
        persisted = db.get_scores("2024-01-01", "2024-01-17")
        assert persisted["M2"].dropna().tolist() == [70.0]

    def test_calculate_health(self, calculator):
        indicator_results = {
            "M1": {"status": "normal"},
            "M2": {"status": "normal"},
            "M3": {"status": "missing"},
        }
        health = calculator.calculate_health(indicator_results)
        # missing 计入 impaired 集，新公式: 0.667*40 + 0.667*40 + 20 = 73.33
        assert abs(health - 73.33) < 0.01

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

    def test_run_reports_status_counts(self, calculator):
        """P0-3：12 个指标的成败必须被计数，让调用方能发现「只算出来 9 个」。"""
        result = calculator.run("2024-01-10")
        counts = ("error_count", "degraded_count", "missing_count", "ok_count")
        for key in counts:
            assert key in result, key
            assert result[key] >= 0
        assert sum(result[k] for k in counts) == len(result["indicator_results"]) == 12

    def test_run_counts_indicator_crashes(self, calculator):
        """指标抛异常时计入 error_count，而不是静默少一维。"""
        broken = ["M1", "M2", "M3"]
        for name in broken:
            calc = calculator._calculators[name]

            def boom(*a, **k):
                raise RuntimeError("upstream down")

            calc.run = boom  # type: ignore[method-assign]

        result = calculator.run("2024-01-10")
        assert result["error_count"] == len(broken)
        assert result["error_count"] + result["ok_count"] <= 12


ALL_INDICATORS = ["M1", "M2", "M3", "M4", "S2", "S3", "V1", "V2", "V4", "F1", "F2", "F3"]


@pytest.fixture
def down_calculator(db):
    """所有链只挂一个健康状态为 False 的源：模拟上游全部不可用。"""
    from fgi.collector.chains import DEFAULT_CHAINS

    manager = DataSourceManager()
    manager.register_source("mock", MockSource("mock", healthy=False))
    for chain in DEFAULT_CHAINS:
        manager.configure_chain(chain, ["mock"])
    return FGICalculator(manager, db)


class TestTotalOutage:
    """P1-9：所有数据源同时挂掉时，必须产出「没算出来」，而不是一个看起来正常的分数。"""

    def test_no_fgi_when_every_source_is_down(self, down_calculator, db):
        result = down_calculator.run("2024-01-10")

        assert result["fgi_final"] is None
        assert result["fgi_raw"] is None
        assert result["ok_count"] == 0
        assert result["health_score"] <= 20

        # 状态必须落库，事后能查出「这天为什么没数」
        statuses = db.get_status("2024-01-10")
        assert not statuses.empty
        assert set(statuses["status"]) <= {"missing", "error"}

    def test_outage_does_not_raise(self, down_calculator):
        """全源故障属于预期内的失败模式，不能抛异常中断整条管道。"""
        down_calculator.run("2024-01-10")


class TestStaleUpstream:
    """P1-9：上游停更时靠 forward-fill 出数，但必须留下可追溯的痕迹。"""

    def _seed(self, db, date, value=55.0):
        db.upsert_score(date, dict.fromkeys(ALL_INDICATORS, value))
        db.commit()

    def test_one_day_stale_is_flagged_by_source_date(self, down_calculator, db):
        self._seed(db, "2024-01-09")

        result = down_calculator.run("2024-01-10")

        # 昨日值能用，但每个指标都要说明自己来自哪天，且状态不是 normal
        assert result["fgi_final"] is not None
        assert result["degraded_count"] == 12
        for name, r in result["indicator_results"].items():
            assert r["source_date"] == "2024-01-09", name
            assert r["status"] == "forward_fill", name

    def test_two_days_stale_degrades_health(self, down_calculator, db):
        self._seed(db, "2024-01-08")
        # 01-09 是交易日但没有指标数据 → 01-10 的 elapsed = 2
        db.upsert_score("2024-01-09", {"FGI_raw": 50.0})
        db.commit()

        result = down_calculator.run("2024-01-10")

        assert result["fgi_final"] is not None
        assert result["degraded_count"] == 12
        assert result["health_score"] < 100


class TestNumericEdgeCases:
    """P1-9：NaN 必须被当作缺失，不能被聚合成一个假分数。"""

    def test_nan_indicator_scores_are_missing(self, calculator):
        results = {k: {"score": float("nan"), "status": "missing"} for k in ALL_INDICATORS}
        for dim in ("momentum", "sentiment", "valuation", "volatility", "funding"):
            assert calculator.calculate_dimension_score(results, dim) is None
        assert calculator.calculate_health(results) <= 20

    def test_nan_score_is_never_aggregated_even_if_status_says_normal(self, calculator):
        """score 与 status 冲突时以 score 为准：NaN 不得被聚合成一个假分数。

        health_score 仍按 status 聚合（各 calculator 负责「NaN ⇒ 非 normal」，
        见 TestTotalOutage / 各指标单测），这里只锁 score 侧。
        """
        results = {k: {"score": float("nan"), "status": "normal"} for k in ALL_INDICATORS}
        for dim in ("momentum", "sentiment", "valuation", "volatility", "funding"):
            assert calculator.calculate_dimension_score(results, dim) is None

    def test_nan_dimension_dropped_and_weights_renormalized(self):
        from fgi.common.utils import calculate_fgi

        dims = {
            "momentum": float("nan"),
            "sentiment": 60.0,
            "valuation": 60.0,
            "volatility": 60.0,
            "funding": 60.0,
        }
        assert calculate_fgi(dims) == pytest.approx(60.0)

    def test_two_dimensions_is_the_floor(self):
        from fgi.common.utils import calculate_fgi

        assert (
            calculate_fgi(
                {
                    "momentum": 80.0,
                    "sentiment": None,
                    "valuation": None,
                    "volatility": None,
                    "funding": None,
                }
            )
            is None
        )
        assert calculate_fgi(
            {
                "momentum": 80.0,
                "sentiment": 40.0,
                "valuation": None,
                "volatility": None,
                "funding": None,
            }
        ) == pytest.approx(60.0)
