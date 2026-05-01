#!/usr/bin/env python3
"""
Earnings Calendar Daemon — 财报日历守护进程

定期检查即将发布的财报，发送飞书预警，并自动复盘已发布财报。

Usage:
    uv run python src/tools/earnings_calendar_daemon.py

Daemon 行为:
    - 每天 08:00 ~ 22:00 每 30 分钟检查一次
    - 非交易时段降低检查频率（每 2 小时）
    - 使用 fcntl.flock 单例锁确保只有一个实例
"""

import fcntl
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.earnings_calendar import EarningsCalendar
from src.tools.trading_calendar import is_trading_day
from src.utils.logging_config import setup_logger
from src.sim_trading.db import get_connection, init_db

logger = setup_logger("earnings_calendar_daemon")

LOCK_FILE = PROJECT_ROOT / "data" / ".earnings_calendar_daemon.lock"
CHECK_INTERVAL_SEC = 1800   # 30 分钟（交易时段）
LONG_CHECK_INTERVAL_SEC = 7200  # 2 小时（非交易时段）
DAEMON_MODE = True
STALE_JOB_MS = 15 * 60 * 1000


def is_trading_hours() -> bool:
    """检查是否在交易时段（A股 09:00-15:00）"""
    now = datetime.now()
    t = now.hour * 100 + now.minute
    if is_trading_day("CN"):
        return 900 <= t <= 1500
    return False


def _now_ms() -> int:
    return int(time.time() * 1000)


def _rollback_quietly(conn) -> None:
    try:
        conn.execute("ROLLBACK")
    except Exception:
        pass


def claim_pending_job_request(job_type: str) -> dict | None:
    """Atomically claim the oldest pending job request for this daemon."""
    conn = get_connection()
    now_ms = _now_ms()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT id, job_type, requested_by, request_payload_json, correlation_id
            FROM job_requests
            WHERE job_type = ? AND status = 'pending'
            ORDER BY created_at_ms, id
            LIMIT 1
            """,
            (job_type,),
        ).fetchone()
        if row is None:
            conn.execute("COMMIT")
            return None
        conn.execute(
            """
            UPDATE job_requests
            SET status = 'claimed', claimed_at_ms = ?
            WHERE id = ? AND status = 'pending'
            """,
            (now_ms, row["id"]),
        )
        conn.execute("COMMIT")
        return dict(row)
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def recover_stale_jobs(
    job_type: str,
    *,
    stale_after_ms: int = STALE_JOB_MS,
    now_ms: int | None = None,
) -> None:
    """Requeue stale claimed requests and fail stale running jobs."""
    cutoff_ms = (now_ms if now_ms is not None else _now_ms()) - stale_after_ms
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE job_requests
            SET status = 'pending', claimed_at_ms = NULL
            WHERE job_type = ? AND status = 'claimed'
              AND claimed_at_ms IS NOT NULL AND claimed_at_ms < ?
              AND id NOT IN (
                SELECT request_id FROM job_runs
                WHERE job_type = ? AND status = 'running' AND request_id IS NOT NULL
              )
            """,
            (job_type, cutoff_ms, job_type),
        )
        conn.execute(
            """
            UPDATE job_runs
            SET status = 'failed',
                finished_at_ms = ?,
                error = COALESCE(error, 'stale running job recovered')
            WHERE job_type = ? AND status = 'running'
              AND COALESCE(heartbeat_at_ms, started_at_ms) < ?
            """,
            (now_ms if now_ms is not None else _now_ms(), job_type, cutoff_ms),
        )
        conn.execute(
            """
            UPDATE job_requests
            SET status = 'failed',
                completed_at_ms = ?
            WHERE id IN (
                SELECT request_id FROM job_runs
                WHERE job_type = ? AND status = 'failed' AND request_id IS NOT NULL
                  AND error = 'stale running job recovered'
            )
              AND status = 'claimed'
            """,
            (now_ms if now_ms is not None else _now_ms(), job_type),
        )
        conn.execute("COMMIT")
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def _insert_job_run(
    *,
    run_id: str,
    request_id: str | None,
    runner: str,
    started_at_ms: int,
    input_json: str,
    correlation_id: str,
) -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO job_runs (
              id, request_id, job_type, runner, status, started_at_ms,
              heartbeat_at_ms, input_json, correlation_id
            )
            VALUES (?, ?, 'earnings_check', ?, 'running', ?, ?, ?, ?)
            """,
            (
                run_id,
                request_id,
                runner,
                started_at_ms,
                started_at_ms,
                input_json,
                correlation_id,
            ),
        )
    finally:
        conn.close()


def _finalize_job_run(
    *,
    run_id: str,
    request_id: str | None,
    status: str,
    output_json: str | None = None,
    error: str | None = None,
) -> None:
    finished_at_ms = _now_ms()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE job_runs
            SET status = ?, finished_at_ms = ?, heartbeat_at_ms = ?,
                output_json = COALESCE(?, output_json),
                error = ?
            WHERE id = ?
            """,
            (status, finished_at_ms, finished_at_ms, output_json, error, run_id),
        )
        if request_id:
            request_status = "completed" if status == "success" else "failed"
            conn.execute(
                """
                UPDATE job_requests
                SET status = ?, completed_at_ms = ?
                WHERE id = ?
                """,
                (request_status, finished_at_ms, request_id),
            )
        conn.execute("COMMIT")
    except Exception:
        _rollback_quietly(conn)
        raise
    finally:
        conn.close()


def run_earnings_check(
    calendar: EarningsCalendar,
    *,
    request: dict | None = None,
    runner: str = "earnings_calendar_daemon",
) -> bool:
    """Run one earnings check and persist its job_run outcome."""
    run_id = str(uuid4())
    started_at_ms = _now_ms()
    correlation_id = (
        request.get("correlation_id")
        if request and request.get("correlation_id")
        else str(uuid4())
    )
    request_id = request.get("id") if request else None
    input_json = json.dumps(
        {
            "request_id": request_id,
            "request_payload": request.get("request_payload_json") if request else None,
        },
        ensure_ascii=False,
    )

    try:
        _insert_job_run(
            run_id=run_id,
            request_id=request_id,
            runner=runner,
            started_at_ms=started_at_ms,
            input_json=input_json,
            correlation_id=correlation_id,
        )

        calendar.check_and_alert()

        _finalize_job_run(
            run_id=run_id,
            request_id=request_id,
            status="success",
            output_json=json.dumps({"ok": True}),
        )
        return True
    except Exception as e:
        try:
            _finalize_job_run(
                run_id=run_id,
                request_id=request_id,
                status="failed",
                error=str(e),
            )
        except Exception as record_error:
            logger.error("failed to persist earnings_check failure: %s", record_error)
        logger.error("earnings_check job failed: %s", e)
        return False


def main():
    # 单例锁
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.warning("财报日历 Daemon 已在运行，退出")
        sys.exit(0)

    logger.info("财报日历 Daemon 启动")
    init_db()
    ec = EarningsCalendar()
    consecutive_errors = 0

    while True:
        # ── 执行 API/CLI 请求的手动检查 ──
        try:
            recover_stale_jobs("earnings_check")
            request = claim_pending_job_request("earnings_check")
        except Exception as e:
            consecutive_errors += 1
            logger.error("earnings_check job claim failed: %s", e)
            time.sleep(min(60, 5 * consecutive_errors))
            continue
        if request:
            logger.info("claim earnings_check job request: %s", request["id"])
            if run_earnings_check(ec, request=request):
                consecutive_errors = 0
            else:
                consecutive_errors += 1
            continue

        # ── 定时检查 ──
        if run_earnings_check(ec):
            consecutive_errors = 0
        else:
            consecutive_errors += 1

        # 根据交易时段调整间隔
        if is_trading_hours():
            sleep_sec = CHECK_INTERVAL_SEC
        else:
            sleep_sec = LONG_CHECK_INTERVAL_SEC

        logger.debug(f"下次检查: {sleep_sec // 60} 分钟后")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
