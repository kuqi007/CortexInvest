import json

import src.sim_trading.db as db
from src.sim_trading.futu_position_sync import FutuPositionSync
from src.sim_trading.futu_trade_adapter import FutuFunds, FutuPosition, OrderUpdate


class FakeAdapter:
    def get_funds(self):
        return FutuFunds(total_assets=1_010_000, cash=900_000, market_val=110_000)

    def get_positions(self):
        return {"HK00700": FutuPosition(code="HK00700", quantity=100, avg_price=300.0, market_val=32_000)}


def test_futu_position_sync_records_daily_pnl_and_order_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_db()

    sync = FutuPositionSync(FakeAdapter())
    sync.save_daily_pnl("2026-04-29")
    sync.save_order("order-1", "HK00700", "BUY", 320.0, 100, reason="entry")
    sync.update_order_status(
        [OrderUpdate(order_id="order-1", code="HK00700", status="FILLED", filled_qty=100, avg_fill_price=320.0)]
    )

    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT payload_json FROM trading_audit_outbox ORDER BY rowid").fetchall()
    finally:
        conn.close()

    payloads = [json.loads(row["payload_json"]) for row in rows]
    assert ("daily_pnl", "live:2026-04-29") in {
        (payload["entity"], payload["key"]) for payload in payloads
    }
    assert ("futu_orders", "order-1") in {
        (payload["entity"], payload["key"]) for payload in payloads
    }
    assert any(payload["entity"] == "futu_orders" and payload["action"] == "update" for payload in payloads)


def test_futu_save_trade_does_not_audit_ignored_duplicate(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_db()

    sync = FutuPositionSync(FakeAdapter())
    trade = {
        "trade_id": "futu-trade-1",
        "code": "HK00700",
        "action": "SELL",
        "direction": "close_long",
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
        "commission": 0,
        "total_cost": 0,
        "confidence": 0,
        "trigger_signals": [],
        "exit_reason": "manual",
        "notes": "futu_sim",
    }

    sync._save_trade(trade)
    sync._save_trade(trade)

    conn = db.get_connection()
    try:
        trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM trading_audit_outbox").fetchone()[0]
    finally:
        conn.close()

    assert trade_count == 1
    assert audit_count == 1
