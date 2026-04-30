from __future__ import annotations

from src.tools.screenshot_import.models import (
    ClassificationResult,
    FieldConfidence,
    NormalizedRow,
    PlatformCandidate,
)
from src.tools.screenshot_import.planner import build_import_plan, compute_actionable_rows_hash


def _fc(**kwargs: float) -> FieldConfidence:
    return FieldConfidence(
        code=kwargs.get("code", 0.99),
        name=kwargs.get("name", 0.95),
        cost=kwargs.get("cost"),
        shares=kwargs.get("shares"),
    )


def test_high_confidence_holding_enters_auto_apply() -> None:
    row = NormalizedRow(
        code="600519",
        name="贵州茅台",
        is_holding=True,
        cost=1500.0,
        shares=200,
        field_confidence=_fc(code=0.95, name=0.94, cost=0.92, shares=0.91),
    )
    classification = ClassificationResult(
        platform="ths",
        screenshot_type="holding",
        confidence=0.92,
        candidate_platforms=[PlatformCandidate(platform="ths", confidence=0.92)],
    )
    plan = build_import_plan(
        rows=[row],
        provider="kimi",
        model="moonshot-v1",
        classification=classification,
        model_confidence=0.92,
        threshold=0.8,
        content_fingerprint="fp1",
        existing_codes=frozenset(),
        manual_platform_after_low_confidence=False,
    )
    assert len(plan.auto_apply) == 1
    assert plan.auto_apply[0].code == "600519"
    assert plan.auto_apply[0].action == "add"
    assert plan.auto_apply[0].reason_codes == []
    assert plan.needs_confirmation == []


def test_manual_platform_after_low_confidence_holdings_only_manual_reason() -> None:
    row = NormalizedRow(
        code="HK00700",
        name="腾讯控股",
        is_holding=True,
        cost=320.5,
        shares=100,
        field_confidence=_fc(code=0.99, name=0.98, cost=0.97, shares=0.96),
    )
    classification = ClassificationResult(
        platform="hk_panda",
        screenshot_type="holding",
        confidence=0.35,
        candidate_platforms=[PlatformCandidate(platform="hk_panda", confidence=0.35)],
    )
    plan = build_import_plan(
        rows=[row],
        provider="glm",
        model="glm-4",
        classification=classification,
        model_confidence=0.95,
        threshold=0.8,
        content_fingerprint="fp2",
        existing_codes=frozenset(),
        manual_platform_after_low_confidence=True,
    )
    assert plan.auto_apply == []
    assert len(plan.needs_confirmation) == 1
    assert plan.needs_confirmation[0].reason_codes == ["manual_platform_low_confidence"]
    assert "below_classifier_threshold" not in plan.needs_confirmation[0].reason_codes


def test_actionable_rows_hash_is_deterministic() -> None:
    fc = _fc(code=0.9, name=0.9, cost=0.9, shares=0.9)
    a = NormalizedRow(
        code="600519",
        name="A",
        is_holding=True,
        cost=1.0,
        shares=1,
        field_confidence=fc,
    )
    b = NormalizedRow(
        code="000001",
        name="B",
        is_holding=True,
        cost=2.0,
        shares=2,
        field_confidence=fc,
    )
    h1 = compute_actionable_rows_hash([a, b])
    h2 = compute_actionable_rows_hash([b, a])
    assert h1 == h2
    assert h1.startswith("sha256:")


def test_low_model_confidence_marks_below_model_threshold() -> None:
    row = NormalizedRow(
        code="600519",
        name="贵州茅台",
        is_holding=False,
        field_confidence=_fc(code=0.99, name=0.99),
    )
    classification = ClassificationResult(
        platform="ths",
        screenshot_type="watchlist",
        confidence=0.95,
        candidate_platforms=[PlatformCandidate(platform="ths", confidence=0.95)],
    )
    plan = build_import_plan(
        rows=[row],
        provider="kimi",
        model="moonshot-v1",
        classification=classification,
        model_confidence=0.1,
        threshold=0.8,
        content_fingerprint="fp-model",
        existing_codes=frozenset(),
        manual_platform_after_low_confidence=False,
    )
    assert plan.auto_apply == []
    assert len(plan.needs_confirmation) == 1
    assert "below_model_threshold" in plan.needs_confirmation[0].reason_codes


def test_name_only_match_reason_when_code_low_name_high() -> None:
    row = NormalizedRow(
        code="600519",
        name="贵州茅台",
        is_holding=False,
        field_confidence=_fc(code=0.7, name=0.95),
    )
    classification = ClassificationResult(
        platform="ths",
        screenshot_type="watchlist",
        confidence=0.95,
        candidate_platforms=[PlatformCandidate(platform="ths", confidence=0.95)],
    )
    plan = build_import_plan(
        rows=[row],
        provider="kimi",
        model="moonshot-v1",
        classification=classification,
        model_confidence=0.95,
        threshold=0.8,
        content_fingerprint="fp-noname",
        existing_codes=frozenset(),
        manual_platform_after_low_confidence=False,
    )
    assert plan.auto_apply == []
    assert len(plan.needs_confirmation) == 1
    rc = plan.needs_confirmation[0].reason_codes
    assert "name_only_match" in rc
    assert "below_field_threshold" in rc


def test_abnormal_holding_delta_routes_to_needs_confirmation() -> None:
    row = NormalizedRow(
        code="600519",
        name="贵州茅台",
        is_holding=True,
        cost=500.0,
        shares=100,
        field_confidence=_fc(code=0.95, name=0.94, cost=0.92, shares=0.91),
    )
    classification = ClassificationResult(
        platform="ths",
        screenshot_type="holding",
        confidence=0.92,
        candidate_platforms=[PlatformCandidate(platform="ths", confidence=0.92)],
    )
    existing_wl: dict[str, object] = {
        "600519": {"cost": 100.0, "shares": 100},
    }
    plan = build_import_plan(
        rows=[row],
        provider="kimi",
        model="moonshot-v1",
        classification=classification,
        model_confidence=0.92,
        threshold=0.8,
        content_fingerprint="fp-ab",
        existing_codes=frozenset(["600519"]),
        manual_platform_after_low_confidence=False,
        existing_watchlist=existing_wl,
    )
    assert plan.auto_apply == []
    assert len(plan.needs_confirmation) == 1
    assert "abnormal_delta" in plan.needs_confirmation[0].reason_codes
