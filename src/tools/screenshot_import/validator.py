from __future__ import annotations

import re

from src.tools.screenshot_import.models import HoldingStockRow, NormalizedRow, StockRow, WatchlistStockRow


HK_CODE = re.compile(r"^HK\d{5}$")
ASHARE_CODE = re.compile(r"^\d{6}$")


def normalize_code(raw: str) -> str:
    """Normalize broker screenshot codes to A-share (6 digits) or HK (HK + 5 digits)."""
    s = raw.strip().replace(" ", "")
    if not s:
        msg = "empty stock code"
        raise ValueError(msg)

    upper = s.upper()
    if upper.startswith("HK"):
        digits = re.sub(r"[^0-9]", "", upper[2:])
        if not digits:
            msg = f"unsupported stock code: {raw!r}"
            raise ValueError(msg)
        if len(digits) > 5:
            msg = f"unsupported stock code: {raw!r}"
            raise ValueError(msg)
        return "HK" + digits.zfill(5)

    if s.isdigit():
        if len(s) == 6:
            return s
        if 1 <= len(s) <= 5:
            return "HK" + s.zfill(5)

    msg = f"unsupported stock code: {raw!r}"
    raise ValueError(msg)


def normalize_row(row: StockRow, source_row_index: int | None = None) -> NormalizedRow:
    """Apply code normalization; watchlist rows omit cost/shares."""
    code = normalize_code(row.code)
    base = {
        "code": code,
        "name": row.name,
        "is_holding": row.is_holding,
        "field_confidence": row.field_confidence,
        "source_row_index": source_row_index,
    }
    if isinstance(row, HoldingStockRow):
        return NormalizedRow(
            **base,
            cost=float(row.cost),
            shares=int(row.shares),
        )
    if isinstance(row, WatchlistStockRow):
        return NormalizedRow(**base, cost=None, shares=None)
    # Discriminated union — satisfy type checkers
    msg = f"unexpected stock row type: {type(row)}"
    raise TypeError(msg)


def is_valid_normalized_code(code: str) -> bool:
    return bool(ASHARE_CODE.fullmatch(code) or HK_CODE.fullmatch(code))
