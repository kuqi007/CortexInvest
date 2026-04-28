import time

import src.sim_trading.db as db
from src.tools import monitor_lock


def test_monitor_lock_ignores_recent_market_data_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    market_data = tmp_path / "market_data.json"
    market_data.write_text('{"_updated_by": "remote-host"}')
    monkeypatch.setattr(monitor_lock, "_hostname", lambda: "local-host")

    lock = monitor_lock.MonitorLock()

    assert lock.try_acquire() is True


def test_monitor_lock_rejects_active_remote_poller_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(monitor_lock, "_hostname", lambda: "local-host")
    db.init_config_db()
    now_ms = int(time.time() * 1000)
    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO poller_leader_lease (
            name, holder_id, hostname, pid, generation, lease_until_ms, heartbeat_ts_ms
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            monitor_lock.LOCK_NAME,
            "remote-host:123",
            "remote-host",
            123,
            1,
            now_ms + 60_000,
            now_ms,
        ),
    )
    conn.commit()
    conn.close()

    lock = monitor_lock.MonitorLock()

    assert lock.try_acquire() is False
    assert lock.get_lock_holder() == ("remote-host", 123)
