"""PushPlus transport layer: token handling, HTTP failure paths, report assembly.

Rendering assertions live in test_renderer.py.
"""

import tempfile
from pathlib import Path

import pytest

from fgi.output import pushplus
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


class _Resp:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


class TestPost:
    def test_missing_token_never_calls_http(self, monkeypatch):
        def boom(*a, **k):
            raise AssertionError("must not call requests.post without a token")

        monkeypatch.setattr(pushplus.requests, "post", boom)
        assert pushplus._post("", "t", "c") is False

    def test_success(self, monkeypatch):
        monkeypatch.setattr(pushplus.requests, "post", lambda *a, **k: _Resp(200, {"code": 200}))
        assert pushplus._post("tok", "t", "c") is True

    def test_api_level_error_is_failure(self, monkeypatch):
        # HTTP 200 但 code != 200：上游业务失败，必须当成失败
        monkeypatch.setattr(
            pushplus.requests, "post", lambda *a, **k: _Resp(200, {"code": 500}, "bad token")
        )
        assert pushplus._post("tok", "t", "c") is False

    def test_http_error_is_failure(self, monkeypatch):
        monkeypatch.setattr(pushplus.requests, "post", lambda *a, **k: _Resp(403, {}, "forbidden"))
        assert pushplus._post("tok", "t", "c") is False

    def test_network_error_is_failure(self, monkeypatch):
        def boom(*a, **k):
            raise ConnectionError("network down")

        monkeypatch.setattr(pushplus.requests, "post", boom)
        assert pushplus._post("tok", "t", "c") is False

    def test_friend_target_is_forwarded(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            pushplus.requests,
            "post",
            lambda url, json, timeout: seen.update(json) or _Resp(200, {"code": 200}),
        )
        pushplus._post("tok", "t", "c", to="friend")
        assert seen["to"] == "friend"


class TestBroadcast:
    def test_without_token_skips(self, monkeypatch):
        monkeypatch.delenv("FGI_PUSHPLUS_TOKEN", raising=False)
        assert pushplus._broadcast("t", "c") is False

    def test_primary_success(self, monkeypatch):
        monkeypatch.setenv("FGI_PUSHPLUS_TOKEN", "tok")
        monkeypatch.delenv("FGI_PUSHPLUS_FRIENDS", raising=False)
        calls = []
        monkeypatch.setattr(
            pushplus, "_post", lambda tok, title, content, to=None: calls.append(to) or True
        )
        assert pushplus._broadcast("t", "c") is True
        assert calls == [None]

    def test_friends_failure_does_not_undo_primary(self, monkeypatch):
        monkeypatch.setenv("FGI_PUSHPLUS_TOKEN", "tok")
        monkeypatch.setenv("FGI_PUSHPLUS_FRIENDS", "friend")
        monkeypatch.setattr(pushplus, "_post", lambda tok, title, content, to=None: to is None)
        assert pushplus._broadcast("t", "c") is True

    def test_all_failures_return_false(self, monkeypatch):
        monkeypatch.setenv("FGI_PUSHPLUS_TOKEN", "tok")
        monkeypatch.delenv("FGI_PUSHPLUS_FRIENDS", raising=False)
        monkeypatch.setattr(pushplus, "_post", lambda *a, **k: False)
        assert pushplus._broadcast("t", "c") is False


class TestSendFgiReport:
    def _send(self, db, monkeypatch, transport_result=True):
        captured = {}

        def fake_broadcast(title, content):
            captured["title"] = title
            captured["content"] = content
            return transport_result

        monkeypatch.setattr(pushplus, "_broadcast", fake_broadcast)
        ok = pushplus.send_fgi_report(
            db,
            fgi_raw=72.5,
            dimension_scores={"momentum": 70.0, "sentiment": None},
            indicator_results={
                "M1": {"score": 80.0, "status": "normal", "source_date": "2024-01-10"},
                "S2": {"score": None, "status": "missing", "source_date": None},
            },
            health=88.0,
            date_str="2024-01-10",
            decision_matrix=None,
        )
        return ok, captured

    def test_content_carries_date_score_and_health(self, db, monkeypatch):
        ok, captured = self._send(db, monkeypatch)

        assert ok is True
        assert "2024-01-10" in captured["title"]
        assert "72.5" in captured["content"]
        assert "88" in captured["content"]

    def test_missing_indicator_is_disclosed(self, db, monkeypatch):
        """缺失指标必须在正文里被点名，而不是悄悄少一行。"""
        _, captured = self._send(db, monkeypatch)
        assert "股吧热度 缺失" in captured["content"]

    def test_transport_failure_propagates(self, db, monkeypatch):
        ok, _ = self._send(db, monkeypatch, transport_result=False)
        assert ok is False
