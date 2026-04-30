"""Redacted debug payloads for vision screenshot import (Tier A — no PII)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

TIER_A_IMAGE_FINGERPRINT = "image_fingerprint_redacted.json"
TIER_A_PROVIDER_REQUEST = "provider_request_redacted.json"
TIER_A_IMPORT_PLAN_SUMMARY = "import_plan_summary_redacted.json"


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


def write_tier_a_bundle(
    debug_dir: Path,
    *,
    image_fingerprint_redacted: dict[str, object],
    provider_request_redacted: dict[str, object],
    import_plan_summary_redacted: dict[str, object],
) -> None:
    """Write Tier A filenames from the vision design spec (no raw paths or portfolio fields)."""
    debug_dir.mkdir(parents=True, exist_ok=True)
    (debug_dir / TIER_A_IMAGE_FINGERPRINT).write_text(
        json.dumps(image_fingerprint_redacted, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (debug_dir / TIER_A_PROVIDER_REQUEST).write_text(
        json.dumps(provider_request_redacted, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    (debug_dir / TIER_A_IMPORT_PLAN_SUMMARY).write_text(
        json.dumps(import_plan_summary_redacted, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
