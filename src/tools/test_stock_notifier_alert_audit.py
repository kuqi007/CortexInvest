import json

import src.sim_trading.db as db
from src.tools.stock_notifier import write_alert_events


def test_write_alert_events_records_hashed_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    write_alert_events(
        [
            {
                "symbol": "HK00700",
                "title": "腾讯控股",
                "message": "腾讯控股 触价告警 320.00",
                "display": "腾讯控股 alert display",
                "_kind": "threshold",
                "_level": 1,
                "_change_pct": 3.2,
                "_price": 320.0,
            }
        ]
    )

    conn = db.get_connection()
    try:
        alert_count = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
        row = conn.execute("SELECT payload_json FROM trading_audit_outbox").fetchone()
    finally:
        conn.close()

    assert alert_count == 1
    payload = json.loads(row["payload_json"])
    assert payload["entity"] == "alert_events"
    assert payload["action"] == "create"
    assert payload["after"]["symbol"] == "HK00700"
    assert payload["after"]["message_hash"].startswith("sha256:")
    assert payload["after"]["display_hash"].startswith("sha256:")
    assert "message" not in payload["after"]
    assert "display" not in payload["after"]


def test_write_alert_events_does_not_audit_ignored_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr("src.tools.stock_notifier.time.time", lambda: 1777376520.0)
    db.init_trading_db()
    alert = {
        "symbol": "HK00700",
        "title": "腾讯控股",
        "message": "duplicate alert",
        "display": "duplicate display",
        "_kind": "threshold",
        "_level": 1,
        "_change_pct": 3.2,
        "_price": 320.0,
    }

    write_alert_events([alert])
    write_alert_events([alert])

    conn = db.get_connection()
    try:
        alert_count = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM trading_audit_outbox").fetchone()[0]
    finally:
        conn.close()

    assert alert_count == 1
    assert audit_count == 1
