import json

import pytest

import src.sim_trading.db as db
from src.utils.audit_log import (
    build_audit_event,
    insert_config_outbox,
    insert_trading_outbox,
)


def test_build_audit_event_derives_utc_ts_from_ts_ms():
    event = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before={"shares": 1000},
        after={"shares": 800},
        correlation_id="01HWNM4Z7G8E6Q9M3R2T1V0X5Y",
    )

    assert event["schema_version"] == 1
    assert event["ts"] == "2026-04-28T11:42:00Z"
    assert event["ts_ms"] == 1777376520000
    assert event["db"] == "config.db"
    assert event["before"] == {"shares": 1000}
    assert event["after"] == {"shares": 800}


def test_build_audit_event_rejects_mismatched_ts():
    with pytest.raises(ValueError, match="ts does not match ts_ms"):
        build_audit_event(
            event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
            ts_ms=1777376520000,
            ts="2026-04-28T00:00:00Z",
            source="api_config",
            action="update",
            entity="monitor_watchlist",
            key="HK09988",
            db_name="config.db",
            before=None,
            after={"shares": 800},
        )


def test_build_audit_event_rejects_wrong_db_name():
    with pytest.raises(ValueError, match="Unsupported audit db"):
        build_audit_event(
            event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
            ts_ms=1777376520000,
            source="api_config",
            action="update",
            entity="monitor_watchlist",
            key="HK09988",
            db_name="other.db",
            before=None,
            after={},
        )


def test_build_audit_event_rejects_obvious_secret_fields():
    with pytest.raises(ValueError, match="sensitive audit field"):
        build_audit_event(
            event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
            ts_ms=1777376520000,
            source="api_config",
            action="update",
            entity="monitor_watchlist",
            key="HK09988",
            db_name="config.db",
            before=None,
            after={"api_token": "secret"},
        )


def test_build_audit_event_rejects_sensitive_fields_inside_tuple():
    with pytest.raises(ValueError, match="sensitive audit field"):
        build_audit_event(
            event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
            ts_ms=1777376520000,
            source="api_config",
            action="update",
            entity="monitor_watchlist",
            key="HK09988",
            db_name="config.db",
            before=None,
            after={"items": ({"api_token": "secret"},)},
        )


def test_build_audit_event_rejects_obvious_secret_values():
    with pytest.raises(ValueError, match="sensitive audit value"):
        build_audit_event(
            event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
            ts_ms=1777376520000,
            source="api_config",
            action="update",
            entity="monitor_watchlist",
            key="HK09988",
            db_name="config.db",
            before=None,
            after={"note": "Bearer abcdefghijklmnopqrstuvwxyz"},
        )


def test_build_audit_event_allows_authority_but_rejects_authorization():
    event = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before=None,
        after={"authority": "db", "author": "system"},
    )
    assert event["after"] == {"authority": "db", "author": "system"}

    with pytest.raises(ValueError, match="sensitive audit field"):
        build_audit_event(
            event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5L",
            ts_ms=1777376520000,
            source="api_config",
            action="update",
            entity="monitor_watchlist",
            key="HK09988",
            db_name="config.db",
            before=None,
            after={"authorization": "Bearer secret"},
        )


def test_insert_config_outbox_uses_existing_transaction(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    event = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before=None,
        after={"shares": 800},
    )

    try:
        conn.execute("BEGIN")
        insert_config_outbox(conn, event)
        row = conn.execute(
            "SELECT event_id, db, payload_json FROM config_audit_outbox"
        ).fetchone()
        assert row["event_id"] == event["event_id"]
        assert row["db"] == "config.db"
        assert json.loads(row["payload_json"]) == event
        conn.execute("ROLLBACK")

        count = conn.execute("SELECT COUNT(*) FROM config_audit_outbox").fetchone()[0]
        assert count == 0
    finally:
        conn.close()


def test_insert_trading_outbox_rejects_config_event(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_trading_db()
    conn = db.get_connection()
    event = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before=None,
        after={},
    )

    try:
        with pytest.raises(ValueError, match="trading.db"):
            insert_trading_outbox(conn, event)
    finally:
        conn.close()
