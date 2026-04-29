"""Unified audit event helpers shared by Python mutation paths."""

from __future__ import annotations

from typing import Any

from src.utils.audit_log import build_audit_event


def make_actor(
    *,
    actor_type: str,
    actor_id: str,
    host: str | None = None,
) -> dict[str, str]:
    actor = {"type": actor_type, "id": actor_id}
    if host:
        actor["host"] = host
    return actor


def build_audit_event_v2(
    *,
    event_id: str,
    ts_ms: int,
    source: str,
    actor: dict[str, Any],
    action: str,
    entity: str,
    key: str,
    db_name: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
    correlation_id: str | None = None,
) -> dict[str, Any]:
    return build_audit_event(
        event_id=event_id,
        ts_ms=ts_ms,
        correlation_id=correlation_id,
        source=source,
        actor=actor,
        action=action,
        entity=entity,
        key=key,
        db_name=db_name,
        before=before,
        after=after,
        metadata=metadata or {},
        schema_version=2,
    )
