import json

import src.sim_trading.db as db
from src.tools.audit_flush import flush_outbox_once
from src.utils.notification_audit import record_notification_sent


def test_record_notification_sent_writes_trading_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    record_notification_sent(
        channel="terminal",
        title="CI Pipeline Alert",
        message="HK00700 alert",
        metadata={"symbol": "HK00700", "kind": "threshold"},
        ts_ms=1777376520000,
    )

    conn = db.get_connection()
    row = conn.execute(
        "SELECT source, action, entity, key, payload_json FROM trading_audit_outbox"
    ).fetchone()
    conn.close()

    assert row["source"] == "notification"
    assert row["action"] == "sent"
    assert row["entity"] == "notification"
    assert row["key"] == "terminal:HK00700"
    payload = json.loads(row["payload_json"])
    assert payload["after"]["channel"] == "terminal"
    assert payload["after"]["title"] == "CI Pipeline Alert"
    assert payload["after"]["message"] == "HK00700 alert"
    assert payload["after"]["metadata"] == {"symbol": "HK00700", "kind": "threshold"}


def test_notification_audit_flushes_to_trading_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    record_notification_sent(
        channel="feishu",
        title="Tick Batch",
        message="2 alerts",
        metadata={"symbol": "HK00700", "count": 2},
        ts_ms=1777376520000,
    )

    result = flush_outbox_once("trading", data_dir=tmp_path, now_ms=1777376521000)

    assert result.flushed_count == 1
    jsonl_path = tmp_path / "audit" / "trading_events.jsonl"
    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
    assert rows[0]["source"] == "notification"
    assert rows[0]["after"]["channel"] == "feishu"
    assert rows[0]["hash"].startswith("sha256:")
