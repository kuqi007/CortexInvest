#!/usr/bin/env python3
"""Migrate sim_trading.db → config.db + trading.db (file-copy based).

Idempotent: skips if config.db and trading.db already exist and sim_trading.db is absent.
"""

import shutil
import sqlite3
import sys
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "src" / "data"
LEGACY_DB = DATA_DIR / "sim_trading.db"
CONFIG_DB = DATA_DIR / "config.db"
TRADING_DB = DATA_DIR / "trading.db"
BACKUP_DB = DATA_DIR / "sim_trading.db.pre-split.bak"

CONFIG_TABLES = {"monitor_watchlist", "monitor_settings", "tag_meta", "position_change_log"}


def get_tables(db_path: Path) -> set[str]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    conn.close()
    return {r[0] for r in rows}


def get_row_counts(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    tables = get_tables(db_path)
    counts = {}
    for t in tables:
        counts[t] = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()[0]
    conn.close()
    return counts


def main():
    # Already migrated? Only check config.db + trading.db (OneDrive may restore sim_trading.db)
    if CONFIG_DB.exists() and TRADING_DB.exists():
        print("Already migrated. Nothing to do.")
        return

    if not LEGACY_DB.exists():
        print("No legacy sim_trading.db found. Fresh install.")
        return

    print(f"Migrating {LEGACY_DB} ({LEGACY_DB.stat().st_size / 1024 / 1024:.1f} MB)...")

    # Step 1: Checkpoint WAL
    conn = sqlite3.connect(str(LEGACY_DB))
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception:
        pass
    conn.close()

    # Remove any leftover split files from a previous failed migration
    CONFIG_DB.unlink(missing_ok=True)
    TRADING_DB.unlink(missing_ok=True)

    # Step 2: Verify legacy tables
    legacy_tables = get_tables(LEGACY_DB)
    print(f"  Legacy tables: {len(legacy_tables)}")
    legacy_counts = get_row_counts(LEGACY_DB)

    # Step 3: Copy → config.db, drop trading tables, VACUUM
    print("  Creating config.db...")
    shutil.copy2(str(LEGACY_DB), str(CONFIG_DB))
    conn = sqlite3.connect(str(CONFIG_DB))
    conn.execute("PRAGMA journal_mode=DELETE")
    trading_tables = legacy_tables - CONFIG_TABLES
    for t in trading_tables:
        conn.execute(f"DROP TABLE IF EXISTS [{t}]")
    # Drop indexes that belong to trading tables
    for idx_row in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        if idx_row[1] not in CONFIG_TABLES:
            conn.execute(f"DROP INDEX IF EXISTS [{idx_row[0]}]")
    conn.execute("VACUUM")
    conn.close()
    print(f"  config.db: {CONFIG_DB.stat().st_size / 1024:.0f} KB")

    # Step 4: Copy → trading.db, drop config tables
    print("  Creating trading.db...")
    shutil.copy2(str(LEGACY_DB), str(TRADING_DB))
    conn = sqlite3.connect(str(TRADING_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    for t in CONFIG_TABLES:
        conn.execute(f"DROP TABLE IF EXISTS [{t}]")
    for idx_row in conn.execute(
        "SELECT name, tbl_name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
    ).fetchall():
        if idx_row[1] in CONFIG_TABLES:
            conn.execute(f"DROP INDEX IF EXISTS [{idx_row[0]}]")
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    # Step 5: Verify
    config_counts = get_row_counts(CONFIG_DB)
    trading_counts = get_row_counts(TRADING_DB)
    print("\n  Verification:")
    all_ok = True
    for t, expected in legacy_counts.items():
        actual = config_counts.get(t, trading_counts.get(t, 0))
        status = "OK" if actual == expected else "MISMATCH"
        if status != "OK":
            all_ok = False
        print(f"    {t}: {expected} → {actual} [{status}]")

    if not all_ok:
        print("\n  WARNING: Row count mismatch detected!")
        print("  Keeping legacy DB. Manual investigation required.")
        CONFIG_DB.unlink(missing_ok=True)
        TRADING_DB.unlink(missing_ok=True)
        sys.exit(1)

    # Step 6: Backup legacy
    shutil.move(str(LEGACY_DB), str(BACKUP_DB))
    print(f"\n  Legacy backed up to: {BACKUP_DB}")
    print("  Migration complete!")


if __name__ == "__main__":
    main()
