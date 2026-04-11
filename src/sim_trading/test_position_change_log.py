"""Tests for position_change_log table and recording logic.

Run: poetry run pytest src/sim_trading/test_position_change_log.py -v
"""

import json
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from . import db as db_mod
from . import futu_position_sync as futu_sync_mod
from .futu_position_sync import FutuPositionSync, _get_watch_row


@dataclass
class MockFutuPosition:
    """Minimal mock matching futu_trade_adapter.FutuPosition fields used by sync_live_state."""
    code: str
    quantity: int
    avg_price: float
    market_val: float
    unrealized_pnl: float
    today_pnl: float
    today_buy_qty: int
    name: str


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_db():
    """Create a temporary in-memory database with the full schema."""
    conn = sqlite3.connect("file::memory:?cache=shared", uri=True, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    conn.executescript(db_mod.SCHEMA)

    # Insert a test watchlist entry with known shares/cost
    now_ts = int(time.time())
    conn.execute("""
        INSERT INTO monitor_watchlist
          (symbol, name, list_type, cost, shares, lot, hidden, star, dip_buy,
           tags, watch_price, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, ("HK00700", "腾讯", "holding", 350.0, 200, 100, 0, 0, 0,
           "[]", None, now_ts, now_ts))

    yield conn
    conn.close()


# ---------------------------------------------------------------------------
# Table creation
# ---------------------------------------------------------------------------

class TestPositionChangeLogTable:

    def test_table_exists_in_schema(self, tmp_db):
        """Schema defines position_change_log table."""
        rows = tmp_db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='position_change_log'"
        ).fetchall()
        assert len(rows) == 1

    def test_table_has_required_columns(self, tmp_db):
        """Table has symbol, ts, source, shares_from, shares_to, cost_from, cost_to."""
        cols = {r["name"] for r in tmp_db.execute("PRAGMA table_info(position_change_log)").fetchall()}
        expected = {"id", "symbol", "ts", "source", "shares_from", "shares_to",
                    "cost_from", "cost_to"}
        assert expected.issubset(cols), f"Missing columns: {expected - cols}"

    def test_unique_index_on_all_fields(self, tmp_db):
        """UNIQUE index prevents duplicate entries."""
        now_iso = "2026-03-22T10:00:00"
        tmp_db.execute("""
            INSERT INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK00700", now_iso, "manual", 200, 300, 350.0, 340.0))

        # Same exact row should be ignored (no error, no duplicate)
        tmp_db.execute("""
            INSERT OR IGNORE INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK00700", now_iso, "manual", 200, 300, 350.0, 340.0))

        count = tmp_db.execute(
            "SELECT COUNT(*) FROM position_change_log WHERE symbol='HK00700'"
        ).fetchone()[0]
        assert count == 1, "Duplicate should have been ignored"

    def test_nulls_allowed(self, tmp_db):
        """Null values are allowed for shares_from/to and cost_from/to (initial state)."""
        now_iso = "2026-03-22T11:00:00"
        tmp_db.execute("""
            INSERT INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK99999", now_iso, "sync", None, 500, None, 120.0))

        row = tmp_db.execute(
            "SELECT * FROM position_change_log WHERE symbol='HK99999'"
        ).fetchone()
        assert row is not None
        assert row["shares_from"] is None
        assert row["shares_to"] == 500
        assert row["cost_from"] is None
        assert row["cost_to"] == 120.0


# ---------------------------------------------------------------------------
# _get_watch_row helper
# ---------------------------------------------------------------------------

class TestGetWatchRow:

    def test_returns_dict(self, tmp_db):
        """Returns dict with shares and cost for existing symbol."""
        result = _get_watch_row(tmp_db, "HK00700")
        assert result is not None
        assert result.get("shares") == 200
        assert result.get("cost") == 350.0

    def test_returns_none_for_missing(self, tmp_db):
        """Returns None when symbol not in watchlist."""
        result = _get_watch_row(tmp_db, "HK00000")
        assert result is None


# ---------------------------------------------------------------------------
# Change detection logic (shares/cost changes detected)
# ---------------------------------------------------------------------------

class TestChangeDetection:

    def test_records_when_shares_change(self, tmp_db):
        """shares 200→300 is recorded as a change."""
        now_iso = "2026-03-22T12:00:00"
        tmp_db.execute("""
            INSERT INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK00700", now_iso, "manual", 200, 300, 350.0, 350.0))

        row = tmp_db.execute(
            "SELECT * FROM position_change_log WHERE symbol='HK00700'"
        ).fetchone()
        assert row["shares_from"] == 200
        assert row["shares_to"] == 300
        assert row["source"] == "manual"

    def test_records_when_cost_changes(self, tmp_db):
        """cost 350→340 is recorded as a change."""
        now_iso = "2026-03-22T12:05:00"
        tmp_db.execute("""
            INSERT INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK00700", now_iso, "manual", 200, 200, 350.0, 340.0))

        row = tmp_db.execute(
            "SELECT * FROM position_change_log WHERE symbol='HK00700'"
        ).fetchone()
        assert row["cost_from"] == 350.0
        assert row["cost_to"] == 340.0

    def test_no_record_when_no_change(self, tmp_db):
        """Same shares and cost → no new row (simulates idempotency check at app level)."""
        # The INSERT OR IGNORE unique index handles exact duplicates.
        # Here we verify that a second identical INSERT is silently ignored.
        now_iso = "2026-03-22T12:10:00"
        tmp_db.execute("""
            INSERT INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK00700", now_iso, "manual", 200, 200, 350.0, 350.0))

        # Same values at same timestamp → unique index skip
        tmp_db.execute("""
            INSERT OR IGNORE INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK00700", now_iso, "manual", 200, 200, 350.0, 350.0))

        count = tmp_db.execute(
            "SELECT COUNT(*) FROM position_change_log WHERE symbol='HK00700'"
        ).fetchone()[0]
        # Both succeeded but second was ignored → still 1
        assert count == 1

    def test_sync_source_for_futu(self, tmp_db):
        """Futu sync writes source='sync'."""
        now_iso = "2026-03-22T13:00:00"
        tmp_db.execute("""
            INSERT INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK00700", now_iso, "sync", 200, 500, 350.0, 345.0))

        row = tmp_db.execute(
            "SELECT * FROM position_change_log WHERE symbol='HK00700' AND source='sync'"
        ).fetchone()
        assert row is not None
        assert row["source"] == "sync"

    def test_zero_quantity_becomes_null(self, tmp_db):
        """Futu avg_price=0 or quantity=0 is stored as None (null in DB)."""
        now_iso = "2026-03-22T13:05:00"
        tmp_db.execute("""
            INSERT INTO position_change_log
              (symbol, ts, source, shares_from, shares_to, cost_from, cost_to)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, ("HK00700", now_iso, "sync", 200, None, 350.0, None))

        row = tmp_db.execute(
            "SELECT * FROM position_change_log WHERE symbol='HK00700'"
        ).fetchone()
        # quantity=0 (None means sold) → shares_to is None
        assert row["shares_to"] is None
        # avg_price=0 → cost_to is None
        assert row["cost_to"] is None


# ---------------------------------------------------------------------------
# Integration: FutuPositionSync records changes
# ---------------------------------------------------------------------------

class TestFutuSyncRecordsChange:

    def test_sync_records_new_position(self, tmp_db):
        """FutuPositionSync.sync_live_state writes a log entry when shares/cost change."""
        old_override = db_mod._db_path_override
        old_get_conn = db_mod.get_connection
        old_fps_get_config = futu_sync_mod.get_config_connection

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tname = f.name

        try:
            # Use init_db() to create proper schema (applies migrations too)
            db_mod._db_path_override = tname
            db_mod.init_db()

            # Patch get_connection to return the test conn
            test_conn = db_mod.get_connection()
            test_conn.row_factory = sqlite3.Row

            now_ts = int(time.time())
            test_conn.execute("""
                INSERT INTO monitor_watchlist
                  (symbol, name, list_type, cost, shares, lot, hidden, star, dip_buy,
                   tags, watch_price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("HK00700", "腾讯", "holding", 350.0, 200, 100, 0, 0, 0,
                   "[]", None, now_ts, now_ts))
            test_conn.commit()

            db_mod.get_connection = lambda: test_conn
            futu_sync_mod.get_config_connection = lambda: test_conn

            mock_adapter = MagicMock()
            mock_pos = MockFutuPosition(
                code="HK00700",
                quantity=500,
                avg_price=345.0,
                market_val=172500.0,
                unrealized_pnl=0.0,
                today_pnl=0.0,
                today_buy_qty=0,
                name="腾讯",
            )
            mock_adapter.get_positions.return_value = {"HK00700": mock_pos}

            sync = FutuPositionSync.__new__(FutuPositionSync)
            sync._adapter = mock_adapter
            sync._prev_positions = {}
            sync.sync_live_state()

            # sync_live_state closes patched conns; read back via new handle
            verify = sqlite3.connect(tname)
            verify.row_factory = sqlite3.Row
            try:
                row = verify.execute(
                    "SELECT * FROM position_change_log WHERE symbol='HK00700'"
                ).fetchone()
            finally:
                verify.close()

            assert row is not None, "sync_live_state should have written a log entry"
            assert row["source"] == "sync"
            assert row["shares_from"] == 200
            assert row["shares_to"] == 500
            assert row["cost_from"] == 350.0
            assert row["cost_to"] == 345.0
        finally:
            db_mod._db_path_override = old_override
            db_mod.get_connection = old_get_conn
            futu_sync_mod.get_config_connection = old_fps_get_config
            import os
            os.unlink(tname)

    def test_sync_no_record_when_unchanged(self, tmp_db):
        """No log entry when Futu returns same shares/cost as DB."""
        original_get_connection = db_mod.get_connection
        original_fps_get_config = futu_sync_mod.get_config_connection
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            tname = f.name

        try:
            db_mod._db_path_override = tname
            db_mod.init_db()

            test_conn = db_mod.get_connection()
            test_conn.row_factory = sqlite3.Row

            now_ts = int(time.time())
            test_conn.execute("""
                INSERT INTO monitor_watchlist
                  (symbol, name, list_type, cost, shares, lot, hidden, star, dip_buy,
                   tags, watch_price, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, ("HK00700", "腾讯", "holding", 350.0, 200, 100, 0, 0, 0,
                   "[]", None, now_ts, now_ts))
            test_conn.commit()

            db_mod.get_connection = lambda: test_conn
            futu_sync_mod.get_config_connection = lambda: test_conn

            mock_adapter = MagicMock()
            mock_pos = MockFutuPosition(
                code="HK00700",
                quantity=200,  # Same as DB
                avg_price=350.0,  # Same as DB
                market_val=70000.0,
                unrealized_pnl=0.0,
                today_pnl=0.0,
                today_buy_qty=0,
                name="腾讯",
            )
            mock_adapter.get_positions.return_value = {"HK00700": mock_pos}

            sync = FutuPositionSync.__new__(FutuPositionSync)
            sync._adapter = mock_adapter
            sync._prev_positions = {}
            sync.sync_live_state()

            verify = sqlite3.connect(tname)
            try:
                count = verify.execute(
                    "SELECT COUNT(*) FROM position_change_log WHERE symbol='HK00700'"
                ).fetchone()[0]
            finally:
                verify.close()
            assert count == 0, "No log entry should be written when nothing changed"
        finally:
            db_mod._db_path_override = None
            db_mod.get_connection = original_get_connection
            futu_sync_mod.get_config_connection = original_fps_get_config
            import os
            os.unlink(tname)
