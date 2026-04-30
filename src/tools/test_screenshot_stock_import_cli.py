from __future__ import annotations

import json

from PIL import Image

from src.tools import screenshot_stock_import as cli
from src.tools.screenshot_import.models import ClassificationResult


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
            candidate_platforms=[],
        ),
    )

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


def test_cli_apply_requires_plan():
    exit_code = cli.main(["--apply"])
    assert exit_code == 1
