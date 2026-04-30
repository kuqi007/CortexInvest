"""Redacted debug payloads for vision screenshot import (Tier A — no PII)."""

from __future__ import annotations

import hashlib


def _hash_code(code: str) -> str:
    digest = hashlib.sha256(code.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def tier_a_debug_payload(
    provider: str,
    codes: list[str],
    confidence_stats: dict[str, float],
    plan_summary: dict[str, int],
) -> dict[str, object]:
    """Build a Tier A debug dict: hashes codes only; no raw codes, names, amounts, or paths."""
    return {
        "tier": "A",
        "provider": provider,
        "code_hashes": [_hash_code(c) for c in codes],
        "confidence_stats": confidence_stats,
        "plan_summary": plan_summary,
    }
