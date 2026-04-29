import json

import src.sim_trading.db as db
import src.tools.stock_notifier as stock_notifier
from src.tools.stock_notifier import TradePlanEngine


def test_trade_plan_engine_save_records_trading_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    engine = object.__new__(TradePlanEngine)
    engine._last_mtime = 0
    engine._pending_plan_events = [
        (
            1777376520000,
            "2026-04-29",
            "plan-audit",
            "buy",
            "entry",
            "entry",
            300.0,
            100,
            "entry @ 300.00",
        )
    ]
    engine._plans = {
        "plan-audit": {
            "name": "Audit Plan",
            "symbol": "HK00700",
            "status": "active",
            "scope": "real",
            "created_at": "2026-04-29",
            "orders": [
                {
                    "id": "entry",
                    "side": "buy",
                    "op": ">=",
                    "price": 300,
                    "shares": 100,
                    "label": "entry",
                    "triggered": True,
                    "triggered_at": "2026-04-29T15:00:00",
                }
            ],
        }
    }

    engine._save_plans()

    conn = db.get_connection()
    try:
        plan_row = conn.execute(
            "SELECT id, symbol, orders_json FROM trade_plans WHERE id = ?",
            ("plan-audit",),
        ).fetchone()
        audit_row = conn.execute(
            "SELECT payload_json FROM trading_audit_outbox WHERE action = 'save' AND entity = 'trade_plans'"
        ).fetchone()
        event_row = conn.execute(
            "SELECT plan_id, event_type, condition_id FROM trade_plan_events WHERE plan_id = ?",
            ("plan-audit",),
        ).fetchone()
    finally:
        conn.close()

    assert plan_row["symbol"] == "HK00700"
    assert json.loads(plan_row["orders_json"])[0]["triggered"] is True
    payload = json.loads(audit_row["payload_json"])
    assert payload["schema_version"] == 2
    assert payload["source"] == "stock_notifier"
    assert payload["action"] == "save"
    assert payload["entity"] == "trade_plans"
    assert payload["key"] == "plan-audit"
    assert payload["after"]["trade_plans"][0]["id"] == "plan-audit"
    assert dict(event_row) == {
        "plan_id": "plan-audit",
        "event_type": "buy",
        "condition_id": "entry",
    }
    assert engine._pending_plan_events == []


def test_trade_plan_engine_reload_handles_legacy_null_updated_at(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    conn = db.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            ("legacy", "Legacy", "HK00700", "active", "real", "2026-04-29", "[]"),
        )
    finally:
        conn.close()

    engine = object.__new__(TradePlanEngine)
    engine._plans = {}
    engine._last_mtime = 0
    engine._reload_plans()

    assert engine._plans["legacy"]["symbol"] == "HK00700"


def test_check_mainline_alerts_handles_config_connection_failure(monkeypatch):
    def fail_config_connection():
        raise RuntimeError("config unavailable")

    monkeypatch.setattr(db, "get_config_connection", fail_config_connection)

    assert stock_notifier.check_mainline_alerts() == []
