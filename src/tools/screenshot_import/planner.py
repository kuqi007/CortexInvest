from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import datetime, timezone
from typing import Literal

from src.tools.screenshot_import.models import (
    ClassificationResult,
    ImportPlan,
    NormalizedRow,
    PlannedAction,
    ReasonCode,
)
from src.tools.screenshot_import.validator import is_valid_normalized_code


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256(payload: object) -> str:
    digest = hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _row_payload(row: NormalizedRow) -> dict[str, object]:
    payload: dict[str, object] = {
        "code": row.code,
        "name": row.name,
        "type": "holding" if row.is_holding else "watching",
        "star": True,
    }
    if row.is_holding:
        payload["cost"] = row.cost
        payload["shares"] = row.shares
    return payload


def compute_actionable_rows_hash(rows: Sequence[NormalizedRow]) -> str:
    payloads = [_row_payload(r) for r in rows]
    payloads.sort(key=lambda p: str(p["code"]))
    return _sha256(payloads)


def _min_required_confidence(row: NormalizedRow) -> float:
    fc = row.field_confidence
    parts: list[float] = [float(fc.code)]
    parts.append(float(fc.name) if fc.name is not None else 0.0)
    if row.is_holding:
        parts.append(float(fc.cost) if fc.cost is not None else 0.0)
        parts.append(float(fc.shares) if fc.shares is not None else 0.0)
    return min(parts)


def _action_for(row: NormalizedRow, existing_codes: Collection[str]) -> Literal["add", "update"]:
    if row.is_holding:
        return "add"
    if row.code in existing_codes:
        return "update"
    return "add"


def _row_apply_id(import_run_id: str, row: NormalizedRow, action: str) -> str:
    return _sha256(
        {
            "action": action,
            "import_run_id": import_run_id,
            "payload": _row_payload(row),
        }
    )


def _reason_codes_for_row(
    row: NormalizedRow,
    *,
    classification: ClassificationResult,
    model_confidence: float,
    threshold: float,
    code_counts: Mapping[str, int],
    manual_platform_after_low_confidence: bool,
) -> list[ReasonCode]:
    reasons: list[ReasonCode] = []
    if not is_valid_normalized_code(row.code):
        reasons.append("invalid_code")
    if row.is_holding and (row.cost is None or row.shares is None):
        reasons.append("missing_required_field")
    if code_counts.get(row.code, 0) > 1:
        reasons.append("duplicate_code_conflict")
    if model_confidence < threshold:
        reasons.append("below_model_threshold")
    if manual_platform_after_low_confidence and row.is_holding:
        reasons.append("manual_platform_low_confidence")
    elif classification.confidence < threshold:
        reasons.append("below_classifier_threshold")
    if _min_required_confidence(row) < threshold:
        reasons.append("below_field_threshold")
    return reasons


def _is_hard_reject(reasons: Sequence[ReasonCode]) -> bool:
    return "invalid_code" in reasons or "missing_required_field" in reasons


def compute_plan_integrity_hash(plan: ImportPlan) -> str:
    """Canonical hash over persisted plan fields, excluding mutable apply_log and plan_hash."""
    payload = plan.model_dump(mode="json", exclude={"apply_log", "plan_hash"})
    return _sha256(payload)


def verify_import_plan_integrity(plan: ImportPlan) -> tuple[bool, str]:
    expected = plan.plan_hash
    computed = compute_plan_integrity_hash(plan)
    if expected == computed:
        return True, ""
    return (
        False,
        "plan content does not match plan_hash — file may have been edited after export",
    )


def build_import_plan(
    rows: Sequence[NormalizedRow],
    provider: str,
    model: str,
    classification: ClassificationResult,
    model_confidence: float,
    threshold: float,
    content_fingerprint: str,
    existing_codes: Collection[str],
    manual_platform_after_low_confidence: bool,
) -> ImportPlan:
    import_run_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc)
    sorted_rows = sorted(rows, key=lambda r: r.code)
    actionable_rows_hash = compute_actionable_rows_hash(sorted_rows)

    code_counts: dict[str, int] = {}
    for item in sorted_rows:
        code = item.code
        code_counts[code] = code_counts.get(code, 0) + 1

    auto_apply: list[PlannedAction] = []
    needs_confirmation: list[PlannedAction] = []
    rejected: list[dict[str, object]] = []

    for seq, row in enumerate(sorted_rows):
        reasons = _reason_codes_for_row(
            row,
            classification=classification,
            model_confidence=model_confidence,
            threshold=threshold,
            code_counts=code_counts,
            manual_platform_after_low_confidence=manual_platform_after_low_confidence,
        )
        if _is_hard_reject(reasons):
            rejected.append(
                {
                    "code": row.code,
                    "plan_sequence": seq,
                    "reason_codes": list(reasons),
                    "row": row.model_dump(mode="python"),
                }
            )
            continue

        action = _action_for(row, existing_codes)
        pa = PlannedAction(
            row_apply_id=_row_apply_id(import_run_id, row, action),
            plan_sequence=seq,
            action=action,
            code=row.code,
            payload=_row_payload(row),
            reason_codes=list(reasons),
        )
        if reasons:
            needs_confirmation.append(pa)
        else:
            auto_apply.append(pa)

    plan = ImportPlan(
        import_run_id=import_run_id,
        created_at=created_at,
        provider=provider,
        model=model,
        platform=classification.platform,
        screenshot_type=classification.screenshot_type,
        classification=classification,
        thresholds={"confirm_threshold": threshold},
        content_fingerprint=content_fingerprint,
        plan_hash="",
        actionable_rows_hash=actionable_rows_hash,
        auto_apply=auto_apply,
        needs_confirmation=needs_confirmation,
        rejected=rejected,
        apply_log=[],
    )
    return plan.model_copy(update={"plan_hash": compute_plan_integrity_hash(plan)})
