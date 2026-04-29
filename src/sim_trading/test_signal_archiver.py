import src.sim_trading.db as db
from src.sim_trading.signal_archiver import SignalArchiver


def test_sample_prices_does_not_write_price_snapshots(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO price_snapshots
            (ts, date, code, name, price, volume, amount, change_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (1700000000000, "2026-04-29", "HK09988", "阿里巴巴", 80.0, 100, 8000, 1.2),
    )
    conn.commit()
    conn.close()

    archiver = SignalArchiver()

    assert archiver.sample_prices() == 0

    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0]
    conn.close()
    assert count == 1
