from __future__ import annotations

from src.tools.screenshot_import.debug_artifacts import (
    _hash_code,
    tier_a_debug_payload,
)


def test_hash_code_format() -> None:
    h = _hash_code("600519")
    assert h.startswith("sha256:")
    assert len(h) == len("sha256:") + 64


def test_tier_a_debug_payload_redacts_sensitive_fields() -> None:
    payload = tier_a_debug_payload(
        provider="kimi",
        codes=["HK00700"],
        confidence_stats={"min": 0.9},
        plan_summary={
            "auto_apply_count": 1,
            "needs_confirmation_count": 0,
            "rejected_count": 0,
        },
    )
    dumped = str(payload)
    assert "HK00700" not in dumped
    assert "cost" not in dumped
    assert "shares" not in dumped
    assert payload["provider"] == "kimi"


def test_tier_a_debug_payload_includes_code_hashes_not_raw() -> None:
    payload = tier_a_debug_payload(
        provider="glm",
        codes=["000001", "000001"],
        confidence_stats={},
        plan_summary={"auto_apply_count": 0, "needs_confirmation_count": 0, "rejected_count": 0},
    )
    hashes = payload["code_hashes"]
    assert len(hashes) == 2
    assert all(isinstance(x, str) and x.startswith("sha256:") for x in hashes)
    assert "000001" not in str(payload)
