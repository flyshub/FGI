import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from fgi.output.alert import Alert
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


class TestAlert:
    @staticmethod
    def _webhook_content(mock_post):
        """从 mock 调用列表中取 webhook 文本载荷（PushPlus 也走 requests.post）。"""
        return next(
            c.kwargs["json"]["text"]["content"]
            for c in mock_post.call_args_list
            if "json" in c.kwargs and "text" in c.kwargs["json"]
        )

    def test_alert_without_webhook(self):
        with patch("fgi.output.alert.WEBHOOK_URL", ""):
            alert = Alert()
            alert.check_and_alert(
                "2024-01-01", {"fgi_final": 60.0, "health_score": 70.0, "dimension_scores": {}}
            )

    def test_alert_with_webhook(self, db):
        with (
            patch("fgi.output.alert.WEBHOOK_URL", "http://test.com"),
            patch("fgi.output.alert.requests.post") as mock_post,
        ):
            mock_post.return_value.raise_for_status.return_value = None

            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 70.0})
            db.upsert_score("2024-01-03", {"FGI_final": 80.0})
            db.upsert_score("2024-01-04", {"FGI_final": 90.0})
            db.upsert_score("2024-01-05", {"FGI_final": 100.0})
            db.upsert_score("2024-01-06", {"FGI_final": 1000.0})
            db.commit()

            alert = Alert(db_path=db._path)
            alert.check_and_alert(
                "2024-01-06", {"fgi_final": 1000.0, "health_score": 75.0, "dimension_scores": {}}
            )

            assert mock_post.called

    def test_alert_dingtalk(self, db):
        with (
            patch("fgi.output.alert.WEBHOOK_URL", "http://test.com"),
            patch("fgi.output.alert.requests.post") as mock_post,
        ):
            mock_post.return_value.raise_for_status.return_value = None

            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 70.0})
            db.upsert_score("2024-01-03", {"FGI_final": 80.0})
            db.upsert_score("2024-01-04", {"FGI_final": 90.0})
            db.upsert_score("2024-01-05", {"FGI_final": 100.0})
            db.upsert_score("2024-01-06", {"FGI_final": 1000.0})
            db.commit()

            alert = Alert(db_path=db._path)
            alert.check_and_alert(
                "2024-01-06", {"fgi_final": 1000.0, "health_score": 75.0, "dimension_scores": {}}
            )

            assert mock_post.called

    def test_alert_no_anomaly(self, db):
        with (
            patch("fgi.output.alert.WEBHOOK_URL", "http://test.com"),
            patch("fgi.output.alert.requests.post") as mock_post,
        ):
            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 65.0})
            db.commit()

            alert = Alert(db_path=db._path)
            alert.check_and_alert(
                "2024-01-02", {"fgi_final": 65.0, "health_score": 75.0, "dimension_scores": {}}
            )

            assert not mock_post.called

    def test_alert_message_includes_review_context(self, db):
        """告警消息包含前日 FGI、ΔFGI、阈值，便于人工复核。"""
        with (
            patch("fgi.output.alert.WEBHOOK_URL", "http://test.com"),
            patch("fgi.output.alert.requests.post") as mock_post,
        ):
            mock_post.return_value.raise_for_status.return_value = None

            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 70.0})
            db.upsert_score("2024-01-03", {"FGI_final": 80.0})
            db.upsert_score("2024-01-04", {"FGI_final": 90.0})
            db.upsert_score("2024-01-05", {"FGI_final": 100.0})
            db.upsert_score("2024-01-06", {"FGI_final": 1000.0})
            db.commit()

            alert = Alert(db_path=db._path)
            alert.check_and_alert(
                "2024-01-06", {"fgi_final": 1000.0, "health_score": 75.0, "dimension_scores": {}}
            )

            assert mock_post.called
            content = self._webhook_content(mock_post)
            assert "Prev FGI: 100.00" in content
            assert "ΔFGI: +900.00" in content
            assert "Threshold (99th pct):" in content

    def test_alert_message_without_context_when_check_fails(self, db):
        """异常检测自身失败（保守视为异常）时无上下文数值，消息不包含复核行。"""
        with (
            patch("fgi.output.alert.WEBHOOK_URL", "http://test.com"),
            patch("fgi.output.alert.requests.post") as mock_post,
            patch(
                "fgi.output.alert.Alert._analyze",
                return_value=(True, None, None, None),
            ),
        ):
            mock_post.return_value.raise_for_status.return_value = None
            alert = Alert(db_path=db._path)
            alert.check_and_alert(
                "2024-01-06",
                {"fgi_final": 50.0, "health_score": 75.0, "dimension_scores": {}},
            )

            content = self._webhook_content(mock_post)
            assert "Prev FGI:" not in content
            assert "Push suppressed" in content

    def test_alert_dimension_extreme_does_not_trigger(self, db):
        """dimension scores > 85 / < 15 no longer trigger anomaly (spec: only |ΔFGI| does)."""
        with (
            patch("fgi.output.alert.WEBHOOK_URL", "http://test.com"),
            patch("fgi.output.alert.requests.post") as mock_post,
        ):
            mock_post.return_value.raise_for_status.return_value = None

            db.upsert_score("2024-01-01", {"FGI_final": 60.0})
            db.upsert_score("2024-01-02", {"FGI_final": 70.0})
            db.commit()

            alert = Alert(db_path=db._path)
            # ΔFGI = 10 (normal), but dimension extremes should NOT trigger
            alert.check_and_alert(
                "2024-01-02",
                {
                    "fgi_final": 70.0,
                    "health_score": 75.0,
                    "dimension_scores": {"M1": 90.0, "M2": 10.0},
                },
            )

            assert not mock_post.called
