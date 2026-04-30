from __future__ import annotations

import pytest

from src.tools.screenshot_import.models import FieldConfidence, HoldingStockRow, WatchlistStockRow
from src.tools.screenshot_import.validator import normalize_code, normalize_row


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("00700", "HK00700"),
        ("HK.700", "HK00700"),
        ("HK00700", "HK00700"),
        ("hk00700", "HK00700"),
        ("HK 700", "HK00700"),
    ],
)
def test_normalize_hk_variants(raw: str, expected: str) -> None:
    assert normalize_code(raw) == expected


def test_normalize_ashare_code() -> None:
    assert normalize_code("600519") == "600519"
    assert normalize_code("000001") == "000001"


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
