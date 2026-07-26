"""Tests for fgi/output/status.py — record_indicator_status."""

import tempfile
from pathlib import Path

import pytest

from fgi.output.status import record_indicator_status
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


class TestRecordIndicatorStatus:
    def test_writes_normal_status(self, db):
        """写入正常状态"""
        results = {"M1": {"score": 50.0, "status": "normal", "source": "akshare"}}
        record_indicator_status(db, "2024-01-02", results)
        status = db.get_status("2024-01-02")
        assert len(status) == 1
        assert status.iloc[0]["indicator"] == "m1"
        assert status.iloc[0]["status"] == "normal"
        assert status.iloc[0]["source"] == "akshare"

    def test_writes_missing_status(self, db):
        """写入缺失状态"""
        results = {"S2": {"score": None, "status": "missing", "error": "API timeout"}}
        record_indicator_status(db, "2024-01-02", results)
        status = db.get_status("2024-01-02")
        assert len(status) == 1
        assert status.iloc[0]["indicator"] == "s2"
        assert status.iloc[0]["status"] == "missing"
        assert status.iloc[0]["error"] == "API timeout"

    def test_writes_degraded_status_with_keep_source(self, db):
        """已存在 source 时，upsert_status_keep_source 不覆盖 source"""
        db.upsert_status("2024-01-02", "F2", "normal", "database", "initial fetch")
        results = {"F2": {"score": 45.0, "status": "degraded", "source": ""}}
        record_indicator_status(db, "2024-01-02", results)
        status = db.get_status("2024-01-02")
        row = status[status["indicator"] == "f2"].iloc[0]
        assert row["status"] == "degraded"
        assert row["source"] == "database"  # 保留旧 source

    def test_no_results(self, db):
        """空结果不写入"""
        record_indicator_status(db, "2024-01-02", {})
        status = db.get_status("2024-01-02")
        assert status.empty

    def test_no_results_none(self, db):
        """None 不写入"""
        record_indicator_status(db, "2024-01-02", None)
        status = db.get_status("2024-01-02")
        assert status.empty

    def test_multiple_indicators(self, db):
        """多个指标同时写入"""
        results = {
            "M1": {"score": 50.0, "status": "normal", "source": "akshare"},
            "M2": {"score": 60.0, "status": "normal", "source": "zzshare"},
            "F2": {"score": None, "status": "missing", "error": "No data"},
        }
        record_indicator_status(db, "2024-01-02", results)
        status = db.get_status("2024-01-02")
        assert len(status) == 3
        by_indicator = dict(zip(status["indicator"], status["status"], strict=False))
        assert by_indicator["m1"] == "normal"
        assert by_indicator["m2"] == "normal"
        assert by_indicator["f2"] == "missing"

    def test_overwrites_same_date(self, db):
        """同日期覆写"""
        record_indicator_status(db, "2024-01-02", {"M1": {"score": 50.0, "status": "normal", "source": "akshare"}})
        record_indicator_status(db, "2024-01-02", {"M1": {"score": 50.0, "status": "degraded", "source": "akshare"}})
        status = db.get_status("2024-01-02")
        assert len(status) == 1
        assert status.iloc[0]["status"] == "degraded"
