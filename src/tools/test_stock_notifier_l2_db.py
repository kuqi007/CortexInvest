import json
from datetime import datetime

import src.sim_trading.db as db
import src.tools.stock_notifier as notifier


def test_check_l2_signals_reads_unconsumed_signals_from_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    notifier.check_l2_signals._last_consumed = 0
    notifier.check_l2_signals._daily_counts = {}
    notifier.check_l2_signals._seen_today = set()
    ts = int(datetime.now().timestamp() * 1000)
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO signals
            (ts, date, time, strategy, code, direction, notify, detail, display)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            datetime.now().strftime("%Y-%m-%d"),
            "10:00:00",
            "tick_imbalance",
            "HK00700",
            "bullish",
            1,
            json.dumps({"score": 3}),
            "腾讯 L2 买盘增强",
        ),
    )
    conn.commit()
    conn.close()

    alerts = notifier.check_l2_signals()

    assert alerts == [
        {
            "symbol": "HK00700",
            "title": "L2 tick_imbalance",
            "message": "腾讯 L2 买盘增强",
            "_kind": "l2_strategy",
            "_change_pct": 0,
            "_name": "HK00700",
            "_stealth": "腾讯 L2 买盘增强",
            "_notify": True,
        }
    ]
    assert notifier.check_l2_signals._last_consumed == ts


def test_read_l2_indicators_reads_latest_session_snapshot_from_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    ts = int(datetime.now().timestamp() * 1000)
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO session_snapshots (ts, date, time, code, session_json)
        VALUES (?, ?, ?, ?, ?)
        """,
        (
            ts,
            datetime.now().strftime("%Y-%m-%d"),
            "10:00:00",
            "HK00700",
            json.dumps({"indicators": {"rsi": 61, "vol_ratio": 1.8}}),
        ),
    )
    conn.commit()
    conn.close()

    assert notifier._read_l2_indicators() == {
        "HK00700": {"rsi": 61, "vol_ratio": 1.8}
    }


def test_check_l2_signals_startup_watermark_skips_historical_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    old_ts = 1777376520000
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO signals
            (ts, date, time, strategy, code, direction, notify, detail, display)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            old_ts,
            "2026-04-28",
            "10:00:00",
            "tick_imbalance",
            "HK00700",
            "bullish",
            1,
            "{}",
            "old signal",
        ),
    )
    conn.commit()
    conn.close()

    notifier.check_l2_signals._last_consumed = notifier._load_l2_signal_watermark_from_db()
    notifier.check_l2_signals._daily_counts = {}
    notifier.check_l2_signals._seen_today = set()

    assert notifier.check_l2_signals() == []
    assert notifier.check_l2_signals._last_consumed == old_ts


def test_check_l2_signals_resumes_from_persisted_watermark(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    old_ts = 1777376520000
    new_ts = old_ts + 1000
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO tick_monitor_state (key, value_json, updated_at_ms) VALUES (?, ?, ?)",
        ("stock_notifier_l2_last_consumed", str(old_ts), old_ts),
    )
    for ts, display in ((old_ts, "old"), (new_ts, "new")):
        conn.execute(
            """
            INSERT INTO signals
                (ts, date, time, strategy, code, direction, notify, detail, display)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts,
                datetime.now().strftime("%Y-%m-%d"),
                "10:00:00",
                "tick_imbalance",
                "HK00700",
                "bullish",
                1,
                "{}",
                display,
            ),
        )
    conn.commit()
    conn.close()

    notifier.check_l2_signals._last_consumed = notifier._load_l2_signal_watermark_from_db()
    notifier.check_l2_signals._daily_counts = {}
    notifier.check_l2_signals._seen_today = set()

    alerts = notifier.check_l2_signals()

    assert [a["message"] for a in alerts] == ["new"]
    conn = db.get_connection()
    row = conn.execute(
        "SELECT value_json FROM tick_monitor_state WHERE key = ?",
        ("stock_notifier_l2_last_consumed",),
    ).fetchone()
    conn.close()
    assert row["value_json"] == str(new_ts)


def test_panic_sell_engine_reads_thresholds_from_db_not_alert_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    for key, value in {
        "panic_market_amo1_min": 9.0,
        "panic_market_drop_pct": 3.0,
        "panic_stock_amo1_min": 8.0,
        "panic_stock_drop_pct": 4.0,
        "panic_cooldown_hours": 6.0,
    }.items():
        conn.execute(
            "INSERT INTO monitor_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, 1777376520),
        )
    conn.commit()
    conn.close()

    engine = notifier.PanicSellEngine(tmp_path / "missing_alert_config.json")

    assert engine._market_amo1_min == 9.0
    assert engine._market_drop_pct == 3.0
    assert engine._stock_amo1_min == 8.0
    assert engine._stock_drop_pct == 4.0
    assert engine._cooldown_hours == 6.0


def test_alert_clusterer_reads_settings_from_db_not_alert_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    conn.execute(
        "INSERT INTO monitor_settings (key, value, updated_at) VALUES (?, ?, ?)",
        ("cluster_enabled", 0, 1777376520),
    )
    conn.execute(
        "INSERT INTO monitor_settings (key, value, updated_at) VALUES (?, ?, ?)",
        ("cluster_min_count", 7, 1777376520),
    )
    conn.commit()
    conn.close()

    clusterer = notifier.AlertClusterer(tmp_path / "missing_alert_config.json")

    assert clusterer.enabled is False
    assert clusterer._min_count == 7
