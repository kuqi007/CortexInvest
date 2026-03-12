"""Fetch 9-month daily klines → daily_kline table.

Primary source: Tencent Finance (web.ifzq.gtimg.cn) — no Futu dependency.
Fallback: Futu OpenD (if running).

Usage:
    poetry run python -m src.sim_trading.kline_fetcher
    poetry run python -m src.sim_trading.kline_fetcher --codes HK09988,HK00700
    poetry run python -m src.sim_trading.kline_fetcher --months 12
"""

import argparse
import datetime
import json
import logging
import time

import requests

from src.sim_trading.db import get_connection, init_db

logger = logging.getLogger(__name__)


def fetch_klines(codes: list[str] | None = None, months: int = 9):
    """Fetch daily klines for stocks and store in SQLite.

    Args:
        codes: List of stock codes. If None, reads HK stocks from monitor_watchlist.
        months: Number of months of history (default 9).
    """
    init_db()
    conn = get_connection()

    if codes is None:
        rows = conn.execute(
            "SELECT symbol FROM monitor_watchlist WHERE symbol LIKE 'HK%'"
        ).fetchall()
        codes = [r["symbol"] for r in rows]
        if not codes:
            logger.warning("No HK stocks in monitor_watchlist")
            conn.close()
            return 0

    today = datetime.date.today()
    start_date = today - datetime.timedelta(days=months * 30 + 30)
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = today.strftime("%Y-%m-%d")

    logger.info(f"Fetching {months}-month klines for {len(codes)} stocks "
                f"({start_str} ~ {end_str})")

    total_inserted = 0
    for i, code in enumerate(codes):
        logger.info(f"[{i+1}/{len(codes)}] {code}")

        bars = _fetch_tencent_kline(code, start_str, end_str)
        if not bars:
            logger.warning(f"  No data for {code}")
            time.sleep(0.5)
            continue

        rows_to_insert = []
        prev_close = None
        for bar in bars:
            change_pct = bar.get("change_pct", 0)
            if prev_close and prev_close > 0 and change_pct == 0:
                change_pct = (bar["close"] / prev_close - 1) * 100
            rows_to_insert.append((
                bar["date"], code,
                bar["open"], bar["high"], bar["low"], bar["close"],
                bar["volume"], bar.get("turnover", 0),
                round(change_pct, 4),
            ))
            prev_close = bar["close"]

        conn.executemany(
            """INSERT OR REPLACE INTO daily_kline
               (date, code, open, high, low, close, volume, turnover, change_pct)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            rows_to_insert,
        )
        conn.commit()
        total_inserted += len(rows_to_insert)
        logger.info(f"  {len(rows_to_insert)} bars inserted")

        # Rate limit: be polite to tencent API
        if i < len(codes) - 1:
            time.sleep(0.8)

    conn.close()
    logger.info(f"Done. Total {total_inserted} bars for {len(codes)} stocks.")
    return total_inserted


def _fetch_tencent_kline(code: str, start: str, end: str) -> list[dict] | None:
    """Fetch QFQ daily kline from Tencent Finance.

    Returns list of {date, open, high, low, close, volume} or None.
    Tencent format: [date, open, close, high, low, volume, ...]
    """
    prefix = _tencent_prefix(code)
    raw_code = _raw_code(code)
    url = (
        f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
        f"?param={prefix}{raw_code},day,{start},{end},500,qfq"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        data = r.json()

        stock_key = f"{prefix}{raw_code}"
        kline = data.get("data", {}).get(stock_key, {})
        days = kline.get("qfqday") or kline.get("day", [])
        if not days:
            return None

        result = []
        prev_close = None
        for row in days:
            date_str = row[0]
            open_p = float(row[1])
            close_p = float(row[2])
            high_p = float(row[3])
            low_p = float(row[4])
            volume = float(row[5]) if len(row) > 5 and isinstance(row[5], (int, float, str)) else 0
            # row[5] might be a string like "12345.000" or a dict (metadata)
            if isinstance(row[5], dict):
                volume = 0
            else:
                try:
                    volume = float(row[5])
                except (ValueError, TypeError):
                    volume = 0

            change_pct = 0
            if prev_close and prev_close > 0:
                change_pct = (close_p / prev_close - 1) * 100

            result.append({
                "date": date_str,
                "open": open_p,
                "high": high_p,
                "low": low_p,
                "close": close_p,
                "volume": volume,
                "change_pct": round(change_pct, 4),
            })
            prev_close = close_p

        return result
    except Exception as exc:
        logger.warning(f"Tencent kline {code} failed: {exc}")
        return None


def _tencent_prefix(code: str) -> str:
    """Market prefix for Tencent API."""
    if code.startswith("HK"):
        return "hk"
    raw = _raw_code(code)
    if raw.startswith(("6", "9")):
        return "sh"
    return "sz"


def _raw_code(code: str) -> str:
    """Strip market prefix: HK09988 → 09988, 000792 → 000792."""
    if code.startswith("HK"):
        return code[2:]
    if code.startswith("KR"):
        return code[2:]
    return code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")

    parser = argparse.ArgumentParser(description="Fetch daily klines from Tencent Finance")
    parser.add_argument("--codes", type=str, help="Comma-separated codes (e.g. HK09988,HK00700)")
    parser.add_argument("--months", type=int, default=9, help="Months of history (default 9)")
    args = parser.parse_args()

    codes = args.codes.split(",") if args.codes else None
    n = fetch_klines(codes=codes, months=args.months)
    print(f"\nTotal bars inserted: {n}")
