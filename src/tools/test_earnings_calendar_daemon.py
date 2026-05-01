import sqlite3

import pytest

import src.sim_trading.db as db
from src.tools import earnings_calendar_daemon as daemon


@pytest.fixture()
def trading_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(daemon, "get_connection", db.get_connection)
    db.init_trading_db()
    return tmp_path / "trading.db"


class FakeCalendar:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.calls = 0

    def check_and_alert(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("boom")


def _insert_job_request(conn: sqlite3.Connection, request_id: str):
    conn.execute(
        """
        INSERT INTO job_requests
          (id, job_type, requested_by, status, correlation_id, created_at_ms)
        VALUES (?, 'earnings_check', 'api', 'pending', ?, 1000)
        """,
        (request_id, f"corr-{request_id}"),
    )


def test_claim_pending_earnings_job_marks_request_claimed(trading_db):
    conn = db.get_connection()
    try:
        _insert_job_request(conn, "req-1")
    finally:
        conn.close()

    request = daemon.claim_pending_job_request("earnings_check")

    assert request is not None
    assert request["id"] == "req-1"

    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT status, claimed_at_ms FROM job_requests WHERE id = 'req-1'"
        ).fetchone()
        assert row["status"] == "claimed"
        assert row["claimed_at_ms"] > 0
    finally:
        conn.close()


def test_run_earnings_check_records_successful_job_run(trading_db):
    conn = db.get_connection()
    try:
        _insert_job_request(conn, "req-success")
    finally:
        conn.close()

    request = daemon.claim_pending_job_request("earnings_check")
    calendar = FakeCalendar()

    assert daemon.run_earnings_check(calendar, request=request) is True
    assert calendar.calls == 1

    conn = db.get_connection()
    try:
        request_row = conn.execute(
            "SELECT status, completed_at_ms FROM job_requests WHERE id = 'req-success'"
        ).fetchone()
        run_row = conn.execute(
            "SELECT request_id, job_type, status, error FROM job_runs"
        ).fetchone()
        assert request_row["status"] == "completed"
        assert request_row["completed_at_ms"] > 0
        assert run_row["request_id"] == "req-success"
        assert run_row["job_type"] == "earnings_check"
        assert run_row["status"] == "success"
        assert run_row["error"] is None
    finally:
        conn.close()


def test_run_earnings_check_records_failed_job_run(trading_db):
    conn = db.get_connection()
    try:
        _insert_job_request(conn, "req-failed")
    finally:
        conn.close()

    request = daemon.claim_pending_job_request("earnings_check")
    calendar = FakeCalendar(fail=True)

    assert daemon.run_earnings_check(calendar, request=request) is False

    conn = db.get_connection()
    try:
        request_row = conn.execute(
            "SELECT status, completed_at_ms FROM job_requests WHERE id = 'req-failed'"
        ).fetchone()
        run_row = conn.execute("SELECT status, error FROM job_runs").fetchone()
        assert request_row["status"] == "failed"
        assert request_row["completed_at_ms"] > 0
        assert run_row["status"] == "failed"
        assert "boom" in run_row["error"]
    finally:
        conn.close()


def test_recover_stale_jobs_requeues_claimed_and_fails_running(trading_db):
    conn = db.get_connection()
    try:
        conn.execute(
            """
            INSERT INTO job_requests
              (id, job_type, requested_by, status, correlation_id, created_at_ms, claimed_at_ms)
            VALUES ('req-stale', 'earnings_check', 'api', 'claimed', 'corr-stale', 1000, 1000)
            """
        )
        conn.execute(
            """
            INSERT INTO job_requests
              (id, job_type, requested_by, status, correlation_id, created_at_ms, claimed_at_ms)
            VALUES ('req-running', 'earnings_check', 'api', 'claimed', 'corr-running', 1000, 1000)
            """
        )
        conn.execute(
            """
            INSERT INTO job_runs
              (id, request_id, job_type, runner, status, started_at_ms, heartbeat_at_ms, correlation_id)
            VALUES ('run-stale', 'req-running', 'earnings_check', 'test', 'running', 1000, 1000, 'corr-running')
            """
        )
    finally:
        conn.close()

    daemon.recover_stale_jobs("earnings_check", stale_after_ms=1, now_ms=10_000)

    conn = db.get_connection()
    try:
        request_row = conn.execute(
            "SELECT status, claimed_at_ms FROM job_requests WHERE id = 'req-stale'"
        ).fetchone()
        running_request_row = conn.execute(
            "SELECT status, completed_at_ms FROM job_requests WHERE id = 'req-running'"
        ).fetchone()
        run_row = conn.execute(
            "SELECT status, error FROM job_runs WHERE id = 'run-stale'"
        ).fetchone()
        assert request_row["status"] == "pending"
        assert request_row["claimed_at_ms"] is None
        assert running_request_row["status"] == "failed"
        assert running_request_row["completed_at_ms"] == 10_000
        assert run_row["status"] == "failed"
        assert "stale" in run_row["error"]
    finally:
        conn.close()
