#!/usr/bin/env python3
"""回填 session_snapshots 和 daily_l2_digest 从归档的 l2_strategy_signals.json 文件。"""

import json
import argparse
from datetime import datetime
from pathlib import Path
import sys

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))
from src.sim_trading.db import get_connection


def backfill_session_snapshots(archive_dir: Path, dates: list[str] | None = None) -> int:
    """从归档的 l2_strategy_signals.json 回填 session_snapshots 表。"""
    conn = get_connection()
    saved = 0

    # Find all archive files
    if dates:
        files = [archive_dir / f"l2_strategy_signals_{d}.json" for d in dates]
    else:
        files = sorted(archive_dir.glob("l2_strategy_signals_*.json"))

    for f in files:
        if not f.exists():
            print(f"跳过: {f} 不存在")
            continue

        date_str = f.stem.replace("l2_strategy_signals_", "")
        print(f"处理: {f.name} ...")

        with open(f, encoding="utf-8") as fp:
            try:
                data = json.load(fp)
            except json.JSONDecodeError as e:
                print(f"  错误: JSON 解析失败 {e}")
                continue

        session = data.get("session", {})
        if not session:
            print(f"  跳过: 无 session 数据")
            continue

        ts = int(datetime.now().timestamp() * 1000)
        t = datetime.now().strftime("%H:%M:%S")

        for code, ctx in session.items():
            if not code or not isinstance(ctx, dict):
                continue
            try:
                conn.execute(
                    """INSERT OR REPLACE INTO session_snapshots
                       (ts, date, time, code, session_json)
                       VALUES (?, ?, ?, ?, ?)""",
                    (ts, date_str, t, code, json.dumps(ctx, ensure_ascii=False)),
                )
                saved += 1
            except Exception as e:
                print(f"  警告: {code} 写入失败: {e}")

        conn.commit()
        print(f"  完成: {saved} 条记录")

    conn.close()
    return saved


def backfill_daily_l2_digest(archive_dir: Path, dates: list[str] | None = None):
    """从归档的 l2_strategy_signals.json 回填 daily_l2_digest 表。"""
    from src.tools.daily_summary_generator import _compute_l2_digest

    if dates:
        for date_str in dates:
            print(f"计算 L2 digest: {date_str} ...")
            digests = _compute_l2_digest(date_str)
            print(f"  完成: {len(digests)} 条记录")
    else:
        # Find all dates with session_snapshots
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT date FROM session_snapshots ORDER BY date"
        ).fetchall()
        conn.close()

        for row in rows:
            date_str = row[0]
            print(f"计算 L2 digest: {date_str} ...")
            digests = _compute_l2_digest(date_str)
            print(f"  完成: {len(digests)} 条记录")


def main():
    parser = argparse.ArgumentParser(description="回填 session_snapshots 和 daily_l2_digest")
    parser.add_argument(
        "--dates",
        nargs="+",
        help="指定日期 (如 2026-03-13 2026-03-16)，默认全部",
    )
    parser.add_argument(
        "--skip-digest",
        action="store_true",
        help="跳过 daily_l2_digest 计算",
    )
    parser.add_argument(
        "--archive-dir",
        default="src/data/archive",
        help="归档目录",
    )
    args = parser.parse_args()

    archive_dir = Path(args.archive_dir)

    # Step 1: Backfill session_snapshots
    print("=" * 50)
    print("Step 1: 回填 session_snapshots")
    print("=" * 50)
    saved = backfill_session_snapshots(archive_dir, args.dates)
    print(f"\n共写入 {saved} 条 session 记录")

    # Step 2: Compute daily_l2_digest
    if not args.skip_digest:
        print("\n" + "=" * 50)
        print("Step 2: 计算 daily_l2_digest")
        print("=" * 50)
        backfill_daily_l2_digest(archive_dir, args.dates)

    print("\n完成!")


if __name__ == "__main__":
    main()
