import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from fgi.storage.database import Database
from fgi.output.alert import Alert


@pytest.fixture
def db():
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        path = Path(tmp.name)
    database = Database(path)
    with database:
        database.init_schema()
        yield database
    path.unlink(missing_ok=True)


class TestAlert:
    def test_alert_without_webhook(self):
        with patch('fgi.output.alert.WEBHOOK_URL', ''):
            alert = Alert()
            alert.check_and_alert("2024-01-01", {"fgi_final": 60.0, "health_score": 70.0, "dimension_scores": {}})

    def test_alert_with_webhook(self, db):
        with patch('fgi.output.alert.WEBHOOK_URL', 'http://test.com'), \
             patch('fgi.output.alert.WEBHOOK_TYPE', 'wecom'), \
             patch('fgi.output.alert.requests.post') as mock_post:
            mock_post.return_value.raise_for_status.return_value = None

            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 70.0})
            db.upsert_score("2024-01-03", {"FGI_final": 80.0})
            db.upsert_score("2024-01-04", {"FGI_final": 90.0})
            db.upsert_score("2024-01-05", {"FGI_final": 100.0})
            db.upsert_score("2024-01-06", {"FGI_final": 1000.0})
            db.commit()

            alert = Alert(db_path=db._path)
            alert.check_and_alert("2024-01-06", {"fgi_final": 1000.0, "health_score": 75.0, "dimension_scores": {}})

            assert mock_post.called

    def test_alert_dingtalk(self, db):
        with patch('fgi.output.alert.WEBHOOK_URL', 'http://test.com'), \
             patch('fgi.output.alert.WEBHOOK_TYPE', 'dingtalk'), \
             patch('fgi.output.alert.requests.post') as mock_post:
            mock_post.return_value.raise_for_status.return_value = None

            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 70.0})
            db.upsert_score("2024-01-03", {"FGI_final": 80.0})
            db.upsert_score("2024-01-04", {"FGI_final": 90.0})
            db.upsert_score("2024-01-05", {"FGI_final": 100.0})
            db.upsert_score("2024-01-06", {"FGI_final": 1000.0})
            db.commit()

            alert = Alert(db_path=db._path)
            alert.check_and_alert("2024-01-06", {"fgi_final": 1000.0, "health_score": 75.0, "dimension_scores": {}})

            assert mock_post.called

    def test_alert_no_anomaly(self, db):
        with patch('fgi.output.alert.WEBHOOK_URL', 'http://test.com'), \
             patch('fgi.output.alert.WEBHOOK_TYPE', 'wecom'), \
             patch('fgi.output.alert.requests.post') as mock_post:

            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 65.0})
            db.commit()

            alert = Alert(db_path=db._path)
            alert.check_and_alert("2024-01-02", {"fgi_final": 65.0, "health_score": 75.0, "dimension_scores": {}})

            assert not mock_post.called

    def test_alert_anomaly_indicator(self, db):
        with patch('fgi.output.alert.WEBHOOK_URL', 'http://test.com'), \
             patch('fgi.output.alert.WEBHOOK_TYPE', 'wecom'), \
             patch('fgi.output.alert.requests.post') as mock_post:
            mock_post.return_value.raise_for_status.return_value = None

            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 70.0})
            db.commit()

            alert = Alert(db_path=db._path)
            alert.check_and_alert("2024-01-02", {"fgi_final": 70.0, "health_score": 75.0, "dimension_scores": {"M1": 90.0, "M2": 10.0}})

            assert mock_post.called
