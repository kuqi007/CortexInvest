#!/usr/bin/env python3
"""Tests for tick_monitor.py"""

import json
import socket
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Import after path setup
import src.tools.tick_monitor as tm
import src.sim_trading.db as db


# ══════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════


@pytest.fixture(autouse=True)
def reset_globals():
    """Reset module-level cache before each test."""
    tm._plans_cache = None
    yield
    tm._plans_cache = None


@pytest.fixture
def tmp_trading_db(tmp_path, monkeypatch):
    """Return a temporary trading.db with the canonical schema."""
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    return tmp_path / "trading.db"


def _insert_trade_plan(
    plan_id: str,
    *,
    name: str,
    symbol: str,
    status: str,
    scope: str,
    orders: list[dict] | None = None,
):
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            plan_id,
            name,
            symbol,
            status,
            scope,
            "2026-04-28",
            json.dumps(orders or []),
        ),
    )
    conn.commit()
    conn.close()


# ══════════════════════════════════════════
# check_trigger
# ══════════════════════════════════════════


class TestCheckTrigger:
    """Pure-function tests for order trigger logic."""

    @pytest.mark.parametrize(
        "op,price,tick,expected",
        [
            # buy <=
            ("<=", 85.0, 84.0, True),
            ("<=", 85.0, 85.0, True),
            ("<=", 85.0, 86.0, False),
            # buy >=
            (">=", 95.0, 96.0, True),
            (">=", 95.0, 95.0, True),
            (">=", 95.0, 94.0, False),
            # sell <=
            ("<=", 90.0, 89.0, True),
            # sell >=
            (">=", 100.0, 101.0, True),
            # strict < / >
            ("<", 85.0, 84.0, True),
            ("<", 85.0, 85.0, False),
            (">", 95.0, 96.0, True),
            (">", 95.0, 95.0, False),
            # ==
            ("==", 85.0, 85.0, True),
            ("==", 85.0, 85.0005, True),
            ("==", 85.0, 85.002, False),
            ("==", 85.0, 86.0, False),
            # unknown op
            ("?", 85.0, 84.0, False),
        ],
    )
    def test_trigger_combinations(self, op, price, tick, expected):
        order = {"op": op, "price": price}
        assert tm.check_trigger(order, tick) is expected

    def test_missing_price_returns_false(self):
        assert tm.check_trigger({"op": "<="}, 85.0) is False

    def test_none_price_returns_false(self):
        assert tm.check_trigger({"op": "<=", "price": None}, 85.0) is False


# ══════════════════════════════════════════
# load_tick_monitor_plans
# ══════════════════════════════════════════


class TestLoadTickMonitorPlans:
    def test_empty_when_no_db_plans(self, tmp_trading_db):
        assert tm.load_tick_monitor_plans() == {}

    def test_filters_by_scope_and_status(self, tmp_trading_db):
        _insert_trade_plan(
            "plan_a",
            name="A",
            symbol="HK00001",
            status="active",
            scope="tick_monitor",
            orders=[{"id": "buy"}],
        )
        _insert_trade_plan(
            "plan_b",
            name="B",
            symbol="HK00002",
            status="paused",
            scope="tick_monitor",
        )
        _insert_trade_plan(
            "plan_c",
            name="C",
            symbol="HK00003",
            status="active",
            scope="l2_strategy",
        )

        plans = tm.load_tick_monitor_plans()

        assert set(plans.keys()) == {"plan_a"}
        assert plans["plan_a"]["name"] == "A"
        assert plans["plan_a"]["orders"] == [{"id": "buy"}]

    def test_reads_latest_db_without_file_mtime_cache(self, tmp_trading_db):
        _insert_trade_plan(
            "p1",
            name="Initial",
            symbol="HK00001",
            status="active",
            scope="tick_monitor",
        )
        assert tm.load_tick_monitor_plans()["p1"]["name"] == "Initial"

        conn = db.get_connection()
        conn.execute("UPDATE trade_plans SET name = ? WHERE id = ?", ("Updated", "p1"))
        conn.commit()
        conn.close()

        assert tm.load_tick_monitor_plans()["p1"]["name"] == "Updated"


# ══════════════════════════════════════════
# send_tick_notification
# ══════════════════════════════════════════


class TestSendTickNotification:
    @patch("src.tools.tick_monitor._write_signals")
    def test_buy_notification(self, mock_write_signals):
        mock_write_signals.return_value = True
        order = {
            "side": "buy",
            "op": "<=",
            "price": 85,
            "label": "回踩85买入",
        }
        tick = {"price": 84.5, "direction": "BUY", "volume": 100, "time": "10:30:00"}
        result = tm.send_tick_notification("华勤技术", "HK03296", order, tick)
        assert result is True
        mock_write_signals.assert_called_once()
        call_args = mock_write_signals.call_args[0][0]
        assert len(call_args) == 1
        alert = call_args[0]
        assert alert["symbol"] == "HK03296"
        assert alert["side"] == "buy"
        assert alert["trigger_price"] == 85
        assert alert["tick_price"] == 84.5
        assert alert["_level"] == 1

    @patch("src.tools.tick_monitor._write_signals")
    def test_sell_notification(self, mock_write_signals):
        mock_write_signals.return_value = True
        order = {
            "side": "sell",
            "op": ">=",
            "price": 95,
            "label": "反弹95卖出",
        }
        tick = {"price": 95.5, "direction": "SELL", "volume": 200, "time": "14:00:00"}
        result = tm.send_tick_notification("华勤技术", "HK03296", order, tick)
        assert result is True
        call_args = mock_write_signals.call_args[0][0]
        assert len(call_args) == 1
        alert = call_args[0]
        assert alert["symbol"] == "HK03296"
        assert alert["side"] == "sell"
        assert alert["trigger_price"] == 95
        assert alert["tick_price"] == 95.5

    @patch("src.tools.tick_monitor._write_signals")
    def test_write_signals_failure(self, mock_write_signals):
        mock_write_signals.return_value = False
        order = {"side": "buy", "op": "<=", "price": 85}
        tick = {"price": 84.0}
        result = tm.send_tick_notification("Test", "HK00001", order, tick)
        assert result is False


# ══════════════════════════════════════════
# _get_latest_price (reads from price_snapshots)
# ══════════════════════════════════════════


class TestGetLatestPrice:
    @patch("src.sim_trading.db.get_connection")
    def test_returns_price_and_change_pct(self, mock_get_conn):
        """正常返回价格和涨跌幅"""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (8.0, -1.5)
        mock_get_conn.return_value = mock_conn

        result = tm._get_latest_price("HK03896")

        assert result is not None
        assert result["price"] == 8.0
        assert result["change_pct"] == -1.5
        assert "time" in result
        mock_conn.close.assert_called_once()

    @patch("src.sim_trading.db.get_connection")
    def test_returns_none_when_no_data(self, mock_get_conn):
        """无数据时返回 None"""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_get_conn.return_value = mock_conn

        result = tm._get_latest_price("UNKNOWN")

        assert result is None
        mock_conn.close.assert_called_once()

    @patch("src.sim_trading.db.get_connection")
    def test_returns_none_on_exception(self, mock_get_conn):
        """异常时返回 None"""
        mock_get_conn.side_effect = Exception("DB error")

        result = tm._get_latest_price("HK03896")

        assert result is None


# ══════════════════════════════════════════
# get_pending_tick_signals
# ══════════════════════════════════════════


class TestGetPendingTickSignals:
    @patch("src.sim_trading.db.get_connection")
    def test_empty_db_returns_empty_lists(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_get_conn.return_value = mock_conn

        alerts, web_only = tm.get_pending_tick_signals()
        assert alerts == []
        assert web_only == []

    @patch("src.sim_trading.db.get_connection")
    def test_returns_alerts_split_by_level(self, mock_get_conn):
        mock_conn = MagicMock()
        # Two rows: one L1, one L2
        mock_conn.execute.return_value.fetchall.return_value = [
            (1, 1000, "HK00001", "plan1", "o1", "buy", "<=", "label1",
             85.0, 84.5, "10:00:00", "BUY", 100, "title1", "msg1", 1),
            (2, 2000, "HK00002", "plan2", "o2", "sell", ">=", "label2",
             95.0, 95.5, "10:01:00", "SELL", 200, "title2", "msg2", 2),
        ]
        mock_get_conn.return_value = mock_conn

        alerts, web_only = tm.get_pending_tick_signals()
        assert len(alerts) == 1
        assert len(web_only) == 1
        assert alerts[0]["symbol"] == "HK00001"
        assert alerts[0]["_level"] == 1
        assert web_only[0]["symbol"] == "HK00002"
        assert web_only[0]["_level"] == 2

    @patch("src.sim_trading.db.get_connection")
    def test_marks_as_dispatched(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            (1, 1000, "HK00001", "plan1", "o1", "buy", "<=", "label1",
             85.0, 84.5, "10:00:00", "BUY", 100, "title1", "msg1", 1),
        ]
        mock_get_conn.return_value = mock_conn

        tm.get_pending_tick_signals()
        # Verify UPDATE was called with dispatched = 1
        update_call = mock_conn.execute.call_args
        assert "UPDATE tick_monitor_events SET dispatched = 1" in str(update_call)


# ══════════════════════════════════════════
# FutuConnection
# ══════════════════════════════════════════


class TestFutuConnection:
    def test_init_defaults(self):
        conn = tm.FutuConnection()
        assert conn._host == "127.0.0.1"
        assert conn._port == 11111
        assert conn._ctx is None

    @patch("socket.socket")
    def test_is_port_open_true(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 0
        mock_socket_cls.return_value = mock_sock
        conn = tm.FutuConnection()
        assert conn._is_port_open() is True

    @patch("socket.socket")
    def test_is_port_open_false(self, mock_socket_cls):
        mock_sock = MagicMock()
        mock_sock.connect_ex.return_value = 1
        mock_socket_cls.return_value = mock_sock
        conn = tm.FutuConnection()
        assert conn._is_port_open() is False

    def test_connect_returns_true_when_already_connected(self):
        conn = tm.FutuConnection()
        conn._ctx = MagicMock()
        assert conn.connect() is True

    @patch("src.tools.tick_monitor.FutuConnection._is_port_open")
    def test_connect_skips_when_port_closed(self, mock_port):
        mock_port.return_value = False
        conn = tm.FutuConnection()
        assert conn.connect() is False

    def test_close_sets_ctx_none(self):
        conn = tm.FutuConnection()
        conn._ctx = MagicMock()
        conn.close()
        assert conn._ctx is None


# ══════════════════════════════════════════
# run_tick_monitor main loop
# ══════════════════════════════════════════


class TestRunTickMonitor:
    @patch("src.tools.tick_monitor.time.sleep")
    @patch("src.tools.tick_monitor.is_any_market_open")
    @patch("src.tools.tick_monitor._get_latest_price")
    @patch("src.tools.tick_monitor._write_signals")
    def test_triggers_and_cools_down(
        self, mock_write_signals, mock_get_price, mock_open, mock_sleep
    ):
        """
        Simulate ticks: tick_monitor has a 60s producer-side cooldown per order.
        Only the first trigger writes; subsequent triggers within 60s are skipped.
        stock_notifier adds its own 3-min cooldown on top.
        """
        mock_write_signals.return_value = True
        mock_open.return_value = True

        # tick 1: triggers at price 84 → writes (cooldown starts)
        # tick 2: 5s later → skipped (cooldown active, 5s < 60s)
        # tick 3: 5s later → skipped (cooldown active)
        # sentinel: stop the loop
        mock_get_price.side_effect = [
            {"price": 84.0, "direction": "BUY", "volume": 100, "time": "10:00:00"},
            {"price": 84.0, "direction": "BUY", "volume": 100, "time": "10:00:05"},
            {"price": 84.0, "direction": "BUY", "volume": 100, "time": "10:00:10"},
            {"price": 84.0, "direction": "BUY", "volume": 100, "time": "10:00:15"},
        ]

        plans = {
            "p1": {
                "name": "测试计划",
                "symbol": "HK00001",
                "status": "active",
                "scope": "tick_monitor",
                "orders": [
                    {
                        "id": "o1",
                        "side": "buy",
                        "op": "<=",
                        "price": 85,
                        "label": "测试",
                    }
                ],
            }
        }

        loop_count = [0]

        def counting_sleep(seconds):
            loop_count[0] += 1
            if loop_count[0] >= 4:
                raise StopIteration("stop loop")

        mock_sleep.side_effect = counting_sleep

        # Reset module-level cooldown dict so test is isolated
        tm._order_cooldown.clear()

        with patch.object(tm, "load_tick_monitor_plans", return_value=plans):
            with pytest.raises(StopIteration):
                tm.run_tick_monitor(poll_interval=5)

        # Only the first trigger writes; 60s cooldown blocks the rest
        assert mock_write_signals.call_count == 1

    @patch("src.tools.tick_monitor.time.sleep")
    @patch("src.tools.tick_monitor.is_any_market_open")
    def test_market_closed_sleeps_60s(self, mock_open, mock_sleep):
        mock_open.return_value = False
        loop_count = [0]

        def counting_sleep(seconds):
            assert seconds == 60
            loop_count[0] += 1
            if loop_count[0] >= 2:
                raise StopIteration("stop loop")

        mock_sleep.side_effect = counting_sleep
        with pytest.raises(StopIteration):
            tm.run_tick_monitor()

    @patch("src.tools.tick_monitor.time.sleep")
    @patch("src.tools.tick_monitor.is_any_market_open")
    @patch("src.tools.tick_monitor.FutuConnection")
    def test_no_plans_sleeps_poll_interval(self, mock_conn_cls, mock_open, mock_sleep):
        mock_open.return_value = True
        loop_count = [0]

        def counting_sleep(seconds):
            loop_count[0] += 1
            if loop_count[0] >= 2:
                raise StopIteration("stop loop")

        mock_sleep.side_effect = counting_sleep
        with patch.object(tm, "load_tick_monitor_plans", return_value={}):
            with pytest.raises(StopIteration):
                tm.run_tick_monitor(poll_interval=5)

    @patch("src.tools.tick_monitor.time.sleep")
    @patch("src.tools.tick_monitor.is_any_market_open")
    @patch("src.tools.tick_monitor.FutuConnection")
    def test_all_ticks_fail_backoff(self, mock_conn_cls, mock_open, mock_sleep):
        """When all get_latest_tick calls fail repeatedly, sleep should increase."""
        mock_open.return_value = True
        mock_conn = MagicMock()
        mock_conn.get_latest_tick.return_value = None
        mock_conn_cls.return_value = mock_conn

        plans = {
            "p1": {
                "name": "测试",
                "symbol": "HK00001",
                "status": "active",
                "scope": "tick_monitor",
                "orders": [
                    {"id": "o1", "side": "buy", "op": "<=", "price": 85}
                ],
            }
        }

        sleeps = []

        def record_sleep(seconds):
            sleeps.append(seconds)
            if len(sleeps) >= 5:
                raise StopIteration("stop loop")

        mock_sleep.side_effect = record_sleep
        with patch.object(tm, "load_tick_monitor_plans", return_value=plans):
            with pytest.raises(StopIteration):
                tm.run_tick_monitor(poll_interval=5)

        # After repeated failures, backoff kicks in
        assert any(s > 5 for s in sleeps)


# ══════════════════════════════════════════
# CLI
# ══════════════════════════════════════════


class TestCLI:
    def test_daemon_lock_prevents_duplicate(self):
        """Simulate another process already holding the lock."""
        mock_fcntl = MagicMock()
        mock_fcntl.flock.side_effect = BlockingIOError()
        mock_fcntl.LOCK_EX = 2
        mock_fcntl.LOCK_NB = 4
        with patch.dict("sys.modules", {"fcntl": mock_fcntl}):
            with patch.object(sys, "exit") as mock_exit:
                with patch("builtins.print"):
                    with patch("os.open", return_value=3):
                        with patch("os.close"):
                            with patch("os.getuid", return_value=1000):
                                with patch(
                                    "argparse.ArgumentParser.parse_args",
                                    return_value=MagicMock(interval=5, dry_run=False),
                                ):
                                    mock_exit.side_effect = SystemExit(1)
                                    with pytest.raises(SystemExit) as exc:
                                        tm.main()
                                    assert exc.value.code == 1
