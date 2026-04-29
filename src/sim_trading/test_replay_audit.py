import json

import src.sim_trading.db as db
from src.sim_trading.replay_runner import _save_results


def test_save_results_records_param_trade_and_daily_pnl_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    conn = db.get_connection()
    try:
        _save_results(
            conn,
            trades=[
                {
                    "trade_id": "replay-trade-1",
                    "code": "HK00700",
                    "action": "BUY",
                    "direction": "LONG",
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
            ],
            daily_pnl=[
                {
                    "date": "2026-04-29",
                    "total_equity": 1_010_000,
                    "cash": 900_000,
                    "invested": 110_000,
                    "daily_return": 0.01,
                    "cumulative_return": 0.01,
                    "drawdown_pct": 0,
                    "positions": {"HK00700": {"qty": 100}},
                }
            ],
            version="audit-test",
            rules={"initial_capital": 1_000_000},
        )
    finally:
        conn.close()

    conn = db.get_connection()
    try:
        rows = conn.execute("SELECT payload_json FROM trading_audit_outbox ORDER BY entity, key").fetchall()
    finally:
        conn.close()
    entities = {(json.loads(row["payload_json"])["entity"], json.loads(row["payload_json"])["key"]) for row in rows}
    assert ("param_versions", "audit-test") in entities
    assert ("trades", "replay-trade-1") in entities
    assert ("daily_pnl", "audit-test:2026-04-29") in entities
