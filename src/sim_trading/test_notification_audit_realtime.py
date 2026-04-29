import json
from types import SimpleNamespace

import src.sim_trading.db as db
from src.sim_trading.realtime_engine import RealtimeSimEngine
import src.sim_trading.realtime_engine as rt


def test_dip_buy_terminal_notification_records_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    monkeypatch.setattr(
        rt.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0),
    )

    RealtimeSimEngine._notify_dip_buy(
        object(),
        code="HK00700",
        drawdown_pct=-8.5,
        price=320.0,
        score=82,
        high_20d=350.0,
        date="2026-04-29",
        name="腾讯",
    )

    conn = db.get_connection()
    rows = conn.execute("SELECT payload_json FROM trading_audit_outbox").fetchall()
    conn.close()

    # Two audit entries: notification sent + alert_events created
    assert len(rows) == 2, f"Expected 2 audit entries, got {len(rows)}: {[r['payload_json'][:100] for r in rows]}"

    # Parse both entries
    payloads = [json.loads(row["payload_json"]) for row in rows]
    actions = {p["action"] for p in payloads}
    assert actions == {"sent", "create"}, f"Expected actions {{sent, create}}, got {actions}"

    # Find notification audit entry
    notif = next(p for p in payloads if p["action"] == "sent")
    assert notif["after"]["channel"] == "terminal"
    assert "message" not in notif["after"]
    assert notif["after"]["message_hash"].startswith("sha256:")
    assert notif["after"]["metadata"]["kind"] == "dip_buy"
    assert notif["after"]["metadata"]["symbol"] == "HK00700"

    # Find alert_events audit entry
    alert = next(p for p in payloads if p["action"] == "create")
    assert alert["entity"] == "alert_events"
    assert alert["after"]["symbol"] == "HK00700"
    assert alert["after"]["kind"] == "dip_buy"
    assert alert["after"]["level"] == "L1"