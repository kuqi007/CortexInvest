"""Unified config reader — DB first, JSON as backup.

All Python tools should use this module to read monitor config.
- Primary source: SQLite (sim_trading.db)
- Backup source: JSON (monitor_config.json)

Usage:
    from src.utils.config_reader import read_monitor_config
    
    cfg = read_monitor_config()
    watchlist = cfg["watchlist"]  # {symbol: {...}}
    settings = cfg["settings"]    # {key: value}
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.sim_trading.db import get_config_connection

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
JSON_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"


def _num_or_none(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int_or_none(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _read_from_db() -> dict[str, Any] | None:
    """Read config from SQLite. Returns None if DB/table not available."""
    try:
        conn = get_config_connection()

        # Check if table exists
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='monitor_watchlist'"
        )
        if not cursor.fetchone():
            conn.close()
            return None

        # Read watchlist
        rows = conn.execute(
            """
            SELECT symbol, name, alias, list_type, cost, shares, lot,
                   hidden, star, dip_buy, tags, watch_price, watch_price_date
            FROM monitor_watchlist
            ORDER BY symbol
            """
        ).fetchall()

        # Read settings
        settings_rows = conn.execute(
            "SELECT key, value FROM monitor_settings ORDER BY key"
        ).fetchall()

        conn.close()
        
        watchlist: dict[str, dict[str, Any]] = {}
        for r in rows:
            entry: dict[str, Any] = {"name": r["name"]}
            
            if r["alias"]:
                entry["alias"] = r["alias"]
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
        
    except Exception:
        return None


def _read_from_json() -> dict[str, Any]:
    """Read config from JSON file (fallback)."""
    raw = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    
    # Normalize: merge holdings + watching into watchlist
    watchlist_raw = raw.get("watchlist", {}) or {}
    if not watchlist_raw:
        holdings_raw = raw.get("holdings", {}) or {}
        watching_raw = raw.get("watching", {}) or {}
        merged: dict[str, Any] = {}
        
        for symbol, entry in (holdings_raw or {}).items():
            entry = dict(entry or {})
            entry["type"] = "holding"
            merged[symbol] = entry
        
        for symbol, entry in (watching_raw or {}).items():
            if symbol in merged:
                continue
            entry = dict(entry or {})
            entry["type"] = "watching"
            merged[symbol] = entry
        
        watchlist_raw = merged
    
    # Normalize entries
    normalized_watchlist: dict[str, dict[str, Any]] = {}
    for symbol, raw_entry in sorted(watchlist_raw.items()):
        entry = raw_entry or {}
        list_type = "holding" if entry.get("type") == "holding" else "watching"
        raw_tags = entry.get("tags", [])
        tags = list(raw_tags) if isinstance(raw_tags, (list, tuple)) else []
        
        normalized_watchlist[symbol] = {
            "name": str(entry.get("name", symbol)),
            "type": list_type,
            "cost": _num_or_none(entry.get("cost")),
            "shares": _int_or_none(entry.get("shares")),
            "lot": _int_or_none(entry.get("lot")),
            "hidden": bool(entry.get("hidden", False)),
            "star": bool(entry.get("star", False)),
            "dip_buy": bool(entry.get("dip_buy", False)),
            "tags": tags,
            "watch_price": _num_or_none(entry.get("watch_price")),
            "watch_price_date": entry.get("watch_price_date") or None,
        }
        
        # Remove None values for cleaner output
        normalized_watchlist[symbol] = {
            k: v for k, v in normalized_watchlist[symbol].items() 
            if v is not None and v != False and v != []
        }
        if tags:
            normalized_watchlist[symbol]["tags"] = tags
    
    # Normalize settings
    settings_raw = raw.get("settings", {}) or {}
    normalized_settings: dict[str, float] = {}
    for key, val in sorted(settings_raw.items()):
        n = _num_or_none(val)
        if n is not None:
            normalized_settings[key] = n
    
    return {"watchlist": normalized_watchlist, "settings": normalized_settings}


def read_monitor_config(prefer_db: bool = True) -> dict[str, Any]:
    """Read monitor config from DB (primary) or JSON (fallback).
    
    Args:
        prefer_db: If True, try DB first; if False, read JSON directly
    
    Returns:
        dict with keys: watchlist, settings
        watchlist: {symbol: {name, type?, cost?, shares?, lot?, hidden?, star?, dip_buy?, tags?, watch_price?, watch_price_date?}}
        settings: {key: value}
    """
    if prefer_db:
        db_config = _read_from_db()
        if db_config is not None:
            return db_config
    
    return _read_from_json()


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
