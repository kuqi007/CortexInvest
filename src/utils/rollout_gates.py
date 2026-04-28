"""Rollout gate parsing for JSON-to-DB migration phases."""

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class RolloutGates:
    audit_enabled: bool
    config_db_only: bool
    runtime_db_only: bool
    restore_apply_enabled: bool
    allow_json_mixed_mode: bool


def _enabled(name: str) -> bool:
    return os.environ.get(name, "").lower() in {"1", "true", "yes", "on"}


def read_rollout_gates() -> RolloutGates:
    return RolloutGates(
        audit_enabled=_enabled("JSON_DB_AUDIT_ENABLED"),
        config_db_only=_enabled("JSON_DB_ONLY_CONFIG"),
        runtime_db_only=_enabled("JSON_DB_ONLY_RUNTIME"),
        restore_apply_enabled=_enabled("AUDIT_RESTORE_APPLY_ENABLED"),
        allow_json_mixed_mode=_enabled("ALLOW_JSON_MIXED_MODE"),
    )


def validate_gate_order(gates: RolloutGates, *, production: bool) -> None:
    if gates.config_db_only and not gates.audit_enabled:
        raise RuntimeError("JSON_DB_ONLY_CONFIG requires JSON_DB_AUDIT_ENABLED")
    if gates.runtime_db_only and not gates.config_db_only:
        raise RuntimeError("JSON_DB_ONLY_RUNTIME requires JSON_DB_ONLY_CONFIG")
    if gates.restore_apply_enabled and not gates.audit_enabled:
        raise RuntimeError("AUDIT_RESTORE_APPLY_ENABLED requires JSON_DB_AUDIT_ENABLED")
    if production and gates.allow_json_mixed_mode:
        raise RuntimeError("ALLOW_JSON_MIXED_MODE is forbidden in production")
