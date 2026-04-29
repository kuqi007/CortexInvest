import json

import src.sim_trading.db as db
from src.utils.audit_writer import (
    audit_table_enabled,
    hash_text_payload,
    record_db_change_best_effort,
)


def test_audit_registry_excludes_high_frequency_tables():
    assert not audit_table_enabled("trading.db", "price_snapshots")
    assert not audit_table_enabled("trading.db", "market_turnover")
    assert not audit_table_enabled("trading.db", "signals")
    assert not audit_table_enabled("trading.db", "session_snapshots")
    assert not audit_table_enabled("trading.db", "tick_monitor_events")
    assert audit_table_enabled("trading.db", "alert_events")
    assert audit_table_enabled("trading.db", "trades")


def test_hash_text_payload_removes_plaintext():
    payload = hash_text_payload({"message": "secret alert", "display": "shown", "symbol": "HK00700"})

    assert "message" not in payload
    assert "display" not in payload
    assert payload["message_hash"].startswith("sha256:")
    assert payload["message_len"] == len("secret alert")
    assert payload["display_hash"].startswith("sha256:")
    assert payload["display_len"] == len("shown")
    assert payload["symbol"] == "HK00700"


def test_record_db_change_best_effort_writes_trading_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    conn = db.get_connection()
    try:
        ok = record_db_change_best_effort(
            conn,
            db_name="trading.db",
            table="trades",
            action="create",
            key="trade-1",
            source="test",
            actor={"type": "system", "id": "pytest"},
            before=None,
            after={"trade_id": "trade-1", "code": "HK00700"},
        )
        conn.commit()
    finally:
        conn.close()

    assert ok is True
    conn = db.get_connection()
    row = conn.execute("SELECT payload_json FROM trading_audit_outbox").fetchone()
    conn.close()
    payload = json.loads(row["payload_json"])
    assert payload["entity"] == "trades"
    assert payload["key"] == "trade-1"


def test_record_db_change_best_effort_skips_excluded_tables(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    conn = db.get_connection()
    try:
        ok = record_db_change_best_effort(
            conn,
            db_name="trading.db",
            table="signals",
            action="create",
            key="signal-1",
            source="test",
            actor={"type": "system", "id": "pytest"},
            before=None,
            after={"code": "HK00700"},
        )
        conn.commit()
    finally:
        conn.close()

    assert ok is False
    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM trading_audit_outbox").fetchone()[0]
    conn.close()
    assert count == 0


def test_record_db_change_best_effort_does_not_block_business_commit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    conn = db.get_connection()
    try:
        conn.execute(
            "INSERT INTO alert_events (ts, date, time, symbol, kind, message) VALUES (?, ?, ?, ?, ?, ?)",
            (1, "2026-04-29", "10:00:00", "HK00700", "test", "body"),
        )
        ok = record_db_change_best_effort(
            conn,
            db_name="trading.db",
            table="alert_events",
            action="create",
            key="HK00700:1",
            source="test",
            actor={"type": "system", "id": "pytest"},
            before=None,
            after={"api_token": "would fail audit"},
        )
        conn.commit()
    finally:
        conn.close()

    assert ok is False
    conn = db.get_connection()
    alert_count = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
    audit_count = conn.execute("SELECT COUNT(*) FROM trading_audit_outbox").fetchone()[0]
    conn.close()
    assert alert_count == 1
    assert audit_count == 0
