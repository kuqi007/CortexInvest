"""Flush audit outbox rows to JSONL audit files."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import uuid

from src.sim_trading import db as db_mod
from src.utils.audit_hash import GENESIS_HASH, add_hash_chain, verify_hash_chain, write_jsonl_manifest


@dataclass(frozen=True)
class FlushResult:
    db_kind: str
    flushed_count: int
    flush_id: str | None
    jsonl_path: Path | None


def _utc_ts(ms: int) -> str:
    return (
        datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_jsonl(target: Path, rows: list[dict]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("a", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    _fsync_directory(target.parent)


@contextmanager
def _audit_flush_lock(audit_dir: Path, db_kind: str):
    audit_dir.mkdir(parents=True, exist_ok=True)
    lock_path = audit_dir / f".{db_kind}.flush.lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _has_hash_chain(row: dict) -> bool:
    return "prev_hash" in row and "hash" in row


def _legacy_path(target: Path, now_ms: int) -> Path:
    candidate = target.with_name(f"{target.name}.legacy.{now_ms}")
    suffix = 1
    while candidate.exists():
        candidate = target.with_name(f"{target.name}.legacy.{now_ms}.{suffix}")
        suffix += 1
    return candidate


def _read_existing_jsonl(target: Path, *, now_ms: int) -> list[dict]:
    if not target.exists():
        return []
    rows: list[dict] = []
    with target.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            stripped = line.rstrip("\n")
            if not stripped:
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    f"corrupt JSONL at {target}:{line_no}; refusing audit flush"
                ) from exc
    if rows and not any(_has_hash_chain(row) for row in rows):
        legacy_path = _legacy_path(target, now_ms)
        target.replace(legacy_path)
        _fsync_directory(target.parent)
        return []
    verify_hash_chain(rows)
    return rows


def _payload_without_hash_fields(event: dict) -> dict:
    return {key: value for key, value in event.items() if key not in {"prev_hash", "hash"}}


def _assert_existing_payload_matches(
    *,
    existing_rows: list[dict],
    pending_events: list[dict],
) -> None:
    existing_by_id = {row.get("event_id"): row for row in existing_rows}
    for event in pending_events:
        event_id = event["event_id"]
        existing = existing_by_id.get(event_id)
        if existing is None:
            continue
        if _payload_without_hash_fields(existing) != event:
            raise RuntimeError(
                f"payload mismatch for existing JSONL event_id {event_id}; manual audit repair required"
            )


def _mark_flushed(
    conn,
    *,
    table: str,
    event_ids: list[str],
    flush_id: str,
    now_ms: int,
) -> None:
    placeholders = ",".join("?" for _ in event_ids)
    conn.execute(
        f"""
        UPDATE {table}
        SET flushed_at = ?, flushed_at_ms = ?, flush_id = ?
        WHERE event_id IN ({placeholders})
        """,
        (_utc_ts(now_ms), now_ms, flush_id, *event_ids),
    )


def _config_for_kind(db_kind: str):
    if db_kind == "config":
        return {
            "connection": db_mod.get_config_connection,
            "table": "config_audit_outbox",
            "jsonl_name": "config_events.jsonl",
        }
    if db_kind == "trading":
        return {
            "connection": db_mod.get_connection,
            "table": "trading_audit_outbox",
            "jsonl_name": "trading_events.jsonl",
        }
    raise ValueError(f"Unsupported db kind: {db_kind}")


def flush_outbox_once(
    db_kind: str,
    *,
    data_dir: Path | None = None,
    now_ms: int | None = None,
    batch_limit: int = 1000,
) -> FlushResult:
    config = _config_for_kind(db_kind)
    data_dir = data_dir or db_mod.DATA_DIR
    now_ms = now_ms or int(datetime.now(tz=timezone.utc).timestamp() * 1000)
    audit_dir = data_dir / "audit"
    jsonl_path = audit_dir / config["jsonl_name"]
    flush_id = uuid.uuid4().hex

    with _audit_flush_lock(audit_dir, db_kind):
        conn = config["connection"]()
        conn.execute("BEGIN IMMEDIATE")
        try:
            existing_rows = _read_existing_jsonl(jsonl_path, now_ms=now_ms)
            rows = conn.execute(
                f"""
                SELECT event_id, payload_json
                FROM {config['table']}
                WHERE flushed_at IS NULL
                ORDER BY ts_ms, event_id
                LIMIT ?
                """,
                (batch_limit,),
            ).fetchall()
            if not rows:
                conn.execute("COMMIT")
                return FlushResult(db_kind, 0, None, None)

            event_ids = [row["event_id"] for row in rows]
            events = [json.loads(row["payload_json"]) for row in rows]
            existing_ids = {row.get("event_id") for row in existing_rows}
            already_flushed_ids = [
                event_id for event_id in event_ids if event_id in existing_ids
            ]
            if already_flushed_ids:
                if set(already_flushed_ids) != set(event_ids):
                    raise RuntimeError(
                        "partial pending batch already exists in JSONL; manual audit repair required"
                    )
                _assert_existing_payload_matches(
                    existing_rows=existing_rows,
                    pending_events=events,
                )
                write_jsonl_manifest(
                    jsonl_path,
                    existing_rows,
                    generated_at_ms=now_ms,
                    generator_version="audit_flush:1",
                )
                _mark_flushed(
                    conn,
                    table=config["table"],
                    event_ids=event_ids,
                    flush_id=flush_id,
                    now_ms=now_ms,
                )
                conn.execute("COMMIT")
                return FlushResult(db_kind, len(event_ids), flush_id, jsonl_path)

            placeholders = ",".join("?" for _ in event_ids)
            conn.execute(
                f"""
                UPDATE {config['table']}
                SET flush_id = ?, flush_started_at_ms = ?
                WHERE event_id IN ({placeholders})
                """,
                (flush_id, now_ms, *event_ids),
            )
            start_prev_hash = existing_rows[-1]["hash"] if existing_rows else GENESIS_HASH
            chained_events = add_hash_chain(events, start_prev_hash=start_prev_hash)

            _append_jsonl(jsonl_path, chained_events)
            write_jsonl_manifest(
                jsonl_path,
                [*existing_rows, *chained_events],
                generated_at_ms=now_ms,
                generator_version="audit_flush:1",
            )
            _mark_flushed(
                conn,
                table=config["table"],
                event_ids=event_ids,
                flush_id=flush_id,
                now_ms=now_ms,
            )
            conn.execute("COMMIT")
            return FlushResult(db_kind, len(event_ids), flush_id, jsonl_path)
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()


def main() -> None:
    flush_outbox_once("config")
    flush_outbox_once("trading")


if __name__ == "__main__":
    main()
