from __future__ import annotations

import json

import pytest

from src.tools.screenshot_import.models import ClassificationResult, PlatformCandidate
from src.tools.screenshot_import.prompts import build_upload_disclosure, build_vision_prompt
from src.tools.screenshot_import.providers import StubVisionProvider, VisionRequest


def test_prompt_mentions_broker_and_column_confusion():
    prompt = build_vision_prompt(
        ClassificationResult(
            platform="ths",
            screenshot_type="holding",
            confidence=0.86,
            signals=["top_red", "dense_numeric_table"],
            candidate_platforms=[PlatformCandidate(platform="ths", confidence=0.86)],
        )
    )
    assert "同花顺" in prompt
    assert "不要把当前价" in prompt
    assert "JSON" in prompt


def test_upload_disclosure_names_provider():
    assert "kimi" in build_upload_disclosure("kimi").lower()


def test_stub_provider_parses_valid_json_response():
    body = {
        "schema_version": 1,
        "platform": "ths",
        "screenshot_type": "holding",
        "confidence": 0.9,
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
    stub = StubVisionProvider("stub", "stub-m", json.dumps(body, ensure_ascii=False))
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )
    result = stub.complete(req)
    assert result.ok is True
    assert result.response is not None
    assert result.response.stocks[0].code == "000001"
    assert result.error is None


def test_stub_provider_reports_schema_error():
    stub = StubVisionProvider("stub", "stub-m", "not valid json {{{")
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )
    result = stub.complete(req)
    assert result.ok is False
    assert result.response is None
    assert result.error is not None
    assert result.error.kind == "schema_parse"


def test_create_provider_rejects_unknown():
    from src.tools.screenshot_import.providers import create_provider

    with pytest.raises(ValueError, match="unsupported"):
        create_provider("unknown-vendor")


def test_resolve_provider_auto_selects_kimi_then_glm_then_minimax(monkeypatch):
    from src.tools.screenshot_import.providers import create_provider, resolve_provider_name

    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(ValueError, match="no API key"):
        resolve_provider_name("auto")

    monkeypatch.setenv("MINIMAX_API_KEY", "m-key")
    assert resolve_provider_name("auto") == "minimax"
    p = create_provider("auto")
    assert p.name == "minimax"

    monkeypatch.setenv("GLM_API_KEY", "g-key")
    assert resolve_provider_name("auto") == "glm"
    assert create_provider("auto").name == "glm"

    monkeypatch.setenv("KIMI_API_KEY", "k-key")
    assert resolve_provider_name("auto") == "kimi"
    assert create_provider("auto").name == "kimi"
