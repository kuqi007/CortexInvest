"""Notification audit helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import time
import uuid
from typing import Any

from src.sim_trading.db import get_connection
from src.utils.audit_log import _reject_sensitive_fields, insert_trading_outbox
from src.utils.audit_system import build_audit_event_v2, make_actor

NOTIFICATION_METADATA_ALLOWLIST = {
    "_kind",
    "channel",
    "change_pct",
    "code",
    "count",
    "date",
    "drawdown_pct",
    "group",
    "high_20d",
    "kind",
    "level",
    "method",
    "name",
    "price",
    "score",
    "sound",
    "symbol",
    "template_id",
}


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _safe_metadata(value)
    if isinstance(value, list):
        return [_safe_value(item) for item in value]
    return _safe_scalar(value)


def _safe_metadata(metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return {}
    safe: dict[str, Any] = {}
    for key, value in metadata.items():
        if key not in NOTIFICATION_METADATA_ALLOWLIST:
            continue
        safe[key] = _safe_value(value)
    return safe


def _sha256_text(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


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
    _reject_sensitive_fields(metadata, path="metadata")
    safe_meta = _safe_metadata(metadata)
    symbol = str(safe_meta.get("symbol") or safe_meta.get("code") or "system")
    template_id = str(
        safe_meta.get("template_id")
        or safe_meta.get("kind")
        or safe_meta.get("_kind")
        or "notification"
    )
    event = build_audit_event_v2(
        event_id=uuid.uuid4().hex,
        ts_ms=now_ms,
        source="notification",
        actor=make_actor(actor_type="system", actor_id="notification"),
        action="sent",
        entity="notification",
        key=f"{channel}:{symbol}",
        db_name="trading.db",
        before=None,
        after={
            "channel": channel,
            "template_id": template_id,
            "title_hash": _sha256_text(title),
            "title_len": len(title),
            "message_hash": _sha256_text(message),
            "message_len": len(message),
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
