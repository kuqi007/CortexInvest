"""Hash-chain and manifest helpers for audit JSONL files."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


GENESIS_HASH = "sha256:GENESIS"


def canonical_event_bytes(event: dict[str, Any]) -> bytes:
    event_without_hash = {k: v for k, v in event.items() if k != "hash"}
    return json.dumps(
        event_without_hash,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def compute_event_hash(event: dict[str, Any]) -> str:
    return f"sha256:{hashlib.sha256(canonical_event_bytes(event)).hexdigest()}"


def add_hash_chain(
    events: Iterable[dict[str, Any]], *, start_prev_hash: str = GENESIS_HASH
) -> list[dict[str, Any]]:
    chained: list[dict[str, Any]] = []
    prev_hash = start_prev_hash
    for event in events:
        event_with_prev = {**event, "prev_hash": prev_hash}
        event_hash = compute_event_hash(event_with_prev)
        chained_event = {**event_with_prev, "hash": event_hash}
        chained.append(chained_event)
        prev_hash = event_hash
    return chained


def verify_hash_chain(rows: list[dict[str, Any]]) -> None:
    prev_hash = GENESIS_HASH
    for idx, row in enumerate(rows, start=1):
        if row.get("prev_hash") != prev_hash:
            raise RuntimeError(f"audit hash chain prev_hash mismatch at row {idx}")
        expected_hash = compute_event_hash(row)
        if row.get("hash") != expected_hash:
            raise RuntimeError(f"audit hash chain hash mismatch at row {idx}")
        prev_hash = expected_hash


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _fsync_file(path: Path) -> None:
    with path.open("rb") as handle:
        os.fsync(handle.fileno())


def _fsync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_jsonl_manifest(
    jsonl_path: Path,
    rows: list[dict[str, Any]],
    *,
    generated_at_ms: int,
    generator_version: str,
) -> Path:
    if not rows:
        raise ValueError("Cannot write manifest for empty JSONL rows")

    manifest = {
        "schema_version": 1,
        "file_name": jsonl_path.name,
        "file_sha256": _file_sha256(jsonl_path),
        "first_event_id": rows[0]["event_id"],
        "last_event_id": rows[-1]["event_id"],
        "first_ts_ms": rows[0]["ts_ms"],
        "last_ts_ms": rows[-1]["ts_ms"],
        "row_count": len(rows),
        "generated_at_ms": generated_at_ms,
        "generator_version": generator_version,
    }
    manifest_path = jsonl_path.with_name(f"{jsonl_path.name}.manifest.json")
    tmp_path = manifest_path.with_suffix(f"{manifest_path.suffix}.tmp")
    tmp_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    _fsync_file(tmp_path)
    tmp_path.replace(manifest_path)
    _fsync_file(manifest_path)
    _fsync_directory(manifest_path.parent)
    return manifest_path
