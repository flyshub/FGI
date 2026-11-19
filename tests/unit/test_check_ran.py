"""check_ran：兜底 cron 的幂等闸。"""

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from fgi.output import check_ran
from fgi.storage.database import Database


@pytest.fixture
def db_path():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        path = Path(tmp.name)
    with Database(path) as db:
        db.init_schema()
    yield path
    path.unlink(missing_ok=True)


class TestHasScore:
    def test_missing_date_is_not_computed(self, db_path):
        assert check_ran.has_score("2024-01-10", db_path) is False

    def test_row_with_fgi_final_is_computed(self, db_path):
        with Database(db_path) as db:
            db.upsert_score("2024-01-10", {"FGI_final": 55.0})
            db.commit()
        assert check_ran.has_score("2024-01-10", db_path) is True

    def test_row_without_value_is_not_computed(self, db_path):
        """有行但 FGI_final 是 NULL/NaN，不能当成算过。"""
        with Database(db_path) as db:
            db.upsert_score("2024-01-10", {"FGI_raw": 50.0})
            db.commit()
        assert check_ran.has_score("2024-01-10", db_path) is False

    def test_unreadable_db_is_an_error_not_a_false_negative(self, tmp_path):
        with pytest.raises(pd.errors.DatabaseError):
            check_ran.has_score("2024-01-10", tmp_path / "missing.db")


class TestCli:
    def test_exit_zero_when_computed(self, db_path, monkeypatch, capsys):
        with Database(db_path) as db:
            db.upsert_score("2024-01-10", {"FGI_final": 55.0})
            db.commit()
        monkeypatch.setattr("sys.argv", ["check_ran", "--date", "2024-01-10", "--db", str(db_path)])
        assert check_ran.main() == 0
        assert "already computed" in capsys.readouterr().out

    def test_exit_one_when_not_computed(self, db_path, monkeypatch):
        monkeypatch.setattr("sys.argv", ["check_ran", "--date", "2024-01-10", "--db", str(db_path)])
        assert check_ran.main() == 1

    def test_exit_two_when_undeterminable(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv", ["check_ran", "--date", "2024-01-10", "--db", str(tmp_path / "nope.db")]
        )
        assert check_ran.main() == 2
        assert "assuming not computed" in capsys.readouterr().err
