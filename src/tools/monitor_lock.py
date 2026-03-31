#!/usr/bin/env python3
"""
Leader election for monitor scripts via SQLite.

抢锁流程：
1. 启动时删除所有超时锁
2. INSERT OR IGNORE 原子抢锁
3. 抢到 → 成为 leader，每 30s 更新 heartbeat
4. 未抢到 → 退出或只读
"""

import atexit
import logging
import os
import socket
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


def _machine_id() -> str:
    return f"{socket.gethostname()}-{os.getpid()}"


def _ensure_table():
    init_db()
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leader_election (
            lock_name TEXT PRIMARY KEY,
            machine_id TEXT NOT NULL,
            pid INTEGER NOT NULL,
            heartbeat INTEGER NOT NULL
        )
    """)


class MonitorLock:
    def __init__(self):
        _ensure_table()
        self.machine_id = _machine_id()
        self.pid = os.getpid()
        self.is_leader = False

    def try_acquire(self) -> bool:
        now = int(time.time())
        try:
            conn = get_connection()
            with conn:
                conn.execute(
                    "DELETE FROM leader_election WHERE heartbeat < ?",
                    (now - HEARTBEAT_TIMEOUT,),
                )
                cursor = conn.execute(
                    """INSERT OR IGNORE INTO leader_election 
                       (lock_name, machine_id, pid, heartbeat) 
                       VALUES (?, ?, ?, ?)""",
                    (LOCK_NAME, self.machine_id, self.pid, now),
                )
                acquired = cursor.rowcount == 1
            self.is_leader = acquired
            return acquired
        except Exception as e:
            logger.warning(f"try_acquire failed: {e}")
            return False

    def refresh_heartbeat(self) -> bool:
        now = int(time.time())
        try:
            conn = get_connection()
            cursor = conn.execute(
                """UPDATE leader_election 
                   SET heartbeat = ? 
                   WHERE lock_name = ? AND machine_id = ? AND pid = ?""",
                (now, LOCK_NAME, self.machine_id, self.pid),
            )
            return cursor.rowcount == 1
        except Exception as e:
            logger.warning(f"heartbeat refresh failed (DB busy), will retry: {e}")
            return True

    def release(self):
        try:
            conn = get_connection()
            conn.execute(
                "DELETE FROM leader_election WHERE machine_id = ? AND pid = ?",
                (self.machine_id, self.pid),
            )
        except Exception as e:
            logger.warning(f"lock release failed: {e}")

    def get_lock_holder(self) -> tuple[str, int] | None:
        now = int(time.time())
        try:
            conn = get_connection()
            row = conn.execute(
                """SELECT machine_id, pid, heartbeat FROM leader_election 
                   WHERE lock_name = ? AND heartbeat >= ?""",
                (LOCK_NAME, now - HEARTBEAT_TIMEOUT),
            ).fetchone()
            if row:
                return (row["machine_id"], row["pid"])
            return None
        except Exception as e:
            logger.warning(f"get_lock_holder failed: {e}")
            return None


def run_with_lock():
    lock = MonitorLock()
    if not lock.try_acquire():
        holder = lock.get_lock_holder()
        if holder:
            print(f"Monitor locked by {holder[0]} (pid={holder[1]}), exiting")
        else:
            print("Monitor locked by unknown holder, exiting")
        return False
    atexit.register(lock.release)
    print(f"Acquired lock as {lock.machine_id}")
    return lock


if __name__ == "__main__":
    result = run_with_lock()
    if result:
        lock = result
        print("Lock acquired. Press Ctrl+C to exit.")
        try:
            while True:
                time.sleep(HEARTBEAT_INTERVAL)
                if not lock.refresh_heartbeat():
                    print("Lost lock, exiting")
                    break
                print(f"Heartbeat OK at {time.strftime('%H:%M:%S')}")
        except KeyboardInterrupt:
            print("Exiting...")
        finally:
            lock.release()
            print("Lock released")
