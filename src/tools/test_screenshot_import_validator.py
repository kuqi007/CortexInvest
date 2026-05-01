from __future__ import annotations

from pathlib import Path

import pytest

import src.tools.screenshot_import.validator as validator
from src.tools.screenshot_import.models import FieldConfidence, HoldingStockRow, WatchlistStockRow
from src.tools.screenshot_import.validator import (
    normalize_code,
    normalize_row,
    resolve_code_by_catalog_fuzzy,
    resolve_code_by_catalog_name,
    resolve_code_by_name,
    resolve_code_by_mx_name,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00700", "HK00700"),
        ("HK.700", "HK00700"),
        ("HK00700", "HK00700"),
        ("hk00700", "HK00700"),
        ("HK 700", "HK00700"),
        ("00700.HK", "HK00700"),
    ],
)
def test_normalize_hk_variants(raw: str, expected: str) -> None:
    assert normalize_code(raw) == expected


def test_normalize_ashare_code() -> None:
    assert normalize_code("600519") == "600519"
    assert normalize_code("000001") == "000001"
    assert normalize_code("600519.SH") == "600519"
    assert normalize_code("000001.SZ") == "000001"


def test_normalize_code_rejects_unsupported() -> None:
    with pytest.raises(ValueError, match="unsupported"):
        normalize_code("SH600519")
    with pytest.raises(ValueError, match="empty"):
        normalize_code("   ")


def test_normalize_holding_preserves_cost_and_shares() -> None:
    row = HoldingStockRow(
        code="600519",
        name="贵州茅台",
        is_holding=True,
        cost=1688.0,
        shares=100.0,
        current_price=1700.5,
        market_value=170050.0,
        available_shares=80.0,
        daily_pnl=1250.0,
        daily_pnl_pct=0.73,
        field_confidence=FieldConfidence(
            code=0.99,
            name=0.9,
            cost=0.88,
            shares=0.87,
        ),
    )
    out = normalize_row(row, source_row_index=3)
    assert out.code == "600519"
    assert out.is_holding is True
    assert out.cost == 1688.0
    assert out.shares == 100
    assert out.current_price == 1700.5
    assert out.market_value == 170050.0
    assert out.available_shares == 80
    assert out.daily_pnl == 1250.0
    assert out.daily_pnl_pct == 0.73
    assert out.field_confidence.code == 0.99
    assert out.source_row_index == 3


def test_normalize_watchlist_has_no_holding_fields() -> None:
    row = WatchlistStockRow(
        code="00700",
        name="腾讯",
        is_holding=False,
        field_confidence=FieldConfidence(code=0.95, name=0.9),
    )
    out = normalize_row(row)
    assert out.code == "HK00700"
    assert out.is_holding is False
    assert out.cost is None
    assert out.shares is None


def test_normalize_name_only_holding_resolves_existing_watchlist_name() -> None:
    row = HoldingStockRow(
        name="比亚迪股份",
        is_holding=True,
        cost=127.825,
        shares=1100,
        field_confidence=FieldConfidence(name=0.95, cost=0.92, shares=0.91),
    )
    existing = {"HK01211": {"name": "比亚迪股份"}}

    out = normalize_row(row, existing_watchlist=existing)

    assert out.code == "HK01211"
    assert out.name == "比亚迪股份"


def test_normalize_name_only_unresolved_keeps_missing_code_for_planner() -> None:
    row = WatchlistStockRow(
        name="未匹配股票",
        is_holding=False,
        field_confidence=FieldConfidence(name=0.95),
    )

    out = normalize_row(row, existing_watchlist={})

    assert out.code is None
    assert out.name == "未匹配股票"


def test_resolve_code_by_name_requires_unique_match() -> None:
    existing = {
        "HK01211": {"name": "比亚迪股份"},
        "002594": {"name": "比亚迪股份"},
    }

    assert resolve_code_by_name("比亚迪股份", existing) is None


def test_resolve_code_by_name_fuzzy_watchlist_eastmoney_short_label() -> None:
    existing = {"HK01211": {"name": "比亚迪股份"}}
    assert resolve_code_by_name("比亚迪股", existing) == "HK01211"
    assert resolve_code_by_name("比亚迪", existing) == "HK01211"


def test_resolve_code_by_name_fuzzy_watchlist_substring_unique() -> None:
    existing = {"HK00700": {"name": "腾讯控股"}}
    assert resolve_code_by_name("腾讯", existing) == "HK00700"


def test_resolve_code_by_name_fuzzy_watchlist_matches_alias() -> None:
    existing = {"HK00700": {"name": "腾讯控股", "alias": "港股腾讯"}}
    assert resolve_code_by_name("港股腾讯", existing) == "HK00700"


def test_resolve_code_by_name_fuzzy_ambiguous_no_unique_code(tmp_path: Path) -> None:
    existing = {
        "HK01211": {"name": "比亚迪股份"},
        "HK00001": {"name": "比亚迪电子"},
    }
    catalog = tmp_path / "catalog.json"
    catalog.write_text('{"stocks": {}}', encoding="utf-8")
    assert (
        resolve_code_by_name("比亚迪", existing, catalog, allow_external_lookup=False)
        is None
    )


def test_resolve_code_by_catalog_fuzzy_short_label(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"stocks": {"02259.HK": {"name": "紫金黄金国际"}}}',
        encoding="utf-8",
    )
    assert resolve_code_by_catalog_fuzzy("紫金黄金", catalog) == "HK02259"


def test_resolve_code_by_name_catalog_fuzzy_eastmoney_labels(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"stocks": {"01211.HK": {"name": "比亚迪股份"}}}',
        encoding="utf-8",
    )
    assert resolve_code_by_name("比亚迪股", {}, catalog, allow_external_lookup=False) == "HK01211"
    assert resolve_code_by_name("比亚迪A", {}, catalog, allow_external_lookup=False) == "HK01211"


def test_resolve_code_by_catalog_name(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        """
        {
          "stocks": {
            "601138.SH": {"name": "工业富联"},
            "01211.HK": {"name": "比亚迪股份"}
          }
        }
        """,
        encoding="utf-8",
    )

    assert resolve_code_by_catalog_name("工业富联", catalog) == "601138"
    assert resolve_code_by_catalog_name("比亚迪股份", catalog) == "HK01211"


def test_resolve_code_by_catalog_name_uses_hk_suffix_alias(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        """
        {
          "stocks": {
            "01810.HK": {"name": "小米集团-W"},
            "09988.HK": {"name": "阿里巴巴-W"}
          }
        }
        """,
        encoding="utf-8",
    )

    assert resolve_code_by_catalog_name("小米集团", catalog) == "HK01810"
    assert resolve_code_by_catalog_name("阿里巴巴-B", catalog) == "HK09988"


def test_resolve_code_by_name_falls_back_to_catalog(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"stocks": {"000875.SZ": {"name": "电投绿能"}}}',
        encoding="utf-8",
    )

    assert resolve_code_by_name("电投绿能", {}, catalog) == "000875"


def test_resolve_code_by_name_falls_back_to_catalog_without_watchlist(
    tmp_path: Path,
) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '{"stocks": {"601138.SH": {"name": "工业富联"}}}',
        encoding="utf-8",
    )

    assert resolve_code_by_name("工业富联", None, catalog) == "601138"


def test_resolve_code_by_name_accepts_suffixed_watchlist_keys() -> None:
    existing = {"601138.SH": {"name": "工业富联"}}

    assert resolve_code_by_name("工业富联", existing) == "601138"


def test_resolve_code_by_name_uses_external_lookup_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validator, "resolve_code_by_catalog_name", lambda *_args: None)
    monkeypatch.setattr(validator, "resolve_code_by_mx_name", lambda name: "300624")

    assert resolve_code_by_name("万兴科技", {}, allow_external_lookup=False) is None
    assert resolve_code_by_name("万兴科技", {}, allow_external_lookup=True) == "300624"


def test_normalize_row_can_use_external_name_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validator, "resolve_code_by_catalog_name", lambda *_args: None)
    monkeypatch.setattr(validator, "resolve_code_by_mx_name", lambda name: "603538")
    row = HoldingStockRow(
        name="美诺华",
        is_holding=True,
        cost=30.5,
        shares=100,
        field_confidence=FieldConfidence(name=0.95, cost=0.92, shares=0.91),
    )

    out = normalize_row(row, existing_watchlist={}, allow_external_lookup=True)

    assert out.code == "603538"


def test_normalize_row_overrides_mismatched_code_from_unique_name_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validator, "resolve_code_by_catalog_name", lambda *_args: None)
    monkeypatch.setattr(validator, "resolve_code_by_mx_name", lambda name: "601138")
    row = HoldingStockRow(
        code="002194",
        name="工业富联",
        is_holding=True,
        cost=76.765,
        shares=200,
        field_confidence=FieldConfidence(code=1.0, name=1.0, cost=1.0, shares=1.0),
    )

    out = normalize_row(row, existing_watchlist={}, allow_external_lookup=True)

    assert out.code == "601138"
    assert out.field_confidence.code == 0.7


def test_normalize_row_strips_display_suffix_before_name_lookup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validator, "resolve_code_by_catalog_name", lambda *_args: None)
    monkeypatch.setattr(
        validator,
        "resolve_code_by_mx_name",
        lambda name: "HK03896" if name == "金山云" else None,
    )
    row = HoldingStockRow(
        code="002063",
        name="金山云H",
        is_holding=True,
        cost=7.781,
        shares=2000,
        field_confidence=FieldConfidence(code=1.0, name=1.0, cost=1.0, shares=1.0),
    )

    out = normalize_row(row, existing_watchlist={}, allow_external_lookup=True)

    assert out.code == "HK03896"
    assert out.name == "金山云"
    assert out.field_confidence.code == 0.7


def test_normalize_row_treats_unsupported_code_as_name_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(validator, "resolve_code_by_catalog_name", lambda *_args: None)
    monkeypatch.setattr(validator, "resolve_code_by_mx_name", lambda name: "HK00700")
    row = HoldingStockRow(
        code="腾讯控股",
        name="腾讯控股",
        is_holding=True,
        cost=629.61,
        shares=100,
        field_confidence=FieldConfidence(code=0.9, name=0.9, cost=0.9, shares=0.9),
    )

    out = normalize_row(row, existing_watchlist={}, allow_external_lookup=True)

    assert out.code == "HK00700"
    assert out.field_confidence.code == 0.7


def test_normalize_holding_known_non_stock_name_rejects_code() -> None:
    row = HoldingStockRow(
        code="300079",
        name="创业板",
        is_holding=True,
        cost=1.09,
        shares=8300,
        field_confidence=FieldConfidence(code=1.0, name=1.0, cost=1.0, shares=1.0),
    )

    out = normalize_row(row, existing_watchlist={})

    assert out.code is None


def test_mx_lookup_skips_unsafe_name_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MX_APIKEY", "test-key")

    def fail_post(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unsafe names must not be sent to MX")

    monkeypatch.setattr(validator.requests, "post", fail_post)

    assert resolve_code_by_mx_name("万兴科技\n股票代码") is None


def test_mx_lookup_skips_unsafe_api_key_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MX_APIKEY", "bad\nkey")

    def fail_post(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("unsafe API keys must not be sent")

    monkeypatch.setattr(validator.requests, "post", fail_post)

    assert resolve_code_by_mx_name("万兴科技") is None


def test_normalize_watchlist_rejects_known_non_stock_name_even_with_hallucinated_code() -> None:
    row = WatchlistStockRow(
        code="688685",
        name="科创芯片",
        is_holding=False,
        field_confidence=FieldConfidence(code=1.0, name=1.0),
    )

    out = normalize_row(row, existing_watchlist={})

    assert out.code is None
    assert out.name == "科创芯片"


def test_normalize_known_non_stock_name_does_not_call_name_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_resolver(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("known non-stock names must not resolve to stock codes")

    monkeypatch.setattr(validator, "resolve_code_by_name", fail_resolver)
    row = WatchlistStockRow(
        name="科创芯片",
        is_holding=False,
        field_confidence=FieldConfidence(name=1.0),
    )

    out = validator.normalize_row(row)

    assert out.code is None
