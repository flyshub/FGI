import pytest
import tempfile
from pathlib import Path
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


class TestDatabaseInit:
    def test_init_schema(self, db):
        cursor = db._connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cursor.fetchall()}
        assert "raw_data" in tables
        assert "scores_daily" in tables
        assert "daily_status" in tables


class TestRawData:
    def test_upsert_and_get(self, db):
        db.upsert_raw_data("2024-01-01", "m3", 0.5)
        db.upsert_raw_data("2024-01-02", "m3", 0.6)
        df = db.get_raw_data("m3", "2024-01-01", "2024-01-02")
        assert len(df) == 2
        assert df.iloc[0]["value"] == 0.5

    def test_upsert_overwrite(self, db):
        db.upsert_raw_data("2024-01-01", "m3", 0.5)
        db.upsert_raw_data("2024-01-01", "m3", 0.7)
        df = db.get_raw_data("m3", "2024-01-01", "2024-01-01")
        assert len(df) == 1
        assert df.iloc[0]["value"] == 0.7

    def test_upsert_batch(self, db):
        df = pd.DataFrame({
            "date": ["2024-01-01", "2024-01-02"],
            "value": [0.5, 0.6]
        })
        db.upsert_raw_data_batch(df, "m3")
        result = db.get_raw_data("m3", "2024-01-01", "2024-01-02")
        assert len(result) == 2


class TestScores:
    def test_upsert_and_get(self, db):
        scores = {"M1": 50.0, "M2": 60.0, "M3": 70.0, "FGI_final": 60.0}
        db.upsert_score("2024-01-01", scores)
        df = db.get_scores("2024-01-01", "2024-01-01")
        assert len(df) == 1
        assert df.iloc[0]["M1"] == 50.0
        assert df.iloc[0]["FGI_final"] == 60.0

    def test_upsert_overwrite(self, db):
        db.upsert_score("2024-01-01", {"M1": 50.0, "FGI_final": 60.0})
        db.upsert_score("2024-01-01", {"M1": 55.0, "FGI_final": 65.0})
        df = db.get_scores("2024-01-01", "2024-01-01")
        assert len(df) == 1
        assert df.iloc[0]["M1"] == 55.0


class TestStatus:
    def test_upsert_and_get(self, db):
        db.upsert_status("2024-01-01", "m3", "normal", "akshare")
        db.upsert_status("2024-01-01", "m4", "missing", None, "No data")
        df = db.get_status("2024-01-01")
        assert len(df) == 2
        assert df.iloc[0]["status"] == "normal"

    def test_indicator_case_normalized(self, db):
        # 大写写入归一化为小写，避免 (date, indicator) 大小写双写
        db.upsert_status("2024-01-01", "M1", "missing", "", "No data")
        db.upsert_status("2024-01-01", "m1", "normal", "database")
        df = db.get_status("2024-01-01")
        assert len(df) == 1
        assert df.iloc[0]["indicator"] == "m1"
        assert df.iloc[0]["status"] == "normal"


class TestUtilities:
    def test_get_latest_score_date(self, db):
        assert db.get_latest_score_date() is None
        db.upsert_score("2024-01-01", {"M1": 50.0})
        db.upsert_score("2024-01-05", {"M1": 55.0})
        assert db.get_latest_score_date() == "2024-01-05"

    def test_get_missing_dates(self, db):
        db.upsert_raw_data("2024-01-01", "m3", 0.5)
        db.upsert_raw_data("2024-01-03", "m3", 0.6)
        missing = db.get_missing_dates("m3", "2024-01-01", "2024-01-05")
        assert "2024-01-02" in missing
        assert "2024-01-04" in missing
        assert "2024-01-01" not in missing


class TestFgiCurrentFill:
    def test_fgi_current_filled_from_final(self, db):
        db.upsert_score("2024-01-01", {"FGI_final": 61.5})
        df = db.get_scores("2024-01-01", "2024-01-01")
        assert df.iloc[0]["FGI_current"] == 61.5

    def test_fgi_legacy_stays_null(self, db):
        db.upsert_score("2024-01-01", {"FGI_final": 60.0, "FGI_legacy": 55.0})
        df = db.get_scores("2024-01-01", "2024-01-01")
        assert pd.isna(df.iloc[0]["FGI_legacy"])

    def test_explicit_fgi_current_not_overwritten(self, db):
        db.upsert_score("2024-01-01", {"FGI_final": 60.0, "FGI_current": 58.0})
        df = db.get_scores("2024-01-01", "2024-01-01")
        assert df.iloc[0]["FGI_current"] == 58.0


class TestGetMissingDatesCalendar:
    def test_with_explicit_trading_days(self, db):
        db.upsert_raw_data("2024-01-01", "m3", 0.5)
        missing = db.get_missing_dates("m3", "2024-01-01", "2024-01-05",
                                       trading_days=["2024-01-01", "2024-01-03"])
        assert missing == ["2024-01-03"]

    def test_m3_close_fallback(self, db):
        # 无显式日历时用 raw_data 中 m3_close 已有日期作为日历
        db.upsert_raw_data("2024-01-02", "m3_close", 3000.0)
        db.upsert_raw_data("2024-01-03", "m3_close", 3001.0)
        missing = db.get_missing_dates("m3", "2024-01-01", "2024-01-05")
        assert missing == ["2024-01-02", "2024-01-03"]

    def test_business_day_fallback(self, db):
        # 日历与 m3_close 都不可用时回退工作日
        missing = db.get_missing_dates("m3", "2024-01-06", "2024-01-08")
        assert missing == ["2024-01-08"]  # 01-06/07 是周末
