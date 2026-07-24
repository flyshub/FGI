"""TDD tests for fgi.output.decision_matrix."""
import tempfile
import pytest
from pathlib import Path

from fgi.storage.database import Database
from fgi.output.decision_matrix import (
    compute_decision_matrix,
    _classify_sentiment,
    _classify_valuation,
    _lookup_quadrant,
)


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
def db_with_pe_pb_percentile(db):
    """Seed pre-computed percentiles for a target date."""
    target = "2026-07-23"
    db.upsert_raw_data(target, "v1_pe_percentile", 0.20)  # low
    db.upsert_raw_data(target, "v1_pb_percentile", 0.30)  # low
    # avg = 0.25 → just at boundary, treat as 低估 boundary (still < 25 strict? 0.25 not < 25)
    return db, target


class TestClassifiers:
    def test_classify_sentiment_fear(self):
        assert _classify_sentiment(20.0) == "恐惧"

    def test_classify_sentiment_greed(self):
        assert _classify_sentiment(80.0) == "贪婪"

    def test_classify_sentiment_neutral(self):
        assert _classify_sentiment(50.0) == "中性"

    def test_classify_sentiment_none(self):
        assert _classify_sentiment(None) is None

    def test_classify_sentiment_nan(self):
        assert _classify_sentiment(float("nan")) is None

    def test_classify_valuation_low(self):
        assert _classify_valuation(0.20) == "低估"

    def test_classify_valuation_high(self):
        assert _classify_valuation(0.80) == "高估"

    def test_classify_valuation_fair(self):
        assert _classify_valuation(0.50) == "合理"

    def test_classify_valuation_none(self):
        assert _classify_valuation(None) is None


class TestQuadrantTable:
    def test_fear_low_strong_attention(self):
        q, advice = _lookup_quadrant("恐惧", "低估")
        assert q == "强烈关注"
        assert "左侧" in advice

    def test_greed_high_strong_caution(self):
        q, _ = _lookup_quadrant("贪婪", "高估")
        assert q == "强烈谨慎"

    def test_neutral_fair(self):
        q, _ = _lookup_quadrant("中性", "合理")
        assert q == "中性"

    def test_all_9_cells_covered(self):
        for s in ("恐惧", "中性", "贪婪"):
            for v in ("低估", "合理", "高估"):
                q, advice = _lookup_quadrant(s, v)
                assert q != "未知", f"uncovered cell ({s},{v})"
                assert advice


class TestComputeDecisionMatrix:
    def test_with_precomputed_percentile(self, db_with_pe_pb_percentile):
        db, target = db_with_pe_pb_percentile
        # FGI low → fear; valuation low (0.25) → on boundary; let's make < 25
        db.upsert_raw_data(target, "v1_pe_percentile", 0.20)
        db.upsert_raw_data(target, "v1_pb_percentile", 0.20)  # avg = 0.20 → 低估
        dm = compute_decision_matrix(db, target, fgi=15.0)
        assert dm is not None
        assert dm.sentiment_tier == "恐惧"
        assert dm.valuation_tier == "低估"
        assert dm.quadrant == "强烈关注"
        assert dm.pe_pct == pytest.approx(0.20, abs=0.01)
        assert dm.pb_pct == pytest.approx(0.20, abs=0.01)
        assert dm.valuation_pct == pytest.approx(0.20, abs=0.01)

    def test_returns_none_when_percentile_missing(self, db):
        # No data seeded
        dm = compute_decision_matrix(db, "2024-01-01", fgi=50.0)
        assert dm is None

    def test_returns_none_when_fgi_none(self, db_with_pe_pb_percentile):
        db, target = db_with_pe_pb_percentile
        dm = compute_decision_matrix(db, target, fgi=None)
        assert dm is None

    def test_neutral_neutral_quadrant(self, db_with_pe_pb_percentile):
        db, target = db_with_pe_pb_percentile
        db.upsert_raw_data(target, "v1_pe_percentile", 0.50)
        db.upsert_raw_data(target, "v1_pb_percentile", 0.50)
        dm = compute_decision_matrix(db, target, fgi=50.0)
        assert dm.sentiment_tier == "中性"
        assert dm.valuation_tier == "合理"
        assert dm.quadrant == "中性"

    def test_greed_high_quadrant(self, db_with_pe_pb_percentile):
        db, target = db_with_pe_pb_percentile
        db.upsert_raw_data(target, "v1_pe_percentile", 0.90)
        db.upsert_raw_data(target, "v1_pb_percentile", 0.85)
        dm = compute_decision_matrix(db, target, fgi=85.0)
        assert dm.sentiment_tier == "贪婪"
        assert dm.valuation_tier == "高估"
        assert dm.quadrant == "强烈谨慎"

    def test_to_dict(self, db_with_pe_pb_percentile):
        db, target = db_with_pe_pb_percentile
        db.upsert_raw_data(target, "v1_pe_percentile", 0.15)
        db.upsert_raw_data(target, "v1_pb_percentile", 0.20)
        dm = compute_decision_matrix(db, target, fgi=30.0)
        d = dm.to_dict()
        assert d["quadrant"] == "强烈关注"
        assert d["sentiment_tier"] == "恐惧"
        assert d["valuation_tier"] == "低估"
        assert d["fgi"] == 30.0


import tempfile  # noqa: E402