#!/usr/bin/env python3
"""
Tests for L2 tick data integrity in daily summary generation.

 Covers:
 - session_snapshots tick data persistence
 - _compute_l2_digest uses correct time cutoff (16:00)
 - A-shares (no tick data) don't appear with fake tick values
 - Daily report LLM prompt includes stock whitelist
"""

import json
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# Add project root to path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestL2TickIntegrity:
    """Tests for L2 tick data integrity."""

    @pytest.fixture
    def mock_db(self, tmp_path):
        """Create temp SQLite DB with session_snapshots table."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row  # Enable dict-like access
        conn.execute("""
            CREATE TABLE session_snapshots (
                id INTEGER PRIMARY KEY,
                ts INTEGER,
                date TEXT,
                time TEXT,
                code TEXT,
                session_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE daily_l2_digest (
                date TEXT,
                code TEXT,
                lo_buy_count INTEGER,
                lo_sell_count INTEGER,
                lo_net_amount REAL,
                lo_net_ratio REAL,
                tick_imbalance REAL,
                tick_buy_vol INTEGER,
                tick_sell_vol INTEGER,
                cf_net_inflow REAL,
                cf_net_inflow_pct REAL,
                vpd_count INTEGER,
                lor_count INTEGER,
                lor_direction TEXT,
                direction_score INTEGER,
                direction TEXT,
                session_json TEXT,
                PRIMARY KEY (date, code)
            )
        """)
        conn.execute("""
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                ts INTEGER,
                date TEXT,
                time TEXT,
                code TEXT,
                strategy TEXT,
                direction TEXT
            )
        """)
        conn.commit()
        return db_path, conn

    def _make_session_json(
        self, tick_imbalance, buy_vol, sell_vol, lo_buy=0, lo_sell=0, cf_inflow=0
    ):
        """Helper to create session JSON."""
        return json.dumps(
            {
                "direction": "bullish" if tick_imbalance > 0 else "bearish",
                "score": 1 if tick_imbalance > 0 else -1,
                "tick": {
                    "buy_vol": buy_vol,
                    "sell_vol": sell_vol,
                    "imbalance": tick_imbalance,
                    "direction_score": 1
                    if tick_imbalance > 0.1
                    else -1
                    if tick_imbalance < -0.1
                    else 0,
                },
                "large_order": {
                    "buy_count": lo_buy,
                    "sell_count": lo_sell,
                    "buy_amount": lo_buy * 1000000,
                    "sell_amount": lo_sell * 1000000,
                    "net_amount": (lo_buy - lo_sell) * 1000000,
                    "direction_score": 1
                    if lo_buy > lo_sell
                    else -1
                    if lo_sell > lo_buy
                    else 0,
                    "orders": [],
                },
                "capital_flow": {
                    "main_net_inflow": cf_inflow,
                    "main_net_inflow_pct": 5.0,
                    "direction_score": 1 if cf_inflow > 0 else -1,
                },
            }
        )

    def test_compute_l2_digest_uses_1600_cutoff(self, mock_db, monkeypatch):
        """Test that _compute_l2_digest uses 16:00 cutoff to avoid post-close zero tick data."""
        db_path, conn = mock_db

        # Insert records: one at 15:55 with valid tick, one at 16:10 with zero tick
        date_str = datetime.now().strftime("%Y-%m-%d")

        # 15:55 record with valid tick data
        ts_1555 = int(datetime.now().replace(hour=15, minute=55).timestamp() * 1000)
        session_1555 = self._make_session_json(
            tick_imbalance=0.25, buy_vol=1000000, sell_vol=600000
        )
        conn.execute(
            "INSERT INTO session_snapshots (ts, date, time, code, session_json) VALUES (?, ?, ?, ?, ?)",
            (ts_1555, date_str, "15:55:00", "HK00700", session_1555),
        )

        # 16:10 record with zero tick (simulating post-close Futu API behavior)
        ts_1610 = int(datetime.now().replace(hour=16, minute=10).timestamp() * 1000)
        session_1610 = self._make_session_json(
            tick_imbalance=0.0, buy_vol=0, sell_vol=0
        )
        conn.execute(
            "INSERT INTO session_snapshots (ts, date, time, code, session_json) VALUES (?, ?, ?, ?, ?)",
            (ts_1610, date_str, "16:10:01", "HK00700", session_1610),
        )
        conn.commit()
        conn.close()

        # Patch DB connection
        from src.sim_trading import db as db_module

        original_get_conn = db_module.get_connection

        def mock_get_connection():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(db_module, "get_connection", mock_get_connection)

        # Import and test
        from src.tools.daily_summary_generator import _compute_l2_digest

        digests = _compute_l2_digest(date_str)

        assert len(digests) == 1
        digest = digests[0]
        assert digest["code"] == "HK00700"
        # Should use 15:55 data (tick_imbalance=0.25), not 16:10 data (tick_imbalance=0.0)
        assert digest["tick_imbalance"] == 0.25
        assert digest["tick_buy_vol"] == 1000000
        assert digest["tick_sell_vol"] == 600000

    def test_compute_l2_digest_excludes_a_shares(self, mock_db, monkeypatch):
        """Test that A-shares (no tick data) are not in digest."""
        db_path, conn = mock_db
        date_str = datetime.now().strftime("%Y-%m-%d")

        # Only insert HK stock
        ts = int(datetime.now().replace(hour=15, minute=30).timestamp() * 1000)
        session = self._make_session_json(
            tick_imbalance=0.15, buy_vol=500000, sell_vol=400000
        )
        conn.execute(
            "INSERT INTO session_snapshots (ts, date, time, code, session_json) VALUES (?, ?, ?, ?, ?)",
            (ts, date_str, "15:30:00", "HK00700", session),
        )
        conn.commit()
        conn.close()

        # Patch DB connection
        from src.sim_trading import db as db_module

        def mock_get_conn():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(db_module, "get_connection", mock_get_conn)

        from src.tools.daily_summary_generator import _compute_l2_digest

        digests = _compute_l2_digest(date_str)

        codes = [d["code"] for d in digests]
        assert "HK00700" in codes
        # A-shares like 600673 should NOT be in digest (no session data)
        assert "600673" not in codes

    def test_build_llm_prompt_includes_micro_data_whitelist(self, monkeypatch):
        """Test that LLM prompt includes whitelist of stocks with micro data."""
        from src.tools.daily_summary_generator import _build_llm_prompt

        stats = {
            "totalSignals": 10,
            "totalAlerts": 5,
            "stockCount": 3,
            "l1Count": 8,
            "bullish": 3,
            "bearish": 2,
            "upCount": 25,
            "downCount": 15,
        }
        per_stock = [
            {
                "code": "HK00700",
                "name": "腾讯",
                "type": "holding",
                "change": 1.0,
                "signalCount": 2,
                "direction": "偏多",
                "keySignals": ["test"],
                "star": True,
                "mkt_val": 100000,
                "cost": 500.0,
                "shares": 200,
                "pnl_pct": 5.0,
            },
            {
                "code": "600673",
                "name": "东阳光",
                "type": "holding",
                "change": -0.5,
                "signalCount": 0,
                "direction": "中性",
                "keySignals": [],
                "star": False,
                "mkt_val": 50000,
                "cost": 30.0,
                "shares": 1000,
                "pnl_pct": -2.0,
            },
        ]
        l1_displays = []

        # Create digest_map with only HK00700 (simulating no tick data for A-shares)
        l2_digest_map = {
            "HK00700": {
                "code": "HK00700",
                "tick_imbalance": 0.15,
                "lo_net_amount": 1000000,
                "lo_net_ratio": 0.1,
                "cf_net_inflow": 500000,
                "cf_net_inflow_pct": 2.5,
                "direction_score": 2,
                "direction": "bullish",
                "vpd_count": 0,
                "lor_count": 0,
                "lor_direction": None,
            }
        }

        messages = _build_llm_prompt(stats, per_stock, l1_displays, l2_digest_map)

        # Check system message
        system_msg = messages[0]["content"]
        assert "严禁编造数据" in system_msg or "不可杜撰" in system_msg

        # Check user message contains whitelist section
        user_msg = messages[1]["content"]
        # Should have whitelist section with stocks that have micro data
        assert "有微观数据的股票代码列表" in user_msg
        assert "HK00700" in user_msg
        # Should have warning about not fabricating data
        assert "严禁为不在此列表中的股票编造微观数据" in user_msg

    def test_build_llm_prompt_empty_digest_map_has_no_data_warning(self):
        """When digest_map is empty, prompt must include explicit 'no micro data' warning."""
        from src.tools.daily_summary_generator import _build_llm_prompt

        stats = {
            "totalSignals": 5,
            "totalAlerts": 10,
            "stockCount": 2,
            "l1Count": 3,
            "bullish": 1,
            "bearish": 0,
            "upCount": 8,
            "downCount": 2,
        }
        per_stock = [
            {
                "code": "HK03986",
                "name": "兆易创新",
                "type": "holding",
                "change": 13.44,
                "signalCount": 0,
                "direction": "中性",
                "keySignals": ["大幅异动x2", "触价告警"],
                "star": True,
                "mkt_val": 200000,
                "cost": 300.0,
                "shares": 500,
                "pnl_pct": 10.0,
            },
        ]
        l1_displays = ["HK03986 大幅异动"]

        messages = _build_llm_prompt(stats, per_stock, l1_displays, l2_digest_map=None)

        user_msg = messages[1]["content"]

        assert "今日无数据" in user_msg
        assert "严禁编造" in user_msg
        assert "逐股速览" in user_msg or "无微观数据" in user_msg
        assert "有微观数据的股票代码列表" not in user_msg

        all_stocks_micro = [line for line in user_msg.split("\n") if "微观:" in line]
        for line in all_stocks_micro:
            assert "无微观数据" in line

    def test_load_trade_plans_from_db_ignores_trade_plans_json(self, tmp_path, monkeypatch):
        """Daily summary should load trade plans from trading.db only."""
        import src.sim_trading.db as db_module
        import src.tools.daily_summary_generator as dsg

        monkeypatch.setattr(db_module, "_db_path_override", str(tmp_path / "trading.db"))
        monkeypatch.setattr(dsg, "TRADING_DB_PATH", str(tmp_path / "trading.db"))
        monkeypatch.setattr(
            dsg, "TRADE_PLANS_PATH", tmp_path / "missing_trade_plans.json", raising=False
        )
        db_module.init_trading_db()
        conn = db_module.get_connection()
        conn.execute(
            """
            INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "plan-db",
                "DB Plan",
                "HK00700",
                "active",
                "real",
                "2026-04-28",
                json.dumps([{"id": "entry", "side": "buy"}]),
            ),
        )
        conn.commit()
        conn.close()

        plans = dsg._load_trade_plans_from_db()

        assert plans == {
            "plans": {
                "plan-db": {
                    "name": "DB Plan",
                    "symbol": "HK00700",
                    "status": "active",
                    "scope": "real",
                    "created_at": "2026-04-28",
                    "orders": [{"id": "entry", "side": "buy"}],
                }
            }
        }

    def test_digest_direction_score_calculation(self, mock_db, monkeypatch):
        """Test direction score calculation with tick imbalance."""
        db_path, conn = mock_db
        date_str = datetime.now().strftime("%Y-%m-%d")

        ts = int(datetime.now().replace(hour=15, minute=30).timestamp() * 1000)
        # Strong bullish: tick +0.3, large order net positive, capital flow positive
        session = self._make_session_json(
            tick_imbalance=0.30,
            buy_vol=1000000,
            sell_vol=400000,
            lo_buy=5,
            lo_sell=1,
            cf_inflow=10000000,
        )
        conn.execute(
            "INSERT INTO session_snapshots (ts, date, time, code, session_json) VALUES (?, ?, ?, ?, ?)",
            (ts, date_str, "15:30:00", "HK00700", session),
        )
        conn.commit()
        conn.close()

        from src.sim_trading import db as db_module

        def mock_get_conn():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(db_module, "get_connection", mock_get_conn)

        from src.tools.daily_summary_generator import _compute_l2_digest

        digests = _compute_l2_digest(date_str)

        assert len(digests) == 1
        digest = digests[0]
        # Score calculation: tick(1)*2 + lo(1)*3 + cf(1)*1 = 6 -> bullish
        assert digest["direction_score"] > 2
        assert digest["direction"] == "bullish"


class TestTickDataEdgeCases:
    """Edge case tests for tick data handling."""

    @pytest.fixture
    def mock_db(self, tmp_path):
        """Create temp SQLite DB with session_snapshots table."""
        db_path = tmp_path / "test.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("""
            CREATE TABLE session_snapshots (
                id INTEGER PRIMARY KEY,
                ts INTEGER,
                date TEXT,
                time TEXT,
                code TEXT,
                session_json TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE daily_l2_digest (
                date TEXT,
                code TEXT,
                lo_buy_count INTEGER,
                lo_sell_count INTEGER,
                lo_net_amount REAL,
                lo_net_ratio REAL,
                tick_imbalance REAL,
                tick_buy_vol INTEGER,
                tick_sell_vol INTEGER,
                cf_net_inflow REAL,
                cf_net_inflow_pct REAL,
                vpd_count INTEGER,
                lor_count INTEGER,
                lor_direction TEXT,
                direction_score INTEGER,
                direction TEXT,
                session_json TEXT,
                PRIMARY KEY (date, code)
            )
        """)
        conn.execute("""
            CREATE TABLE signals (
                id INTEGER PRIMARY KEY,
                ts INTEGER,
                date TEXT,
                time TEXT,
                code TEXT,
                strategy TEXT,
                direction TEXT
            )
        """)
        conn.commit()
        return db_path, conn

    def test_zero_tick_volume_handling(self, mock_db, monkeypatch):
        """Test handling of zero tick volume (should result in neutral score)."""
        db_path, conn = mock_db
        date_str = datetime.now().strftime("%Y-%m-%d")

        ts = int(datetime.now().replace(hour=15, minute=30).timestamp() * 1000)
        # Zero tick volume but positive capital flow
        session = json.dumps(
            {
                "direction": "neutral",
                "score": 0,
                "tick": {
                    "buy_vol": 0,
                    "sell_vol": 0,
                    "imbalance": 0.0,
                    "direction_score": 0,
                },
                "large_order": {
                    "buy_count": 0,
                    "sell_count": 0,
                    "buy_amount": 0,
                    "sell_amount": 0,
                    "net_amount": 0,
                    "direction_score": 0,
                    "orders": [],
                },
                "capital_flow": {
                    "main_net_inflow": 1000000,
                    "main_net_inflow_pct": 2.0,
                    "direction_score": 1,
                },
            }
        )
        conn.execute(
            "INSERT INTO session_snapshots (ts, date, time, code, session_json) VALUES (?, ?, ?, ?, ?)",
            (ts, date_str, "15:30:00", "HK00700", session),
        )
        conn.commit()
        conn.close()

        from src.sim_trading import db as db_module

        def mock_get_conn():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(db_module, "get_connection", mock_get_conn)

        from src.tools.daily_summary_generator import _compute_l2_digest

        digests = _compute_l2_digest(date_str)

        assert len(digests) == 1
        digest = digests[0]
        assert digest["tick_imbalance"] == 0.0
        assert digest["tick_buy_vol"] == 0
        assert digest["tick_sell_vol"] == 0

    def test_session_accumulator_restore_from_db(self, mock_db, monkeypatch):
        """Test that SessionAccumulator can restore its state from session_snapshots."""
        db_path, conn = mock_db
        date_str = datetime.now().strftime("%Y-%m-%d")

        ts = int(datetime.now().replace(hour=15, minute=30).timestamp() * 1000)
        session = json.dumps(
            {
                "direction": "bullish",
                "score": 3,
                "tick": {
                    "buy_vol": 100000,
                    "sell_vol": 50000,
                    "imbalance": 0.333,
                    "direction_score": 1,
                },
                "large_order": {
                    "buy_count": 5,
                    "sell_count": 2,
                    "buy_amount": 10000000.0,
                    "sell_amount": 3000000.0,
                    "net_amount": 7000000.0,
                    "direction_score": 1,
                    "orders": [],
                },
                "capital_flow": {
                    "main_net_inflow": 2000000.0,
                    "main_net_inflow_pct": 5.0,
                    "direction_score": 1,
                },
                "last_seq": 42,
                "warmed_up": True,
            }
        )
        conn.execute(
            "INSERT INTO session_snapshots (ts, date, time, code, session_json) VALUES (?, ?, ?, ?, ?)",
            (ts, date_str, "15:30:00", "HK00700", session),
        )
        conn.commit()
        conn.close()

        from src.sim_trading import db as db_module

        def mock_get_conn():
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn

        monkeypatch.setattr(db_module, "get_connection", mock_get_conn)

        from src.tools.l2_strategy_engine import SessionAccumulator, LargeOrderTracker

        acc = SessionAccumulator()
        restored = acc.restore_from_db(date_str)

        assert restored == {"HK00700"}
        assert acc._tick_totals["HK00700"]["buy_vol"] == 100000
        assert acc._tick_totals["HK00700"]["sell_vol"] == 50000
        assert acc._large_order_totals["HK00700"]["buy_count"] == 5
        assert acc._large_order_totals["HK00700"]["buy_amount"] == 10000000.0
        assert acc._large_order_totals["HK00700"]["sell_amount"] == 3000000.0
        assert acc._capital_flow["HK00700"]["main_net_inflow"] == 2000000.0
        assert acc._last_seq["HK00700"] == 42
        assert "HK00700" in acc._warmed_up

        # Verify LargeOrderTracker can be synced
        lo = LargeOrderTracker()
        lo.restore_state("HK00700", 42, True)
        assert lo._last_seq["HK00700"] == 42
        assert "HK00700" in lo._warmed_up

    def test_session_accumulator_snapshot_includes_restore_fields(self):
        """Test that snapshot() includes last_seq and warmed_up for persistence."""
        from src.tools.l2_strategy_engine import SessionAccumulator

        acc = SessionAccumulator()
        acc._tick_totals["HK00700"] = {"buy_vol": 1000, "sell_vol": 500}
        acc._last_seq["HK00700"] = 123
        acc._warmed_up.add("HK00700")

        snap = acc.snapshot()
        assert snap["HK00700"]["last_seq"] == 123
        assert snap["HK00700"]["warmed_up"] is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
