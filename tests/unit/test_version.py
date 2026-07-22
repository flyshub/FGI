import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch
from fgi.config.version import VersionManager
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


class TestVersionManager:
    def test_get_active_version(self):
        with patch('fgi.config.version.VERSION_CONFIG', {
            "legacy_enabled": True,
            "current_enabled": True,
            "parallel_months": 3,
            "rollback_version": "",
        }):
            manager = VersionManager()
            assert manager.get_active_version() == "current"

    def test_get_active_version_legacy_only(self):
        with patch('fgi.config.version.VERSION_CONFIG', {
            "legacy_enabled": True,
            "current_enabled": False,
            "parallel_months": 3,
            "rollback_version": "",
        }):
            manager = VersionManager()
            assert manager.get_active_version() == "legacy"

    def test_should_run_legacy(self):
        with patch('fgi.config.version.VERSION_CONFIG', {
            "legacy_enabled": True,
            "current_enabled": True,
            "parallel_months": 3,
            "rollback_version": "",
        }):
            manager = VersionManager()
            assert manager.should_run_legacy() is True

    def test_should_run_current(self):
        with patch('fgi.config.version.VERSION_CONFIG', {
            "legacy_enabled": True,
            "current_enabled": True,
            "parallel_months": 3,
            "rollback_version": "",
        }):
            manager = VersionManager()
            assert manager.should_run_current() is True

    def test_get_parallel_config(self):
        with patch('fgi.config.version.VERSION_CONFIG', {
            "legacy_enabled": True,
            "current_enabled": True,
            "parallel_months": 6,
            "rollback_version": "",
        }):
            manager = VersionManager()
            config = manager.get_parallel_config()
            assert config["parallel_months"] == 6

    def test_get_display_value(self, db):
        db.upsert_score("2024-01-01", {"FGI_current": 55.0})
        # FGI_legacy 只能由版本切换流程写入，upsert_score 会拦截该字段
        db._conn.execute("UPDATE scores_daily SET FGI_legacy=? WHERE date=?", (50.0, "2024-01-01"))
        db.commit()

        manager = VersionManager(db_path=db._path)
        values = manager.get_display_value("2024-01-01")
        assert values["legacy"] == 50.0
        assert values["current"] == 55.0

    def test_get_display_value_empty(self, db):
        manager = VersionManager(db_path=db._path)
        values = manager.get_display_value("2024-01-01")
        assert values["legacy"] == 0.0
        assert values["current"] == 0.0

    def test_rollback_no_version(self):
        with patch('fgi.config.version.VERSION_CONFIG', {
            "legacy_enabled": True,
            "current_enabled": True,
            "parallel_months": 3,
            "rollback_version": "",
        }):
            manager = VersionManager()
            assert manager.rollback("2024-01-01") is False

    def test_get_version_info(self):
        with patch('fgi.config.version.VERSION_CONFIG', {
            "legacy_enabled": True,
            "current_enabled": True,
            "parallel_months": 3,
            "rollback_version": "v1.0",
        }):
            manager = VersionManager()
            info = manager.get_version_info()
            assert info["active_version"] == "current"
            assert info["rollback_version"] == "v1.0"
