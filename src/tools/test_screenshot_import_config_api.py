import json

import pytest

from src.tools.screenshot_import.config_api import (
    AppliedRow,
    ConfigApiClient,
    ConfigApiError,
    DEFAULT_API_URL,
)
from src.tools.screenshot_import.models import PlannedAction


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"success": True}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses, watchlist=None):
        self.responses = list(responses)
        self.watchlist = watchlist if watchlist is not None else {}
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(("GET", url, None))
        return FakeResponse(payload={"watchlist": self.watchlist})

    def post(self, url, json, timeout, headers=None):
        self.calls.append(("POST", url, json))
        return self.responses.pop(0)


def _action(code="HK00700", action="add"):
    return PlannedAction(
        row_apply_id="row-1",
        plan_sequence=1,
        action=action,
        code=code,
        payload={
            "code": code,
            "name": "腾讯控股",
            "type": "holding",
            "cost": 320.5,
            "shares": 100,
        },
        reason_codes=[],
    )


def test_remote_http_url_rejected():
    with pytest.raises(ConfigApiError, match="remote config API"):
        ConfigApiClient(base_url="http://192.168.1.1/api/config", session=FakeSession([]))


def test_default_url_allows_loopback():
    client = ConfigApiClient(base_url=DEFAULT_API_URL, session=FakeSession([]))
    assert "127.0.0.1" in client.base_url


def test_localhost_allowed():
    ConfigApiClient(base_url="http://localhost:3120/api/config", session=FakeSession([]))


def test_apply_posts_add_with_audit_context():
    sess = FakeSession([FakeResponse()])
    client = ConfigApiClient(base_url=DEFAULT_API_URL, session=sess, timeout_s=5)
    run_id = "run-abc"
    act = _action()
    result = client.apply_actions([act], import_run_id=run_id)
    assert result.applied == [
        AppliedRow(
            row_apply_id=act.row_apply_id,
            code="HK00700",
            status_code=200,
            ok=True,
            message="",
        )
    ]
    assert result.failed == []
    post_calls = [c for c in sess.calls if c[0] == "POST"]
    assert len(post_calls) == 1
    _, url, body = post_calls[0]
    assert url == DEFAULT_API_URL
    assert body["action"] == "add"
    assert body["code"] == "HK00700"
    assert "cost" not in body
    assert "shares" not in body
    data = body["data"]
    assert isinstance(data, dict)
    assert data["type"] == "holding"
    assert data["name"] == "腾讯控股"
    assert data["cost"] == 320.5
    assert data["shares"] == 100
    assert "code" not in data
    assert body["audit_context"] == {
        "source": "screenshot_stock_import",
        "import_run_id": run_id,
        "row_apply_id": act.row_apply_id,
    }


def test_partial_failure_collects_failed_row():
    sess = FakeSession(
        [
            FakeResponse(200, {"success": True}),
            FakeResponse(200, {"success": False, "message": "bad row"}),
        ],
        watchlist={},
    )
    client = ConfigApiClient(base_url=DEFAULT_API_URL, session=sess, timeout_s=5)
    a1 = PlannedAction(
        row_apply_id="r1",
        plan_sequence=1,
        action="add",
        code="000001",
        payload={"code": "000001", "name": "A", "type": "holding"},
        reason_codes=[],
    )
    a2 = PlannedAction(
        row_apply_id="r2",
        plan_sequence=2,
        action="add",
        code="000002",
        payload={"code": "000002", "name": "B", "type": "holding"},
        reason_codes=[],
    )
    result = client.apply_actions([a1, a2], import_run_id="run-x")
    assert len(result.applied) == 1
    assert result.applied[0].code == "000001"
    assert result.applied[0].ok is True
    assert len(result.failed) == 1
    assert result.failed[0].code == "000002"
    assert result.failed[0].ok is False
    assert result.failed[0].message == "bad row"


def test_update_when_code_in_watchlist():
    code = "HK00700"
    sess = FakeSession([FakeResponse()], watchlist={code: {"name": "腾讯"}})
    client = ConfigApiClient(base_url=DEFAULT_API_URL, session=sess, timeout_s=5)
    act = PlannedAction(
        row_apply_id="row-1",
        plan_sequence=1,
        action="update",
        code=code,
        payload={"code": code, "name": "腾讯控股", "type": "holding", "cost": 330.0, "shares": 100},
        reason_codes=[],
    )
    result = client.apply_actions([act], import_run_id="run-u")
    assert result.failed == []
    post = next(c for c in sess.calls if c[0] == "POST")
    body = post[2]
    assert body["action"] == "update"
    assert body["code"] == code
    assert "cost" not in body
    ud = body["data"]
    assert ud["name"] == "腾讯控股"
    assert ud["type"] == "holding"
    assert ud["cost"] == 330.0
    assert ud["shares"] == 100


def test_actions_sorted_by_plan_sequence():
    sess = FakeSession(
        [
            FakeResponse(),
            FakeResponse(),
        ],
        watchlist={},
    )
    client = ConfigApiClient(base_url=DEFAULT_API_URL, session=sess, timeout_s=5)
    second = PlannedAction(
        row_apply_id="b",
        plan_sequence=2,
        action="add",
        code="B",
        payload={"code": "B", "name": "b", "type": "watching"},
        reason_codes=[],
    )
    first = PlannedAction(
        row_apply_id="a",
        plan_sequence=1,
        action="add",
        code="A",
        payload={"code": "A", "name": "a", "type": "watching"},
        reason_codes=[],
    )
    client.apply_actions([second, first], import_run_id="run-order")
    post_bodies = [c[2] for c in sess.calls if c[0] == "POST"]
    assert [b["code"] for b in post_bodies] == ["A", "B"]
    assert post_bodies[0]["data"]["type"] == "watching"
    assert post_bodies[1]["data"]["type"] == "watching"
