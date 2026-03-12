"""Fetch daily klines → daily_kline table.

Uses KlineProvider abstraction with market-specific fallback chains:
  - A-share: akshare (EM) → Tencent Finance → SQLite cache
  - HK:      Tencent Finance → SQLite cache  (Futu requires OpenD)

Usage:
    poetry run python -m src.sim_trading.kline_fetcher
    poetry run python -m src.sim_trading.kline_fetcher --codes HK09988,HK00700
    poetry run python -m src.sim_trading.kline_fetcher --months 12
"""

import argparse
import datetime
import logging
import time

from src.sim_trading.db import get_connection, init_db
from src.sim_trading.kline_provider import (
    CompositeKlineProvider,
    TencentKlineProvider,
    AkshareKlineProvider,
    SqliteKlineProvider,
    detect_market,
)

logger = logging.getLogger(__name__)


def fetch_klines(codes: list[str] | None = None, months: int = 9):
    """Fetch daily klines for stocks and store in SQLite.

    Args:
        codes: List of stock codes. If None, reads from monitor_watchlist.
        months: Number of months of history (default 9).
    """
    init_db()
    conn = get_connection()

    if codes is None:
        rows = conn.execute(
            "SELECT symbol FROM monitor_watchlist WHERE hidden = 0"
        ).fetchall()
        codes = [r["symbol"] for r in rows]
        # Exclude KR (Korean stocks, no data source)
        codes = [c for c in codes if not c.startswith("KR")]
        if not codes:
            logger.warning("No stocks in monitor_watchlist")
            conn.close()
            return 0

    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=months * 30 + 30)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    logger.info(f"Fetching {months}-month klines for {len(codes)} stocks "
                f"({start_str} ~ {end_str})")

    # Build providers per market (exclude SQLite to avoid reading stale cache)
    tencent = TencentKlineProvider()
    akshare = AkshareKlineProvider()
    provider_a = CompositeKlineProvider([akshare, tencent])
    provider_hk = CompositeKlineProvider([tencent])  # Tencent is primary for batch

    total_inserted = 0
    for i, code in enumerate(codes):
        logger.info(f"[{i+1}/{len(codes)}] {code}")

        provider = provider_hk if detect_market(code) == "HK" else provider_a
        df = provider.fetch_daily(code, start=start_str, end=end_str)

        if df is None or df.empty:
            logger.warning(f"  No data for {code}")
            time.sleep(0.5)
            continue

        rows_to_insert = []
        for _, row in df.iterrows():
            rows_to_insert.append((
                row["date"], code,
                row["open"], row["high"], row["low"], row["close"],
                row["volume"], 0,  # turnover not always available
                round(row.get("change_pct", 0) or 0, 4),
            ))

        conn.executemany(
            """INSERT OR REPLACE INTO daily_kline
               (date, code, open, high, low, close, volume, turnover, change_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows_to_insert,
        )
        conn.commit()
        total_inserted += len(rows_to_insert)
        logger.info(f"  {len(rows_to_insert)} bars inserted")

        # Rate limit: be polite to APIs
        if i < len(codes) - 1:
            time.sleep(0.8)

    conn.close()
    logger.info(f"Done. Total {total_inserted} bars for {len(codes)} stocks.")
    return total_inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(description="Fetch daily klines")
    parser.add_argument("--codes", type=str, help="Comma-separated codes (e.g. HK09988,HK00700)")
    parser.add_argument("--months", type=int, default=9, help="Months of history (default 9)")
    args = parser.parse_args()

    codes = args.codes.split(",") if args.codes else None
    n = fetch_klines(codes=codes, months=args.months)
    print(f"\nTotal bars inserted: {n}")
