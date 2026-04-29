"""Notification audit helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import time
import uuid
from typing import Any

from src.sim_trading.db import get_connection
from src.utils.audit_log import build_audit_event, insert_trading_outbox


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if isinstance(value, dict):
            safe[key] = _safe_metadata(value)
        elif isinstance(value, list):
            safe[key] = [_safe_scalar(item) for item in value]
        else:
            safe[key] = _safe_scalar(value)
    return safe


def record_notification_sent(
    *,
    channel: str,
    title: str,
    message: str,
    metadata: dict[str, Any] | None = None,
    ts_ms: int | None = None,
) -> None:
    """Record a successfully sent notification in trading audit outbox."""
    now_ms = ts_ms or int(time.time() * 1000)
    safe_meta = _safe_metadata(metadata)
    symbol = str(safe_meta.get("symbol") or safe_meta.get("code") or "system")
    event = build_audit_event(
        event_id=uuid.uuid4().hex,
        ts_ms=now_ms,
        source="notification",
        action="sent",
        entity="notification",
        key=f"{channel}:{symbol}",
        db_name="trading.db",
        before=None,
        after={
            "channel": channel,
            "title": title,
            "message": message,
            "metadata": safe_meta,
            "recorded_at": datetime.fromtimestamp(
                now_ms / 1000, tz=timezone.utc
            ).isoformat().replace("+00:00", "Z"),
        },
    )
    conn = None
    try:
        conn = get_connection()
        insert_trading_outbox(conn, event)
        conn.commit()
    finally:
        if conn is not None:
            conn.close()
