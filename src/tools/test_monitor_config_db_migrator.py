import json

import src.sim_trading.db as db
from src.tools.monitor_config_db_migrator import import_json_to_db, read_config_from_db


def test_import_json_to_db_records_config_audit_and_preserves_new_fields(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_db()

    watch_count, settings_count = import_json_to_db(
        {
            "watchlist": {
                "HK00700": {
                    "name": "Tencent",
                    "alias": "TCEHY",
                    "list_type": "holding",
                    "cost": 320.0,
                    "shares": 100,
                    "lot": 100,
                    "hidden": False,
                    "star": True,
                    "tags": ["AI"],
                    "watch_price": 300.0,
                    "watch_price_date": "2026-04-29",
                    "pin_order": 5,
                }
            },
            "settings": {"poll_interval": 30.0},
        }
    )

    assert (watch_count, settings_count) == (1, 1)
    cfg = read_config_from_db()
    assert cfg["watchlist"]["HK00700"]["alias"] == "TCEHY"
    assert cfg["watchlist"]["HK00700"]["pin_order"] == 5

    conn = db.get_config_connection()
    try:
        audit_row = conn.execute(
            "SELECT payload_json FROM config_audit_outbox ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    payload = json.loads(audit_row["payload_json"])
    assert payload["schema_version"] == 2
    assert payload["source"] == "monitor_config_db_migrator"
    assert payload["action"] == "import"
    assert payload["entity"] == "monitor_config"
    assert payload["after"]["watchlist"]["HK00700"]["alias"] == "TCEHY"
    assert payload["after"]["watchlist"]["HK00700"]["pin_order"] == 5
