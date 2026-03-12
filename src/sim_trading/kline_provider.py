"""Unified kline data provider abstraction.

KlineProvider ABC with market-specific implementations and automatic fallback:
  - A-share: akshare (EM) → Tencent Finance → SQLite cache
  - HK:      Futu OpenD   → Tencent Finance → SQLite cache

Usage:
    provider = get_provider("HK")          # HK with Futu primary
    provider = get_provider("A")           # A-share with akshare primary
    provider = get_provider("auto", code)  # auto-detect from code prefix

    bars = provider.fetch_daily(code, days=120)  # → pd.DataFrame | None
"""

import datetime
import logging
import time
from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

# ── Shared helpers ───────────────────────────────────────────────────────

_COLUMNS = ["date", "open", "high", "low", "close", "volume", "change_pct"]


def detect_market(code: str) -> str:
    """Detect market from code prefix: 'HK' | 'A'."""
    if code.startswith("HK"):
        return "HK"
    return "A"


def _raw_code(code: str) -> str:
    """Strip market prefix: HK09988 → 09988, 000792 → 000792."""
    if code.startswith("HK"):
        return code[2:]
    if code.startswith("KR"):
        return code[2:]
    return code


def _tencent_prefix(code: str) -> str:
    """Market prefix for Tencent API."""
    if code.startswith("HK"):
        return "hk"
    raw = _raw_code(code)
    if raw.startswith(("6", "9", "5")):
        return "sh"
    return "sz"


def _date_range(days: int) -> tuple[str, str]:
    """Return (start, end) date strings for N calendar days back."""
    today = datetime.date.today()
    start = today - datetime.timedelta(days=days)
    return start.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d")


# ── ABC ──────────────────────────────────────────────────────────────────


class KlineProvider(ABC):
    """Abstract base for daily kline data retrieval."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name for logging."""

    @abstractmethod
    def fetch_daily(
        self,
        code: str,
        start: str | None = None,
        end: str | None = None,
        days: int = 120,
    ) -> Optional[pd.DataFrame]:
        """Fetch daily OHLCV bars.

        Args:
            code: Stock code (e.g. 'HK09988', '000792').
            start: Start date 'YYYY-MM-DD'. If None, computed from `days`.
            end: End date 'YYYY-MM-DD'. If None, today.
            days: Calendar days of history (used when start/end not given).

        Returns:
            DataFrame with columns [date, open, high, low, close, volume, change_pct]
            sorted by date ascending, or None if unavailable.
        """

    def is_available(self) -> bool:
        """Quick check: is this provider usable right now?"""
        return True


# ── Tencent Finance (HTTP, no external deps) ─────────────────────────────


class TencentKlineProvider(KlineProvider):
    """Tencent Finance QFQ daily kline — pure HTTP, supports HK + A-share."""

    @property
    def name(self) -> str:
        return "tencent"

    def fetch_daily(self, code, start=None, end=None, days=120):
        if start is None or end is None:
            start, end = _date_range(days + 30)  # extra buffer
        prefix = _tencent_prefix(code)
        raw = _raw_code(code)
        url = (
            f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
            f"?param={prefix}{raw},day,{start},{end},500,qfq"
        )
        try:
            r = requests.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()

            stock_key = f"{prefix}{raw}"
            kline = data.get("data", {}).get(stock_key, {})
            rows = kline.get("qfqday") or kline.get("day", [])
            if not rows:
                return None

            records = []
            prev_close = None
            for row in rows:
                # Tencent format: [date, open, close, high, low, volume, ...]
                date_str = row[0]
                open_p = float(row[1])
                close_p = float(row[2])
                high_p = float(row[3])
                low_p = float(row[4])
                vol = 0.0
                if len(row) > 5 and not isinstance(row[5], dict):
                    try:
                        vol = float(row[5])
                    except (ValueError, TypeError):
                        pass

                chg = 0.0
                if prev_close and prev_close > 0:
                    chg = (close_p / prev_close - 1) * 100
                prev_close = close_p

                records.append({
                    "date": date_str, "open": open_p, "high": high_p,
                    "low": low_p, "close": close_p, "volume": vol,
                    "change_pct": round(chg, 4),
                })
            df = pd.DataFrame(records, columns=_COLUMNS)
            return df
        except Exception as exc:
            logger.debug("Tencent kline %s failed: %s", code, exc)
            return None


# ── akshare (A-share primary) ────────────────────────────────────────────


class AkshareKlineProvider(KlineProvider):
    """A-share daily kline via akshare (东方财富 EM push2)."""

    @property
    def name(self) -> str:
        return "akshare"

    def fetch_daily(self, code, start=None, end=None, days=120):
        try:
            import akshare as ak
        except ImportError:
            logger.debug("akshare not installed")
            return None

        if start is None or end is None:
            start, end = _date_range(days + 30)

        raw = _raw_code(code)
        # akshare expects YYYYMMDD format
        start_ak = start.replace("-", "")
        end_ak = end.replace("-", "")
        try:
            df = ak.stock_zh_a_hist(
                symbol=raw, period="daily",
                start_date=start_ak, end_date=end_ak, adjust="qfq",
            )
            if df is None or df.empty:
                return None
            # Rename Chinese columns → English
            col_map = {
                "日期": "date", "开盘": "open", "最高": "high",
                "最低": "low", "收盘": "close", "成交量": "volume",
                "涨跌幅": "change_pct",
            }
            df = df.rename(columns=col_map)
            # Keep only required columns
            keep = [c for c in _COLUMNS if c in df.columns]
            df = df[keep].copy()
            df["date"] = df["date"].astype(str)
            return df.reset_index(drop=True)
        except Exception as exc:
            logger.debug("akshare kline %s failed: %s", code, exc)
            return None


# ── Futu OpenD (HK primary) ─────────────────────────────────────────────


class FutuKlineProvider(KlineProvider):
    """HK daily kline via Futu OpenD — requires OpenD running on port 11111."""

    def __init__(self, ctx=None):
        """
        Args:
            ctx: Futu OpenQuoteContext. If None, will try to create one.
        """
        self._ctx = ctx
        self._unavailable = False  # skip after first failure

    @property
    def name(self) -> str:
        return "futu"

    def _get_ctx(self):
        if self._ctx is not None:
            return self._ctx
        if self._unavailable:
            return None
        try:
            import socket
            # Quick TCP probe before Futu SDK (avoids 60s SDK retry loop)
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            result = sock.connect_ex(("127.0.0.1", 11111))
            sock.close()
            if result != 0:
                self._unavailable = True
                logger.debug("Futu OpenD not listening on port 11111")
                return None

            from futu import OpenQuoteContext
            ctx = OpenQuoteContext(host="127.0.0.1", port=11111)
            self._ctx = ctx
            return ctx
        except Exception as exc:
            self._unavailable = True
            logger.debug("Futu OpenD not available: %s", exc)
            return None

    def is_available(self) -> bool:
        return self._get_ctx() is not None

    def fetch_daily(self, code, start=None, end=None, days=120):
        ctx = self._get_ctx()
        if ctx is None:
            return None
        try:
            from futu import RET_OK, KLType, AuType

            if start is None or end is None:
                s, e = _date_range(days + 80)  # 200 cal days ≈ 135 trading days
            else:
                s, e = start, end

            # Convert to Futu code format: HK09988 → HK.09988, 600036 → SH.600036
            futu_code = self._to_futu_code(code)
            ret, data, _ = ctx.request_history_kline(
                futu_code, ktype=KLType.K_DAY, autype=AuType.QFQ,
                start=s, end=e, max_count=500,
            )
            if ret != RET_OK or data is None or data.empty:
                return None

            # Normalize Futu columns → standard columns
            col_map = {
                "time_key": "date", "open": "open", "high": "high",
                "low": "low", "close": "close", "volume": "volume",
                "change_rate": "change_pct",
            }
            df = data.rename(columns=col_map)
            keep = [c for c in _COLUMNS if c in df.columns]
            df = df[keep].copy()
            # Futu date format: "2025-03-12 00:00:00" → "2025-03-12"
            df["date"] = df["date"].astype(str).str[:10]
            return df.reset_index(drop=True)
        except Exception as exc:
            logger.debug("Futu kline %s failed: %s", code, exc)
            return None

    @staticmethod
    def _to_futu_code(code: str) -> str:
        """Convert internal code to Futu format."""
        if code.startswith("HK"):
            return f"HK.{code[2:]}"
        raw = _raw_code(code)
        if raw.startswith(("6", "9", "5")):
            return f"SH.{raw}"
        return f"SZ.{raw}"


# ── SQLite Cache (offline / backtest) ────────────────────────────────────


class SqliteKlineProvider(KlineProvider):
    """Read-only provider from daily_kline table in sim_trading.db."""

    @property
    def name(self) -> str:
        return "sqlite"

    def fetch_daily(self, code, start=None, end=None, days=120):
        try:
            from src.sim_trading.db import get_connection
        except ImportError:
            return None

        if start is None or end is None:
            start, end = _date_range(days + 30)

        try:
            conn = get_connection()
            rows = conn.execute(
                """SELECT date, open, high, low, close, volume,
                          COALESCE(turnover, 0) as turnover, change_pct
                   FROM daily_kline
                   WHERE code = ? AND date >= ? AND date <= ?
                   ORDER BY date""",
                (code, start, end),
            ).fetchall()
            conn.close()

            if not rows:
                return None

            records = [
                {
                    "date": r["date"], "open": r["open"], "high": r["high"],
                    "low": r["low"], "close": r["close"], "volume": r["volume"],
                    "change_pct": r["change_pct"] or 0.0,
                }
                for r in rows
            ]
            return pd.DataFrame(records, columns=_COLUMNS)
        except Exception as exc:
            logger.debug("SQLite kline %s failed: %s", code, exc)
            return None


# ── Composite Provider (fallback chain) ──────────────────────────────────


class CompositeKlineProvider(KlineProvider):
    """Tries providers in order, returns first successful result."""

    def __init__(self, providers: list[KlineProvider]):
        self._providers = providers

    @property
    def name(self) -> str:
        names = [p.name for p in self._providers]
        return f"composite({' → '.join(names)})"

    def fetch_daily(self, code, start=None, end=None, days=120):
        for p in self._providers:
            try:
                df = p.fetch_daily(code, start=start, end=end, days=days)
                if df is not None and not df.empty:
                    logger.debug("kline %s: got %d bars from %s",
                                 code, len(df), p.name)
                    return df
            except Exception as exc:
                logger.debug("kline %s: %s failed: %s", code, p.name, exc)
        logger.warning("kline %s: all providers exhausted", code)
        return None


# ── Factory ──────────────────────────────────────────────────────────────

# Singleton instances (lazy-init)
_providers: dict[str, CompositeKlineProvider] = {}


def get_provider(
    market: str = "auto",
    code: str | None = None,
    futu_ctx=None,
) -> CompositeKlineProvider:
    """Get a composite kline provider with appropriate fallback chain.

    Args:
        market: 'HK', 'A', or 'auto' (detect from code).
        code: Stock code (used when market='auto').
        futu_ctx: Optional Futu OpenQuoteContext for HK provider.

    Returns:
        CompositeKlineProvider with market-appropriate fallback chain.

    Fallback chains:
        HK: Futu → Tencent → SQLite
        A:  akshare → Tencent → SQLite
    """
    if market == "auto":
        if code is None:
            raise ValueError("market='auto' requires code parameter")
        market = detect_market(code)

    key = market
    if key in _providers and futu_ctx is None:
        return _providers[key]

    tencent = TencentKlineProvider()
    sqlite = SqliteKlineProvider()

    if market == "HK":
        futu = FutuKlineProvider(ctx=futu_ctx)
        chain = [futu, tencent, sqlite]
    else:  # A-share
        akshare = AkshareKlineProvider()
        chain = [akshare, tencent, sqlite]

    provider = CompositeKlineProvider(chain)
    if futu_ctx is None:  # only cache if no custom ctx
        _providers[key] = provider

    logger.debug("Created kline provider: %s", provider.name)
    return provider


def fetch_daily(
    code: str,
    days: int = 120,
    start: str | None = None,
    end: str | None = None,
    futu_ctx=None,
) -> Optional[pd.DataFrame]:
    """Convenience function: auto-detect market and fetch.

    Usage:
        df = fetch_daily("HK09988", days=120)
        df = fetch_daily("000792", start="2025-06-01", end="2026-03-12")
    """
    provider = get_provider(market="auto", code=code, futu_ctx=futu_ctx)
    return provider.fetch_daily(code, start=start, end=end, days=days)
