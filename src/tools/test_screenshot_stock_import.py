import json

import pytest

import src.sim_trading.db as db
from src.tools.screenshot_stock_import import import_stocks_to_db


def test_import_stocks_to_db_records_config_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))

    added, updated = import_stocks_to_db(
        [
            {
                "code": "HK00700",
                "name": "Tencent",
                "cost": 320,
                "shares": 100,
                "is_holding": True,
            }
        ]
    )

    assert (added, updated) == (1, 0)

    conn = db.get_config_connection()
    try:
        watch_row = conn.execute(
            "SELECT symbol, list_type, cost, shares FROM monitor_watchlist WHERE symbol = ?",
            ("HK00700",),
        ).fetchone()
        audit_row = conn.execute(
            "SELECT payload_json FROM config_audit_outbox ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert dict(watch_row) == {
        "symbol": "HK00700",
        "list_type": "holding",
        "cost": 320.0,
        "shares": 100,
    }
    payload = json.loads(audit_row["payload_json"])
    assert payload["schema_version"] == 2
    assert payload["source"] == "screenshot_stock_import"
    assert payload["action"] == "import"
    assert payload["entity"] == "monitor_watchlist"
    assert payload["key"] == "HK00700"
    assert payload["metadata"]["added"] == 1
    assert payload["after"]["watchlist"][0]["symbol"] == "HK00700"


def test_import_holding_without_shares_rolls_back_without_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))

    with pytest.raises(ValueError, match="missing shares"):
        import_stocks_to_db(
            [
                {
                    "code": "HK00700",
                    "name": "Tencent",
                    "cost": 320,
                    "is_holding": True,
                }
            ]
        )

    conn = db.get_config_connection()
    try:
        count = conn.execute("SELECT COUNT(*) FROM monitor_watchlist").fetchone()[0]
        audit_count = conn.execute("SELECT COUNT(*) FROM config_audit_outbox").fetchone()[0]
    finally:
        conn.close()

    assert count == 0
    assert audit_count == 0
