import json

import src.sim_trading.db as db
from src.tools.stock_data_fetcher import save_fetch_snapshot_to_db


def test_save_fetch_snapshot_to_db_writes_trading_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    output = {
        "fetch_time": "2026-04-29T16:20:00",
        "mode": "quick",
        "stocks": [{"symbol": "688676", "name": "金盘科技"}],
    }

    snapshot_id = save_fetch_snapshot_to_db(output)

    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT fetch_date, mode, payload_json FROM stock_data_fetch_snapshots WHERE id = ?",
            (snapshot_id,),
        ).fetchone()
    finally:
        conn.close()

    assert row["fetch_date"] == "2026-04-29"
    assert row["mode"] == "quick"
    assert json.loads(row["payload_json"]) == output
