# Screenshot Stock Import Vision Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the brittle OCR/direct-DB screenshot importer with a vision-backed, frozen-plan workflow that classifies screenshots, validates structured model output, and applies safe rows through `POST /api/config`.

**Architecture:** Split the importer into focused Python modules under `src/tools/screenshot_import/`: schemas, path safety, classification, provider adapters, prompt building, validation/planning, debug artifacts, config API application, and CLI orchestration. The old `src/tools/screenshot_stock_import.py` remains the module entry point but delegates to the new package; the legacy SQLite writer stays only for its existing tests and is not used by the new runtime path.

**Tech Stack:** Python 3.13, `uv`, `pytest`, `pydantic`, `requests`, `openai` compatible chat completions, Pillow for image metadata/color fingerprints, Next.js `POST /api/config` for writes.

---

## File Structure

- Create `src/tools/screenshot_import/__init__.py`  
Package marker and public constants.
- Create `src/tools/screenshot_import/models.py`  
Pydantic contracts for provider responses, normalized rows, import plans, apply logs, and stable reason codes.
- Create `src/tools/screenshot_import/path_safety.py`  
Regular-file checks, 20 MB image limit, allowlist roots, safe atomic JSON writes.
- Create `src/tools/screenshot_import/classifier.py`  
Lightweight image fingerprinting and broker/type candidate scoring.
- Create `src/tools/screenshot_import/prompts.py`  
Broker/type-aware prompt generation and third-party upload disclosure text.
- Create `src/tools/screenshot_import/providers.py`  
`VisionProvider` protocol, OpenAI-compatible provider, auto provider selection, and fallback constraints.
- Create `src/tools/screenshot_import/validator.py`  
Provider JSON parsing, code normalization, schema validation, and row safety checks.
- Create `src/tools/screenshot_import/planner.py`  
Import plan construction, canonical hashing, frozen-plan load/save, confidence rules, and redacted summaries.
- Create `src/tools/screenshot_import/debug_artifacts.py`  
Tier A/B/C debug artifact writing with private permissions and redaction.
- Create `src/tools/screenshot_import/config_api.py`  
Loopback-only config API client, apply-time snapshot refresh, per-row `add`/`update`, partial failure logging.
- Modify `src/tools/screenshot_stock_import.py`  
Replace the CLI runtime with the new workflow; keep `extract_text_from_image`, `parse_stocks_from_text`, and `import_stocks_to_db` as legacy helpers for existing tests.
- Modify `.claude/skills/screenshot-stock-import/SKILL.md`  
Replace the three-agent image-reading workflow with the frozen-plan CLI workflow.
- Modify `pyproject.toml`  
Add a direct `pydantic` dependency.
- Add `src/tools/test_screenshot_import_models.py`
- Add `src/tools/test_screenshot_import_path_safety.py`
- Add `src/tools/test_screenshot_import_classifier.py`
- Add `src/tools/test_screenshot_import_validator.py`
- Add `src/tools/test_screenshot_import_planner.py`
- Add `src/tools/test_screenshot_import_config_api.py`
- Add `src/tools/test_screenshot_stock_import_cli.py`

---

### Task 1: Dependencies And Package Skeleton

**Files:**

- Modify: `pyproject.toml`
- Create: `src/tools/screenshot_import/__init__.py`
- Create: `src/tools/screenshot_import/models.py`
- Test: `src/tools/test_screenshot_import_models.py`
- **Step 1: Add direct Pydantic dependency**

Edit `pyproject.toml` and add `pydantic` to `[project].dependencies` near the other core Python libraries:

```toml
dependencies = [
    "langchain>=0.3.0,<0.4.0",
    "langchain-openai>=0.2.11,<0.3.0",
    "langgraph>=0.2.56,<0.3.0",
    "pandas>=2.1.0,<3.0.0",
    "numpy>=1.24.0,<2.0.0",
    "python-dotenv>=1.0.0",
    "matplotlib>=3.9.2,<4.0.0",
    "yfinance>=0.2.51,<1.0.0",
    "akshare>=1.11.22,<2.0.0",
    "requests>=2.31.0,<3.0.0",
    "beautifulsoup4>=4.12.3",
    "openai>=1.12.0,<2.0.0",
    "pydantic>=2.7.0,<3.0.0",
    "langchain-core>=0.3.29,<0.4.0",
    "google-generativeai>=0.3.0,<0.4.0",
    "backoff>=2.2.1",
    "google-genai>=0.6.0,<1.0.0",
    "uvicorn>=0.34.0",
    "fastapi>=0.115.12,<0.116.0",
    "playwright>=1.52.0,<2.0.0",
    "rich>=13.7.0",
    "futu-api>=9.6.5608,<10.0.0",
    "libsql-client>=0.3.1",
    "socksio>=1.0.0",
]
```

- **Step 2: Install dependency lock/update environment**

Run:

```bash
uv sync
```

Expected: command completes without dependency resolution errors.

- **Step 3: Create package marker**

Create `src/tools/screenshot_import/__init__.py`:

```python
"""Vision-based screenshot stock import package."""

DEFAULT_CONFIRM_THRESHOLD = 0.8
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_API_URL = "http://127.0.0.1:3120/api/config"
```

- **Step 4: Write model tests first**

Create `src/tools/test_screenshot_import_models.py`:

```python
import pytest
from pydantic import ValidationError

from src.tools.screenshot_import.models import (
    ClassificationResult,
    FieldConfidence,
    HoldingStockRow,
    ImportPlan,
    PlanGroup,
    PlannedAction,
    VisionResponse,
    WatchlistStockRow,
)


def test_holding_response_accepts_required_cost_and_shares():
    response = VisionResponse.model_validate(
        {
            "schema_version": 1,
            "platform": "eastmoney",
            "screenshot_type": "holding",
            "confidence": 0.91,
            "stocks": [
                {
                    "code": "HK00700",
                    "name": "腾讯控股",
                    "is_holding": True,
                    "cost": 320.5,
                    "shares": 100,
                    "field_confidence": {
                        "code": 0.98,
                        "name": 0.96,
                        "cost": 0.93,
                        "shares": 0.94,
                    },
                    "evidence": {"kind": "row_index", "value": "3"},
                }
            ],
            "warnings": [],
        }
    )

    assert response.schema_version == 1
    assert isinstance(response.stocks[0], HoldingStockRow)
    assert response.stocks[0].field_confidence.cost == 0.93


def test_watchlist_response_rejects_cost_and_shares():
    with pytest.raises(ValidationError):
        VisionResponse.model_validate(
            {
                "schema_version": 1,
                "platform": "ths",
                "screenshot_type": "watchlist",
                "confidence": 0.9,
                "stocks": [
                    {
                        "code": "600519",
                        "name": "贵州茅台",
                        "is_holding": False,
                        "cost": 1800.0,
                        "shares": 100,
                        "field_confidence": {"code": 0.95, "name": 0.95},
                    }
                ],
                "warnings": [],
            }
        )


def test_import_plan_hash_fields_are_separate_from_apply_log():
    plan = ImportPlan(
        import_run_id="11111111-1111-4111-8111-111111111111",
        created_at="2026-04-30T17:00:00+08:00",
        provider="kimi",
        model="moonshot-v1-vision-preview",
        platform="eastmoney",
        screenshot_type="holding",
        classification=ClassificationResult(
            platform="eastmoney",
            screenshot_type="holding",
            confidence=0.88,
            signals=["top_dark_bar", "dense_numeric_table"],
        ),
        thresholds={"confirm": 0.8},
        content_fingerprint="sha256:image",
        plan_hash="sha256:plan",
        actionable_rows_hash="sha256:rows",
        auto_apply=PlanGroup(actions=[]),
        needs_confirmation=PlanGroup(actions=[]),
        rejected=PlanGroup(actions=[]),
        apply_log=[],
    )

    assert plan.plan_hash == "sha256:plan"
    assert plan.actionable_rows_hash == "sha256:rows"
```

- **Step 5: Run model tests to verify failure**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_models.py -q
```

Expected: FAIL because `src.tools.screenshot_import.models` does not exist or does not define the requested models.

- **Step 6: Implement Pydantic models**

Create `src/tools/screenshot_import/models.py`:

```python
from __future__ import annotations

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
    shares: int = Field(gt=0)


class WatchlistStockRow(BaseStockRow):
    is_holding: Literal[False]

    @model_validator(mode="before")
    @classmethod
    def reject_holding_fields(cls, data: object) -> object:
        if isinstance(data, dict) and ("cost" in data or "shares" in data):
            raise ValueError("watchlist rows must not contain cost or shares")
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
    candidate_platforms: list[dict[str, float | str]] = Field(default_factory=list)


class NormalizedRow(StrictModel):
    code: str
    name: str
    is_holding: bool
    cost: float | None = None
    shares: int | None = None
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
    ts: str
    http_status: int | None = None
    message: str | None = None


class ImportPlan(StrictModel):
    schema_version: Literal[1] = 1
    import_run_id: str
    created_at: str
    provider: str
    model: str
    platform: Platform
    screenshot_type: ScreenshotType
    classification: ClassificationResult
    thresholds: dict[str, float]
    content_fingerprint: str
    plan_hash: str
    actionable_rows_hash: str
    auto_apply: PlanGroup
    needs_confirmation: PlanGroup
    rejected: PlanGroup
    apply_log: list[ApplyLogEntry] = Field(default_factory=list)
```

- **Step 7: Run model tests to verify pass**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_models.py -q
```

Expected: PASS.

- **Step 8: Commit Task 1**

```bash
git add pyproject.toml src/tools/screenshot_import/__init__.py src/tools/screenshot_import/models.py src/tools/test_screenshot_import_models.py
git commit -m "feat: add screenshot import schema models"
```

---

### Task 2: Path Safety And Atomic JSON Writes

**Files:**

- Create: `src/tools/screenshot_import/path_safety.py`
- Test: `src/tools/test_screenshot_import_path_safety.py`
- **Step 1: Write failing path safety tests**

Create `src/tools/test_screenshot_import_path_safety.py`:

```python
import json
from pathlib import Path

import pytest

from src.tools.screenshot_import.path_safety import (
    PathSafetyError,
    atomic_write_json,
    validate_existing_image_path,
    validate_output_path,
)


def test_validate_existing_image_path_allows_regular_file_under_allowed_root(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")

    resolved = validate_existing_image_path(image, allowed_roots=[tmp_path], max_bytes=20)

    assert resolved == image.resolve()


def test_validate_existing_image_path_rejects_large_file(tmp_path):
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * 21)

    with pytest.raises(PathSafetyError, match="larger than"):
        validate_existing_image_path(image, allowed_roots=[tmp_path], max_bytes=20)


def test_validate_output_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "escape"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathSafetyError):
        validate_output_path(link / "plan.json", allowed_roots=[allowed])


def test_atomic_write_json_writes_complete_json(tmp_path):
    path = tmp_path / "plan.json"

    atomic_write_json(path, {"a": 1, "b": ["x"]})

    assert json.loads(path.read_text(encoding="utf-8")) == {"a": 1, "b": ["x"]}
```

- **Step 2: Run path tests to verify failure**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_path_safety.py -q
```

Expected: FAIL because `path_safety.py` does not exist.

- **Step 3: Implement path safety**

Create `src/tools/screenshot_import/path_safety.py`:

```python
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Iterable

from . import DEFAULT_MAX_IMAGE_BYTES


class PathSafetyError(ValueError):
    """Raised when a path violates screenshot import path policy."""


def _resolve_roots(allowed_roots: Iterable[Path]) -> list[Path]:
    roots = []
    for root in allowed_roots:
        roots.append(Path(root).expanduser().resolve(strict=False))
    return roots


def _is_under(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def validate_existing_image_path(
    path: Path,
    *,
    allowed_roots: Iterable[Path],
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> Path:
    resolved = Path(path).expanduser().resolve(strict=True)
    roots = _resolve_roots(allowed_roots)
    if not _is_under(resolved, roots):
        raise PathSafetyError(f"path is outside allowed roots: {resolved}")
    if not resolved.is_file():
        raise PathSafetyError(f"path is not a regular file: {resolved}")
    size = resolved.stat().st_size
    if size > max_bytes:
        raise PathSafetyError(f"image is larger than {max_bytes} bytes: {size}")
    return resolved


def validate_output_path(path: Path, *, allowed_roots: Iterable[Path]) -> Path:
    candidate = Path(path).expanduser()
    parent = candidate.parent.resolve(strict=False)
    roots = _resolve_roots(allowed_roots)
    if not _is_under(parent, roots):
        raise PathSafetyError(f"output path is outside allowed roots: {candidate}")
    if candidate.exists() and candidate.is_symlink():
        raise PathSafetyError(f"output path must not be a symlink: {candidate}")
    return candidate


def atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise
```

- **Step 4: Run path tests to verify pass**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_path_safety.py -q
```

Expected: PASS.

- **Step 5: Commit Task 2**

```bash
git add src/tools/screenshot_import/path_safety.py src/tools/test_screenshot_import_path_safety.py
git commit -m "feat: add screenshot import path safety"
```

---

### Task 3: Image Fingerprint And Two-Stage Classifier

**Files:**

- Create: `src/tools/screenshot_import/classifier.py`
- Test: `src/tools/test_screenshot_import_classifier.py`
- **Step 1: Write classifier tests**

Create `src/tools/test_screenshot_import_classifier.py`:

```python
from PIL import Image, ImageDraw

from src.tools.screenshot_import.classifier import classify_fingerprint, extract_fingerprint


def _image_with_top_bar(path, rgb):
    image = Image.new("RGB", (200, 300), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 200, 50), fill=rgb)
    draw.rectangle((10, 80, 190, 260), outline=(80, 80, 80), width=2)
    image.save(path)


def test_red_top_bar_prefers_ths(tmp_path):
    path = tmp_path / "ths.png"
    _image_with_top_bar(path, (210, 30, 30))

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="auto")

    assert result.platform == "ths"
    assert "top_red" in result.signals


def test_dark_top_bar_can_prefer_eastmoney(tmp_path):
    path = tmp_path / "em.png"
    _image_with_top_bar(path, (20, 35, 80))

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="auto", forced_type="holding")

    assert result.platform == "eastmoney"
    assert result.screenshot_type == "holding"


def test_forced_platform_sets_platform_but_keeps_confidence(tmp_path):
    path = tmp_path / "unknown.png"
    _image_with_top_bar(path, (245, 245, 245))

    fp = extract_fingerprint(path)
    result = classify_fingerprint(fp, forced_platform="hk_panda", forced_type="watchlist")

    assert result.platform == "hk_panda"
    assert result.screenshot_type == "watchlist"
    assert 0 <= result.confidence <= 1
```

- **Step 2: Run classifier tests to verify failure**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_classifier.py -q
```

Expected: FAIL because `classifier.py` does not exist.

- **Step 3: Implement classifier**

Create `src/tools/screenshot_import/classifier.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .models import ClassificationResult, Platform, ScreenshotType


@dataclass(frozen=True)
class ImageFingerprint:
    width: int
    height: int
    top_bar_color: str
    background_color: str
    red_ratio_top: float
    orange_ratio_top: float
    dark_ratio_top: float
    light_ratio_total: float
    layout: str
    visible_keywords: tuple[str, ...] = ()


def _color_name(rgb: tuple[int, int, int]) -> str:
    r, g, b = rgb
    if r > 170 and g < 90 and b < 90:
        return "red"
    if r > 180 and 80 <= g <= 170 and b < 80:
        return "orange"
    if r < 60 and g < 80 and b < 110:
        return "dark_blue_or_black"
    if r > 220 and g > 220 and b > 220:
        return "white"
    return "mixed"


def _ratio(pixels: list[tuple[int, int, int]], predicate) -> float:
    if not pixels:
        return 0.0
    return sum(1 for pixel in pixels if predicate(pixel)) / len(pixels)


def extract_fingerprint(image_path: Path) -> ImageFingerprint:
    with Image.open(image_path) as image:
        rgb = image.convert("RGB")
        width, height = rgb.size
        small = rgb.resize((100, max(1, int(100 * height / max(width, 1)))))
        sw, sh = small.size
        top = [small.getpixel((x, y)) for y in range(max(1, sh // 5)) for x in range(sw)]
        all_pixels = [small.getpixel((x, y)) for y in range(sh) for x in range(sw)]
        top_avg = tuple(int(sum(pixel[i] for pixel in top) / len(top)) for i in range(3))
        all_avg = tuple(int(sum(pixel[i] for pixel in all_pixels) / len(all_pixels)) for i in range(3))
        red_ratio_top = _ratio(top, lambda p: p[0] > 170 and p[1] < 100 and p[2] < 100)
        orange_ratio_top = _ratio(top, lambda p: p[0] > 180 and 70 <= p[1] <= 180 and p[2] < 100)
        dark_ratio_top = _ratio(top, lambda p: p[0] < 70 and p[1] < 90 and p[2] < 120)
        light_ratio_total = _ratio(all_pixels, lambda p: p[0] > 220 and p[1] > 220 and p[2] > 220)
    return ImageFingerprint(
        width=width,
        height=height,
        top_bar_color=_color_name(top_avg),
        background_color=_color_name(all_avg),
        red_ratio_top=red_ratio_top,
        orange_ratio_top=orange_ratio_top,
        dark_ratio_top=dark_ratio_top,
        light_ratio_total=light_ratio_total,
        layout="dense_table" if height > width else "wide_table",
    )


def classify_fingerprint(
    fingerprint: ImageFingerprint,
    *,
    forced_platform: str = "auto",
    forced_type: str = "auto",
) -> ClassificationResult:
    signals: list[str] = []
    platform: Platform = "unknown"
    confidence = 0.35
    if fingerprint.red_ratio_top >= 0.25:
        platform = "ths"
        confidence = 0.78
        signals.append("top_red")
    elif fingerprint.orange_ratio_top >= 0.2:
        platform = "eastmoney"
        confidence = 0.72
        signals.append("top_orange")
    elif fingerprint.dark_ratio_top >= 0.2:
        platform = "eastmoney"
        confidence = 0.62
        signals.append("top_dark_bar")
    elif fingerprint.light_ratio_total >= 0.75:
        platform = "hk_panda"
        confidence = 0.55
        signals.append("light_background")
    if fingerprint.layout == "dense_table":
        signals.append("dense_numeric_table")
    screenshot_type: ScreenshotType = "holding" if fingerprint.layout == "dense_table" else "watchlist"
    if forced_platform != "auto":
        platform = forced_platform  # type: ignore[assignment]
    if forced_type != "auto":
        screenshot_type = forced_type  # type: ignore[assignment]
    return ClassificationResult(
        platform=platform,
        screenshot_type=screenshot_type,
        confidence=confidence,
        signals=signals,
        candidate_platforms=[{"platform": platform, "confidence": confidence}],
    )
```

- **Step 4: Run classifier tests to verify pass**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_classifier.py -q
```

Expected: PASS.

- **Step 5: Commit Task 3**

```bash
git add src/tools/screenshot_import/classifier.py src/tools/test_screenshot_import_classifier.py
git commit -m "feat: classify screenshot broker fingerprints"
```

---

### Task 4: Prompt Builder And Vision Provider Abstraction

**Files:**

- Create: `src/tools/screenshot_import/prompts.py`
- Create: `src/tools/screenshot_import/providers.py`
- Test: `src/tools/test_screenshot_import_providers.py`
- **Step 1: Write provider and prompt tests**

Create `src/tools/test_screenshot_import_providers.py`:

```python
import json

from src.tools.screenshot_import.models import ClassificationResult
from src.tools.screenshot_import.prompts import build_upload_disclosure, build_vision_prompt
from src.tools.screenshot_import.providers import ProviderError, StubVisionProvider, VisionRequest


def test_prompt_mentions_broker_and_column_confusion():
    prompt = build_vision_prompt(
        ClassificationResult(
            platform="ths",
            screenshot_type="holding",
            confidence=0.86,
            signals=["top_red", "dense_numeric_table"],
        )
    )

    assert "同花顺" in prompt
    assert "不要把当前价" in prompt
    assert "JSON" in prompt


def test_upload_disclosure_names_provider():
    assert "kimi" in build_upload_disclosure("kimi").lower()


def test_stub_provider_parses_valid_json_response():
    provider = StubVisionProvider(
        name="stub",
        model="stub-vision",
        response=json.dumps(
            {
                "schema_version": 1,
                "platform": "eastmoney",
                "screenshot_type": "watchlist",
                "confidence": 0.9,
                "stocks": [
                    {
                        "code": "600519",
                        "name": "贵州茅台",
                        "is_holding": False,
                        "field_confidence": {"code": 0.95, "name": 0.95},
                    }
                ],
                "warnings": [],
            },
            ensure_ascii=False,
        ),
    )

    result = provider.complete(
        VisionRequest(
            image_path="/local/shot.png",
            mime_type="image/png",
            prompt="prompt",
            json_schema={},
            timeout_s=10,
            run_id="run",
        )
    )

    assert result.ok
    assert result.response is not None
    assert result.response.stocks[0].code == "600519"


def test_stub_provider_reports_schema_error():
    provider = StubVisionProvider(name="stub", model="stub-vision", response="{not json")
    result = provider.complete(
        VisionRequest(
            image_path="/local/shot.png",
            mime_type="image/png",
            prompt="prompt",
            json_schema={},
            timeout_s=10,
            run_id="run",
        )
    )

    assert not result.ok
    assert result.error is not None
    assert result.error.kind == "schema_parse"
```

- **Step 2: Run provider tests to verify failure**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_providers.py -q
```

Expected: FAIL because `prompts.py` and `providers.py` do not exist.

- **Step 3: Implement prompts**

Create `src/tools/screenshot_import/prompts.py`:

```python
from __future__ import annotations

from .models import ClassificationResult


PLATFORM_LABELS = {
    "ths": "同花顺",
    "eastmoney": "东方财富",
    "hk_panda": "香港熊猫证券",
    "other": "其他券商",
    "unknown": "未知券商",
}


def build_upload_disclosure(provider_name: str) -> str:
    return (
        f"即将把截图上传到 {provider_name} 的视觉模型进行识别。"
        "截图可能包含真实持仓、成本和股数。继续前请确认你接受该第三方处理。"
    )


def build_vision_prompt(classification: ClassificationResult) -> str:
    platform_label = PLATFORM_LABELS[classification.platform]
    type_label = "持仓" if classification.screenshot_type == "holding" else "自选"
    holding_rules = ""
    if classification.screenshot_type == "holding":
        holding_rules = (
            "持仓截图必须抽取股票代码、名称、成本价 cost、持仓股数 shares。"
            "不要把当前价、最新价、市值、浮盈亏、可用数量、涨跌幅当成成本价。"
        )
    else:
        holding_rules = "自选截图只抽取股票代码和名称，不要输出 cost 或 shares。"
    return (
        "你是一个股票截图结构化识别器。"
        f"这张图疑似来自 {platform_label}，类型疑似为{type_label}。"
        f"本地分类信号: {', '.join(classification.signals) or '无'}。"
        f"{holding_rules}"
        "港股代码统一输出为 HKxxxxx，A 股输出 6 位数字。"
        "只返回符合 JSON Schema 的 JSON，不要返回 Markdown 或解释文字。"
    )
```

- **Step 4: Implement provider abstraction**

Create `src/tools/screenshot_import/providers.py`:

```python
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Protocol

from openai import OpenAI
from pydantic import ValidationError

from .models import VisionResponse


@dataclass(frozen=True)
class VisionRequest:
    image_path: str
    mime_type: str
    prompt: str
    json_schema: dict[str, object]
    timeout_s: int
    run_id: str


@dataclass(frozen=True)
class ProviderError:
    kind: str
    message: str
    retryable: bool
    raw_redacted: str = ""


@dataclass(frozen=True)
class VisionResult:
    ok: bool
    provider: str
    model: str
    response: VisionResponse | None = None
    error: ProviderError | None = None


class VisionProvider(Protocol):
    name: str
    model: str

    def complete(self, request: VisionRequest) -> VisionResult:
        ...


class StubVisionProvider:
    def __init__(self, *, name: str, model: str, response: str):
        self.name = name
        self.model = model
        self._response = response

    def complete(self, request: VisionRequest) -> VisionResult:
        try:
            parsed_json = json.loads(self._response)
            response = VisionResponse.model_validate(parsed_json)
            return VisionResult(ok=True, provider=self.name, model=self.model, response=response)
        except (json.JSONDecodeError, ValidationError) as exc:
            return VisionResult(
                ok=False,
                provider=self.name,
                model=self.model,
                error=ProviderError(
                    kind="schema_parse",
                    message=str(exc),
                    retryable=True,
                    raw_redacted=self._response[:500],
                ),
            )


class OpenAICompatibleVisionProvider:
    def __init__(self, *, name: str, api_key: str, base_url: str, model: str):
        self.name = name
        self.model = model
        self._client = OpenAI(api_key=api_key, base_url=base_url)

    def complete(self, request: VisionRequest) -> VisionResult:
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "Return strict JSON only."},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": request.prompt},
                            {"type": "image_url", "image_url": {"url": f"file://{request.image_path}"}},
                        ],
                    },
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {
                        "name": "vision_stock_import_response_v1",
                        "strict": True,
                        "schema": request.json_schema,
                    },
                },
                timeout=request.timeout_s,
            )
            content = response.choices[0].message.content or "{}"
            parsed = VisionResponse.model_validate_json(content)
            return VisionResult(ok=True, provider=self.name, model=self.model, response=parsed)
        except (json.JSONDecodeError, ValidationError) as exc:
            return VisionResult(
                ok=False,
                provider=self.name,
                model=self.model,
                error=ProviderError(kind="schema_parse", message=str(exc), retryable=True),
            )
        except Exception as exc:
            return VisionResult(
                ok=False,
                provider=self.name,
                model=self.model,
                error=ProviderError(kind="transport", message=str(exc), retryable=True),
            )


def create_provider(provider: str) -> VisionProvider:
    if provider == "kimi":
        return OpenAICompatibleVisionProvider(
            name="kimi",
            api_key=os.environ["KIMI_API_KEY"],
            base_url=os.environ.get("KIMI_BASE_URL", "https://api.moonshot.cn/v1"),
            model=os.environ.get("KIMI_VISION_MODEL", os.environ.get("KIMI_MODEL", "moonshot-v1-8k-vision-preview")),
        )
    if provider == "glm":
        return OpenAICompatibleVisionProvider(
            name="glm",
            api_key=os.environ["GLM_API_KEY"],
            base_url=os.environ.get("GLM_BASE_URL", "https://open.bigmodel.cn/api/paas/v4"),
            model=os.environ.get("GLM_VISION_MODEL", os.environ.get("GLM_MODEL", "glm-4v-flash")),
        )
    if provider == "minimax":
        return OpenAICompatibleVisionProvider(
            name="minimax",
            api_key=os.environ["MINIMAX_API_KEY"],
            base_url=os.environ.get("MINIMAX_BASE_URL", "https://api.minimax.chat/v1"),
            model=os.environ.get("MINIMAX_VISION_MODEL", os.environ.get("MINIMAX_MODEL", "MiniMax-Text-01")),
        )
    raise ValueError(f"unsupported provider: {provider}")
```

- **Step 5: Run provider tests to verify pass**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_providers.py -q
```

Expected: PASS.

- **Step 6: Commit Task 4**

```bash
git add src/tools/screenshot_import/prompts.py src/tools/screenshot_import/providers.py src/tools/test_screenshot_import_providers.py
git commit -m "feat: add screenshot import vision providers"
```

---

### Task 5: Validator And Import Planner

**Files:**

- Create: `src/tools/screenshot_import/validator.py`
- Create: `src/tools/screenshot_import/planner.py`
- Test: `src/tools/test_screenshot_import_validator.py`
- Test: `src/tools/test_screenshot_import_planner.py`
- **Step 1: Write validator tests**

Create `src/tools/test_screenshot_import_validator.py`:

```python
from src.tools.screenshot_import.models import FieldConfidence, HoldingStockRow, WatchlistStockRow
from src.tools.screenshot_import.validator import normalize_code, normalize_row


def test_normalize_hk_code_variants():
    assert normalize_code("00700") == "HK00700"
    assert normalize_code("HK.700") == "HK00700"
    assert normalize_code("HK00700") == "HK00700"


def test_normalize_a_share_code_keeps_six_digits():
    assert normalize_code("600519") == "600519"


def test_normalize_holding_row_preserves_cost_and_shares():
    row = HoldingStockRow(
        code="00700",
        name="腾讯控股",
        is_holding=True,
        cost=320.5,
        shares=100,
        field_confidence=FieldConfidence(code=0.9, name=0.9, cost=0.9, shares=0.9),
    )

    normalized = normalize_row(row, source_row_index=2)

    assert normalized.code == "HK00700"
    assert normalized.cost == 320.5
    assert normalized.shares == 100


def test_normalize_watchlist_has_no_holding_fields():
    row = WatchlistStockRow(
        code="600519",
        name="贵州茅台",
        is_holding=False,
        field_confidence=FieldConfidence(code=0.9, name=0.9),
    )

    normalized = normalize_row(row, source_row_index=1)

    assert normalized.code == "600519"
    assert normalized.cost is None
    assert normalized.shares is None
```

- **Step 2: Write planner tests**

Create `src/tools/test_screenshot_import_planner.py`:

```python
from src.tools.screenshot_import.models import ClassificationResult, FieldConfidence, NormalizedRow
from src.tools.screenshot_import.planner import build_import_plan, compute_actionable_rows_hash


def test_high_confidence_holding_enters_auto_apply():
    row = NormalizedRow(
        code="HK00700",
        name="腾讯控股",
        is_holding=True,
        cost=320.5,
        shares=100,
        field_confidence=FieldConfidence(code=0.95, name=0.95, cost=0.95, shares=0.95),
    )

    plan = build_import_plan(
        rows=[row],
        provider="kimi",
        model="vision",
        classification=ClassificationResult(
            platform="eastmoney",
            screenshot_type="holding",
            confidence=0.9,
            signals=["top_dark_bar"],
        ),
        threshold=0.8,
        content_fingerprint="sha256:image",
        existing_codes=set(),
        manual_platform_after_low_confidence=False,
    )

    assert [a.code for a in plan.auto_apply.actions] == ["HK00700"]
    assert plan.needs_confirmation.actions == []


def test_manual_platform_after_low_confidence_blocks_holding_auto_apply():
    row = NormalizedRow(
        code="HK00700",
        name="腾讯控股",
        is_holding=True,
        cost=320.5,
        shares=100,
        field_confidence=FieldConfidence(code=0.95, name=0.95, cost=0.95, shares=0.95),
    )

    plan = build_import_plan(
        rows=[row],
        provider="kimi",
        model="vision",
        classification=ClassificationResult(
            platform="eastmoney",
            screenshot_type="holding",
            confidence=0.5,
            signals=["manual_platform"],
        ),
        threshold=0.8,
        content_fingerprint="sha256:image",
        existing_codes=set(),
        manual_platform_after_low_confidence=True,
    )

    assert plan.auto_apply.actions == []
    assert plan.needs_confirmation.actions[0].reason_codes == ["manual_platform_low_confidence"]


def test_actionable_rows_hash_ignores_apply_log():
    rows = [
        NormalizedRow(
            code="600519",
            name="贵州茅台",
            is_holding=False,
            field_confidence=FieldConfidence(code=0.95, name=0.95),
        )
    ]

    assert compute_actionable_rows_hash(rows) == compute_actionable_rows_hash(rows)
```

- **Step 3: Run validator/planner tests to verify failure**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_validator.py src/tools/test_screenshot_import_planner.py -q
```

Expected: FAIL because `validator.py` and `planner.py` do not exist.

- **Step 4: Implement validator**

Create `src/tools/screenshot_import/validator.py`:

```python
from __future__ import annotations

import re

from .models import HoldingStockRow, NormalizedRow, StockRow


def normalize_code(raw: str) -> str:
    text = raw.strip().upper().replace(".", "").replace(" ", "")
    if text.startswith("HK"):
        digits = re.sub(r"\D", "", text[2:])
        if 1 <= len(digits) <= 5:
            return f"HK{int(digits):05d}"
    if re.fullmatch(r"0\d{4}", text):
        return f"HK{text}"
    if re.fullmatch(r"\d{1,5}", text) and text.startswith("0"):
        return f"HK{int(text):05d}"
    if re.fullmatch(r"\d{6}", text):
        return text
    raise ValueError(f"unsupported stock code: {raw}")


def normalize_row(row: StockRow, *, source_row_index: int | None = None) -> NormalizedRow:
    code = normalize_code(row.code)
    if isinstance(row, HoldingStockRow):
        return NormalizedRow(
            code=code,
            name=row.name,
            is_holding=True,
            cost=float(row.cost),
            shares=int(row.shares),
            field_confidence=row.field_confidence,
            source_row_index=source_row_index,
        )
    return NormalizedRow(
        code=code,
        name=row.name,
        is_holding=False,
        field_confidence=row.field_confidence,
        source_row_index=source_row_index,
    )
```

- **Step 5: Implement planner**

Create `src/tools/screenshot_import/planner.py`:

```python
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from .models import (
    ClassificationResult,
    ImportPlan,
    NormalizedRow,
    PlanGroup,
    PlannedAction,
    ReasonCode,
)


def _canonical_json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(payload: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


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


def compute_actionable_rows_hash(rows: list[NormalizedRow]) -> str:
    normalized = [_row_payload(row) for row in sorted(rows, key=lambda item: item.code)]
    return _sha256(normalized)


def _min_required_confidence(row: NormalizedRow) -> float:
    values = [row.field_confidence.code]
    if row.field_confidence.name is not None:
        values.append(row.field_confidence.name)
    if row.is_holding:
        values.append(row.field_confidence.cost or 0.0)
        values.append(row.field_confidence.shares or 0.0)
    return min(values)


def _action_for(row: NormalizedRow, existing_codes: set[str]) -> str:
    return "add" if row.code not in existing_codes or row.is_holding else "update"


def _row_apply_id(import_run_id: str, row: NormalizedRow, action: str) -> str:
    return _sha256({"run": import_run_id, "action": action, "payload": _row_payload(row)})


def build_import_plan(
    *,
    rows: list[NormalizedRow],
    provider: str,
    model: str,
    classification: ClassificationResult,
    threshold: float,
    content_fingerprint: str,
    existing_codes: set[str],
    manual_platform_after_low_confidence: bool,
) -> ImportPlan:
    import_run_id = str(uuid.uuid4())
    auto: list[PlannedAction] = []
    confirm: list[PlannedAction] = []
    rejected: list[PlannedAction] = []
    seen: set[str] = set()
    duplicates = {row.code for row in rows if row.code in seen or seen.add(row.code)}
    for index, row in enumerate(sorted(rows, key=lambda item: item.code), start=1):
        reason_codes: list[ReasonCode] = []
        if classification.confidence < threshold:
            reason_codes.append("below_classifier_threshold")
        if _min_required_confidence(row) < threshold:
            reason_codes.append("below_field_threshold")
        if row.code in duplicates:
            reason_codes.append("duplicate_code_conflict")
        if manual_platform_after_low_confidence and row.is_holding:
            reason_codes.append("manual_platform_low_confidence")
        action = _action_for(row, existing_codes)
        planned = PlannedAction(
            row_apply_id=_row_apply_id(import_run_id, row, action),
            plan_sequence=index,
            action=action,  # type: ignore[arg-type]
            code=row.code,
            payload=_row_payload(row),
            reason_codes=reason_codes,
        )
        if "invalid_code" in reason_codes or "missing_required_field" in reason_codes:
            rejected.append(planned)
        elif reason_codes:
            confirm.append(planned)
        else:
            auto.append(planned)
    plan_without_hashes = {
        "import_run_id": import_run_id,
        "provider": provider,
        "model": model,
        "classification": classification.model_dump(),
        "rows": [_row_payload(row) for row in rows],
    }
    actionable_hash = compute_actionable_rows_hash(rows)
    plan_hash = _sha256(plan_without_hashes | {"actionable_rows_hash": actionable_hash})
    return ImportPlan(
        import_run_id=import_run_id,
        created_at=datetime.now(timezone.utc).isoformat(),
        provider=provider,
        model=model,
        platform=classification.platform,
        screenshot_type=classification.screenshot_type,
        classification=classification,
        thresholds={"confirm": threshold},
        content_fingerprint=content_fingerprint,
        plan_hash=plan_hash,
        actionable_rows_hash=actionable_hash,
        auto_apply=PlanGroup(actions=auto),
        needs_confirmation=PlanGroup(actions=confirm),
        rejected=PlanGroup(actions=rejected),
        apply_log=[],
    )
```

- **Step 6: Run validator/planner tests to verify pass**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_validator.py src/tools/test_screenshot_import_planner.py -q
```

Expected: PASS.

- **Step 7: Commit Task 5**

```bash
git add src/tools/screenshot_import/validator.py src/tools/screenshot_import/planner.py src/tools/test_screenshot_import_validator.py src/tools/test_screenshot_import_planner.py
git commit -m "feat: plan safe screenshot imports"
```

---

### Task 6: Config API Client And Apply Logs

**Files:**

- Create: `src/tools/screenshot_import/config_api.py`
- Test: `src/tools/test_screenshot_import_config_api.py`
- **Step 1: Write config API tests**

Create `src/tools/test_screenshot_import_config_api.py`:

```python
import json

import pytest

from src.tools.screenshot_import.config_api import ConfigApiClient, ConfigApiError
from src.tools.screenshot_import.models import PlanGroup, PlannedAction


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"success": True}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, timeout):
        self.calls.append(("GET", url, None))
        return FakeResponse(payload={"watchlist": {}})

    def post(self, url, json, timeout, headers=None):
        self.calls.append(("POST", url, json))
        return self.responses.pop(0)


def _action(code="HK00700"):
    return PlannedAction(
        row_apply_id="row-1",
        plan_sequence=1,
        action="add",
        code=code,
        payload={"code": code, "name": "腾讯控股", "type": "holding", "cost": 320.5, "shares": 100},
        reason_codes=[],
    )


def test_config_api_rejects_remote_http_url():
    with pytest.raises(ConfigApiError):
        ConfigApiClient("http://example.com/api/config")


def test_apply_posts_auto_apply_rows_to_loopback():
    session = FakeSession([FakeResponse()])
    client = ConfigApiClient("http://127.0.0.1:3120/api/config", session=session)

    result = client.apply_actions([_action()], import_run_id="run")

    assert result.failed == []
    assert result.applied[0].code == "HK00700"
    assert session.calls[-1][2]["action"] == "add"


def test_apply_collects_partial_failure():
    session = FakeSession([FakeResponse(status_code=500, payload={"success": False, "error": "boom"})])
    client = ConfigApiClient("http://127.0.0.1:3120/api/config", session=session)

    result = client.apply_actions([_action()], import_run_id="run")

    assert result.applied == []
    assert result.failed[0].code == "HK00700"
```

- **Step 2: Run config API tests to verify failure**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_config_api.py -q
```

Expected: FAIL because `config_api.py` does not exist.

- **Step 3: Implement config API client**

Create `src/tools/screenshot_import/config_api.py`:

```python
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import requests

from . import DEFAULT_API_URL
from .models import PlannedAction


class ConfigApiError(RuntimeError):
    """Raised for unsafe or unavailable config API operations."""


@dataclass(frozen=True)
class AppliedRow:
    code: str
    status_code: int
    message: str = ""


@dataclass(frozen=True)
class ApplyResult:
    applied: list[AppliedRow]
    failed: list[AppliedRow]


class ConfigApiClient:
    def __init__(self, base_url: str = DEFAULT_API_URL, *, session=None, timeout_s: int = 10):
        parsed = urlparse(base_url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise ConfigApiError("remote config API URLs are disabled by default")
        self.base_url = base_url
        self.session = session or requests.Session()
        self.timeout_s = timeout_s

    def fetch_watchlist(self) -> dict[str, object]:
        response = self.session.get(self.base_url, timeout=self.timeout_s)
        if response.status_code >= 400:
            raise ConfigApiError(f"GET /api/config failed: {response.status_code}")
        payload = response.json()
        watchlist = payload.get("watchlist", {})
        return watchlist if isinstance(watchlist, dict) else {}

    def apply_actions(self, actions: list[PlannedAction], *, import_run_id: str) -> ApplyResult:
        applied: list[AppliedRow] = []
        failed: list[AppliedRow] = []
        for action in sorted(actions, key=lambda item: item.plan_sequence):
            current = self.fetch_watchlist()
            exists = action.code in current
            api_action = "add" if action.action == "add" or not exists else "update"
            body = {
                "action": api_action,
                **action.payload,
                "audit_context": {
                    "source": "screenshot_stock_import",
                    "import_run_id": import_run_id,
                    "row_apply_id": action.row_apply_id,
                },
            }
            response = self.session.post(self.base_url, json=body, timeout=self.timeout_s, headers={})
            ok = response.status_code < 400
            try:
                payload = response.json()
                ok = ok and bool(payload.get("success", True))
                message = str(payload.get("error", ""))
            except Exception:
                message = response.text[:200]
            target = applied if ok else failed
            target.append(AppliedRow(code=action.code, status_code=response.status_code, message=message))
        return ApplyResult(applied=applied, failed=failed)
```

- **Step 4: Run config API tests to verify pass**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_config_api.py -q
```

Expected: PASS.

- **Step 5: Commit Task 6**

```bash
git add src/tools/screenshot_import/config_api.py src/tools/test_screenshot_import_config_api.py
git commit -m "feat: apply screenshot imports through config api"
```

---

### Task 7: CLI Orchestration And Legacy Boundary

**Files:**

- Modify: `src/tools/screenshot_stock_import.py`
- Test: `src/tools/test_screenshot_stock_import_cli.py`
- **Step 1: Write CLI tests**

Create `src/tools/test_screenshot_stock_import_cli.py`:

```python
import json

from PIL import Image

from src.tools import screenshot_stock_import as cli


def test_cli_dry_run_writes_plan_without_apply(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    Image.new("RGB", (200, 300), "white").save(image)
    out = tmp_path / "plan.json"

    class FakeProvider:
        name = "stub"
        model = "stub-model"

        def complete(self, request):
            from src.tools.screenshot_import.providers import VisionResult
            from src.tools.screenshot_import.models import VisionResponse

            response = VisionResponse.model_validate(
                {
                    "schema_version": 1,
                    "platform": "hk_panda",
                    "screenshot_type": "watchlist",
                    "confidence": 0.95,
                    "stocks": [
                        {
                            "code": "600519",
                            "name": "贵州茅台",
                            "is_holding": False,
                            "field_confidence": {"code": 0.95, "name": 0.95},
                        }
                    ],
                    "warnings": [],
                }
            )
            return VisionResult(ok=True, provider="stub", model="stub-model", response=response)

    monkeypatch.setattr(cli, "create_provider", lambda provider: FakeProvider())

    exit_code = cli.main([str(image), "--provider", "kimi", "--dry-run", "--output-json", str(out), "--allow-path", str(tmp_path)])

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["auto_apply"]["actions"][0]["code"] == "600519"


def test_cli_apply_requires_plan(tmp_path):
    exit_code = cli.main(["--apply"])

    assert exit_code == 1
```

- **Step 2: Run CLI tests to verify failure**

Run:

```bash
uv run pytest src/tools/test_screenshot_stock_import_cli.py -q
```

Expected: FAIL because the current CLI does not support `argv`, `--dry-run`, `--apply`, or `--output-json`.

- **Step 3: Replace runtime CLI while preserving legacy helpers**

Modify the bottom half of `src/tools/screenshot_stock_import.py`. Keep existing `extract_text_from_image`, `parse_stocks_from_text`, and `import_stocks_to_db`. Replace `main()` with:

```python
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="从截图导入股票信息")
    parser.add_argument("image_path", nargs="?", type=Path, help="截图文件路径")
    parser.add_argument("--provider", choices=["auto", "kimi", "glm", "minimax"], default="auto")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--confirm-threshold", type=float, default=0.8)
    parser.add_argument("--type", choices=["auto", "holding", "watchlist"], default="auto")
    parser.add_argument("--platform", choices=["auto", "ths", "eastmoney", "hk_panda", "other"], default="auto")
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--debug-dir", type=Path)
    parser.add_argument("--debug-sensitive", action="store_true")
    parser.add_argument("--allow-path", action="append", type=Path, default=[])
    return parser


def _allowed_roots(args: argparse.Namespace) -> list[Path]:
    roots = [PROJECT_ROOT, Path.home() / "Downloads"]
    roots.extend(args.allow_path)
    return roots


def _schema() -> dict[str, Any]:
    from src.tools.screenshot_import.models import VisionResponse

    return VisionResponse.model_json_schema()


def _write_plan(path: Path, plan) -> None:
    from src.tools.screenshot_import.path_safety import atomic_write_json

    atomic_write_json(path, plan.model_dump(mode="json"))


def run_recognition(args: argparse.Namespace) -> int:
    import hashlib

    from src.tools.screenshot_import.classifier import classify_fingerprint, extract_fingerprint
    from src.tools.screenshot_import.path_safety import validate_existing_image_path, validate_output_path
    from src.tools.screenshot_import.planner import build_import_plan
    from src.tools.screenshot_import.prompts import build_upload_disclosure, build_vision_prompt
    from src.tools.screenshot_import.providers import create_provider
    from src.tools.screenshot_import.validator import normalize_row

    roots = _allowed_roots(args)
    image_path = validate_existing_image_path(args.image_path, allowed_roots=roots)
    fingerprint = extract_fingerprint(image_path)
    classification = classify_fingerprint(
        fingerprint,
        forced_platform=args.platform,
        forced_type=args.type,
    )
    if classification.confidence < args.confirm_threshold and args.yes and args.platform == "auto":
        print(json.dumps({"needs_user_input": True, "classification": classification.model_dump()}, ensure_ascii=False))
        return 3
    provider_name = "kimi" if args.provider == "auto" else args.provider
    print(build_upload_disclosure(provider_name))
    provider = create_provider(provider_name)
    prompt = build_vision_prompt(classification)
    result = provider.complete(
        VisionRequest(
            image_path=str(image_path),
            mime_type="image/png",
            prompt=prompt,
            json_schema=_schema(),
            timeout_s=60,
            run_id="recognition",
        )
    )
    if not result.ok or result.response is None:
        print(result.error.message if result.error else "provider failed")
        return 4
    rows = [normalize_row(row, source_row_index=index) for index, row in enumerate(result.response.stocks)]
    plan = build_import_plan(
        rows=rows,
        provider=result.provider,
        model=result.model,
        classification=classification,
        threshold=args.confirm_threshold,
        content_fingerprint="sha256:" + hashlib.sha256(image_path.read_bytes()).hexdigest(),
        existing_codes=set(),
        manual_platform_after_low_confidence=classification.confidence < args.confirm_threshold and args.platform != "auto",
    )
    output_path = args.output_json or PROJECT_ROOT / ".screenshot_import_runs" / plan.import_run_id / "import_plan.json"
    output_path = validate_output_path(output_path, allowed_roots=roots)
    _write_plan(output_path, plan)
    print(f"import plan written: {output_path}")
    return 0


def run_apply(args: argparse.Namespace) -> int:
    from src.tools.screenshot_import.config_api import ConfigApiClient
    from src.tools.screenshot_import.models import ImportPlan

    if args.plan is None:
        print("--apply requires --plan")
        return 1
    payload = json.loads(args.plan.read_text(encoding="utf-8"))
    plan = ImportPlan.model_validate(payload)
    client = ConfigApiClient()
    result = client.apply_actions(plan.auto_apply.actions, import_run_id=plan.import_run_id)
    for row in result.applied:
        print(f"applied {row.code}")
    for row in result.failed:
        print(f"failed {row.code}: {row.message}")
    return 6 if result.failed else 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.apply:
            return run_apply(args)
        if args.image_path is None:
            print("image_path is required unless --apply is used")
            return 1
        return run_recognition(args)
    except Exception as exc:
        print(f"错误: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
```

Also add the missing import inside `run_recognition`:

```python
from src.tools.screenshot_import.providers import VisionRequest
```

- **Step 4: Run CLI tests**

Run:

```bash
uv run pytest src/tools/test_screenshot_stock_import_cli.py -q
```

Expected: PASS.

- **Step 5: Run legacy writer tests**

Run:

```bash
uv run pytest src/tools/test_screenshot_stock_import.py -q
```

Expected: PASS. This proves legacy helper behavior remains intact for now.

- **Step 6: Commit Task 7**

```bash
git add src/tools/screenshot_stock_import.py src/tools/test_screenshot_stock_import_cli.py
git commit -m "feat: add frozen-plan screenshot import cli"
```

---

### Task 8: Debug Artifacts And Skill Documentation

**Files:**

- Create: `src/tools/screenshot_import/debug_artifacts.py`
- Modify: `.claude/skills/screenshot-stock-import/SKILL.md`
- Test: extend `src/tools/test_screenshot_import_planner.py`
- **Step 1: Add debug artifact test**

Append to `src/tools/test_screenshot_import_planner.py`:

```python
from src.tools.screenshot_import.debug_artifacts import tier_a_debug_payload


def test_tier_a_debug_payload_redacts_sensitive_fields():
    payload = tier_a_debug_payload(
        provider="kimi",
        codes=["HK00700"],
        confidence_stats={"min": 0.9},
        plan_summary={"auto_apply_count": 1, "needs_confirmation_count": 0, "rejected_count": 0},
    )

    dumped = str(payload)
    assert "HK00700" not in dumped
    assert "cost" not in dumped
    assert "shares" not in dumped
    assert payload["provider"] == "kimi"
```

- **Step 2: Run debug test to verify failure**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_planner.py::test_tier_a_debug_payload_redacts_sensitive_fields -q
```

Expected: FAIL because `debug_artifacts.py` does not exist.

- **Step 3: Implement debug redaction helper**

Create `src/tools/screenshot_import/debug_artifacts.py`:

```python
from __future__ import annotations

import hashlib


def _hash_code(code: str) -> str:
    return "sha256:" + hashlib.sha256(code.encode("utf-8")).hexdigest()


def tier_a_debug_payload(
    *,
    provider: str,
    codes: list[str],
    confidence_stats: dict[str, float],
    plan_summary: dict[str, int],
) -> dict[str, object]:
    return {
        "tier": "A",
        "provider": provider,
        "code_hashes": [_hash_code(code) for code in codes],
        "confidence_stats": confidence_stats,
        "plan_summary": plan_summary,
    }
```

- **Step 4: Update skill documentation**

Replace `.claude/skills/screenshot-stock-import/SKILL.md` with:

```markdown
---
name: screenshot-stock-import
description: 从截图中识别股票信息并导入系统。支持持仓截图（更新成本和股数）和自选截图（添加股票到自选列表）
user-invocable: true
---

# 截图股票导入

从截图中自动识别股票信息，根据截图类型生成 frozen import plan，再通过配置 API 应用高置信结果。

## 核心规则

- 必须调用 `src.tools.screenshot_stock_import`，不要让多个 agent 直接读图猜结果。
- 识别阶段只生成 plan，不写配置。
- 写入阶段只允许 `--apply --plan <import_plan.json>`，并且只应用 `auto_apply`。
- 低置信、冲突、name-only、或手动指定券商后的持仓结果必须让用户确认。
- 禁止直接修改 JSON 或 SQLite。

## 推荐流程

1. 先 dry-run：

```bash
uv run python -m src.tools.screenshot_stock_import /path/to/screenshot.png \
  --provider auto \
  --dry-run
```

1. 查看输出的 `import_plan.json`：
  - `auto_apply`: 可应用的高置信项目
  - `needs_confirmation`: 需要用户确认
  - `rejected`: 不应导入
2. 如果脚本提示无法确认券商，询问用户属于：
  - 同花顺
  - 东方财富
  - 香港熊猫
  - 其他
   然后带参数重跑：

```bash
uv run python -m src.tools.screenshot_stock_import /path/to/screenshot.png \
  --provider auto \
  --platform eastmoney \
  --type holding \
  --dry-run
```

1. 用户确认 plan 后再应用：

```bash
uv run python -m src.tools.screenshot_stock_import \
  --apply \
  --plan .screenshot_import_runs/<import_run_id>/import_plan.json \
  --yes
```

## 隐私提醒

视觉识别会把截图发送给所选 provider。截图可能包含真实持仓、成本和股数。发送前必须让用户知道。

Debug artifact 默认关闭。Tier A 只允许脱敏摘要；完整 provider response 需要显式 `--debug-sensitive`。

```

- [ ] **Step 5: Run debug and skill-related tests**

Run:

```bash
uv run pytest src/tools/test_screenshot_import_planner.py::test_tier_a_debug_payload_redacts_sensitive_fields -q
```

Expected: PASS.

- **Step 6: Commit Task 8**

```bash
git add src/tools/screenshot_import/debug_artifacts.py src/tools/test_screenshot_import_planner.py .claude/skills/screenshot-stock-import/SKILL.md
git commit -m "docs: update screenshot import skill workflow"
```

---

### Task 9: Full Verification And Cleanup

**Files:**

- Modify only files required by failures from the commands below.
- **Step 1: Run focused screenshot import tests**

Run:

```bash
uv run pytest \
  src/tools/test_screenshot_import_models.py \
  src/tools/test_screenshot_import_path_safety.py \
  src/tools/test_screenshot_import_classifier.py \
  src/tools/test_screenshot_import_providers.py \
  src/tools/test_screenshot_import_validator.py \
  src/tools/test_screenshot_import_planner.py \
  src/tools/test_screenshot_import_config_api.py \
  src/tools/test_screenshot_stock_import_cli.py \
  src/tools/test_screenshot_stock_import.py \
  -q
```

Expected: PASS.

- **Step 2: Run smoke sim-trading tests to guard DB helpers**

Run:

```bash
uv run pytest src/sim_trading/test_sim_trading.py -m smoke -q
```

Expected: PASS.

- **Step 3: Run static import check**

Run:

```bash
uv run python -m src.tools.screenshot_stock_import --help
```

Expected: exits `0` and prints CLI options including `--provider`, `--dry-run`, `--apply`, `--plan`, and `--debug-sensitive`.

- **Step 4: Inspect git diff for forbidden writes**

Run:

```bash
git diff -- src/tools/screenshot_stock_import.py src/tools/screenshot_import .claude/skills/screenshot-stock-import/SKILL.md pyproject.toml
```

Expected: runtime CLI applies through `ConfigApiClient`, not `import_stocks_to_db`; legacy DB helper remains only as a callable helper for existing tests.

- **Step 5: Commit verification fixes**

If Step 1-4 required fixes, commit them:

```bash
git add src/tools/screenshot_stock_import.py src/tools/screenshot_import src/tools/test_screenshot_import_*.py src/tools/test_screenshot_stock_import_cli.py .claude/skills/screenshot-stock-import/SKILL.md pyproject.toml
git commit -m "test: verify screenshot import vision workflow"
```

If no files changed, skip this commit.

---

## Self-Review

Spec coverage:

- Config updates through `POST /api/config`: Task 6 and Task 7.
- Frozen plan preview/apply separation: Task 5 and Task 7.
- Structured JSON/Pydantic schema: Task 1.
- Color/layout classification: Task 3.
- Prompt/provider abstraction: Task 4.
- Validation, confidence, name-only, low-confidence manual platform rules: Task 5.
- Debug privacy tiers: Task 8.
- Skill update: Task 8.
- Legacy OCR/direct DB boundary: Task 7 and Task 9.

No placeholders are left in the plan. All named functions and classes are introduced before later tasks reference them. The plan keeps commits task-sized and test-first.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-04-30-screenshot-stock-import-vision-implementation.md`. Two execution options:

**1. Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?