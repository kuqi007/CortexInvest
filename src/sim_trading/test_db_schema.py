import sqlite3

import pytest

import src.sim_trading.db as db


REQUIRED_CONFIG_TABLES = {
    "monitor_watchlist",
    "monitor_settings",
    "tag_meta",
    "config_audit_outbox",
    "config_restore_sessions",
    "config_restore_applied_events",
    "poller_leader_lease",
    "l2_strategy_config",
    "signal_rules",
}

REQUIRED_TRADING_TABLES = {
    "signals",
    "price_snapshots",
    "trading_audit_outbox",
    "trading_restore_sessions",
    "trading_restore_applied_events",
    "trade_plans",
    "tick_monitor_state",
    "trading_calendar_cache",
    "sentiment_cache",
    "daily_summaries",
    "morning_briefings",
}


@pytest.fixture()
def temp_db_paths(tmp_path, monkeypatch):
    config_path = tmp_path / "config.db"
    trading_path = tmp_path / "trading.db"
    monkeypatch.setattr(db, "_config_db_path_override", str(config_path))
    monkeypatch.setattr(db, "_db_path_override", str(trading_path))
    return config_path, trading_path


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }


def index_names(conn: sqlite3.Connection) -> set[str]:
    return {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }


def test_config_connection_enables_foreign_keys_and_busy_timeout(temp_db_paths):
    conn = db.get_config_connection()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 15000
    finally:
        conn.close()


def test_trading_connection_enables_foreign_keys_and_busy_timeout(temp_db_paths):
    conn = db.get_connection()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 15000
    finally:
        conn.close()


def test_trading_connection_uses_wal_journal_mode(temp_db_paths):
    conn = db.get_connection()
    try:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_fresh_init_creates_config_tables_and_indexes(temp_db_paths):
    db.init_config_db()
    conn = db.get_config_connection()
    try:
        assert REQUIRED_CONFIG_TABLES <= table_names(conn)
        indexes = index_names(conn)
        assert "idx_config_audit_outbox_pending" in indexes
        assert "idx_config_audit_outbox_correlation" in indexes
    finally:
        conn.close()


def test_fresh_init_creates_trading_tables_and_indexes(temp_db_paths):
    db.init_db()
    conn = db.get_connection()
    try:
        assert REQUIRED_TRADING_TABLES <= table_names(conn)
        indexes = index_names(conn)
        assert "idx_trading_audit_outbox_pending" in indexes
        assert "idx_trading_audit_outbox_correlation" in indexes
    finally:
        conn.close()


@pytest.mark.parametrize(
    ("init_func", "connection_func", "table_name"),
    [
        (db.init_config_db, db.get_config_connection, "config_restore_applied_events"),
        (db.init_trading_db, db.get_connection, "trading_restore_applied_events"),
    ],
)
def test_restore_applied_events_ledger_does_not_cascade_delete(
    temp_db_paths, init_func, connection_func, table_name
):
    init_func()
    conn = connection_func()
    try:
        fk_rows = conn.execute(f"PRAGMA foreign_key_list({table_name})").fetchall()
        assert fk_rows
        assert all(row["on_delete"].upper() != "CASCADE" for row in fk_rows)
    finally:
        conn.close()


def test_config_restore_applied_events_references_restore_sessions(temp_db_paths):
    db.init_config_db()
    conn = db.get_config_connection()
    try:
        fk_rows = conn.execute(
            "PRAGMA foreign_key_list(config_restore_applied_events)"
        ).fetchall()
        assert fk_rows
        assert {row["table"] for row in fk_rows} == {"config_restore_sessions"}
    finally:
        conn.close()
