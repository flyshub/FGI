import os
from typing import Dict, Any
from fgi.config.settings import DB_PATH
from fgi.storage.database import Database


VERSION_CONFIG = {
    "legacy_enabled": os.getenv("FGI_LEGACY_ENABLED", "true").lower() == "true",
    "current_enabled": os.getenv("FGI_CURRENT_ENABLED", "true").lower() == "true",
    "parallel_months": int(os.getenv("FGI_PARALLEL_MONTHS", "3")),
    "rollback_version": os.getenv("FGI_ROLLBACK_VERSION", ""),
}


class VersionManager:
    def __init__(self, db_path=None):
        self._db_path = db_path or DB_PATH
        self._config = VERSION_CONFIG

    def get_active_version(self) -> str:
        if self._config["current_enabled"]:
            return "current"
        elif self._config["legacy_enabled"]:
            return "legacy"
        return "current"

    def get_parallel_config(self) -> Dict[str, Any]:
        return {
            "legacy_enabled": self._config["legacy_enabled"],
            "current_enabled": self._config["current_enabled"],
            "parallel_months": self._config["parallel_months"],
        }

    def should_run_legacy(self) -> bool:
        return self._config["legacy_enabled"]

    def should_run_current(self) -> bool:
        return self._config["current_enabled"]

    def get_display_value(self, date: str) -> Dict[str, float]:
        with Database(self._db_path) as db:
            scores = db.get_scores(date, date)
            if scores.empty:
                return {"legacy": 0.0, "current": 0.0}
            row = scores.iloc[0]
            return {
                "legacy": float(row.get("FGI_legacy", 0) or 0),
                "current": float(row.get("FGI_current", 0) or 0),
            }

    def rollback(self, target_date: str) -> bool:
        if not self._config["rollback_version"]:
            return False
        with Database(self._db_path) as db:
            scores = db.get_scores(target_date, target_date)
            if scores.empty:
                return False
            return True

    def get_version_info(self) -> Dict[str, Any]:
        return {
            "active_version": self.get_active_version(),
            "parallel_config": self.get_parallel_config(),
            "rollback_version": self._config["rollback_version"],
        }
