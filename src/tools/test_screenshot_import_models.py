from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from src.tools.screenshot_import.models import (
    ClassificationResult,
    ImportPlan,
    PlanGroup,
    VisionResponse,
)


def test_vision_response_holding_accepts_cost_shares_and_field_confidence():
    data = {
        "schema_version": 1,
        "platform": "ths",
        "screenshot_type": "holding",
        "confidence": 0.95,
        "stocks": [
            {
                "code": "000001",
                "name": "平安银行",
                "is_holding": True,
                "cost": 12.5,
                "shares": 1000,
                "field_confidence": {
                    "code": 0.99,
                    "name": 0.9,
                    "cost": 0.85,
                    "shares": 0.88,
                },
            }
        ],
    }
    vr = VisionResponse.model_validate(data)
    assert len(vr.stocks) == 1
    row = vr.stocks[0]
    assert row.is_holding is True
    assert row.cost == 12.5
    assert row.shares == 1000
    assert row.field_confidence.code == 0.99
    assert row.field_confidence.cost == 0.85
    assert row.field_confidence.shares == 0.88


def test_vision_response_watchlist_rejects_cost_or_shares():
    base = {
        "schema_version": 1,
        "platform": "eastmoney",
        "screenshot_type": "watchlist",
        "confidence": 0.9,
        "stocks": [],
    }
    with pytest.raises(ValidationError, match="watchlist row must not include"):
        VisionResponse.model_validate(
            {
                **base,
                "stocks": [
                    {
                        "code": "000001",
                        "name": "平安银行",
                        "is_holding": False,
                        "cost": 1.0,
                        "field_confidence": {"code": 0.9},
                    }
                ],
            }
        )
    with pytest.raises(ValidationError, match="watchlist row must not include"):
        VisionResponse.model_validate(
            {
                **base,
                "stocks": [
                    {
                        "code": "000001",
                        "name": "平安银行",
                        "is_holding": False,
                        "shares": 100,
                        "field_confidence": {"code": 0.9},
                    }
                ],
            }
        )



def test_import_plan_hashes_distinct_and_plan_group_empty_ok():
    classification = ClassificationResult(
        platform="unknown",
        screenshot_type="unknown",
        confidence=0.5,
    )
    plan = ImportPlan(
        import_run_id="run-1",
        created_at=datetime.now(timezone.utc),
        provider="test",
        model="test-model",
        platform="other",
        screenshot_type="watchlist",
        classification=classification,
        thresholds={},
        content_fingerprint="cfp",
        plan_hash="hash-plan",
        actionable_rows_hash="hash-rows",
        auto_apply=False,
        needs_confirmation=True,
    )
    assert plan.plan_hash == "hash-plan"
    assert plan.actionable_rows_hash == "hash-rows"
    assert plan.plan_hash != plan.actionable_rows_hash

    assert PlanGroup(actions=[]).model_dump() == {"actions": []}
