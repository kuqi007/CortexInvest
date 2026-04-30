"""Trading calendar via Futu OpenD — shared module.

Provides is_trading_day(market, date) with SQLite-backed cache.
Falls back to weekday check if Futu is unavailable.

Usage:
    from src.tools.trading_calendar import is_trading_day
    if is_trading_day("HK"):    # today
    if is_trading_day("CN", "2026-03-05"):
"""

import json
import socket
import time
from datetime import datetime, timedelta
from typing import Optional

from src.sim_trading.db import get_connection, init_trading_db
from src.utils.logging_config import setup_logger

logger = setup_logger("trading_calendar")

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111

# Cache: {(market, month_key): {date_str: trade_type}}
# Refreshed once per day per market, covers current month ± 7 days
_cache: dict[tuple[str, str], dict[str, str]] = {}
_cache_date: str = ""  # date when cache was last refreshed


def _load_cache_from_db() -> bool:
    """Load cache from trading.db. Returns True if loaded successfully."""
    global _cache, _cache_date
    conn = None
    try:
        init_trading_db()
        conn = get_connection()
        rows = conn.execute(
            "SELECT date, calendar_json, updated_at_ms FROM trading_calendar_cache"
        ).fetchall()
        _cache.clear()
        latest_ms = 0
        for row in rows:
            parts = row["date"].split("|||")
            if len(parts) == 2:
                payload = json.loads(row["calendar_json"])
                days = payload.get("days", {})
                if isinstance(days, dict):
                    # Split by month — DB stores flat date range per row, split into month buckets
                    for date_str, trade_type in days.items():
                        month_key = date_str[:7]
                        month_cache_key = (parts[0], month_key)
                        if month_cache_key not in _cache:
                            _cache[month_cache_key] = {}
                        _cache[month_cache_key][date_str] = trade_type
                latest_ms = max(latest_ms, int(row["updated_at_ms"] or 0))
        if latest_ms:
            _cache_date = datetime.fromtimestamp(latest_ms / 1000).strftime("%Y-%m-%d")
        return bool(rows)
    except Exception as e:
        logger.debug(f"Failed to load trading calendar cache: {e}")
        return False
    finally:
        if conn is not None:
            conn.close()


def _save_cache_to_db():
    """Save cache to trading.db."""
    global _cache, _cache_date
    conn = None
    try:
        init_trading_db()
        conn = get_connection()
        updated_at_ms = int(time.time() * 1000)
        for (market, month_key), days in _cache.items():
            payload = {"market": market, "month": month_key, "days": days}
            conn.execute(
                """
                INSERT OR REPLACE INTO trading_calendar_cache
                    (date, calendar_json, updated_at_ms)
                VALUES (?, ?, ?)
                """,
                (
                    f"{market}|||{month_key}",
                    json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                    updated_at_ms,
                ),
            )
        conn.commit()
    except Exception as e:
        logger.debug(f"Failed to save trading calendar cache: {e}")
    finally:
        if conn is not None:
            conn.close()


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

    # Load from DB on first call
    if not _cache_date:
        _load_cache_from_db()

    today = datetime.now().strftime("%Y-%m-%d")
    month_key = today[:7]  # "2026-03"
    cache_key = (market.upper(), month_key)

    # Re-fetch if today's date is not in the cache for this market/month.
    # Previously used `_cache_date == today` (DB row mtime), which incorrectly
    # triggered early-return when the row was updated today but only contained
    # older dates (e.g. 04-28 cached on 04-30 → stale cache not refreshed).
    if cache_key in _cache and today in _cache[cache_key]:
        return

    # Fetch current month ± 7 days to cover edges
    start = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    end = (datetime.now() + timedelta(days=37)).strftime("%Y-%m-%d")

    data = _fetch_trading_days(market, start, end)
    if data is not None:
        # Group by month — Futu returns a flat date range, split into month buckets
        for date_str, trade_type in data.items():
            month_key = date_str[:7]  # "2026-04"
            month_cache_key = (market.upper(), month_key)
            if month_cache_key not in _cache:
                _cache[month_cache_key] = {}
            _cache[month_cache_key][date_str] = trade_type
        _cache_date = today
        _save_cache_to_db()
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
        trade_type = cal.get(date_str)
        # WHOLE=全天交易, MORNING=上午交易, AFTERNOON=下午交易
        # 不在日历里 = 周末或假期（非交易日，不要 fallback 到 weekday）
        return trade_type in ("WHOLE", "MORNING", "AFTERNOON")

    # Fallback: weekday check (only when cache is completely empty, e.g., Futu unreachable)
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
