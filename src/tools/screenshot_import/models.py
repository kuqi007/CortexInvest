from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Platform = Literal["ths", "eastmoney", "hk_panda", "other", "unknown"]
ScreenshotType = Literal["holding", "watchlist", "unknown"]
ReasonCode = Literal[
    "below_classifier_threshold",
    "below_model_threshold",
    "below_field_threshold",
    "duplicate_code_conflict",
    "name_only_match",
    "abnormal_delta",
    "missing_required_field",
    "invalid_code",
    "manual_platform_low_confidence",
    "schema_error",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(StrictModel):
    kind: Literal["row_index", "bbox", "header_label_hash", "none"] = "none"
    value: str = Field(default="", max_length=120)


class FieldConfidence(StrictModel):
    code: float = Field(ge=0, le=1)
    name: float | None = Field(default=None, ge=0, le=1)
    cost: float | None = Field(default=None, ge=0, le=1)
    shares: float | None = Field(default=None, ge=0, le=1)


class BaseStockRow(StrictModel):
    code: str
    name: str
    is_holding: bool
    field_confidence: FieldConfidence
    evidence: Evidence = Field(default_factory=Evidence)


class HoldingStockRow(BaseStockRow):
    is_holding: Literal[True]
    cost: float = Field(gt=0)
    shares: float = Field(gt=0)


class WatchlistStockRow(BaseStockRow):
    is_holding: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def _reject_cost_or_shares(cls, data: object) -> object:
        if isinstance(data, dict) and ("cost" in data or "shares" in data):
            raise ValueError("watchlist row must not include cost or shares")
        return data


StockRow = Annotated[HoldingStockRow | WatchlistStockRow, Field(discriminator="is_holding")]


class VisionResponse(StrictModel):
    schema_version: Literal[1]
    platform: Platform
    screenshot_type: ScreenshotType
    confidence: float = Field(ge=0, le=1)
    stocks: list[StockRow]
    warnings: list[str] = Field(default_factory=list, max_length=20)


class ClassificationResult(StrictModel):
    platform: Platform
    screenshot_type: ScreenshotType
    confidence: float = Field(ge=0, le=1)
    signals: list[str] = Field(default_factory=list)
    candidate_platforms: list[Platform] = Field(default_factory=list)


class NormalizedRow(StrictModel):
    code: str
    name: str
    is_holding: bool
    cost: float | None = None
    shares: float | None = None
    field_confidence: FieldConfidence
    source_row_index: int | None = None


class PlannedAction(StrictModel):
    row_apply_id: str
    plan_sequence: int
    action: Literal["add", "update"]
    code: str
    payload: dict[str, object]
    reason_codes: list[ReasonCode] = Field(default_factory=list)


class PlanGroup(StrictModel):
    actions: list[PlannedAction]


class ApplyLogEntry(StrictModel):
    row_apply_id: str
    code: str
    action: Literal["add", "update"]
    request_payload_hash: str
    response_ok: bool
    ts: datetime
    http_status: int | None = None
    message: str | None = None


class ImportPlan(StrictModel):
    schema_version: Literal[1] = 1
    import_run_id: str
    created_at: datetime
    provider: str
    model: str
    platform: Platform
    screenshot_type: ScreenshotType
    classification: ClassificationResult
    thresholds: dict[str, object]
    content_fingerprint: str
    plan_hash: str
    actionable_rows_hash: str
    auto_apply: bool
    needs_confirmation: bool
    rejected: list[dict[str, object]] = Field(default_factory=list)
    apply_log: list[ApplyLogEntry] = Field(default_factory=list)
