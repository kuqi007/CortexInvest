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
    assert "同花顺持仓版式提示" in prompt
    assert "成本/现价" in prompt
    assert "必要信息只有三类" in prompt
    assert "代码或名称至少一个可靠" in prompt
    assert "都是可选辅助字段" in prompt
    assert "以截图表头为准" in prompt
    assert "daily_pnl_pct" in prompt
    assert "JSON" in prompt


def test_prompt_mentions_eastmoney_two_line_layout():
    prompt = build_vision_prompt(
        ClassificationResult(
            platform="eastmoney",
            screenshot_type="holding",
            confidence=0.82,
            signals=["dark_theme", "white_green_red_text"],
            candidate_platforms=[PlatformCandidate(platform="eastmoney", confidence=0.82)],
        )
    )
    assert "东方财富持仓版式提示" in prompt
    assert "名称/市值" in prompt
    assert "持仓/可用" in prompt
    assert "现价/成本" in prompt
    assert "第一行是现价、第二行是成本价" in prompt
    assert "东方财富持仓截图通常不显示完整代码" in prompt
    assert "「沪」「深」「港」标签" in prompt
    assert "不要把市值当作成本" in prompt
    assert "普通/信用" in prompt
    assert "证券市值" in prompt
    assert "港股通 HKD" in prompt


def test_prompt_mentions_hk_panda_code_and_value_layout():
    prompt = build_vision_prompt(
        ClassificationResult(
            platform="hk_panda",
            screenshot_type="holding",
            confidence=0.82,
            signals=["light_background", "minimal_top_chrome"],
            candidate_platforms=[PlatformCandidate(platform="hk_panda", confidence=0.82)],
        )
    )
    assert "香港熊猫证券持仓版式提示" in prompt
    assert "名称下方是 5 位港股代码" in prompt
    assert "market_value" in prompt
    assert "current_price" in prompt


def test_upload_disclosure_names_provider():
    assert "kimi" in build_upload_disclosure("kimi").lower()


def test_watchlist_prompt_only_requires_stock_identity():
    prompt = build_vision_prompt(
        ClassificationResult(
            platform="ths",
            screenshot_type="watchlist",
            confidence=0.82,
            signals=["top_red"],
            candidate_platforms=[PlatformCandidate(platform="ths", confidence=0.82)],
        )
    )
    assert "导入自选只需要能确定是哪只股票" in prompt
    assert "is_holding=false" in prompt
    assert "不要伪造股票代码" in prompt
    assert "1B0685" in prompt
    assert "禁止" in prompt
    assert "cost" in prompt


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


def test_stub_provider_fills_is_holding_from_watchlist_response_type():
    body = {
        "schema_version": 1,
        "platform": "ths",
        "screenshot_type": "watchlist",
        "confidence": 0.9,
        "stocks": [
            {
                "code": "688027",
                "name": "国盾量子",
                "field_confidence": {"code": 1, "name": 1},
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
    assert result.response.stocks[0].is_holding is False


def test_stub_provider_repairs_markdown_json_and_numeric_confidence():
    body = """```json
{
  "platform": "ths",
  "screenshot_type": "watchlist",
  "stocks": [
    {"code": "688027", "name": "国盾量子", "field_confidence": 1.0}
  ]
}
```"""
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    assert result.response.schema_version == 1
    assert result.response.confidence == 0.85
    assert result.response.stocks[0].is_holding is False
    assert result.response.stocks[0].field_confidence.code == 1.0
    assert result.response.stocks[0].field_confidence.name == 1.0


def test_stub_provider_repairs_missing_comma_between_stock_objects():
    body = """
    {
      "platform": "ths",
      "screenshot_type": "watchlist",
      "stocks": [
        {"code": "688027", "name": "国盾量子", "field_confidence": 1.0}
        {"code": "601100", "name": "恒立液压", "field_confidence": 1.0}
      ]
    }
    """
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    assert [row.code for row in result.response.stocks] == ["688027", "601100"]


def test_stub_provider_preserves_raw_excerpt_when_json_unrepairable():
    body = '{"platform": "ths", "stocks": [ this is not json ]}'
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind == "schema_parse"
    assert result.error.raw_redacted.startswith('{"platform": "ths"')


def test_stub_provider_preserves_raw_excerpt_when_schema_validation_fails():
    body = (
        '{"platform": "ths", "screenshot_type": "holding", "stocks": ['
        '{"code": "000001", "name": "平安银行", "is_holding": false, '
        '"cost": 1, "shares": 1, "field_confidence": 1.0}'
        "]}"
    )
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is False
    assert result.error is not None
    assert result.error.kind == "schema_parse"
    assert result.error.raw_response == body


def test_stub_provider_repairs_truncated_stocks_array_by_keeping_complete_rows():
    body = """
    {
      "platform": "ths",
      "screenshot_type": "holding",
      "stocks": [
        {
          "code": "601138",
          "name": "工业富联",
          "cost": "76.765",
          "shares": "200",
          "field_confidence": 1.0
        },
        {
          "code": "002196",
          "name": "方正科技",
          "current_price":
    """
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    assert len(result.response.stocks) == 1
    assert result.response.stocks[0].name == "工业富联"


def test_stub_provider_repairs_truncated_stocks_array_with_nested_confidence():
    body = """
    {
      "platform": "ths",
      "screenshot_type": "holding",
      "stocks": [
        {
          "code": "601138",
          "name": "工业富联",
          "cost": "76.765",
          "shares": "200",
          "field_confidence": {
            "code": 1.0,
            "name": 1.0,
            "cost": 1.0,
            "shares": 1.0
          }
        },
        {
          "code": "002196",
          "name": "方正科技",
          "current_price":
    """
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    assert len(result.response.stocks) == 1
    assert result.response.stocks[0].field_confidence.cost == 1.0


def test_stub_provider_repairs_numeric_strings_for_holdings():
    body = {
        "schema_version": 1,
        "platform": "ths",
        "screenshot_type": "holding",
        "confidence": 0.9,
        "stocks": [
            {
                "code": "601138",
                "name": "工业富联",
                "cost": "76.765",
                "shares": "200",
                "current_price": "62.880",
                "market_value": "12,576.00",
                "available_shares": "200",
                "daily_pnl": "-676.00",
                "daily_pnl_pct": "-5.101%",
                "field_confidence": 1.0,
            }
        ],
    }
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    row = result.response.stocks[0]
    assert row.cost == 76.765
    assert row.shares == 200
    assert row.market_value == 12576
    assert row.daily_pnl_pct == -5.101


def test_stub_provider_moves_row_warnings_and_skips_zero_holdings():
    body = {
        "schema_version": 1,
        "platform": "ths",
        "screenshot_type": "holding",
        "confidence": 0.9,
        "stocks": [
            {
                "code": None,
                "name": "工业富联",
                "cost": "76.765",
                "shares": "200",
                "field_confidence": {"name": 1, "cost": 1, "shares": 1},
                "warnings": ["Code not visible"],
            },
            {
                "code": None,
                "name": "中材科技",
                "cost": "55.362",
                "shares": "0",
                "field_confidence": {"name": 1, "cost": 1, "shares": 1},
            },
        ],
    }
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    assert len(result.response.stocks) == 1
    assert result.response.stocks[0].name == "工业富联"
    assert "Code not visible" in result.response.warnings
    assert "skipped non-positive holding row: 中材科技" in result.response.warnings


def test_stub_provider_flattens_nested_stocks_and_defaults_confidence():
    body = {
        "schema_version": 1,
        "platform": "eastmoney",
        "screenshot_type": "holding",
        "confidence": 0.9,
        "stocks": [
            [
                {
                    "code": "00700",
                    "name": "腾讯控股",
                    "cost": "517.000",
                    "shares": "100",
                },
                {
                    "code": "09988",
                    "name": "阿里巴巴",
                    "cost": "130.600",
                    "shares": "600",
                    "warning": "code inferred",
                },
            ]
        ],
    }
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    assert len(result.response.stocks) == 2
    assert result.response.stocks[0].field_confidence.name == 0.7
    assert result.response.stocks[0].field_confidence.cost == 0.7
    assert "code inferred" in result.response.warnings


def test_stub_provider_drops_common_extra_alias_fields():
    body = {
        "schema_version": 1,
        "platform": "ths",
        "screenshot_type": "holding",
        "confidence": 0.9,
        "stocks": [
            {
                "code": "601138",
                "name": "工业富联",
                "cost": "76.765",
                "shares": "200",
                "pnl": "-2777.04",
                "pnl_pct": "-18.088%",
                "confidence": 1.0,
            }
        ],
    }
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    assert result.response.stocks[0].name == "工业富联"


def test_stub_provider_swaps_cost_and_current_price_using_market_value():
    body = {
        "schema_version": 1,
        "platform": "ths",
        "screenshot_type": "holding",
        "confidence": 0.9,
        "stocks": [
            {
                "code": "601138",
                "name": "工业富联",
                "current_price": "76.765",
                "cost": "62.880",
                "market_value": "12576.00",
                "shares": "200",
                "field_confidence": 1.0,
            }
        ],
    }
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    row = result.response.stocks[0]
    assert row.cost == 76.765
    assert row.current_price == 62.88


def test_stub_provider_lowers_confidence_for_market_value_as_cost():
    body = {
        "schema_version": 1,
        "platform": "eastmoney",
        "screenshot_type": "holding",
        "confidence": 0.9,
        "stocks": [
            {
                "code": "00700",
                "name": "腾讯控股",
                "cost": "51700.00",
                "shares": "100",
                "field_confidence": 1.0,
            }
        ],
    }
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    assert result.response.stocks[0].field_confidence.cost == 0.5


def test_stub_provider_keeps_valid_eastmoney_model_code():
    """Valid codes from eastmoney are now kept (before, all were nullified)."""
    body = {
        "schema_version": 1,
        "platform": "eastmoney",
        "screenshot_type": "holding",
        "confidence": 0.9,
        "stocks": [
            {
                "code": "HK00700",
                "name": "腾讯控股",
                "cost": "629.61",
                "shares": "100",
                "field_confidence": 1.0,
            }
        ],
    }
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    row = result.response.stocks[0]
    assert row.code == "HK00700"
    assert row.field_confidence.code == 1.0
    assert row.name == "腾讯控股"


def test_stub_provider_nulls_invalid_eastmoney_model_code():
    """Invalid codes from eastmoney are still nullified (gibberish, index codes)."""
    body = {
        "schema_version": 1,
        "platform": "eastmoney",
        "screenshot_type": "holding",
        "confidence": 0.9,
        "stocks": [
            {
                "code": "1B0685",
                "name": "科创芯片",
                "cost": "100.0",
                "shares": "100",
                "field_confidence": 1.0,
            }
        ],
    }
    stub = StubVisionProvider("stub", "stub-m", body)
    req = VisionRequest(
        image_path="/dev/null",
        mime_type="image/png",
        prompt="p",
        json_schema={},
    )

    result = stub.complete(req)

    assert result.ok is True
    assert result.response is not None
    row = result.response.stocks[0]
    assert row.code is None
    assert row.field_confidence.code is None
    assert row.name == "科创芯片"


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


def test_minimax_uses_json_object_response_format():
    from src.tools.screenshot_import.providers import _response_format_for_provider

    assert _response_format_for_provider("minimax", {"type": "object"}) is None
    assert _response_format_for_provider("kimi", {"type": "object"})["type"] == "json_schema"


def test_create_provider_rejects_unknown():
    from src.tools.screenshot_import.providers import create_provider

    with pytest.raises(ValueError, match="unsupported"):
        create_provider("unknown-vendor")


def test_resolve_provider_auto_selects_glm_then_kimi_then_minimax(monkeypatch):
    from src.tools.screenshot_import.providers import create_provider, resolve_provider_name

    monkeypatch.delenv("KIMI_API_KEY", raising=False)
    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    with pytest.raises(ValueError, match="no API key"):
        resolve_provider_name("auto")

    monkeypatch.setenv("MINIMAX_API_KEY", "m-key")
    assert resolve_provider_name("auto") == "minimax"
    p = create_provider("auto")
    assert p.name == "minimax"

    monkeypatch.setenv("KIMI_API_KEY", "k-key")
    assert resolve_provider_name("auto") == "kimi"
    assert create_provider("auto").name == "kimi"

    monkeypatch.setenv("GLM_API_KEY", "g-key")
    assert resolve_provider_name("auto") == "glm"
    assert create_provider("auto").name == "glm"

    monkeypatch.delenv("GLM_API_KEY", raising=False)
    monkeypatch.setenv("ZHIPU_API_KEY", "z-key")
    assert resolve_provider_name("auto") == "glm"
    assert create_provider("auto").name == "glm"


def test_create_provider_rejects_header_unsafe_api_key(monkeypatch):
    from src.tools.screenshot_import.providers import create_provider

    monkeypatch.setenv("KIMI_API_KEY", "bad\nkey")

    with pytest.raises(ValueError, match="unsafe"):
        create_provider("kimi")


def test_create_provider_rejects_untrusted_base_url(monkeypatch):
    from src.tools.screenshot_import.providers import create_provider

    monkeypatch.setenv("GLM_API_KEY", "g-key")
    monkeypatch.setenv("GLM_BASE_URL", "https://evil.example/v1")

    with pytest.raises(ValueError, match="untrusted"):
        create_provider("glm")


class TestCoerceOptionalNumber:
    """Parametrized tests for _coerce_optional_number."""

    @pytest.mark.parametrize(
        ("input_val", "expected"),
        [
            # --- Chinese unit multipliers ---
            ("1.2万", 12000.0),
            ("5亿", 500_000_000.0),
            ("10.5万", 105000.0),
            ("1亿", 100_000_000.0),
            # --- Currency suffixes ---
            ("HKD500.00", 500.0),
            ("629.61USD", 629.61),
            ("人民币100", 100.0),
            ("100港币", 100.0),
            ("500港元", 500.0),
            # --- Unit markers ---
            ("629.61元", 629.61),
            ("持有1000股", 1000.0),
            # --- Prefix text ---
            ("约629.61元", 629.61),
            ("约10.5万", 105000.0),
            # --- Negative percentages ---
            ("-5.101%", -5.101),
            # --- Scientific notation ---
            ("1E5", 100_000.0),
            ("1.0E+5", 100_000.0),
            # --- Empty / null / missing ---
            ("N/A", None),
            ("---", None),
            ("", None),
            (None, None),
            # --- Integer pass-through ---
            (42, 42),
            # --- Single/minus dashes ---
            ("--", None),
            ("-", None),
        ],
    )
    def test_coerce_optional_number(self, input_val, expected) -> None:
        from src.tools.screenshot_import.providers import _coerce_optional_number

        assert _coerce_optional_number(input_val) == expected


class TestMaybeSwapCostAndShares:
    """Tests for _maybe_swap_cost_and_shares."""

    def test_normal_no_swap(self) -> None:
        """Normal case: cost=45, shares=100 — NOT triggered."""
        from src.tools.screenshot_import.providers import _maybe_swap_cost_and_shares

        item: dict = {"is_holding": True, "cost": 45.0, "shares": 100}
        _maybe_swap_cost_and_shares(item)
        assert item["cost"] == 45.0
        assert item["shares"] == 100

    def test_swap_triggered(self) -> None:
        """OCR reversed case: cost=15000, shares=5 — triggered, swapped."""
        from src.tools.screenshot_import.providers import _maybe_swap_cost_and_shares

        item: dict = {"is_holding": True, "cost": 15000.0, "shares": 5}
        _maybe_swap_cost_and_shares(item)
        assert item["cost"] == 5.0
        assert item["shares"] == 15000.0

    def test_moutai_high_priced_stock_not_swapped(self) -> None:
        """Moutai-like data: cost=1500, shares=10 — NOT triggered (guard >=10000)."""
        from src.tools.screenshot_import.providers import _maybe_swap_cost_and_shares

        item: dict = {"is_holding": True, "cost": 1500.0, "shares": 10}
        _maybe_swap_cost_and_shares(item)
        assert item["cost"] == 1500.0
        assert item["shares"] == 10

    def test_not_holding_skips_swap(self) -> None:
        """Watchlist items (not holding) should skip swap entirely."""
        from src.tools.screenshot_import.providers import _maybe_swap_cost_and_shares

        item: dict = {"is_holding": False, "cost": 15000.0, "shares": 5}
        _maybe_swap_cost_and_shares(item)
        assert item["cost"] == 15000.0
        assert item["shares"] == 5

    def test_non_numeric_fields_skip(self) -> None:
        """Non-numeric cost/shares should not cause errors."""
        from src.tools.screenshot_import.providers import _maybe_swap_cost_and_shares

        item: dict = {"is_holding": True, "cost": "N/A", "shares": "N/A"}
        _maybe_swap_cost_and_shares(item)
        assert item["cost"] == "N/A"
        assert item["shares"] == "N/A"

    def test_missing_fields_skip(self) -> None:
        """Missing cost or shares should not cause errors."""
        from src.tools.screenshot_import.providers import _maybe_swap_cost_and_shares

        item: dict = {"is_holding": True}
        _maybe_swap_cost_and_shares(item)
        assert "cost" not in item or item["cost"] is None

    def test_non_positive_fields_skip(self) -> None:
        """Zero or negative cost/shares should be skipped."""
        from src.tools.screenshot_import.providers import _maybe_swap_cost_and_shares

        item: dict = {"is_holding": True, "cost": 0, "shares": 5}
        _maybe_swap_cost_and_shares(item)
        assert item["cost"] == 0
        assert item["shares"] == 5
