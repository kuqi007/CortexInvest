from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import requests

from src.tools.screenshot_import.models import (
    FieldConfidence,
    HoldingStockRow,
    NormalizedRow,
    StockRow,
    WatchlistStockRow,
)


HK_CODE = re.compile(r"^HK\d{5}$")
ASHARE_CODE = re.compile(r"^\d{6}$")
PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_STOCK_CATALOG_PATH = PROJECT_ROOT / "stocks" / "catalog.json"
MX_BASE_URL = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
_MX_NAME_CACHE: dict[str, str] = {}
NON_STOCK_WATCHLIST_NAMES = frozenset(
    {
        "创业板",
        "港股通(HKD)",
        "港股通(hkd)",
        "科创半导",
        "航空航天",
        "半导体",
        "科创芯片",
    }
)


def normalize_code(raw: str) -> str:
    """Normalize broker screenshot codes to A-share (6 digits) or HK (HK + 5 digits)."""
    s = raw.strip().replace(" ", "")
    if not s:
        msg = "empty stock code"
        raise ValueError(msg)

    upper = s.upper()
    if upper.endswith((".SH", ".SZ", ".BJ")):
        digits = upper.rsplit(".", 1)[0]
        if ASHARE_CODE.fullmatch(digits):
            return digits

    if upper.endswith(".HK"):
        digits = upper.removesuffix(".HK")
        if digits.isdigit() and 1 <= len(digits) <= 5:
            return "HK" + digits.zfill(5)

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


def _normalized_name(value: object) -> str:
    return str(value or "").strip().casefold()


def _stock_name_alias(value: object) -> str:
    text = _display_name_for_lookup(value)
    return re.sub(r"-(W|B|S|SW)$", "", text, flags=re.IGNORECASE).strip()


def _display_name_for_lookup(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[（(](沪|深|港|HKD|hkd)[）)]$", "", text).strip()
    if len(text) > 1 and text.endswith("H") and not text.endswith("-W"):
        text = text[:-1].strip()
    return text


def _is_safe_stock_name_query(name: str) -> bool:
    stripped = name.strip()
    return bool(stripped) and len(stripped) <= 40 and not any(
        ord(ch) < 32 for ch in stripped
    )


def _is_safe_api_key(value: str) -> bool:
    return bool(value) and not any(ch in value for ch in "\r\n")


def _normalize_catalog_code(raw_code: str) -> str | None:
    code = raw_code.strip().upper()
    if code.endswith(".HK"):
        return "HK" + code.removesuffix(".HK").zfill(5)
    if code.endswith(".SZ") or code.endswith(".SH") or code.endswith(".BJ"):
        return code.rsplit(".", 1)[0]
    try:
        return normalize_code(code)
    except ValueError:
        return None


@lru_cache(maxsize=4)
def _load_catalog_name_index(catalog_path: str) -> dict[str, tuple[str, ...]]:
    path = Path(catalog_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    stocks = raw.get("stocks") if isinstance(raw, dict) else None
    if not isinstance(stocks, dict):
        return {}

    by_name: dict[str, set[str]] = {}
    for raw_code, raw_item in stocks.items():
        if not isinstance(raw_item, Mapping):
            continue
        name = _normalized_name(raw_item.get("name"))
        alias_name = _normalized_name(_stock_name_alias(raw_item.get("name")))
        code = _normalize_catalog_code(str(raw_code))
        if name and code is not None:
            by_name.setdefault(name, set()).add(code)
        if alias_name and code is not None:
            by_name.setdefault(alias_name, set()).add(code)
    return {name: tuple(sorted(codes)) for name, codes in by_name.items()}


def resolve_code_by_catalog_name(
    name: str,
    catalog_path: Path = DEFAULT_STOCK_CATALOG_PATH,
) -> str | None:
    index = _load_catalog_name_index(str(catalog_path))
    codes = index.get(_normalized_name(name), ())
    if not codes:
        codes = index.get(_normalized_name(_stock_name_alias(name)), ())
    return codes[0] if len(codes) == 1 else None


def _mx_entity_tags(payload: object) -> list[Mapping[str, object]]:
    if not isinstance(payload, Mapping):
        return []
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    inner_data = data.get("data")
    if not isinstance(inner_data, Mapping):
        return []
    search_result = inner_data.get("searchDataResultDTO")
    if not isinstance(search_result, Mapping):
        return []
    entity_tags = search_result.get("entityTagDTOList")
    if not isinstance(entity_tags, list):
        return []
    return [tag for tag in entity_tags if isinstance(tag, Mapping)]


def resolve_code_by_mx_name(name: str) -> str | None:
    if not _is_safe_stock_name_query(name):
        return None
    api_key = os.getenv("MX_APIKEY")
    if not api_key or not _is_safe_api_key(api_key):
        return None
    cache_key = _normalized_name(name)
    if cache_key in _MX_NAME_CACHE:
        return _MX_NAME_CACHE[cache_key]

    try:
        response = requests.post(
            MX_BASE_URL,
            headers={"Content-Type": "application/json", "apikey": api_key},
            json={"toolQuery": f"{name.strip()} 股票代码"},
            timeout=8,
            allow_redirects=False,
        )
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return None

    needle = _normalized_name(name)
    matches: set[str] = set()
    for tag in _mx_entity_tags(payload):
        full_name = _normalized_name(tag.get("fullName"))
        if full_name != needle:
            continue
        raw_code = str(tag.get("secuCode") or "")
        market = str(tag.get("marketChar") or "")
        code = _normalize_catalog_code(f"{raw_code}{market}" if market else raw_code)
        if code is not None:
            matches.add(code)
    if len(matches) != 1:
        return None
    code = next(iter(matches))
    _MX_NAME_CACHE[cache_key] = code
    return code


def resolve_code_by_name(
    name: str,
    existing_watchlist: Mapping[str, object] | None = None,
    catalog_path: Path = DEFAULT_STOCK_CATALOG_PATH,
    allow_external_lookup: bool = False,
) -> str | None:
    lookup_name = _display_name_for_lookup(name)
    needle = _normalized_name(lookup_name)
    if not needle:
        return None

    matches: list[str] = []
    if existing_watchlist is not None:
        for raw_code, raw_item in existing_watchlist.items():
            if not isinstance(raw_item, Mapping):
                continue
            item_name = _normalized_name(raw_item.get("name"))
            if item_name and item_name == needle:
                code = _normalize_catalog_code(str(raw_code))
                if code is not None:
                    matches.append(code)

    unique = sorted(set(matches))
    if len(unique) == 1:
        return unique[0]
    if len(unique) > 1:
        return None
    code = resolve_code_by_catalog_name(lookup_name, catalog_path)
    if code is not None:
        return code
    return resolve_code_by_mx_name(lookup_name) if allow_external_lookup else None


def _is_known_non_stock_name(name: str) -> bool:
    return name.strip() in NON_STOCK_WATCHLIST_NAMES


def normalize_row(
    row: StockRow,
    source_row_index: int | None = None,
    existing_watchlist: Mapping[str, object] | None = None,
    allow_external_lookup: bool = False,
) -> NormalizedRow:
    """Apply code normalization; watchlist rows omit cost/shares."""
    treat_watchlist_name_as_name_only = _is_known_non_stock_name(row.name)
    if treat_watchlist_name_as_name_only:
        code = None
    else:
        code_from_model = row.code is not None
        try:
            code = normalize_code(row.code) if row.code is not None else None
        except ValueError:
            code = None
            code_from_model = False
    if code is None and not treat_watchlist_name_as_name_only:
        code = resolve_code_by_name(
            row.name,
            existing_watchlist,
            allow_external_lookup=allow_external_lookup,
        )
    field_confidence = row.field_confidence
    normalized_name = _display_name_for_lookup(row.name) or row.name
    if code is not None and not treat_watchlist_name_as_name_only and row.name.strip():
        name_code = resolve_code_by_name(
            row.name,
            existing_watchlist,
            allow_external_lookup=allow_external_lookup,
        )
        if name_code is not None and name_code != code:
            code = name_code
            field_confidence = FieldConfidence(
                code=min(float(row.field_confidence.code or 1.0), 0.7),
                name=row.field_confidence.name,
                cost=row.field_confidence.cost,
                shares=row.field_confidence.shares,
            )
        elif name_code is not None and not code_from_model:
            field_confidence = FieldConfidence(
                code=min(float(row.field_confidence.code or 1.0), 0.7),
                name=row.field_confidence.name,
                cost=row.field_confidence.cost,
                shares=row.field_confidence.shares,
            )
    base = {
        "code": code,
        "name": normalized_name if not treat_watchlist_name_as_name_only else row.name,
        "is_holding": row.is_holding,
        "field_confidence": field_confidence,
        "source_row_index": source_row_index,
    }
    if isinstance(row, HoldingStockRow):
        return NormalizedRow(
            **base,
            cost=float(row.cost),
            shares=int(row.shares),
            current_price=(
                float(row.current_price) if row.current_price is not None else None
            ),
            market_value=float(row.market_value) if row.market_value is not None else None,
            available_shares=float(row.available_shares)
            if row.available_shares is not None
            else None,
            daily_pnl=float(row.daily_pnl) if row.daily_pnl is not None else None,
            daily_pnl_pct=(
                float(row.daily_pnl_pct) if row.daily_pnl_pct is not None else None
            ),
        )
    if isinstance(row, WatchlistStockRow):
        return NormalizedRow(**base, cost=None, shares=None)
    # Discriminated union — satisfy type checkers
    msg = f"unexpected stock row type: {type(row)}"
    raise TypeError(msg)


def is_valid_normalized_code(code: str | None) -> bool:
    if code is None:
        return False
    return bool(ASHARE_CODE.fullmatch(code) or HK_CODE.fullmatch(code))
