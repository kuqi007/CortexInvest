import json
from types import SimpleNamespace

import src.sim_trading.db as db
import src.tools.stock_monitor as sm


def test_notify_records_terminal_audit_only_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    def fake_run(args, **kwargs):
        if args[:2] == ["which", "terminal-notifier"]:
            return SimpleNamespace(returncode=0)
        if args and args[0] == "terminal-notifier":
            return SimpleNamespace(returncode=0)
        raise AssertionError(f"unexpected command: {args}")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)

    sm.notify(
        "CI Pipeline Alert",
        "HK00700 crossed threshold",
        sound="",
        group="test-group",
        stock_info={"code": "HK00700", "_kind": "threshold"},
    )

    conn = db.get_connection()
    rows = conn.execute("SELECT payload_json FROM trading_audit_outbox").fetchall()
    conn.close()

    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["after"]["channel"] == "terminal"
    assert "message" not in payload["after"]
    assert payload["after"]["message_hash"].startswith("sha256:")
    assert payload["after"]["metadata"]["symbol"] == "HK00700"


def test_feishu_send_records_audit_on_success(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    monkeypatch.setattr(sm, "feishu_get_token", lambda: "token")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0}

    monkeypatch.setattr(sm.requests, "post", lambda *args, **kwargs: Response())

    assert sm.feishu_send(
        "Trade Plan",
        "HK00700 buy condition triggered",
        stock_info={"code": "HK00700", "name": "腾讯", "_kind": "trade_plan"},
    )

    conn = db.get_connection()
    rows = conn.execute("SELECT payload_json FROM trading_audit_outbox").fetchall()
    conn.close()

    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["after"]["channel"] == "feishu"
    assert "message" not in payload["after"]
    assert payload["after"]["message_hash"].startswith("sha256:")
    assert payload["after"]["metadata"]["symbol"] == "HK00700"


def test_notify_does_not_record_audit_when_terminal_send_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    monkeypatch.setattr(sm.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))

    sm.notify(
        "CI Pipeline Alert",
        "HK00700 crossed threshold",
        sound="",
        group="test-group",
        stock_info={"code": "HK00700", "_kind": "threshold"},
    )

    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM trading_audit_outbox").fetchone()[0]
    conn.close()

    assert count == 0


def test_feishu_send_does_not_record_audit_on_api_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    monkeypatch.setattr(sm, "feishu_get_token", lambda: "token")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 999, "msg": "failed"}

    monkeypatch.setattr(sm.requests, "post", lambda *args, **kwargs: Response())

    assert not sm.feishu_send(
        "Trade Plan",
        "HK00700 buy condition triggered",
        stock_info={"code": "HK00700", "name": "腾讯", "_kind": "trade_plan"},
    )

    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM trading_audit_outbox").fetchone()[0]
    conn.close()

    assert count == 0
