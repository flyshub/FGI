"""StatusRecorder 与单一 upsert 语义（daily_status 只有一个 writer）。"""

import tempfile
from pathlib import Path

import pytest

from fgi.calculator.status import StatusRecorder
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


class TestImmediateMode:
    def test_record_writes_immediately(self, db):
        recorder = StatusRecorder(db)
        recorder.begin("2024-01-10")
        recorder.record("m1", "normal", "akshare", "fetched_at=x")

        row = db.get_status("2024-01-10")
        assert row.iloc[0]["status"] == "normal"
        assert row.iloc[0]["source"] == "akshare"

    def test_record_before_begin_is_a_programming_error(self, db):
        recorder = StatusRecorder(db)
        with pytest.raises(RuntimeError):
            recorder.record("m1", "normal", "akshare")


class TestBufferedMode:
    def test_records_are_held_until_flush(self, db):
        recorder = StatusRecorder(db, buffered=True)
        recorder.begin("2024-01-10")
        recorder.record("m1", "normal", "akshare")
        recorder.record("m2", "missing", "database", "No data")

        assert recorder.pending() == 2
        assert db.get_status("2024-01-10").empty

        recorder.flush("2024-01-10")

        assert recorder.pending() == 0
        statuses = db.get_status("2024-01-10")
        assert set(statuses["indicator"]) == {"m1", "m2"}
        assert set(statuses["status"]) == {"normal", "missing"}

    def test_flush_is_idempotent(self, db):
        recorder = StatusRecorder(db, buffered=True)
        recorder.begin("2024-01-10")
        recorder.record("m1", "normal", "akshare")
        recorder.flush("2024-01-10")
        recorder.flush("2024-01-10")  # 第二次是空操作

        assert len(db.get_status("2024-01-10")) == 1


class TestSourcePrecedence:
    """source=None 保留已有来源；传字符串则覆盖——这就是原 keep_source 的全部语义。"""

    def _write(self, db, source):
        db.upsert_status("2024-01-10", "m1", "normal", source, "")
        return db.get_status("2024-01-10").iloc[0]["source"]

    def test_none_preserves_existing(self, db):
        db.upsert_status("2024-01-10", "m1", "normal", "akshare", "")
        assert self._write(db, None) == "akshare"

    def test_string_overwrites(self, db):
        db.upsert_status("2024-01-10", "m1", "normal", "akshare", "")
        assert self._write(db, "database") == "database"

    def test_empty_string_clears(self, db):
        db.upsert_status("2024-01-10", "m1", "normal", "akshare", "")
        assert self._write(db, "") == ""

    def test_new_row_with_none_source_is_empty_not_null(self, db):
        db.upsert_status("2024-02-01", "m1", "normal", None, None)
        row = db.get_status("2024-02-01").iloc[0]
        assert row["source"] == ""
        assert row["error"] == ""

    def test_error_none_preserves_and_string_replaces(self, db):
        db.upsert_status("2024-01-10", "m1", "missing", "database", "upstream down")
        db.upsert_status("2024-01-10", "m1", "normal", None, None)
        assert db.get_status("2024-01-10").iloc[0]["error"] == "upstream down"

        db.upsert_status("2024-01-10", "m1", "normal", None, "fresh")
        assert db.get_status("2024-01-10").iloc[0]["error"] == "fresh"

        db.upsert_status("2024-01-10", "m1", "normal", None, "")
        assert db.get_status("2024-01-10").iloc[0]["error"] == ""


class TestSingleWriterWiring:
    def test_fgi_calculator_flushes_its_recorder(self, db):
        """跑完一天后缓冲必须清空，且状态已经落库。"""
        from fgi.calculator.fgi import FGICalculator
        from fgi.collector.fallback import DataSourceManager
        from fgi.collector.mock_source import MockSource

        manager = DataSourceManager()
        manager.register_source("mock", MockSource("mock", healthy=True))
        for chain in ("m3_index", "v1_pe"):
            manager.configure_chain(chain, ["mock"])

        calculator = FGICalculator(manager, db)
        calculator.run("2024-01-10")

        assert calculator._status.pending() == 0
        assert not db.get_status("2024-01-10").empty
