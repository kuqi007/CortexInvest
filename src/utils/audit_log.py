"""Audit event envelope and outbox insertion helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any


SUPPORTED_DBS = {"config.db", "trading.db"}
SENSITIVE_FIELD_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "auth_token",
    "password",
    "private_key",
    "secret",
    "token",
)


def _ts_from_ms(ts_ms: int) -> str:
    return (
        datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _reject_sensitive_fields(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in SENSITIVE_FIELD_FRAGMENTS):
                raise ValueError(f"sensitive audit field is not allowed: {path}.{key}")
            _reject_sensitive_fields(child, path=f"{path}.{key}")
    elif isinstance(value, list):
        for idx, child in enumerate(value):
            _reject_sensitive_fields(child, path=f"{path}[{idx}]")


def build_audit_event(
    *,
    event_id: str,
    ts_ms: int,
    source: str,
    action: str,
    entity: str,
    key: str,
    db_name: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    correlation_id: str | None = None,
    ts: str | None = None,
    schema_version: int = 1,
) -> dict[str, Any]:
    if db_name not in SUPPORTED_DBS:
        raise ValueError(f"Unsupported audit db: {db_name}")
    derived_ts = _ts_from_ms(ts_ms)
    if ts is not None and ts != derived_ts:
        raise ValueError("ts does not match ts_ms")
    _reject_sensitive_fields(before, path="before")
    _reject_sensitive_fields(after, path="after")

    return {
        "event_id": event_id,
        "schema_version": schema_version,
        "ts": derived_ts,
        "ts_ms": ts_ms,
        "correlation_id": correlation_id,
        "source": source,
        "action": action,
        "entity": entity,
        "key": key,
        "before": before,
        "after": after,
        "db": db_name,
    }


def _insert_outbox(
    conn: sqlite3.Connection,
    *,
    table: str,
    expected_db: str,
    event: dict[str, Any],
) -> None:
    if event.get("db") != expected_db:
        raise ValueError(f"Expected audit event for {expected_db}")
    payload_json = json.dumps(
        event, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    conn.execute(
        f"""
        INSERT INTO {table} (
            event_id, schema_version, correlation_id, ts, ts_ms, source,
            action, entity, key, db, payload_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event["event_id"],
            event["schema_version"],
            event.get("correlation_id"),
            event["ts"],
            event["ts_ms"],
            event["source"],
            event["action"],
            event["entity"],
            event["key"],
            event["db"],
            payload_json,
        ),
    )


def insert_config_outbox(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    _insert_outbox(
        conn,
        table="config_audit_outbox",
        expected_db="config.db",
        event=event,
    )


def insert_trading_outbox(conn: sqlite3.Connection, event: dict[str, Any]) -> None:
    _insert_outbox(
        conn,
        table="trading_audit_outbox",
        expected_db="trading.db",
        event=event,
    )
