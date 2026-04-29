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

    assert len(rows) == 1
    payload = json.loads(rows[0]["payload_json"])
    assert payload["after"]["channel"] == "terminal"
    assert payload["after"]["metadata"]["kind"] == "dip_buy"
    assert payload["after"]["metadata"]["symbol"] == "HK00700"
