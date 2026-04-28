"""Unified config reader — DB-only runtime source.

All Python tools should use this module to read monitor config.
- Primary source: SQLite config.db
- JSON files are audit/archive only and are not runtime fallback.

Usage:
    from src.utils.config_reader import read_monitor_config
    
    cfg = read_monitor_config()
    watchlist = cfg["watchlist"]  # {symbol: {...}}
    settings = cfg["settings"]    # {key: value}
"""

from __future__ import annotations

import json
from typing import Any

from src.sim_trading.db import get_config_connection


def _read_from_db() -> dict[str, Any]:
    """Read config from SQLite. Raises when DB/table is not available."""
    conn = None
    try:
        conn = get_config_connection()

        # Check if table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='monitor_watchlist'"
        )
        if not cursor.fetchone():
            raise RuntimeError("config.db monitor_watchlist table is missing")

        # Read watchlist
        rows = conn.execute(
            """
            SELECT symbol, name, list_type, cost, shares, lot,
                   hidden, star, dip_buy, tags, watch_price, watch_price_date
            FROM monitor_watchlist
            ORDER BY symbol
            """
        ).fetchall()

        # Read settings
        settings_rows = conn.execute(
            "SELECT key, value FROM monitor_settings ORDER BY key"
        ).fetchall()

        watchlist: dict[str, dict[str, Any]] = {}
        for r in rows:
            entry: dict[str, Any] = {"name": r["name"]}
            
            if r["list_type"]:
                entry["type"] = r["list_type"]
            if r["cost"] is not None:
                entry["cost"] = float(r["cost"])
            if r["shares"] is not None:
                entry["shares"] = int(r["shares"])
            if r["lot"] is not None:
                entry["lot"] = int(r["lot"])
            if r["hidden"]:
                entry["hidden"] = True
            if r["star"]:
                entry["star"] = True
            if r["dip_buy"]:
                entry["dip_buy"] = True
            
            # Parse tags JSON
            if r["tags"]:
                try:
                    tags = json.loads(r["tags"])
                    if isinstance(tags, list) and tags:
                        entry["tags"] = tags
                except json.JSONDecodeError:
                    pass
            
            if r["watch_price"] is not None:
                entry["watch_price"] = float(r["watch_price"])
            if r["watch_price_date"]:
                entry["watch_price_date"] = r["watch_price_date"]
            
            watchlist[r["symbol"]] = entry
        
        settings: dict[str, float] = {}
        for r in settings_rows:
            if r["value"] is not None:
                settings[r["key"]] = float(r["value"])
        
        return {"watchlist": watchlist, "settings": settings}
        
    except Exception as exc:
        raise RuntimeError(f"Failed to read monitor config from config.db: {exc}") from exc
    finally:
        if conn is not None:
            conn.close()

def read_monitor_config(prefer_db: bool = True) -> dict[str, Any]:
    """Read monitor config from config.db only.
    
    Args:
        prefer_db: Deprecated compatibility argument; ignored.
    
    Returns:
        dict with keys: watchlist, settings
        watchlist: {symbol: {name, type?, cost?, shares?, lot?, hidden?, star?, dip_buy?, tags?, watch_price?, watch_price_date?}}
        settings: {key: value}
    """
    return _read_from_db()


def get_watchlist_codes() -> list[str]:
    """Get list of all watchlist symbols."""
    cfg = read_monitor_config()
    return list(cfg.get("watchlist", {}).keys())


def get_holdings_codes() -> list[str]:
    """Get list of holding symbols only."""
    cfg = read_monitor_config()
    watchlist = cfg.get("watchlist", {})
    return [s for s, e in watchlist.items() if e.get("type") == "holding"]


def get_dip_buy_codes() -> list[str]:
    """Get list of symbols with dip_buy=True."""
    cfg = read_monitor_config()
    watchlist = cfg.get("watchlist", {})
    return [s for s, e in watchlist.items() if e.get("dip_buy")]


if __name__ == "__main__":
    # Test the reader
    cfg = read_monitor_config()
    print(f"Watchlist count: {len(cfg['watchlist'])}")
    print(f"Settings count: {len(cfg['settings'])}")
    
    # Show first 3 entries
    for symbol, entry in list(cfg["watchlist"].items())[:3]:
        print(f"  {symbol}: {entry}")
