import time
from unittest.mock import MagicMock

import src.sim_trading.db as db
from src.tools import monitor_lock


def test_monitor_lock_acquires_when_no_active_lease(tmp_path, monkeypatch):
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


def test_refresh_heartbeat_returns_false_when_db_update_fails(monkeypatch):
    lock = monitor_lock.MonitorLock.__new__(monitor_lock.MonitorLock)
    lock.hostname = "local-host"
    lock.pid = 123
    lock.holder_id = "local-host:123"
    lock.is_leader = True

    class BrokenConn:
        def execute(self, *args, **kwargs):
            raise RuntimeError("db busy")

        def close(self):
            pass

    monkeypatch.setattr(monitor_lock, "get_config_connection", lambda: BrokenConn())

    assert lock.refresh_heartbeat() is False


def test_same_host_processes_share_lease_heartbeat(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(monitor_lock, "_hostname", lambda: "local-host")

    first = monitor_lock.MonitorLock()
    first.pid = 111
    first.holder_id = "local-host:111"
    second = monitor_lock.MonitorLock()
    second.pid = 222
    second.holder_id = "local-host:222"

    assert first.try_acquire() is True
    assert second.try_acquire() is True
    assert second.get_lock_holder() == ("local-host", 111)
    assert first.refresh_heartbeat() is True


def test_try_acquire_closes_connection(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(monitor_lock, "_hostname", lambda: "local-host")
    db.init_config_db()
    real_conn = db.get_config_connection()
    wrapped_conn = MagicMock(wraps=real_conn)
    wrapped_conn.close = MagicMock(wraps=real_conn.close)
    monkeypatch.setattr(monitor_lock, "get_config_connection", lambda: wrapped_conn)

    lock = monitor_lock.MonitorLock.__new__(monitor_lock.MonitorLock)
    lock.hostname = "local-host"
    lock.pid = 123
    lock.holder_id = "local-host:123"
    lock.is_leader = False

    assert lock.try_acquire() is True
    wrapped_conn.close.assert_called_once()
