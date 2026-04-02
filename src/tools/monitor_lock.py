#!/usr/bin/env python3
"""
Leader election for monitor scripts via SQLite.

设计：
- 一把锁 (monitor_lock) 代表一台机器
- 同一台机器上的 Poller/Notifier/L2 共享锁，都可以运行
- 不同机器互斥：只有一台机器能成为 leader
- 同机器的进程心跳刷新同一行（保持锁活跃）
- 退出时不释放锁（让心跳超时自动过期），避免影响同机器其他进程
"""

import atexit
import logging
import os
import socket
import sqlite3
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sim_trading.db import get_connection, init_db

logger = logging.getLogger(__name__)

LOCK_NAME = "monitor_lock"
HEARTBEAT_INTERVAL = 30
HEARTBEAT_TIMEOUT = 180

# Secondary guard: if market_data.json was updated by another machine
# within this window, assume a remote poller is active.
MARKET_DATA_PATH = PROJECT_ROOT / "src" / "data" / "market_data.json"
MARKET_DATA_ACTIVE_WINDOW = 60  # seconds


def _hostname() -> str:
    return socket.gethostname()


def _ensure_table():
    init_db()
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leader_election (
            lock_name TEXT PRIMARY KEY,
            hostname  TEXT NOT NULL,
            pid INTEGER NOT NULL,
            heartbeat INTEGER NOT NULL
        )
    """)


class MonitorLock:
    def __init__(self):
        _ensure_table()
        self.hostname = _hostname()
        self.pid = os.getpid()
        self.is_leader = False

    def _is_market_data_active_remotely(self) -> str | None:
        """Check if market_data.json was recently updated by another machine.

        Returns the hostname of the active machine if detected, else None.
        This is a secondary guard against OneDrive sync delays that can
        make the SQLite leader_election table stale.
        """
        try:
            if not MARKET_DATA_PATH.exists():
                return None
            mtime = MARKET_DATA_PATH.stat().st_mtime
            age = time.time() - mtime
            if age >= MARKET_DATA_ACTIVE_WINDOW:
                return None
            # File was recently updated — check if it was us or another machine
            # by reading the last updater field
            import json
            try:
                data = json.loads(MARKET_DATA_PATH.read_text(encoding="utf-8"))
                updater = data.get("_updated_by", "")
                if updater and updater != self.hostname:
                    return updater
            except (json.JSONDecodeError, KeyError):
                pass
            return None
        except Exception:
            return None

    def try_acquire(self) -> bool:
        # ── Secondary guard: market_data.json mtime check ──
        remote = self._is_market_data_active_remotely()
        if remote:
            print(f"[LOCK] market_data.json 最近被 {remote} 更新，"
                  f"推测远程 poller 仍在运行，退出")
            self.is_leader = False
            return False

        now = int(time.time())
        try:
            conn = get_connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "DELETE FROM leader_election WHERE heartbeat < ?",
                    (now - HEARTBEAT_TIMEOUT,),
                )

                row = conn.execute(
                    "SELECT hostname FROM leader_election WHERE lock_name = ?",
                    (LOCK_NAME,),
                ).fetchone()

                if row:
                    if row["hostname"] == self.hostname:
                        conn.execute(
                            "UPDATE leader_election SET heartbeat=? WHERE lock_name=?",
                            (now, LOCK_NAME),
                        )
                        conn.execute("COMMIT")
                        self.is_leader = True
                        return True
                    else:
                        conn.execute("COMMIT")
                        self.is_leader = False
                        return False

                try:
                    conn.execute(
                        "INSERT INTO leader_election (lock_name, hostname, pid, heartbeat) VALUES (?,?,?,?)",
                        (LOCK_NAME, self.hostname, self.pid, now),
                    )
                    conn.execute("COMMIT")
                    self.is_leader = True
                    return True
                except sqlite3.IntegrityError:
                    conn.execute("ROLLBACK")
                    self.is_leader = False
                    return False
            except Exception:
                conn.execute("ROLLBACK")
                raise
        except Exception as e:
            logger.warning(f"try_acquire failed: {e}")
            return False

    def refresh_heartbeat(self) -> bool:
        now = int(time.time())
        try:
            conn = get_connection()
            cursor = conn.execute(
                "UPDATE leader_election SET heartbeat=? WHERE lock_name=? AND hostname=?",
                (now, LOCK_NAME, self.hostname),
            )
            if cursor.rowcount == 1:
                return True
            return self.try_acquire()
        except Exception as e:
            logger.warning(f"heartbeat refresh failed (DB busy), will retry: {e}")
            return True

    def get_lock_holder(self) -> tuple[str, int] | None:
        now = int(time.time())
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT hostname, pid, heartbeat FROM leader_election WHERE lock_name=? AND heartbeat >= ?",
                (LOCK_NAME, now - HEARTBEAT_TIMEOUT),
            ).fetchone()
            if row:
                return (row["hostname"], row["pid"])
            return None
        except Exception as e:
            logger.warning(f"get_lock_holder failed: {e}")
            return None

    def release(self):
        pass


if __name__ == "__main__":
    lock = MonitorLock()
    if not lock.try_acquire():
        holder = lock.get_lock_holder()
        if holder:
            print(f"Lock held by {holder[0]} (pid={holder[1]}), exiting")
        else:
            print("Lock held by unknown holder, exiting")
        sys.exit(1)
    print(f"Acquired lock as {lock.hostname} (pid={lock.pid})")
    print("Press Ctrl+C to exit.")
    try:
        while True:
            time.sleep(HEARTBEAT_INTERVAL)
            if not lock.refresh_heartbeat():
                print("Lost lock, exiting")
                break
            print(f"Heartbeat OK at {time.strftime('%H:%M:%S')}")
    except KeyboardInterrupt:
        print("Exiting...")
