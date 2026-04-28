import pytest

from src.utils.rollout_gates import (
    RolloutGates,
    read_rollout_gates,
    validate_gate_order,
)


def test_config_db_only_requires_audit_gate():
    gates = RolloutGates(
        audit_enabled=False,
        config_db_only=True,
        runtime_db_only=False,
        restore_apply_enabled=False,
        allow_json_mixed_mode=False,
    )

    with pytest.raises(RuntimeError, match="JSON_DB_AUDIT_ENABLED"):
        validate_gate_order(gates, production=False)


def test_runtime_db_only_requires_config_db_only_gate():
    gates = RolloutGates(
        audit_enabled=True,
        config_db_only=False,
        runtime_db_only=True,
        restore_apply_enabled=False,
        allow_json_mixed_mode=False,
    )

    with pytest.raises(RuntimeError, match="JSON_DB_ONLY_CONFIG"):
        validate_gate_order(gates, production=False)


def test_restore_apply_requires_audit_gate():
    gates = RolloutGates(
        audit_enabled=False,
        config_db_only=False,
        runtime_db_only=False,
        restore_apply_enabled=True,
        allow_json_mixed_mode=False,
    )

    with pytest.raises(RuntimeError, match="JSON_DB_AUDIT_ENABLED"):
        validate_gate_order(gates, production=False)


def test_mixed_mode_is_forbidden_in_production():
    gates = RolloutGates(
        audit_enabled=True,
        config_db_only=False,
        runtime_db_only=False,
        restore_apply_enabled=False,
        allow_json_mixed_mode=True,
    )

    with pytest.raises(RuntimeError, match="ALLOW_JSON_MIXED_MODE"):
        validate_gate_order(gates, production=True)


def test_mixed_mode_is_allowed_outside_production():
    gates = RolloutGates(
        audit_enabled=True,
        config_db_only=False,
        runtime_db_only=False,
        restore_apply_enabled=False,
        allow_json_mixed_mode=True,
    )

    validate_gate_order(gates, production=False)


def test_valid_gate_chain_passes_in_production():
    gates = RolloutGates(
        audit_enabled=True,
        config_db_only=True,
        runtime_db_only=True,
        restore_apply_enabled=True,
        allow_json_mixed_mode=False,
    )

    validate_gate_order(gates, production=True)


def test_read_rollout_gates_parses_truthy_environment_values(monkeypatch):
    monkeypatch.setenv("JSON_DB_AUDIT_ENABLED", "1")
    monkeypatch.setenv("JSON_DB_ONLY_CONFIG", "true")
    monkeypatch.setenv("JSON_DB_ONLY_RUNTIME", "yes")
    monkeypatch.setenv("AUDIT_RESTORE_APPLY_ENABLED", "on")
    monkeypatch.setenv("ALLOW_JSON_MIXED_MODE", "TRUE")

    gates = read_rollout_gates()

    assert gates == RolloutGates(
        audit_enabled=True,
        config_db_only=True,
        runtime_db_only=True,
        restore_apply_enabled=True,
        allow_json_mixed_mode=True,
    )


def test_read_rollout_gates_treats_unset_values_as_false(monkeypatch):
    for name in [
        "JSON_DB_AUDIT_ENABLED",
        "JSON_DB_ONLY_CONFIG",
        "JSON_DB_ONLY_RUNTIME",
        "AUDIT_RESTORE_APPLY_ENABLED",
        "ALLOW_JSON_MIXED_MODE",
    ]:
        monkeypatch.delenv(name, raising=False)

    gates = read_rollout_gates()

    assert gates == RolloutGates(
        audit_enabled=False,
        config_db_only=False,
        runtime_db_only=False,
        restore_apply_enabled=False,
        allow_json_mixed_mode=False,
    )
