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
    "schema_migrations",
    "job_requests",
    "job_runs",
    "ai_investment_events",
    "pre_earnings_scan_cache",
    "trading_audit_outbox",
    "trading_restore_sessions",
    "trading_restore_applied_events",
    "trade_plans",
    "tick_monitor_state",
    "trading_calendar_cache",
    "sentiment_cache",
    "stock_data_fetch_snapshots",
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


def column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
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
        assert "idx_ai_investment_events_notify" in indexes
        assert "idx_ai_investment_events_symbol_date" in indexes
    finally:
        conn.close()


def test_fresh_init_creates_ai_investment_foundation_columns(temp_db_paths):
    db.init_db()
    conn = db.get_connection()
    try:
        assert {
            "id",
            "job_type",
            "requested_by",
            "request_payload_json",
            "status",
            "correlation_id",
            "created_at_ms",
            "claimed_at_ms",
            "completed_at_ms",
        } <= column_names(conn, "job_requests")
        assert {
            "id",
            "request_id",
            "job_type",
            "runner",
            "status",
            "started_at_ms",
            "finished_at_ms",
            "heartbeat_at_ms",
            "input_json",
            "output_json",
            "error",
            "correlation_id",
        } <= column_names(conn, "job_runs")
        assert {
            "id",
            "event_date",
            "symbol",
            "source",
            "event_type",
            "severity",
            "delivery_scope",
            "verdict",
            "dedupe_key",
            "source_record_id",
            "source_run_id",
            "notify_status",
        } <= column_names(conn, "ai_investment_events")
    finally:
        conn.close()


def test_fresh_init_reconciles_summary_and_briefing_contracts(temp_db_paths):
    db.init_db()
    conn = db.get_connection()
    try:
        assert {
            "date",
            "market",
            "stats_json",
            "per_stock_json",
            "report_md",
            "generated_at",
        } <= column_names(conn, "daily_summaries")
        assert {"date", "generated_at", "content_json"} <= column_names(
            conn, "morning_briefings"
        )

        conn.execute(
            """
            INSERT INTO daily_summaries
              (date, market, stats_json, per_stock_json, report_md, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("2026-05-01", "mixed", "{}", "[]", "## report", 123),
        )
        conn.execute(
            """
            INSERT INTO morning_briefings (date, generated_at, content_json)
            VALUES (?, ?, ?)
            """,
            ("2026-05-01", "2026-05-01T08:00:00", "{}"),
        )
    finally:
        conn.close()


def test_init_migrates_legacy_summary_and_briefing_tables(temp_db_paths):
    conn = db.get_connection()
    try:
        conn.executescript(
            """
            CREATE TABLE daily_summaries (
                date TEXT PRIMARY KEY,
                summary_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );
            INSERT INTO daily_summaries (date, summary_json, updated_at_ms)
            VALUES ('2026-04-30', '{"report":"legacy"}', 111);

            CREATE TABLE morning_briefings (
                date TEXT PRIMARY KEY,
                briefing_json TEXT NOT NULL,
                updated_at_ms INTEGER NOT NULL
            );
            INSERT INTO morning_briefings (date, briefing_json, updated_at_ms)
            VALUES ('2026-04-30', '{"brief":"legacy"}', 222);
            """
        )
    finally:
        conn.close()

    db.init_trading_db()

    conn = db.get_connection()
    try:
        assert "report_md" in column_names(conn, "daily_summaries")
        assert "content_json" in column_names(conn, "morning_briefings")
        legacy_summary = conn.execute(
            "SELECT report_md, generated_at FROM daily_summaries WHERE date = ?",
            ("2026-04-30",),
        ).fetchone()
        assert legacy_summary is not None
        assert legacy_summary["report_md"] == '{"report":"legacy"}'
        assert legacy_summary["generated_at"] == 111

        legacy_briefing = conn.execute(
            "SELECT content_json, generated_at FROM morning_briefings WHERE date = ?",
            ("2026-04-30",),
        ).fetchone()
        assert legacy_briefing is not None
        assert legacy_briefing["content_json"] == '{"brief":"legacy"}'
        assert legacy_briefing["generated_at"] == "222"

        conn.execute(
            """
            INSERT INTO daily_summaries
              (date, market, stats_json, per_stock_json, report_md, generated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            ("2026-05-01", "mixed", "{}", "[]", "## new", 333),
        )
        conn.execute(
            """
            INSERT INTO morning_briefings (date, generated_at, content_json)
            VALUES (?, ?, ?)
            """,
            ("2026-05-01", "2026-05-01T08:00:00", "{}"),
        )

        db.init_trading_db()
        assert (
            conn.execute("SELECT COUNT(*) FROM daily_summaries").fetchone()[0] == 2
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM morning_briefings").fetchone()[0] == 2
        )
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
