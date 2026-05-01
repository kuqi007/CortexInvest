"""Best-effort audit writer for ordinary DB mutations.

High-frequency telemetry tables are explicitly excluded. Other business tables
should record compact before/after snapshots through this helper.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from typing import Any

from src.utils.audit_log import insert_config_outbox, insert_trading_outbox
from src.utils.audit_system import build_audit_event_v2

logger = logging.getLogger("audit_writer")

EXCLUDED_AUDIT_TABLES: dict[str, set[str]] = {
    "trading.db": {
        "price_snapshots",
        "market_turnover",
        "market_amo_history",
        "signals",
        "session_snapshots",
        "tick_monitor_events",
        "tick_monitor_state",
        "poller_leader_lease",
        "daily_kline",
        "earnings_calendar",
        "earnings_history",
        "indicator_cache",
        "sentiment_cache",
        "sector_rotation",
        "sector_daily",
        "sector_alerts",
        "stock_daily",
        "stock_data_fetch_snapshots",
        "trading_calendar_cache",
        "morning_briefings",
        "daily_l2_digest",
        "daily_summaries",
        "job_requests",
        "job_runs",
    },
    "config.db": set(),
}

TEXT_HASH_FIELDS = {"message", "display", "title", "body", "notes"}


def audit_table_enabled(db_name: str, table: str) -> bool:
    return table not in EXCLUDED_AUDIT_TABLES.get(db_name, set())


def hash_text_payload(payload: dict[str, Any] | None) -> dict[str, Any] | None:
    if payload is None:
        return None
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if key in TEXT_HASH_FIELDS and value is not None:
            text = str(value)
            safe[f"{key}_hash"] = f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"
            safe[f"{key}_len"] = len(text)
        else:
            safe[key] = value
    return safe


def record_db_change_best_effort(
    conn,
    *,
    db_name: str,
    table: str,
    action: str,
    key: str,
    source: str,
    actor: dict[str, Any],
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    metadata: dict[str, Any] | None = None,
    hash_text_fields: bool = False,
) -> bool:
    """Record a mutation audit event without blocking the business write."""
    if not audit_table_enabled(db_name, table):
        return False
    try:
        event = build_audit_event_v2(
            event_id=uuid.uuid4().hex,
            ts_ms=int(time.time() * 1000),
            source=source,
            actor=actor,
            action=action,
            entity=table,
            key=key,
            db_name=db_name,
            before=hash_text_payload(before) if hash_text_fields else before,
            after=hash_text_payload(after) if hash_text_fields else after,
            metadata=metadata or {},
        )
        if db_name == "config.db":
            insert_config_outbox(conn, event)
        elif db_name == "trading.db":
            insert_trading_outbox(conn, event)
        else:
            raise ValueError(f"Unsupported audit db: {db_name}")
        return True
    except Exception as exc:
        logger.warning(
            "best-effort audit failed for %s.%s %s %s: %s",
            db_name,
            table,
            action,
            key,
            exc,
        )
        return False
