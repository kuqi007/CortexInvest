import json
from unittest.mock import patch, MagicMock

import src.sim_trading.db as db
import src.tools.l2_strategy_daemon as daemon
import src.tools.l2_strategy_engine as l2_engine
from src.tools.l2_strategy_engine import L2StrategyEngine


def test_load_configs_reads_l2_config_from_db_without_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(
        daemon,
        "read_monitor_config",
        lambda: {"watchlist": {"HK00700": {"name": "腾讯", "type": "holding"}}},
    )
    db.init_config_db()
    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO l2_strategy_config (strategy, enabled, config_json, updated_at_ms)
        VALUES (?, ?, ?, ?)
        """,
        ("tick_imbalance", 1, json.dumps({"threshold": 0.2}), 1777376520000),
    )
    conn.commit()
    conn.close()

    monitor, l2_config = daemon.load_configs()

    assert "HK00700" in monitor["watchlist"]
    assert l2_config["enabled"] is True
    assert l2_config["strategies"] == {
        "tick_imbalance": {"enabled": True, "threshold": 0.2}
    }


def test_load_realtime_rules_reads_signal_rules_from_db_without_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO signal_rules (rule_id, rule_json, enabled, updated_at_ms)
        VALUES (?, ?, ?, ?)
        """,
        ("futu_trade", json.dumps({"enabled": True, "host": "127.0.0.1"}), 1, 1777376520000),
    )
    conn.commit()
    conn.close()

    rules = daemon.load_realtime_rules()

    assert rules == {"futu_trade": {"enabled": True, "host": "127.0.0.1"}}


def test_write_signals_flushes_to_db_not_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    signals_file = tmp_path / "l2_strategy_signals.json"
    db.init_trading_db()
    daemon.write_signals._buffer = []
    daemon.write_signals._session = {}
    daemon.write_signals._indicators = {}
    daemon.write_signals._last_flush = 0

    signals = [
        {
            "ts": 1777376520000 + idx,
            "time": "10:00:00",
            "strategy": "tick_imbalance",
            "code": f"HK0000{idx}",
            "direction": "bullish",
            "notify": idx == 0,
            "detail": {"score": idx},
            "display": f"signal {idx}",
        }
        for idx in range(5)
    ]

    daemon.write_signals(
        signals,
        session_snapshot={"HK00700": {"capital_flow": {"main_net_inflow": 1000}}},
        indicators={"HK00700": {"rsi": 60}},
    )

    assert not signals_file.exists()
    conn = db.get_connection()
    signal_count = conn.execute("SELECT COUNT(*) AS c FROM signals").fetchone()["c"]
    session_count = conn.execute("SELECT COUNT(*) AS c FROM session_snapshots").fetchone()["c"]
    first = conn.execute(
        "SELECT strategy, code, direction, notify, detail, display FROM signals ORDER BY ts LIMIT 1"
    ).fetchone()
    session = conn.execute(
        "SELECT code, session_json FROM session_snapshots WHERE code = ?",
        ("HK00700",),
    ).fetchone()
    conn.close()

    assert signal_count == 5
    assert session_count == 1
    assert first["strategy"] == "tick_imbalance"
    assert first["notify"] == 1
    assert json.loads(first["detail"]) == {"score": 0}
    assert first["display"] == "signal 0"
    assert json.loads(session["session_json"]) == {
        "capital_flow": {"main_net_inflow": 1000},
        "indicators": {"rsi": 60},
    }


def test_write_signals_restores_buffer_when_db_flush_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_trading_db()
    daemon.write_signals._buffer = []
    daemon.write_signals._session = {}
    daemon.write_signals._indicators = {}
    daemon.write_signals._last_flush = 0

    class BrokenConn:
        def execute(self, *args, **kwargs):
            raise RuntimeError("db unavailable")

        def close(self):
            pass

    monkeypatch.setattr(daemon, "get_connection", lambda: BrokenConn())
    signals = [
        {"ts": 1777376520000 + idx, "strategy": "tick_imbalance", "code": f"HK{idx:05d}"}
        for idx in range(5)
    ]

    daemon.write_signals(signals)

    assert daemon.write_signals._buffer == signals


def test_write_signals_infers_direction_when_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_trading_db()
    daemon.write_signals._buffer = []
    daemon.write_signals._session = {}
    daemon.write_signals._indicators = {}
    daemon.write_signals._last_flush = 0

    daemon.write_signals(
        [
            {
                "ts": 1777376520000 + idx,
                "time": "10:00:00",
                "strategy": "tick_imbalance",
                "code": f"HK0000{idx}",
                "detail": {"imbalance": 0.2},
            }
            for idx in range(5)
        ]
    )

    conn = db.get_connection()
    row = conn.execute("SELECT direction FROM signals ORDER BY ts LIMIT 1").fetchone()
    conn.close()
    assert row["direction"] == "bullish"


def test_write_signals_infers_large_order_and_divergence_direction(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_trading_db()
    daemon.write_signals._buffer = []
    daemon.write_signals._session = {}
    daemon.write_signals._indicators = {}
    daemon.write_signals._last_flush = 0
    signals = [
        {
            "ts": 1777376520000,
            "time": "10:00:00",
            "strategy": "large_order",
            "code": "HK00700",
            "detail": {"direction": "SELL"},
        },
        {
            "ts": 1777376520001,
            "time": "10:00:01",
            "strategy": "volume_price_divergence",
            "code": "HK09988",
            "detail": {"main_net_inflow": 100},
        },
        *[
            {
                "ts": 1777376520002 + idx,
                "time": "10:00:02",
                "strategy": "tick_imbalance",
                "code": f"HK{idx:05d}",
            }
            for idx in range(3)
        ],
    ]

    daemon.write_signals(signals)

    conn = db.get_connection()
    rows = conn.execute(
        "SELECT strategy, direction FROM signals ORDER BY ts LIMIT 2"
    ).fetchall()
    conn.close()
    assert [(r["strategy"], r["direction"]) for r in rows] == [
        ("large_order", "bearish"),
        ("volume_price_divergence", "bearish"),
    ]


def test_write_signals_flushes_more_than_max_without_tail_drop(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_trading_db()
    daemon.write_signals._buffer = []
    daemon.write_signals._session = {}
    daemon.write_signals._indicators = {}
    daemon.write_signals._last_flush = 0
    signals = [
        {"ts": 1777376520000 + idx, "strategy": "tick_imbalance", "code": f"HK{idx:05d}"}
        for idx in range(daemon.MAX_SIGNALS + 5)
    ]

    daemon.write_signals(signals)

    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) AS c FROM signals").fetchone()["c"]
    conn.close()
    assert count == daemon.MAX_SIGNALS + 5


# ─── Bug Regression: degraded mode restores from DB ─────────────────────────

def test_poll_degraded_mode_restore_from_db(tmp_path, monkeypatch):
    """
    Regression test for the degraded-mode bug:
    When Futu OpenD is unavailable, poll_once must call _poll_degraded()
    which restores last known session_snapshots from DB and marks them stale.

    Before the fix: returning [] on connect failure would skip degraded restore.
    After fix: _poll_degraded() restores from DB, marks data stale=True,
    and logs a WARNING (not debug).
    """
    import datetime

    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_trading_db()

    # Use today's date so the DB query finds the record
    today_str = datetime.datetime.now().strftime("%Y-%m-%d")

    # Pre-populate session_snapshots in DB
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO session_snapshots (ts, date, time, code, session_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            1700000000000,
            today_str,
            "10:00:00",
            "HK00700",
            json.dumps({
                "direction": "bullish",
                "score": 75,
                "tick": {"buy_vol": 1000, "sell_vol": 800, "imbalance": 0.2},
                "large_order": {"buy_count": 5, "sell_count": 3, "buy_amount": 50000.0},
                "capital_flow": {"main_net_inflow": 1000000.0},
                "last_seq": 12345,
                "warmed_up": True,
            }),
        ),
    )
    conn.commit()
    conn.close()

    # L2StrategyEngine requires (strategy_config, watchlist)
    strategy_config = {
        "enabled": True,
        "strategies": {
            "tick_imbalance": {"enabled": True},
            "large_order": {"enabled": True},
            "volume_price_divergence": {"enabled": True},
        },
    }
    watchlist = {
        "HK00700": {"name": "腾讯", "type": "holding", "hidden": False, "star": False},
    }

    engine = L2StrategyEngine(strategy_config, watchlist)

    # Mock connect() to return False (Futu unavailable)
    connect_called = []

    def fake_connect():
        connect_called.append(True)
        return False

    engine.connect = fake_connect

    # Patch the module-level logger (used by _poll_degraded)
    logged_warnings = []
    logged_infos = []

    class MockLogger:
        def warning(self, msg, *args):
            logged_warnings.append(msg % args if args else msg)

        def info(self, msg, *args):
            logged_infos.append(msg % args if args else msg)

        def debug(self, msg, *args):
            pass

    with patch.object(l2_engine, "logger", MockLogger()):
        signals, session_snapshot = engine.poll_once()

    # _poll_degraded must be called (not just returning empty)
    assert len(connect_called) == 1, "connect() should be called once"

    # No signals should be produced in degraded mode
    assert signals == [], "Degraded mode should return empty signals"

    # Session snapshot should be restored from DB
    assert "HK00700" in session_snapshot, \
        "HK00700 should be restored from session_snapshots in degraded mode"

    restored = session_snapshot["HK00700"]

    # Must be marked stale
    assert restored.get("stale") is True, \
        "Restored session must be marked stale=True for downstream consumers"

    # Data integrity from DB
    assert restored.get("direction") == "bullish"
    assert restored.get("score") == 75
    assert restored["tick"]["imbalance"] == 0.2

    # Warning must be logged (not debug) about degraded mode entry
    assert any("degraded" in w.lower() or "unavailable" in w.lower()
               for w in logged_warnings), \
        f"Warning about degraded mode must be logged. Got warnings: {logged_warnings}"
