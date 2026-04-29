import json

import src.sim_trading.db as db
from src.sim_trading.position_manager import Position, PositionManager
from src.sim_trading.realtime_engine import RealtimeSimEngine


def test_save_trade_records_trading_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    engine = RealtimeSimEngine.__new__(RealtimeSimEngine)
    engine._save_trade(
        {
            "trade_id": "t-audit-1",
            "code": "HK00700",
            "action": "SELL",
            "direction": "EXIT",
            "entry_price": 300.0,
            "exit_price": 330.0,
            "quantity": 100,
            "entry_time": 1,
            "exit_time": 2,
            "entry_date": "2026-04-28",
            "exit_date": "2026-04-29",
            "hold_days": 1,
            "pnl": 3000.0,
            "pnl_pct": 0.1,
            "commission": 10.0,
            "confidence": 0.8,
            "trigger_signals": [{"strategy": "test"}],
            "exit_reason": "take_profit",
            "notes": "unit test",
        }
    )

    conn = db.get_connection()
    try:
        row = conn.execute("SELECT payload_json FROM trading_audit_outbox").fetchone()
    finally:
        conn.close()
    payload = json.loads(row["payload_json"])
    assert payload["entity"] == "trades"
    assert payload["key"] == "t-audit-1"
    assert payload["after"]["code"] == "HK00700"


def test_save_trade_does_not_audit_ignored_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    engine = RealtimeSimEngine.__new__(RealtimeSimEngine)
    trade = {
        "trade_id": "t-dup-1",
        "code": "HK00700",
        "action": "SELL",
        "direction": "EXIT",
        "entry_price": 300.0,
        "exit_price": 330.0,
        "quantity": 100,
        "entry_time": 1,
        "exit_time": 2,
        "entry_date": "2026-04-28",
        "exit_date": "2026-04-29",
        "hold_days": 1,
        "pnl": 3000.0,
        "pnl_pct": 0.1,
        "commission": 10.0,
        "confidence": 0.8,
        "trigger_signals": [],
        "exit_reason": "take_profit",
    }

    engine._save_trade(trade)
    engine._save_trade(trade)

    conn = db.get_connection()
    try:
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM trading_audit_outbox").fetchone()[0]
    finally:
        conn.close()

    assert trade_count == 1
    assert audit_count == 1


def test_persist_state_audits_only_structural_live_state_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    engine = RealtimeSimEngine.__new__(RealtimeSimEngine)
    engine._pos_mgr = PositionManager(500_000, {})
    engine._engine = object()
    engine._daily_tracker = None
    engine._score_cache = {}
    engine._get_score = lambda _code: {"total": 75}
    engine._pos_mgr._positions["HK00700"] = Position(
        code="HK00700",
        entry_price=300.0,
        quantity=100,
        entry_time=1,
        entry_date="2026-04-29",
        stop_loss=270.0,
        take_profit=360.0,
        max_hold_days=10,
        confidence=0.8,
        trigger_signals=[{"strategy": "test"}],
        entry_strategy="daily_score",
        buy_cost_per_share=301.0,
        atr_at_entry=8.0,
    )

    engine._persist_state({"HK00700": 320.0})
    engine._persist_state({"HK00700": 321.0})

    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT payload_json FROM trading_audit_outbox ORDER BY ts_ms, event_id").fetchall()
    finally:
        conn.close()
    payloads = [json.loads(row["payload_json"]) for row in rows]
    assert [p["entity"] for p in payloads] == ["live_state"]
    assert payloads[0]["action"] == "create"
    assert payloads[0]["after"]["code"] == "HK00700"
    assert "current_price" not in payloads[0]["after"]
    assert "unrealized_pnl" not in payloads[0]["after"]
