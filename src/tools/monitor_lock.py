#!/usr/bin/env python3
"""Poller leader lease backed by config.db."""

import logging
import os
import socket
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.sim_trading.db import get_config_connection, init_config_db

logger = logging.getLogger(__name__)

LOCK_NAME = "market_data_poller"
HEARTBEAT_INTERVAL = 30
LEASE_TTL_MS = 90_000


def _hostname() -> str:
    return socket.gethostname()


def _ensure_table():
    init_config_db()


class MonitorLock:
    def __init__(self):
        _ensure_table()
        self.hostname = _hostname()
        self.pid = os.getpid()
        self.holder_id = f"{self.hostname}:{self.pid}"
        self.is_leader = False

    def try_acquire(self) -> bool:
        now_ms = int(time.time() * 1000)
        lease_until_ms = now_ms + LEASE_TTL_MS
        conn = None
        try:
            conn = get_config_connection()
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    """
                    SELECT hostname, pid, generation, lease_until_ms
                    FROM poller_leader_lease
                    WHERE name = ?
                    """,
                    (LOCK_NAME,),
                ).fetchone()

                if row:
                    if row["hostname"] == self.hostname or row["lease_until_ms"] < now_ms:
                        generation = int(row["generation"]) + (
                            1 if row["hostname"] != self.hostname else 0
                        )
                        conn.execute(
                            """
                            UPDATE poller_leader_lease
                            SET holder_id = ?, hostname = ?, pid = ?,
                                generation = ?, lease_until_ms = ?,
                                heartbeat_ts_ms = ?
                            WHERE name = ?
                            """,
                            (
                                self.holder_id,
                                self.hostname,
                                self.pid,
                                generation,
                                lease_until_ms,
                                now_ms,
                                LOCK_NAME,
                            ),
                        )
                        conn.execute("COMMIT")
                        self.is_leader = True
                        return True
                    else:
                        conn.execute("COMMIT")
                        self.is_leader = False
                        return False

                conn.execute(
                    """
                    INSERT INTO poller_leader_lease (
                        name, holder_id, hostname, pid, generation,
                        lease_until_ms, heartbeat_ts_ms
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        LOCK_NAME,
                        self.holder_id,
                        self.hostname,
                        self.pid,
                        1,
                        lease_until_ms,
                        now_ms,
                    ),
                )
                conn.execute("COMMIT")
                self.is_leader = True
                return True
            except Exception:
                conn.execute("ROLLBACK")
                raise
        except Exception as e:
            logger.warning(f"try_acquire failed: {e}")
            return False
        finally:
            if conn is not None:
                conn.close()

    def refresh_heartbeat(self) -> bool:
        now_ms = int(time.time() * 1000)
        lease_until_ms = now_ms + LEASE_TTL_MS
        conn = None
        try:
            conn = get_config_connection()
            cursor = conn.execute(
                """
                UPDATE poller_leader_lease
                SET heartbeat_ts_ms = ?, lease_until_ms = ?
                WHERE name = ? AND holder_id = ?
                """,
                (now_ms, lease_until_ms, LOCK_NAME, self.holder_id),
            )
            if cursor.rowcount == 1:
                return True
        except Exception as e:
            logger.warning(f"heartbeat refresh failed; leadership is not proven: {e}")
            return False
        finally:
            if conn is not None:
                conn.close()
        return self.try_acquire()

    def get_lock_holder(self) -> tuple[str, int] | None:
        now_ms = int(time.time() * 1000)
        conn = None
        try:
            conn = get_config_connection()
            row = conn.execute(
                """
                SELECT hostname, pid
                FROM poller_leader_lease
                WHERE name = ? AND lease_until_ms >= ?
                """,
                (LOCK_NAME, now_ms),
            ).fetchone()
            if row:
                return (row["hostname"], row["pid"])
            return None
        except Exception as e:
            logger.warning(f"get_lock_holder failed: {e}")
            return None
        finally:
            if conn is not None:
                conn.close()

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
