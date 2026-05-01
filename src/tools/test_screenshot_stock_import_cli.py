from __future__ import annotations

import json

from PIL import Image

from src.tools import screenshot_stock_import as cli
from src.tools.screenshot_import.models import ClassificationResult, PlatformCandidate


class _StubConfigApiEmptyWatchlist:
    def fetch_watchlist(self):
        return {}


def test_cli_dry_run_writes_plan_without_apply(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    Image.new("RGB", (200, 300), "white").save(image)
    out = tmp_path / "plan.json"

    monkeypatch.setattr(
        cli,
        "classify_fingerprint",
        lambda *args, **kwargs: ClassificationResult(
            platform="hk_panda",
            screenshot_type="watchlist",
            confidence=0.95,
            signals=[],
            candidate_platforms=[
                PlatformCandidate(platform="hk_panda", confidence=0.95),
            ],
        ),
    )

    monkeypatch.setattr(cli, "ConfigApiClient", lambda: _StubConfigApiEmptyWatchlist())

    class FakeProvider:
        name = "stub"
        model = "stub-model"

        def complete(self, request):
            from src.tools.screenshot_import.models import VisionResponse
            from src.tools.screenshot_import.providers import VisionResult

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
            return VisionResult(
                ok=True, provider="stub", model="stub-model", response=response
            )

    monkeypatch.setattr(cli, "create_provider", lambda provider: FakeProvider())
    exit_code = cli.main(
        [
            str(image),
            "--provider",
            "kimi",
            "--dry-run",
            "--output-json",
            str(out),
            "--allow-path",
            str(tmp_path),
        ]
    )
    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["auto_apply"][0]["code"] == "600519"


def test_cli_noninteractive_low_classification_exits_3_without_provider(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    Image.new("RGB", (200, 300), "white").save(image)
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    monkeypatch.setattr(
        cli,
        "classify_fingerprint",
        lambda *a, **k: ClassificationResult(
            platform="unknown",
            screenshot_type="watchlist",
            confidence=0.3,
            signals=["no_strong_classifier_signal"],
            candidate_platforms=[
                PlatformCandidate(platform="ths", confidence=0.4),
                PlatformCandidate(platform="eastmoney", confidence=0.38),
            ],
        ),
    )

    def _should_not_create_provider(_p):
        raise AssertionError("create_provider must not be called")

    monkeypatch.setattr(cli, "create_provider", _should_not_create_provider)

    exit_code = cli.main(
        [
            str(image),
            "--dry-run",
            "--output-json",
            str(tmp_path / "out.json"),
            "--allow-path",
            str(tmp_path),
        ]
    )
    assert exit_code == 3
    assert not (tmp_path / "out.json").exists()


def test_cli_schema_parse_failure_writes_raw_response_sidecar(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    Image.new("RGB", (200, 300), "white").save(image)
    out = tmp_path / "plan.json"

    monkeypatch.setattr(
        cli,
        "classify_fingerprint",
        lambda *args, **kwargs: ClassificationResult(
            platform="ths",
            screenshot_type="holding",
            confidence=0.95,
            signals=[],
            candidate_platforms=[PlatformCandidate(platform="ths", confidence=0.95)],
        ),
    )
    monkeypatch.setattr(cli, "ConfigApiClient", lambda: _StubConfigApiEmptyWatchlist())

    class FakeProvider:
        def complete(self, request):
            from src.tools.screenshot_import.providers import ProviderError, VisionResult

            return VisionResult(
                ok=False,
                provider="stub",
                model="stub-model",
                error=ProviderError(
                    kind="schema_parse",
                    message="bad json",
                    retryable=False,
                    raw_response='{"platform":"ths","stocks":[bad]}',
                ),
            )

    monkeypatch.setattr(cli, "create_provider", lambda provider: FakeProvider())

    exit_code = cli.main(
        [
            str(image),
            "--provider",
            "kimi",
            "--dry-run",
            "--output-json",
            str(out),
            "--allow-path",
            str(tmp_path),
        ]
    )

    assert exit_code == 4
    raw_path = tmp_path / "plan.provider_raw_response.txt"
    assert raw_path.read_text(encoding="utf-8") == '{"platform":"ths","stocks":[bad]}'


def test_cli_ignores_provider_code_when_classified_as_eastmoney(tmp_path, monkeypatch):
    image = tmp_path / "shot.png"
    Image.new("RGB", (200, 300), "white").save(image)
    out = tmp_path / "plan.json"

    monkeypatch.setattr(
        cli,
        "classify_fingerprint",
        lambda *args, **kwargs: ClassificationResult(
            platform="eastmoney",
            screenshot_type="holding",
            confidence=0.95,
            signals=[],
            candidate_platforms=[PlatformCandidate(platform="eastmoney", confidence=0.95)],
        ),
    )
    class _StubConfigApiTencent:
        def fetch_watchlist(self):
            return {"HK00700": {"name": "腾讯控股"}}

    monkeypatch.setattr(cli, "ConfigApiClient", lambda: _StubConfigApiTencent())

    class FakeProvider:
        name = "stub"
        model = "stub-model"

        def complete(self, request):
            from src.tools.screenshot_import.models import VisionResponse
            from src.tools.screenshot_import.providers import VisionResult

            response = VisionResponse.model_validate(
                {
                    "schema_version": 1,
                    "platform": "unknown",
                    "screenshot_type": "holding",
                    "confidence": 0.95,
                    "stocks": [
                        {
                            "code": "HK00700",
                            "name": "腾讯控股",
                            "is_holding": True,
                            "cost": 629.61,
                            "shares": 100,
                            "field_confidence": {
                                "code": 0.99,
                                "name": 0.99,
                                "cost": 0.99,
                                "shares": 0.99,
                            },
                        }
                    ],
                    "warnings": [],
                }
            )
            return VisionResult(
                ok=True, provider="stub", model="stub-model", response=response
            )

    monkeypatch.setattr(cli, "create_provider", lambda provider: FakeProvider())

    exit_code = cli.main(
        [
            str(image),
            "--provider",
            "kimi",
            "--dry-run",
            "--output-json",
            str(out),
            "--allow-path",
            str(tmp_path),
        ]
    )

    assert exit_code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["auto_apply"] == []
    action = payload["needs_confirmation"][0]
    assert action["code"] == "HK00700"
    assert "name_only_match" in action["reason_codes"]


def test_cli_apply_plan_hash_mismatch_exits_5_without_client(tmp_path, monkeypatch):
    from src.tools.screenshot_import.models import FieldConfidence, NormalizedRow
    from src.tools.screenshot_import.planner import build_import_plan

    row = NormalizedRow(
        code="600519",
        name="贵州茅台",
        is_holding=False,
        field_confidence=FieldConfidence(code=0.99, name=0.99),
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
        model="m",
        classification=classification,
        model_confidence=0.95,
        threshold=0.8,
        content_fingerprint="cfp",
        existing_codes=frozenset(),
        manual_platform_after_low_confidence=False,
    )
    plan_path = tmp_path / "plan.json"
    raw = plan.model_dump(mode="json")
    raw["platform"] = "eastmoney"
    plan_path.write_text(json.dumps(raw), encoding="utf-8")

    class _BoomApi:
        def apply_actions(self, *a, **k):
            raise AssertionError("apply_actions must not run")

    monkeypatch.setattr(cli, "ConfigApiClient", lambda: _BoomApi())
    exit_code = cli.main(
        ["--apply", "--plan", str(plan_path), "--allow-path", str(tmp_path)]
    )
    assert exit_code == 5


def test_cli_debug_dir_non_sensitive_writes_tier_a_not_provider_response(tmp_path, monkeypatch):
    from src.tools.screenshot_import import debug_artifacts as da
    from src.tools.screenshot_import.models import VisionResponse
    from src.tools.screenshot_import.providers import VisionResult

    image = tmp_path / "shot.png"
    Image.new("RGB", (200, 400), "white").save(image)
    out = tmp_path / "plan.json"
    dbg = tmp_path / "dbg"

    monkeypatch.setattr(
        cli,
        "classify_fingerprint",
        lambda *a, **k: ClassificationResult(
            platform="hk_panda",
            screenshot_type="watchlist",
            confidence=0.95,
            signals=[],
            candidate_platforms=[PlatformCandidate(platform="hk_panda", confidence=0.95)],
        ),
    )

    monkeypatch.setattr(cli, "ConfigApiClient", lambda: _StubConfigApiEmptyWatchlist())

    class FakeProvider:
        name = "stub"
        model = "stub-model"

        def complete(self, request):
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
    code = cli.main(
        [
            str(image),
            "--provider",
            "kimi",
            "--dry-run",
            "--output-json",
            str(out),
            "--debug-dir",
            str(dbg),
            "--allow-path",
            str(tmp_path),
        ]
    )
    assert code == 0
    assert (dbg / da.TIER_A_IMAGE_FINGERPRINT).is_file()
    assert (dbg / da.TIER_A_PROVIDER_REQUEST).is_file()
    assert (dbg / da.TIER_A_IMPORT_PLAN_SUMMARY).is_file()
    assert not (dbg / "provider_response.json").exists()


def test_cli_dry_run_jpeg_image_sets_jpeg_mime_on_request(tmp_path, monkeypatch):
    from src.tools.screenshot_import.models import VisionResponse
    from src.tools.screenshot_import.providers import VisionResult

    image = tmp_path / "shot.jpg"
    Image.new("RGB", (120, 120), "gray").save(image, format="JPEG")
    out = tmp_path / "plan.json"
    captured: dict[str, str] = {}

    monkeypatch.setattr(
        cli,
        "classify_fingerprint",
        lambda *a, **k: ClassificationResult(
            platform="hk_panda",
            screenshot_type="watchlist",
            confidence=0.95,
            signals=[],
            candidate_platforms=[PlatformCandidate(platform="hk_panda", confidence=0.95)],
        ),
    )
    monkeypatch.setattr(cli, "ConfigApiClient", lambda: _StubConfigApiEmptyWatchlist())

    class FakeProvider:
        name = "stub"
        model = "stub-model"

        def complete(self, request):
            captured["mime"] = request.mime_type
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
    assert (
        cli.main(
            [
                str(image),
                "--provider",
                "kimi",
                "--dry-run",
                "--output-json",
                str(out),
                "--allow-path",
                str(tmp_path),
            ]
        )
        == 0
    )
    assert captured.get("mime") == "image/jpeg"


def test_cli_apply_requires_plan():
    exit_code = cli.main(["--apply"])
    assert exit_code == 1


def test_cli_debug_dir_symlink_escape_exits_before_provider(tmp_path, monkeypatch):
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "escape"
    link.symlink_to(outside, target_is_directory=True)
    image = allowed / "shot.png"
    Image.new("RGB", (40, 40), "white").save(image)

    monkeypatch.setattr(
        cli,
        "classify_fingerprint",
        lambda *a, **k: ClassificationResult(
            platform="hk_panda",
            screenshot_type="watchlist",
            confidence=0.95,
            signals=[],
            candidate_platforms=[PlatformCandidate(platform="hk_panda", confidence=0.95)],
        ),
    )

    def _boom(_p):
        raise AssertionError("create_provider must not run")

    monkeypatch.setattr(cli, "create_provider", _boom)
    exit_code = cli.main(
        [
            str(image),
            "--dry-run",
            "--output-json",
            str(allowed / "plan.json"),
            "--debug-dir",
            str(link / "dbg"),
            "--allow-path",
            str(allowed),
        ]
    )
    assert exit_code == 2


def test_cli_apply_skips_rows_already_logged_success(tmp_path, monkeypatch):
    from datetime import datetime, timezone

    from src.tools.screenshot_import.models import ApplyLogEntry, FieldConfidence, NormalizedRow
    from src.tools.screenshot_import.path_safety import atomic_write_json
    from src.tools.screenshot_import.planner import apply_log_request_payload_hash, build_import_plan

    row = NormalizedRow(
        code="600519",
        name="贵州茅台",
        is_holding=False,
        field_confidence=FieldConfidence(code=0.99, name=0.99),
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
        model="m",
        classification=classification,
        model_confidence=0.95,
        threshold=0.8,
        content_fingerprint="cfp",
        existing_codes=frozenset(),
        manual_platform_after_low_confidence=False,
    )
    ra = plan.auto_apply[0]
    with_log = plan.model_copy(
        update={
            "apply_log": [
                ApplyLogEntry(
                    row_apply_id=ra.row_apply_id,
                    code=ra.code,
                    action=ra.action,
                    request_payload_hash=apply_log_request_payload_hash(ra.payload),
                    response_ok=True,
                    ts=datetime.now(timezone.utc),
                    http_status=200,
                    message=None,
                )
            ]
        }
    )
    plan_path = tmp_path / "plan.json"
    atomic_write_json(plan_path, with_log.model_dump(mode="json"))

    class _NoApply:
        def apply_actions(self, *a, **k):
            raise AssertionError("apply_actions must not run when nothing pending")

    monkeypatch.setattr(cli, "ConfigApiClient", lambda: _NoApply())
    exit_code = cli.main(
        ["--apply", "--plan", str(plan_path), "--allow-path", str(tmp_path)]
    )
    assert exit_code == 0


def test_cli_apply_writes_apply_log_on_success(tmp_path, monkeypatch):
    from src.tools.screenshot_import.config_api import AppliedRow, ApplyResult
    from src.tools.screenshot_import.models import FieldConfidence, NormalizedRow
    from src.tools.screenshot_import.path_safety import atomic_write_json
    from src.tools.screenshot_import.planner import build_import_plan

    row = NormalizedRow(
        code="600519",
        name="贵州茅台",
        is_holding=False,
        field_confidence=FieldConfidence(code=0.99, name=0.99),
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
        model="m",
        classification=classification,
        model_confidence=0.95,
        threshold=0.8,
        content_fingerprint="cfp2",
        existing_codes=frozenset(),
        manual_platform_after_low_confidence=False,
    )
    plan_path = tmp_path / "plan.json"
    atomic_write_json(plan_path, plan.model_dump(mode="json"))

    class OkApi:
        def apply_actions(self, actions, import_run_id):
            return ApplyResult(
                rows=[
                    AppliedRow(
                        row_apply_id=a.row_apply_id,
                        code=a.code,
                        status_code=200,
                        ok=True,
                        message="",
                    )
                    for a in actions
                ]
            )

    monkeypatch.setattr(cli, "ConfigApiClient", lambda: OkApi())
    assert (
        cli.main(
            ["--apply", "--plan", str(plan_path), "--allow-path", str(tmp_path)]
        )
        == 0
    )
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert len(data["apply_log"]) == 1
    assert data["apply_log"][0]["response_ok"] is True
    assert data["apply_log"][0]["code"] == "600519"


def test_cli_apply_writes_apply_log_on_failure(tmp_path, monkeypatch):
    from src.tools.screenshot_import.config_api import AppliedRow, ApplyResult
    from src.tools.screenshot_import.models import FieldConfidence, NormalizedRow
    from src.tools.screenshot_import.path_safety import atomic_write_json
    from src.tools.screenshot_import.planner import build_import_plan

    row = NormalizedRow(
        code="000001",
        name="平安银行",
        is_holding=False,
        field_confidence=FieldConfidence(code=0.99, name=0.99),
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
        model="m",
        classification=classification,
        model_confidence=0.95,
        threshold=0.8,
        content_fingerprint="cfp-fail",
        existing_codes=frozenset(),
        manual_platform_after_low_confidence=False,
    )
    plan_path = tmp_path / "plan.json"
    atomic_write_json(plan_path, plan.model_dump(mode="json"))

    class FailApi:
        def apply_actions(self, actions, import_run_id):
            return ApplyResult(
                rows=[
                    AppliedRow(
                        row_apply_id=a.row_apply_id,
                        code=a.code,
                        status_code=500,
                        ok=False,
                        message="bad",
                    )
                    for a in actions
                ]
            )

    monkeypatch.setattr(cli, "ConfigApiClient", lambda: FailApi())
    assert (
        cli.main(
            ["--apply", "--plan", str(plan_path), "--allow-path", str(tmp_path)]
        )
        == 6
    )
    data = json.loads(plan_path.read_text(encoding="utf-8"))
    assert len(data["apply_log"]) == 1
    assert data["apply_log"][0]["response_ok"] is False
    assert data["apply_log"][0]["message"] == "bad"


def test_cli_debug_sensitive_writes_validated_result_and_import_plan_copy(
    tmp_path, monkeypatch
):
    from src.tools.screenshot_import.models import VisionResponse
    from src.tools.screenshot_import.providers import VisionResult

    image = tmp_path / "shot.png"
    Image.new("RGB", (80, 80), "white").save(image)
    out = tmp_path / "plan.json"
    dbg = tmp_path / "dbg"

    monkeypatch.setattr(
        cli,
        "classify_fingerprint",
        lambda *a, **k: ClassificationResult(
            platform="hk_panda",
            screenshot_type="watchlist",
            confidence=0.95,
            signals=[],
            candidate_platforms=[PlatformCandidate(platform="hk_panda", confidence=0.95)],
        ),
    )
    monkeypatch.setattr(cli, "ConfigApiClient", lambda: _StubConfigApiEmptyWatchlist())

    class FakeProvider:
        name = "stub"
        model = "stub-model"

        def complete(self, request):
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
    code = cli.main(
        [
            str(image),
            "--provider",
            "kimi",
            "--dry-run",
            "--output-json",
            str(out),
            "--debug-dir",
            str(dbg),
            "--debug-sensitive",
            "--allow-path",
            str(tmp_path),
        ]
    )
    assert code == 0
    assert (dbg / "validated_result.json").is_file()
    assert (dbg / "import_plan.json").is_file()
    vr = json.loads((dbg / "validated_result.json").read_text(encoding="utf-8"))
    assert vr[0]["code"] == "600519"
