"""Trading calendar via Futu OpenD — shared module.

Provides is_trading_day(market, date) with file-based cache.
Falls back to weekday check if Futu is unavailable.

Usage:
    from src.tools.trading_calendar import is_trading_day
    if is_trading_day("HK"):    # today
    if is_trading_day("CN", "2026-03-05"):
"""

import json
import os
import socket
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.utils.logging_config import setup_logger

logger = setup_logger("trading_calendar")

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111

# Cache file location (absolute path)
_CACHE_DIR = Path(__file__).parent.parent / "data"
CACHE_FILE = _CACHE_DIR / "trading_calendar_cache.json"

# Cache: {(market, month_key): {date_str: trade_type}}
# Refreshed once per day per market, covers current month ± 7 days
_cache: dict[tuple[str, str], dict[str, str]] = {}
_cache_date: str = ""  # date when cache was last refreshed


def _load_cache_from_file() -> bool:
    """Load cache from local file. Returns True if loaded successfully."""
    global _cache, _cache_date
    if not CACHE_FILE.exists():
        return False
    try:
        with open(CACHE_FILE, "r") as f:
            data = json.load(f)
        _cache = {}
        for k, v in data.get("cache", {}).items():
            parts = k.split("|||")
            if len(parts) == 2:
                _cache[(parts[0], parts[1])] = v
        _cache_date = data.get("cache_date", "")
        logger.info(f"Loaded trading calendar from file: {CACHE_FILE}")
        return True
    except Exception as e:
        logger.debug(f"Failed to load trading calendar cache: {e}")
        return False


def _save_cache_to_file():
    """Save cache to local file."""
    global _cache, _cache_date
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        # Convert tuple keys to strings for JSON
        data = {
            "cache": {f"{k[0]}|||{k[1]}": v for k, v in _cache.items()},
            "cache_date": _cache_date,
        }
        # Atomic write: temp file then rename
        tmp = CACHE_FILE.with_suffix(".tmp")
        with open(tmp, "w") as f:
            json.dump(data, f)
        tmp.rename(CACHE_FILE)
        logger.debug(f"Saved trading calendar to file: {CACHE_FILE}")
    except Exception as e:
        logger.debug(f"Failed to save trading calendar cache: {e}")


def _fetch_trading_days(market: str, start: str, end: str) -> Optional[dict[str, str]]:
    """Fetch trading days from Futu OpenD. Returns {date_str: trade_type} or None."""
    # Quick port check
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    if sock.connect_ex((OPEND_HOST, OPEND_PORT)) != 0:
        sock.close()
        return None
    sock.close()

    try:
        from futu import OpenQuoteContext, TradeDateMarket, RET_OK

        market_map = {
            "HK": TradeDateMarket.HK,
            "CN": TradeDateMarket.CN,
            "US": TradeDateMarket.US,
        }
        futu_market = market_map.get(market.upper())
        if futu_market is None:
            return None

        ctx = OpenQuoteContext(host=OPEND_HOST, port=OPEND_PORT)
        try:
            ret, data = ctx.request_trading_days(market=futu_market, start=start, end=end)
            if ret != RET_OK:
                logger.debug(f"Futu trading_days failed: {data}")
                return None
            result = {}
            for item in data:
                result[item["time"]] = item.get("trade_date_type", "WHOLE")
            return result
        finally:
            ctx.close()
    except Exception as e:
        logger.debug(f"Futu trading calendar error: {e}")
        return None


def _ensure_cache(market: str):
    """Refresh cache if needed (once per day)."""
    global _cache, _cache_date

    # Load from file on first call
    if not _cache_date:
        _load_cache_from_file()

    today = datetime.now().strftime("%Y-%m-%d")
    month_key = today[:7]  # "2026-03"
    cache_key = (market.upper(), month_key)

    if _cache_date == today and cache_key in _cache:
        return

    # Fetch current month ± 7 days to cover edges
    start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")

    data = _fetch_trading_days(market, start, end)
    if data is not None:
        _cache[cache_key] = data
        _cache_date = today
        _save_cache_to_file()
        logger.info(f"Trading calendar cached: {market} {len(data)} days ({start}~{end})")


def is_trading_day(market: str = "CN", date_str: Optional[str] = None) -> bool:
    """Check if date is a trading day for the given market.

    Args:
        market: "CN" (A-share), "HK", or "US"
        date_str: "YYYY-MM-DD" format, defaults to today

    Returns:
        True if trading day. Falls back to weekday check if Futu unavailable.
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    market = market.upper()
    _ensure_cache(market)

    month_key = date_str[:7]
    cache_key = (market, month_key)
    cal = _cache.get(cache_key)

    if cal is not None:
        return date_str in cal

    # Fallback: weekday check (no holiday awareness)
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.weekday() < 5
    except ValueError:
        return False


def get_trade_type(market: str = "CN", date_str: Optional[str] = None) -> Optional[str]:
    """Get trading day type: 'WHOLE', 'MORNING', or None (not trading)."""
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    market = market.upper()
    _ensure_cache(market)

    month_key = date_str[:7]
    cache_key = (market, month_key)
    cal = _cache.get(cache_key)

    if cal is not None:
        return cal.get(date_str)
    return None
