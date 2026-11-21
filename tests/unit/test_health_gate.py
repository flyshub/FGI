"""推送前的回归判定（health_gate）。

背景：ERROR_SUPPRESS_THRESHOLD 数的是计算器崩溃（status='error'），
数据源故障落在 status='missing'，那道门看不见。本模块补的是这一段：
「此前连续 normal、今天突然消失」才算源故障，长期缺失不算新回归。
"""

import tempfile
from pathlib import Path

import pytest

from fgi.output.health_gate import find_regressions, regressions_to_suppress
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


def _seed(db, dates, statuses):
    """dates × indicators 全量写状态。statuses: {indicator: status}"""
    for date in dates:
        for indicator, status in statuses.items():
            db.status.upsert_status(date, indicator, status)
    db.commit()


def _today(**statuses):
    """构造 indicator_results：键大写，与 FGICalculator 一致。"""
    return {name.upper(): {"status": status} for name, status in statuses.items()}


PRIOR = ["2026-11-17", "2026-11-18", "2026-11-19"]
TODAY = "2026-11-20"


class TestFindRegressions:
    def test_normal_then_missing_is_regression(self, db):
        _seed(db, PRIOR, {"m3": "normal", "m4": "normal"})
        found = find_regressions(db, TODAY, _today(m3="missing", m4="normal"))
        assert found == ["M3"]

    def test_error_status_also_counts(self, db):
        _seed(db, PRIOR, {"m3": "normal"})
        assert find_regressions(db, TODAY, _today(m3="error")) == ["M3"]

    def test_already_missing_yesterday_is_not_a_new_regression(self, db):
        _seed(db, PRIOR[:2], {"m3": "normal"})
        _seed(db, [PRIOR[2]], {"m3": "missing"})
        assert find_regressions(db, TODAY, _today(m3="missing")) == []

    def test_degraded_in_lookback_breaks_the_chain(self, db):
        _seed(db, PRIOR[:2], {"m3": "normal"})
        _seed(db, [PRIOR[2]], {"m3": "degraded"})
        assert find_regressions(db, TODAY, _today(m3="missing")) == []

    def test_insufficient_history_is_not_judged(self, db):
        _seed(db, PRIOR[:1], {"m3": "normal"})
        assert find_regressions(db, TODAY, _today(m3="missing")) == []

    def test_indicator_absent_from_history_is_not_judged(self, db):
        _seed(db, PRIOR, {"m4": "normal"})
        assert find_regressions(db, TODAY, _today(m3="missing")) == []

    def test_healthy_indicators_are_ignored(self, db):
        _seed(db, PRIOR, {"m3": "normal"})
        assert find_regressions(db, TODAY, _today(m3="normal")) == []


class TestRegressionsToSuppress:
    def test_two_regressions_suppress(self, db):
        _seed(db, PRIOR, {"m3": "normal", "m4": "normal"})
        found = regressions_to_suppress(db, TODAY, _today(m3="missing", m4="missing"))
        assert found == ["M3", "M4"]

    def test_single_regression_does_not_suppress(self, db):
        # 单指标缺失对 FGI 影响有限（2026-09-24 实例 health 仍为 93.3），
        # 抑制只会制造噪音。
        _seed(db, PRIOR, {"m3": "normal"})
        assert regressions_to_suppress(db, TODAY, _today(m3="missing")) == []
