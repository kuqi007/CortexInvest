import json
from datetime import datetime

import src.sim_trading.db as db
from src.tools.futu_enricher import FutuL2Enricher


def test_read_capital_from_l2_session_snapshots_db(tmp_path, monkeypatch):
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
            json.dumps({"capital_flow": {"main_net_inflow": 12345}}),
        ),
    )
    conn.commit()
    conn.close()

    assert FutuL2Enricher()._read_capital_from_l2_signals() == {
        "HK00700": {"mainNetInflow": 12345, "retailNetInflow": 0}
    }
