"""Migrate monitor config JSON into SQLite and verify parity.

Usage examples:
  poetry run python -m src.tools.monitor_config_db_migrator --action import
  poetry run python -m src.tools.monitor_config_db_migrator --action verify
  poetry run python -m src.tools.monitor_config_db_migrator --action import-verify
  poetry run python -m src.tools.monitor_config_db_migrator --action export
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from src.sim_trading.db import get_config_connection, init_db
from src.utils.audit_log import insert_config_outbox
from src.utils.audit_system import build_audit_event_v2, make_actor

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MONITOR_CONFIG_PATH = DATA_DIR / "monitor_config.json"


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


def _normalize_config(cfg: dict[str, Any]) -> dict[str, Any]:
    watchlist_raw = cfg.get("watchlist", {}) or {}
    if not watchlist_raw:
        holdings_raw = cfg.get("holdings", {}) or {}
        watching_raw = cfg.get("watching", {}) or {}
        merged: dict[str, Any] = {}
        for symbol, raw_entry in holdings_raw.items():
            entry = dict(raw_entry or {})
            entry["type"] = "holding"
            merged[symbol] = entry
        for symbol, raw_entry in watching_raw.items():
            if symbol in merged:
                continue
            entry = dict(raw_entry or {})
            entry["type"] = "watching"
            merged[symbol] = entry
        watchlist_raw = merged
    settings_raw = cfg.get("settings", {}) or {}

    normalized_watchlist: dict[str, dict[str, Any]] = {}
    for symbol, raw_entry in sorted(watchlist_raw.items()):
        entry = raw_entry or {}
        list_type = "holding" if entry.get("type") == "holding" else "watching"
        raw_tags = entry.get("tags", [])
        tags = list(raw_tags) if isinstance(raw_tags, (list, tuple)) else []
        normalized_watchlist[symbol] = {
            "name": str(entry.get("name", symbol)),
            "alias": entry.get("alias") or None,
            "list_type": list_type,
            "cost": _num_or_none(entry.get("cost")),
            "shares": _int_or_none(entry.get("shares")),
            "lot": _int_or_none(entry.get("lot")),
            "hidden": bool(entry.get("hidden", False)),
            "star": bool(entry.get("star", False)),
            "tags": tags,
            "watch_price": _num_or_none(entry.get("watch_price")),
            "watch_price_date": entry.get("watch_price_date") or None,
            "pin_order": _int_or_none(entry.get("pin_order")) or 0,
        }

    normalized_settings: dict[str, float] = {}
    for key, val in sorted(settings_raw.items()):
        n = _num_or_none(val)
        if n is not None:
            normalized_settings[key] = n

    return {"watchlist": normalized_watchlist, "settings": normalized_settings}


def read_monitor_config(path: Path = MONITOR_CONFIG_PATH) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return _normalize_config(raw)


def import_json_to_db(cfg: dict[str, Any]) -> tuple[int, int]:
    now_ts = int(time.time())
    conn = get_config_connection()
    try:
        conn.execute("BEGIN")
        before = _read_config_from_conn(conn)
        conn.execute("DELETE FROM monitor_watchlist")
        conn.execute("DELETE FROM monitor_settings")

        for symbol, entry in cfg["watchlist"].items():
            tags_json = json.dumps(entry.get("tags", []), ensure_ascii=False)
            conn.execute(
                """
                INSERT INTO monitor_watchlist (
                    symbol, name, alias, list_type, cost, shares, lot, hidden, star,
                    tags, watch_price, watch_price_date, pin_order,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    symbol,
                    entry["name"],
                    entry.get("alias"),
                    entry["list_type"],
                    entry["cost"],
                    entry["shares"],
                    entry["lot"],
                    1 if entry["hidden"] else 0,
                    1 if entry["star"] else 0,
                    tags_json,
                    entry.get("watch_price"),
                    entry.get("watch_price_date"),
                    int(entry.get("pin_order", 0) or 0),
                    now_ts,
                    now_ts,
                ),
            )

        for key, value in cfg["settings"].items():
            conn.execute(
                "INSERT INTO monitor_settings(key, value, updated_at) VALUES (?, ?, ?)",
                (key, value, now_ts),
            )
        after = _read_config_from_conn(conn)
        event = build_audit_event_v2(
            event_id=uuid.uuid4().hex,
            ts_ms=int(time.time() * 1000),
            source="monitor_config_db_migrator",
            actor=make_actor(
                actor_type="system", actor_id="monitor_config_db_migrator"
            ),
            action="import",
            entity="monitor_config",
            key="config",
            db_name="config.db",
            before=before,
            after=after,
            metadata={
                "watchlist_count": len(cfg["watchlist"]),
                "settings_count": len(cfg["settings"]),
            },
        )
        insert_config_outbox(conn, event)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    return len(cfg["watchlist"]), len(cfg["settings"])


def _read_config_from_conn(conn) -> dict[str, Any]:
    watch_rows = conn.execute(
        """
        SELECT symbol, name, alias, list_type, cost, shares, lot, hidden, star,
               tags, watch_price, watch_price_date, pin_order
        FROM monitor_watchlist
        ORDER BY symbol
        """
    ).fetchall()
    settings_rows = conn.execute(
        "SELECT key, value FROM monitor_settings ORDER BY key"
    ).fetchall()

    watchlist: dict[str, dict[str, Any]] = {}
    for r in watch_rows:
        tags_raw = r["tags"] if "tags" in r.keys() else "[]"
        try:
            tags = json.loads(tags_raw) if tags_raw else []
        except (json.JSONDecodeError, TypeError):
            tags = []
        watchlist[r["symbol"]] = {
            "name": str(r["name"]),
            "alias": r["alias"] if "alias" in r.keys() else None,
            "list_type": "holding" if r["list_type"] == "holding" else "watching",
            "cost": _num_or_none(r["cost"]),
            "shares": _int_or_none(r["shares"]),
            "lot": _int_or_none(r["lot"]),
            "hidden": bool(r["hidden"]),
            "star": bool(r["star"]),
            "tags": tags if isinstance(tags, list) else [],
            "watch_price": _num_or_none(r["watch_price"] if "watch_price" in r.keys() else None),
            "watch_price_date": r["watch_price_date"] if "watch_price_date" in r.keys() else None,
            "pin_order": _int_or_none(r["pin_order"] if "pin_order" in r.keys() else None)
            or 0,
        }

    settings: dict[str, float] = {}
    for r in settings_rows:
        n = _num_or_none(r["value"])
        if n is not None:
            settings[str(r["key"])] = n

    return {"watchlist": watchlist, "settings": settings}


def read_config_from_db() -> dict[str, Any]:
    conn = get_config_connection()
    try:
        return _read_config_from_conn(conn)
    finally:
        conn.close()


def _from_db_normalized_to_snapshot(cfg: dict[str, Any]) -> dict[str, Any]:
    watchlist_out: dict[str, dict[str, Any]] = {}
    holdings_out: dict[str, dict[str, Any]] = {}
    watching_out: dict[str, dict[str, Any]] = {}
    for symbol, entry in sorted(cfg["watchlist"].items()):
        out: dict[str, Any] = {"name": entry["name"]}
        if entry.get("alias"):
            out["alias"] = entry["alias"]
        if entry["list_type"] == "holding":
            out["type"] = "holding"
        if entry["cost"] is not None:
            out["cost"] = entry["cost"]
        if entry["shares"] is not None:
            out["shares"] = entry["shares"]
        if entry["lot"] is not None:
            out["lot"] = entry["lot"]
        if entry["hidden"]:
            out["hidden"] = True
        if entry["star"]:
            out["star"] = True
        tags = entry.get("tags", [])
        if tags:
            out["tags"] = tags
        if entry.get("watch_price") is not None:
            out["watch_price"] = entry["watch_price"]
        if entry.get("watch_price_date") is not None:
            out["watch_price_date"] = entry["watch_price_date"]
        if entry.get("pin_order"):
            out["pin_order"] = entry["pin_order"]
        watchlist_out[symbol] = out
        split_out = dict(out)
        split_out.pop("type", None)
        if entry["list_type"] == "holding":
            holdings_out[symbol] = split_out
        else:
            watching_out[symbol] = split_out

    settings_out: dict[str, Any] = {}
    for key, val in sorted(cfg["settings"].items()):
        settings_out[key] = int(val) if float(val).is_integer() else val

    return {
        "watchlist": watchlist_out,
        "holdings": holdings_out,
        "watching": watching_out,
        "settings": settings_out,
    }


def write_snapshot_from_db(path: Path = MONITOR_CONFIG_PATH) -> None:
    cfg = read_config_from_db()
    snapshot = _from_db_normalized_to_snapshot(cfg)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp_path.replace(path)


def verify_parity(file_cfg: dict[str, Any], db_cfg: dict[str, Any]) -> tuple[bool, str]:
    if file_cfg == db_cfg:
        return True, "Parity OK: JSON and DB are semantically identical."

    file_symbols = set(file_cfg["watchlist"].keys())
    db_symbols = set(db_cfg["watchlist"].keys())
    missing_in_db = sorted(file_symbols - db_symbols)
    extra_in_db = sorted(db_symbols - file_symbols)

    changed = []
    for sym in sorted(file_symbols & db_symbols):
        if file_cfg["watchlist"][sym] != db_cfg["watchlist"][sym]:
            changed.append(sym)

    file_settings = file_cfg["settings"]
    db_settings = db_cfg["settings"]
    setting_diff = sorted(
        set(file_settings.keys()) ^ set(db_settings.keys())
    )
    for k in sorted(set(file_settings.keys()) & set(db_settings.keys())):
        if file_settings[k] != db_settings[k]:
            setting_diff.append(k)

    parts = []
    if missing_in_db:
        parts.append(f"missing_in_db={len(missing_in_db)}")
    if extra_in_db:
        parts.append(f"extra_in_db={len(extra_in_db)}")
    if changed:
        parts.append(f"changed_symbols={len(changed)}")
    if setting_diff:
        parts.append(f"setting_diff={len(setting_diff)}")

    detail = ", ".join(parts) if parts else "unknown mismatch"
    return False, f"Parity FAILED: {detail}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Monitor config DB migrator")
    parser.add_argument(
        "--action",
        choices=["import", "verify", "import-verify", "export"],
        default="verify",
        help="Operation to run",
    )
    args = parser.parse_args()

    init_db()

    if args.action == "import":
        cfg = read_monitor_config()
        watch_count, settings_count = import_json_to_db(cfg)
        print(
            f"Imported monitor config -> DB: {watch_count} watchlist rows, {settings_count} setting rows"
        )
        return 0

    if args.action == "verify":
        file_cfg = read_monitor_config()
        db_cfg = read_config_from_db()
        ok, msg = verify_parity(file_cfg, db_cfg)
        print(msg)
        return 0 if ok else 1

    if args.action == "import-verify":
        file_cfg = read_monitor_config()
        watch_count, settings_count = import_json_to_db(file_cfg)
        print(
            f"Imported monitor config -> DB: {watch_count} watchlist rows, {settings_count} setting rows"
        )
        db_cfg = read_config_from_db()
        ok, msg = verify_parity(file_cfg, db_cfg)
        print(msg)
        return 0 if ok else 1

    if args.action == "export":
        write_snapshot_from_db()
        print(f"Exported DB snapshot -> {MONITOR_CONFIG_PATH}")
        return 0

    print(f"Unsupported action: {args.action}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
