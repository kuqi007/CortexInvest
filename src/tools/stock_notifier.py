#!/usr/bin/env python3
"""
Stock Notifier -- DB-driven notification daemon

Watches trading.db price_snapshots (written by poller), merges with
config.db monitor config, and fires macOS notifications
when price/big-move alerts trigger.

Does NOT fetch any market data -- purely a consumer of poller output.

Usage:
    poetry run python src/tools/stock_notifier.py
"""

import fcntl
import json
import os
import signal
import sqlite3
import sys
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# ── Project root & import path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.stock_monitor import is_hk_symbol, notify
from src.utils.logging_config import setup_logger
from src.utils.audit_log import insert_trading_outbox
from src.utils.audit_system import build_audit_event_v2, make_actor
from src.utils.audit_writer import record_db_change_best_effort

logger = setup_logger("stock_notifier")

# Config is read from config.db only.
from src.utils.config_reader import read_monitor_config

ARCHIVE_DIR = PROJECT_ROOT / "src" / "data" / "archive"

# ── Poll intervals ──
TRADING_CHECK_SEC = 3  # mtime check interval during trading hours
NON_TRADING_CHECK_SEC = 60  # mtime check interval outside trading hours

# tick_monitor per-symbol cooldown: prevents notification spam for same symbol
# format: {symbol: last_notify_ts}
_tick_symbol_cooldown: dict[str, float] = {}
_TICK_SYMBOL_COOLDOWN_SEC = 180  # 3 min per symbol for tick_monitor alerts


def _read_monitor_settings(keys: list[str]) -> dict[str, float]:
    """Read selected numeric monitor settings from config.db."""
    if not keys:
        return {}
    conn = None
    try:
        from src.sim_trading.db import get_config_connection

        placeholders = ",".join("?" * len(keys))
        conn = get_config_connection()
        rows = conn.execute(
            f"SELECT key, value FROM monitor_settings WHERE key IN ({placeholders})",
            tuple(keys),
        ).fetchall()
        return {row["key"]: row["value"] for row in rows}
    except Exception as e:
        logger.debug(f"Failed to read monitor_settings: {e}")
        return {}
    finally:
        if conn:
            conn.close()


def _archive_and_reset(today):
    """Clean old alert/archive records at the daily reset boundary.

    Market data, L2 signals/session snapshots, and daily summaries are
    persisted in trading.db. JSON archive export is handled by the
    dedicated snapshot exporter.
    """
    yesterday = (today - timedelta(days=1)).isoformat()
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)

    # 清理 90 天前的归档文件
    try:
        cutoff_date = (today - timedelta(days=90)).isoformat()
        removed = 0
        for f in ARCHIVE_DIR.glob("*_????-??-??.json"):
            # 提取文件名中的日期: xxx_2026-02-12.json → 2026-02-12
            date_str = f.stem.rsplit("_", 1)[-1]
            if len(date_str) == 10 and date_str < cutoff_date:
                f.unlink()
                removed += 1
        if removed:
            logger.info(f"清理归档: 删除 {removed} 个 90 天前文件")
    except Exception as e:
        logger.warning(f"清理归档失败: {e}")

    # 清理 SQLite 过期数据
    conn = None
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        cutoff_30d = (today - timedelta(days=30)).isoformat()
        cutoff_180d = (today - timedelta(days=180)).isoformat()

        # alert_events: 30 天
        d1 = conn.execute(
            "DELETE FROM alert_events WHERE date < ?", (cutoff_30d,)
        ).rowcount
        if d1:
            record_db_change_best_effort(
                conn,
                db_name="trading.db",
                table="alert_events",
                action="purge",
                key=f"date<{cutoff_30d}",
                source="stock_notifier_cleanup",
                actor=make_actor(actor_type="system", actor_id="stock_notifier"),
                before={"cutoff_date": cutoff_30d, "deleted_count": d1},
                after=None,
            )
        # signals / price_snapshots / session_snapshots: 180 天
        d2 = conn.execute("DELETE FROM signals WHERE date < ?", (cutoff_180d,)).rowcount
        d3 = conn.execute(
            "DELETE FROM price_snapshots WHERE date < ?", (cutoff_180d,)
        ).rowcount
        d4 = conn.execute(
            "DELETE FROM session_snapshots WHERE date < ?", (cutoff_180d,)
        ).rowcount
        # tick_monitor_events: 30 天
        try:
            d5 = conn.execute(
                "DELETE FROM tick_monitor_events WHERE date < ?", (cutoff_30d,)
            ).rowcount
        except Exception:
            d5 = 0

        conn.commit()
        total = d1 + d2 + d3 + d4 + d5
        if total:
            logger.info(
                f"SQLite 清理: alert_events -{d1}, signals -{d2}, price_snap -{d3}, session_snap -{d4}, tick_monitor_events -{d5}"
            )
    except Exception as e:
        logger.warning(f"SQLite 清理失败: {e}")
    finally:
        if conn:
            conn.close()


# ══════════════════════════════════════════
# 1. Trading hours (extended for HK)
# ══════════════════════════════════════════


def is_in_auction_period() -> bool:
    """检查是否在集合竞价时段，竞价时段不发送 alert。

    A股: 09:15-09:25 开盘集合竞价，09:25后开始告警
    港股: 09:00-09:20 开市前时段竞价，09:20后开始告警

    这段时间价格波动大且不稳定，不适合发告警。
    """
    now = datetime.now()
    t = now.hour * 100 + now.minute

    # A股 开盘集合竞价 (09:15-09:24)
    if 915 <= t < 925:
        from src.tools.trading_calendar import is_trading_day

        if is_trading_day("CN"):
            return True

    # 港股 开市前时段竞价 (09:00-09:19)
    if 900 <= t < 920:
        from src.tools.trading_calendar import is_trading_day

        if is_trading_day("HK"):
            return True

    return False


def is_any_market_open(has_hk: bool = False) -> bool:
    """Check if any watched market is currently in trading hours.

    A-shares: trading day 09:15-11:30, 13:00-15:00
        09:15-09:25 开盘集合竞价, 09:30-11:30/13:00-14:57 连续竞价, 14:57-15:00 收盘集合竞价
    HK:       trading day 09:00-12:00, 13:00-16:10 (when has_hk=True)
        09:00-09:30 开市前时段(竞价), 09:30-12:00/13:00-16:00 连续交易, 16:00-16:10 收市竞价

    Uses Futu trading calendar for accurate holiday detection,
    falls back to weekday check if Futu is unavailable.
    """
    from src.tools.trading_calendar import is_trading_day

    now = datetime.now()
    t = now.hour * 100 + now.minute

    # A-share session (09:15 开盘集合竞价 ~ 15:00 收盘集合竞价结束)
    if (915 <= t <= 1130) or (1300 <= t <= 1500):
        if is_trading_day("CN"):
            return True

    # HK session (09:00 开市前时段 ~ 16:10 收市竞价结束)
    if has_hk and ((900 <= t <= 1200) or (1300 <= t <= 1610)):
        if is_trading_day("HK"):
            return True

    return False


# ══════════════════════════════════════════
# 1b. PatternEngine — 通用形态引擎基类 + 注册表
# ══════════════════════════════════════════


class PatternEngine:
    """通用形态检测引擎基类。

    子类实现 check() 即可接入主循环，无需手动接线。
    - check(quotes)  → list[dict]  每 tick 调用
    - reset()                      每日 08:00 重置状态
    - update_config(config)        config 变更时同步
    """

    def check(self, quotes: dict) -> list[dict]:
        return []

    def reset(self):
        pass

    def update_config(self, config: dict):
        pass


# 全局注册表 — 主循环统一驱动
_pattern_engines: list[PatternEngine] = []


def register_pattern_engine(engine: PatternEngine) -> PatternEngine:
    """注册引擎到全局注册表，返回引擎本身（支持链式写法）。

    注册后，引擎自动获得：
    - 每 tick check() 调用（结果并入 all_alerts）
    - 每日 08:00 reset() 重置
    - config 变更时 update_config() 同步
    """
    _pattern_engines.append(engine)
    return engine


def _get_latest_db_ts() -> int:
    """Get latest timestamp from price_snapshots."""
    conn = None
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        row = conn.execute("SELECT MAX(ts) FROM price_snapshots").fetchone()
        return row[0] or 0
    except Exception:
        return 0
    finally:
        if conn:
            conn.close()


def _read_market_snapshot_from_db() -> dict | None:
    """Build market snapshot dict from DB for watchdog compatibility."""
    conn = None
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT code, name, price, change_pct, chg_amt, volume, amount, amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2 "
            "FROM price_snapshots WHERE (code, ts) IN (SELECT code, MAX(ts) FROM price_snapshots GROUP BY code)"
        ).fetchall()
        services = []
        for row in rows:
            services.append(
                {
                    "id": row["code"],
                    "name": row["name"],
                    "price": row["price"],
                    "change": row["change_pct"],
                    "chgAmt": row["chg_amt"],
                    "vol": row["volume"],
                    "amount": row["amount"],
                    "amp": row["amp"],
                    "turnover": row["turnover"],
                    "volRatio": row["vol_ratio"],
                    "high": row["high"],
                    "low": row["low"],
                    "open": row["open"],
                    "prevClose": row["prev_close"],
                    "amo1": row["amo1"],
                    "amo2": row["amo2"],
                }
            )
        ts = _get_latest_db_ts()
        return {"services": services, "ts": ts}
    except Exception as e:
        logger.warning(f"Failed to read market snapshot from DB: {e}")
        return None
    finally:
        if conn:
            conn.close()


def _read_l2_indicators() -> dict:
    """Read latest indicator snapshots from trading.db.

    Returns: {code: {rsi, macd_hist, macd_hist_list, vol_ratio, updated_at}}
    """
    conn = None
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            """
            SELECT code, session_json
            FROM session_snapshots
            WHERE (code, ts) IN (
                SELECT code, MAX(ts) FROM session_snapshots GROUP BY code
            )
            """
        ).fetchall()
        indicators = {}
        for row in rows:
            try:
                payload = json.loads(row["session_json"] or "{}")
            except json.JSONDecodeError:
                continue
            ind = payload.get("indicators")
            if isinstance(ind, dict):
                indicators[row["code"]] = ind
        return indicators
    except Exception as e:
        logger.debug(f"Failed to read L2 indicators from DB: {e}")
        return {}
    finally:
        if conn:
            conn.close()


# ══════════════════════════════════════════
# 3. Data merging
# ══════════════════════════════════════════


def merge_data(market: dict, config: dict) -> dict:
    """Build quotes dict from DB price_snapshots + config.

    Returns: {symbol: {name, price, change_pct, chg_amt}} keyed by stock
    id/code.  The ``chg_amt`` field (absolute price change today) is carried
    through so DeltaAlertEngine can compute daily P&L.
    """
    watchlist = config.get("watchlist", {})

    quotes = {}
    conn = None
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT code, name, price, change_pct, chg_amt, volume, amount, amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2 "
            "FROM price_snapshots WHERE (code, ts) IN (SELECT code, MAX(ts) FROM price_snapshots GROUP BY code)"
        ).fetchall()
        for row in rows:
            sid = row["code"]
            if not sid:
                continue

            # Only alert on stocks that exist in the watchlist
            if sid not in watchlist:
                continue

            # Skip hidden stocks
            if watchlist[sid].get("hidden", False):
                continue

            quotes[sid] = {
                "name": row["name"] or sid,
                "price": row["price"] or 0,
                "change_pct": row["change_pct"] or 0,
                "chg_amt": row["chg_amt"] or 0,
                "amount": row["amount"] or 0,
                "open": row["open"] or 0,
                "prev_close": row["prev_close"] or 0,
                "high": row["high"] or 0,
                "low": row["low"] or 0,
                "amo1": row["amo1"],
            }
    except Exception as e:
        logger.warning(f"merge_data: failed to read price_snapshots from DB: {e}")
    finally:
        if conn:
            conn.close()

    # Inject technical indicators from L2 daemon
    indicators = _read_l2_indicators()
    for sid, q in quotes.items():
        q["indicators"] = indicators.get(sid, {})

    return quotes


# ══════════════════════════════════════════
# 4. Delta-based alert engine (变化驱动)
# ══════════════════════════════════════════
#
# 核心逻辑：以价格变化驱动通知，不重复提醒同一状态。
#
#   首次触发: |日涨跌幅| >= trigger_pct  →  通知，记住当前价格
#   再次触发: |当前价 - 上次通知价| / 上次通知价 * 100 >= delta_pct
#   触价同理: 首次突破通知，之后价格变化 >= delta_pct 才再通知
#
# 例: 掌阅涨停 +10% → 通知1次，记住 31.09。
#     价格不变 → 不再通知。回落到 29.85 (-4%) → 再通知。
#
# Settings (均可通过 config.db monitor_settings 覆盖):
#   trigger_pct  : 首次触发阈值，|日涨跌幅| 超过此值才通知 (default 5%)
#   delta_pct    : 再次触发阈值，距上次通知价变化超过此值才通知 (default 4%)
#   portfolio_delta_pct : 组合 P&L 变化阈值 (default 2%)

DEFAULT_PORTFOLIO_DELTA_PCT = 2.0  # 组合: P&L 变化 >= 2%

# ── 分级通知策略表（配置驱动，可扩展） ──
NOTIFY_POLICIES = {
    1: {
        "trigger_pct": 4,
        "delta_pct": 5,
        "cooldown_min": 5,
        "big_move": True,
        "threshold": True,
        "dispatch": "sound",
    },
    2: {
        "trigger_pct": 6,
        "delta_pct": 7,
        "cooldown_min": 15,
        "big_move": True,
        "threshold": True,
        "dispatch": "silent",
    },
    3: {
        "trigger_pct": None,
        "delta_pct": None,
        "cooldown_min": 30,
        "big_move": False,
        "threshold": True,
        "dispatch": "web_only",
    },
    4: {
        "trigger_pct": None,
        "delta_pct": None,
        "cooldown_min": None,
        "big_move": False,
        "threshold": False,
        "dispatch": "none",
    },
}


def resolve_level(entry: dict) -> int:
    """从 watchlist entry 推导通知级别"""
    if entry.get("hidden"):
        return 4
    if entry.get("star"):
        return 1
    if entry.get("type") == "holding":
        return 2
    return 3


def is_hk_stock(symbol: str) -> bool:
    """判断是否为港股"""
    return symbol.startswith("HK")


def adjust_policy_for_market(policy: dict, symbol: str) -> dict:
    """根据市场调整阈值，港股弹性大使用更宽松的阈值"""
    if not is_hk_stock(symbol):
        return policy
    # 港股: trigger 放宽到 8%, delta 放宽到 6%
    adjusted = dict(policy)
    if policy.get("trigger_pct"):
        adjusted["trigger_pct"] = max(policy["trigger_pct"], 8)
    if policy.get("delta_pct"):
        adjusted["delta_pct"] = max(policy["delta_pct"], 6)
    return adjusted


def get_policy(level: int, settings: dict) -> dict:
    """获取级别策略，合并用户覆盖"""
    base = dict(NOTIFY_POLICIES.get(level, NOTIFY_POLICIES[4]))
    # 用户可通过 settings 覆盖: l1_trigger_pct, l2_delta_pct, etc.
    prefix = f"l{level}_"
    for key in ("trigger_pct", "delta_pct", "cooldown_min"):
        override = settings.get(f"{prefix}{key}")
        if override is not None:
            base[key] = override
    return base


class DeltaAlertEngine:
    """变化驱动的告警引擎。

    替代 AlertEngine + PortfolioAlertEngine，合并同一只股票的
    big_move / P&L / threshold 为一条通知，避免重复。

    每只股票追踪 ``last_notified_price``，只在价格发生显著变化时
    才再次通知。涨停/跌停不会反复弹窗。
    """

    def __init__(self, config: dict):
        self.config = config
        self._alerts: dict = {}
        self._reload_alerts()
        # {symbol: {"price": float, "change_pct": float}}
        self._notified: dict[str, dict] = {}
        # portfolio: last notified total daily P&L
        self._last_portfolio_pnl: float | None = None

    def _reload_alerts(self):
        """从 config.db alert_rules 加载告警规则"""
        conn = None
        try:
            from src.sim_trading.db import get_config_connection

            conn = get_config_connection()
            rows = conn.execute("SELECT symbol, above, below FROM alert_rules").fetchall()
            self._alerts = {
                row["symbol"]: {
                    "above": row["above"],
                    "below": row["below"],
                }
                for row in rows
            }
        except Exception:
            self._alerts = {}
        finally:
            if conn:
                conn.close()

    def reset(self):
        """Midnight reset — clear all tracking state."""
        self._notified.clear()
        self._last_portfolio_pnl = None

    def check(
        self,
        quotes: dict,
        hkd_cny_rate: float | None,
    ) -> list[dict]:
        """Run all alert checks with tiered notification policies.

        Returns list of alert dicts, each with _level for dispatch routing.
        """
        # 集合竞价时段不发送告警
        if is_in_auction_period():
            return []

        self._reload_alerts()
        config = self.config
        watchlist = config.get("watchlist", {})
        settings = config.get("settings", {})
        portfolio_delta_pct = settings.get(
            "portfolio_delta_pct", DEFAULT_PORTFOLIO_DELTA_PCT
        )

        alerts: list[dict] = []
        total_daily_pnl = 0.0
        total_market_value = 0.0
        holdings_counted = 0

        for symbol, quote in quotes.items():
            entry = watchlist.get(symbol, {})
            price = quote.get("price", 0)
            change_pct = quote.get("change_pct", 0)
            chg_amt = quote.get("chg_amt", 0)
            name = quote.get("name", symbol)

            if price <= 0:
                continue

            # ── Resolve level and policy ──
            level = resolve_level(entry)
            policy = get_policy(level, settings)

            if policy["dispatch"] == "none":
                # L4: still accumulate P&L but skip notification
                pass

            is_holding = entry.get("type") == "holding"
            cost = entry.get("cost")
            shares = entry.get("shares")
            has_position = is_holding and cost and shares and cost > 0 and shares > 0

            # ── Accumulate portfolio P&L ──
            if has_position:
                is_hk = is_hk_symbol(symbol)
                fx = hkd_cny_rate if is_hk else 1.0
                if fx is not None and fx > 0:
                    total_daily_pnl += chg_amt * shares * fx
                    total_market_value += price * shares * fx
                    holdings_counted += 1

            if policy["dispatch"] == "none":
                continue

            # ── Should we notify? ──
            reasons: list[str] = []
            prev = self._notified.get(symbol)

            # Threshold check (above/below)
            threshold_hit = False
            if policy["threshold"]:
                alert_entry = self._alerts.get(symbol, {})
                above = alert_entry.get("above")
                below = alert_entry.get("below")
                if above is not None and price >= above:
                    threshold_hit = True
                if below is not None and price <= below:
                    threshold_hit = True

            # 根据市场调整阈值（港股弹性大，使用更宽松的阈值）
            market_policy = adjust_policy_for_market(policy, symbol)
            trigger_pct = market_policy["trigger_pct"]
            delta_pct = market_policy["delta_pct"]

            if prev is None:
                # ── First notification ──
                if threshold_hit:
                    reasons.append("threshold")
                elif (
                    policy["big_move"]
                    and trigger_pct
                    and abs(change_pct) >= trigger_pct
                ):
                    reasons.append("big_move")
                if not reasons:
                    continue
            else:
                # ── Re-trigger: cooldown + price delta >= delta_pct ──
                prev_price = prev["price"]
                cooldown_min = policy.get("cooldown_min")
                cooldown_ok = True
                if cooldown_min and prev.get("ts"):
                    elapsed_min = (time.time() - prev["ts"]) / 60
                    if elapsed_min < cooldown_min:
                        cooldown_ok = False
                if cooldown_ok and prev_price > 0 and delta_pct:
                    delta = abs(price - prev_price) / prev_price * 100
                    if delta >= delta_pct:
                        if threshold_hit:
                            reasons.append("threshold")
                        elif policy["big_move"]:
                            reasons.append("big_move")
                if not reasons:
                    continue

            # ── Record and build alert ──
            self._notified[symbol] = {"price": price, "change_pct": change_pct, "ts": time.time()}
            kind = "threshold" if "threshold" in reasons else "big_move"

            is_retrigger = prev is not None
            if is_retrigger and prev.get("price"):
                delta_from_prev = (price - prev["price"]) / prev["price"] * 100
                if delta_from_prev < -1:
                    alert_pct = change_pct
                    icon = "↓"
                    stealth_fallback = f"回落至{change_pct:+.1f}%"
                else:
                    alert_pct = change_pct
                    icon = "↑" if change_pct >= 0 else "↓"
                    stealth_fallback = None
            else:
                alert_pct = change_pct
                icon = "↑" if change_pct >= 0 else "↓"
                stealth_fallback = None

            # title 格式: 股票名 图标+/-X% → 价格
            sign = "+" if alert_pct >= 0 else ""
            title = f"{name} {icon}{sign}{alert_pct:.1f}% → {price:.2f}"
            message = f"{price:.2f}"
            if "threshold" in reasons:
                message += " !"

            if stealth_fallback:
                stealth_extra = stealth_fallback
            else:
                stealth_extra = f"{icon}{sign}{abs(alert_pct):.1f}%"
            if "threshold" in reasons:
                stealth_extra += " threshold"

            alerts.append(
                {
                    "symbol": symbol,
                    "title": title,
                    "message": message,
                    "_kind": kind,
                    "_level": level,
                    "_change_pct": alert_pct,
                    "_price": price,
                    "_name": name,
                    "_stealth": _notify_line(name, alert_pct, stealth_extra),
                }
            )

        # ── Portfolio summary ──
        if holdings_counted > 0 and total_market_value > 0:
            portfolio_pct = (total_daily_pnl / total_market_value) * 100

            should_notify = False
            if self._last_portfolio_pnl is None:
                if abs(portfolio_pct) >= get_policy(2, settings)["trigger_pct"]:
                    should_notify = True
            else:
                pnl_delta = abs(total_daily_pnl - self._last_portfolio_pnl)
                pnl_delta_pct = (
                    (pnl_delta / total_market_value) * 100
                    if total_market_value > 0
                    else 0
                )
                if pnl_delta_pct >= portfolio_delta_pct:
                    should_notify = True

            if should_notify:
                self._last_portfolio_pnl = total_daily_pnl
                pct_sign = "+" if portfolio_pct >= 0 else ""
                alerts.append(
                    {
                        "symbol": "",
                        "title": f"组合 {pct_sign}{portfolio_pct:.1f}%",
                        "message": f"{holdings_counted} stocks",
                        "_kind": "portfolio",
                        "_level": 2,
                        "_change_pct": portfolio_pct,
                        "_stealth": f"组合 {pct_sign}{portfolio_pct:.1f}%",
                    }
                )

        return alerts


# ══════════════════════════════════════════
# 4b. Data Freshness Watchdog
# ══════════════════════════════════════════
#
# Three-layer staleness detection:
#   Layer 1: File staleness (poller crash/hang)
#   Layer 2: Price freeze (poller running but data stale)
#   Layer 3: Source degradation (running on fallback)
#
# Only active during trading hours. Resets at 08:00 with everything else.

WATCHDOG_COOLDOWN_SEC = 300  # 5 min between repeated alerts per issue type


class DataFreshnessWatchdog:
    """Monitors data pipeline health and alerts on staleness."""

    def __init__(self, poll_interval: int = 30):
        self._poll_interval = poll_interval
        # Layer 1: file staleness
        self._last_seen_mtime: float = 0.0
        self._mtime_unchanged_since: float | None = None
        self._file_stale_alerted: bool = False
        # Layer 2: price freeze (per-market)
        self._price_fingerprints: dict[str, dict] = {}  # {market: {fp, first_seen}}
        self._price_frozen_alerted: dict[str, bool] = {}
        # Layer 3: source degradation
        self._fallback_since: float | None = None
        self._futu_down_since: float | None = None
        self._source_alerted: dict[str, bool] = {}
        # Cooldowns
        self._cooldowns: dict[str, float] = {}

    def reset(self):
        """Daily reset at 08:00."""
        self._last_seen_mtime = 0.0
        self._mtime_unchanged_since = None
        self._file_stale_alerted = False
        self._price_fingerprints.clear()
        self._price_frozen_alerted.clear()
        self._fallback_since = None
        self._futu_down_since = None
        self._source_alerted.clear()
        self._cooldowns.clear()

    def update_poll_interval(self, interval: int):
        self._poll_interval = interval

    def check(self, db_ts: int, market: dict | None, has_hk: bool) -> list[dict]:
        """Run all staleness checks. Called every loop iteration."""
        if not is_any_market_open(has_hk):
            return []

        now = time.time()
        alerts: list[dict] = []

        # Layer 1: DB staleness
        alerts.extend(self._check_db_staleness(db_ts, now))

        if market and not self._file_stale_alerted:
            # Layer 2: price freeze
            alerts.extend(self._check_price_freeze(market, has_hk, now))
            # Layer 3: source degradation
            alerts.extend(self._check_source_degradation(market, now))

        return alerts

    # ── helpers ──

    def _cooled(self, key: str, now: float) -> bool:
        return (now - self._cooldowns.get(key, 0)) >= WATCHDOG_COOLDOWN_SEC

    def _fire(self, key: str, now: float):
        self._cooldowns[key] = now

    @staticmethod
    def _make_alert(
        issue_type: str, display_msg: str, level: int = 2, is_recovery: bool = False
    ) -> dict:
        icon = "OK" if is_recovery else "WARN"
        return {
            "symbol": "",
            "title": f"[{icon}] {display_msg[:40]}",
            "message": display_msg,
            "display": display_msg,
            "_kind": "STALE",
            "_level": level,
            "_change_pct": 0,
            "_stealth": f"[{icon}] {display_msg}",
            "_issue_type": issue_type,
        }

    # ── Layer 1: file staleness ──

    def _check_db_staleness(self, db_ts: int, now: float) -> list[dict]:
        db_mtime = db_ts / 1000.0  # ts is milliseconds
        threshold = self._poll_interval * 3

        if db_mtime <= 0:
            return []

        if db_mtime != self._last_seen_mtime:
            self._last_seen_mtime = db_mtime
            self._mtime_unchanged_since = None
            if self._file_stale_alerted:
                self._file_stale_alerted = False
                return [
                    self._make_alert(
                        "db_stale_recovery",
                        "数据恢复: Poller 已恢复写入 DB",
                        level=3,
                        is_recovery=True,
                    )
                ]
            return []

        # timestamp unchanged
        if self._mtime_unchanged_since is None:
            self._mtime_unchanged_since = now

        elapsed = now - self._mtime_unchanged_since
        if elapsed >= threshold and not self._file_stale_alerted:
            if self._cooled("db_stale", now):
                self._fire("db_stale", now)
                self._file_stale_alerted = True
                mins = int(elapsed // 60) or 1
                return [
                    self._make_alert(
                        "db_stale",
                        f"Poller 可能挂起: DB 行情数据已 {mins} 分钟未更新",
                        level=1,
                    )
                ]
        return []

    # ── Layer 2: price freeze ──

    @staticmethod
    def _compute_fingerprint(services: list[dict], market_prefix: str) -> str:
        import hashlib

        if market_prefix == "HK":
            subset = [s for s in services if s.get("id", "").startswith("HK")]
        else:
            subset = [s for s in services if not s.get("id", "").startswith("HK")]
        subset.sort(key=lambda s: s.get("amount", 0), reverse=True)
        top = subset[:8]
        if not top:
            return ""
        key_data = tuple(sorted((s.get("id", ""), s.get("price", 0)) for s in top))
        return hashlib.md5(str(key_data).encode()).hexdigest()

    @staticmethod
    def _is_market_active(market: str, has_hk: bool) -> bool:
        from src.tools.trading_calendar import is_trading_day

        now = datetime.now()
        t = now.hour * 100 + now.minute
        if market == "A":
            return ((930 <= t <= 1130) or (1300 <= t <= 1500)) and is_trading_day("CN")
        if market == "HK" and has_hk:
            return ((930 <= t <= 1200) or (1300 <= t <= 1610)) and is_trading_day("HK")
        return False

    def _check_price_freeze(self, market: dict, has_hk: bool, now: float) -> list[dict]:
        services = market.get("services", [])
        if not services:
            return []
        alerts = []
        # HK data sources update less frequently than A-share; use a higher threshold
        freeze_thresholds = {
            "A": self._poll_interval * 5,
            "HK": self._poll_interval * 8,
        }

        for mkt in ["A", "HK"] if has_hk else ["A"]:
            if not self._is_market_active(mkt, has_hk):
                self._price_fingerprints.pop(mkt, None)
                continue
            fp = self._compute_fingerprint(services, mkt)
            if not fp:
                continue
            prev = self._price_fingerprints.get(mkt)
            if prev is None or prev["fingerprint"] != fp:
                self._price_fingerprints[mkt] = {"fingerprint": fp, "first_seen": now}
                if self._price_frozen_alerted.get(mkt):
                    self._price_frozen_alerted[mkt] = False
                    alerts.append(
                        self._make_alert(
                            f"price_frozen_recovery_{mkt}",
                            f"价格恢复: {mkt} 行情数据已更新",
                            level=3,
                            is_recovery=True,
                        )
                    )
            else:
                elapsed = now - prev["first_seen"]
                if elapsed >= freeze_thresholds.get(
                    mkt, freeze_thresholds["A"]
                ) and not self._price_frozen_alerted.get(mkt):
                    key = f"price_frozen_{mkt}"
                    if self._cooled(key, now):
                        self._fire(key, now)
                        self._price_frozen_alerted[mkt] = True
                        mins = round(elapsed / 60, 1)
                        alerts.append(
                            self._make_alert(
                                key,
                                f"价格冻结: {mkt} Top8 价格 {mins} 分钟未变化",
                                level=2,
                            )
                        )
        return alerts

    # ── Layer 3: source degradation ──

    def _check_source_degradation(self, market: dict, now: float) -> list[dict]:
        source = market.get("_source")
        if source is None:
            return []
        alerts = []
        threshold = 300  # 5 minutes

        # Primary fallback
        if source.get("is_fallback"):
            if self._fallback_since is None:
                self._fallback_since = now
            elif (
                now - self._fallback_since >= threshold
                and not self._source_alerted.get("primary_fallback")
            ):
                if self._cooled("source_fallback", now):
                    self._fire("source_fallback", now)
                    self._source_alerted["primary_fallback"] = True
                    mins = int((now - self._fallback_since) // 60)
                    alerts.append(
                        self._make_alert(
                            "source_fallback",
                            f"数据源降级: 东方财富不可用，新浪 fallback {mins}min",
                            level=2,
                        )
                    )
        else:
            if self._fallback_since is not None:
                self._fallback_since = None
                if self._source_alerted.get("primary_fallback"):
                    self._source_alerted["primary_fallback"] = False
                    alerts.append(
                        self._make_alert(
                            "source_recovery",
                            "数据源恢复: 东方财富重新连接",
                            level=3,
                            is_recovery=True,
                        )
                    )

        # Futu disconnection
        if not source.get("futu_connected", True):
            if self._futu_down_since is None:
                self._futu_down_since = now
            elif (
                now - self._futu_down_since >= threshold
                and not self._source_alerted.get("futu_down")
            ):
                if self._cooled("futu_down", now):
                    self._fire("futu_down", now)
                    self._source_alerted["futu_down"] = True
                    alerts.append(
                        self._make_alert(
                            "futu_down",
                            "Futu 断连: HK 行情失去 L2 增强",
                            level=2,
                        )
                    )
        else:
            if self._futu_down_since is not None:
                self._futu_down_since = None
                if self._source_alerted.get("futu_down"):
                    self._source_alerted["futu_down"] = False
                    alerts.append(
                        self._make_alert(
                            "futu_recovery",
                            "Futu 恢复: HK L2 增强已重连",
                            level=3,
                            is_recovery=True,
                        )
                    )

        return alerts


# ══════════════════════════════════════════
# 5. Stealth notification layer
# ══════════════════════════════════════════
#
# 伪装成 CI/监控系统通知，同事路过看到也只觉得是工作消息。
# 多条告警合并为 1 条弹窗，减少打扰。
#
# 对照表 (只有你自己知道):
#   SVC-159516  = 半导设备ETF     crit = 大跌    ok = 大涨
#   SVC-HK09988 = 阿里巴巴        threshold = 触价
#   blocked     = 跌               resolved = 涨
#   net: -1,765 = 今日盈亏        coverage = 涨跌幅
#

# 通知标题（低调，不暴露用途）
_TITLES_ALERT = [
    "CI Pipeline Alert",
    "Deploy Monitor",
    "SRE Notification",
    "Build Status",
]
_TITLES_SUMMARY = ["Daily Report", "Sprint Summary"]


def _notify_title(is_summary: bool = False) -> str:
    titles = _TITLES_SUMMARY if is_summary else _TITLES_ALERT
    idx = datetime.now().minute % len(titles)
    return titles[idx]


def _notify_line(name: str, change_pct: float, extra: str = "") -> str:
    """Terminal 通知行：简短中文，带方向图标"""
    icon = "↑" if change_pct >= 0 else "↓"
    sign = "+" if change_pct >= 0 else ""
    if extra:
        return f"{name} {icon}{sign}{abs(change_pct):.1f}% {extra}"
    return f"{name} {icon}{sign}{abs(change_pct):.1f}%"


# ══════════════════════════════════════════
# 5. Mainline detection (once per day after close)
# ══════════════════════════════════════════

# Default alert rules (same as sector_index_engine)
_MAINLINE_DEFAULTS = {
    "cumulative_gain_pct": 8,
    "slope_threshold": 0.05,
    "r_squared_min": 0.4,
    "lookback_days": 10,
    "min_days_since_create": 3,
}


def _linear_regression(ys: list[float]):
    """Manual OLS regression. Returns (slope, r_squared) for y indexed 0..N-1."""
    n = len(ys)
    if n < 3:
        return 0.0, 0.0
    x_mean = (n - 1) / 2
    y_mean = sum(ys) / n
    ss_xy = sum((i - x_mean) * (ys[i] - y_mean) for i in range(n))
    ss_xx = sum((i - x_mean) ** 2 for i in range(n))
    ss_yy = sum((yi - y_mean) ** 2 for yi in ys)
    if ss_xx == 0:
        return 0.0, 0.0
    slope = ss_xy / ss_xx
    r_squared = (ss_xy**2) / (ss_xx * ss_yy) if ss_yy != 0 else 0.0
    return slope, r_squared


# ══════════════════════════════════════════
# 5b. Gap-fade / Gap-recover detection
#     高开低走 / 低开高走
# ══════════════════════════════════════════


class GapFadeEngine(PatternEngine):
    """检测高开低走 & 低开高走形态，每只股票每种形态每日最多触发一次。

    高开低走触发条件（同时满足）：
      1. 开盘价 > 昨收 >= gap_pct（默认 1.5%）
      2. 现价从开盘价跌落 >= fade_pct（默认 2.0%）

    低开高走触发条件（同时满足）：
      1. 开盘价 < 昨收 >= gap_pct（默认 1.5%）
      2. 现价从开盘价反弹 >= fade_pct（默认 2.0%）

    告警级别：仅 ★持仓 (star + holding) 触发，L1
    Settings 覆盖：gap_fade_gap_pct / gap_fade_fade_pct
    """

    DEFAULT_GAP_PCT = 1.5
    DEFAULT_FADE_PCT = 2.0

    def __init__(self, config: dict):
        self.config = config
        # {symbol: {date: set_of_patterns}} — 同一天同一形态只触发一次
        self._fired: dict[str, dict[str, set]] = {}

    def reset(self):
        self._fired.clear()

    def _already_fired(self, symbol: str, pattern: str, today: str) -> bool:
        return pattern in self._fired.get(symbol, {}).get(today, set())

    def _mark_fired(self, symbol: str, pattern: str, today: str):
        self._fired.setdefault(symbol, {}).setdefault(today, set()).add(pattern)

    def check(self, quotes: dict) -> list[dict]:
        settings = self.config.get("settings", {})
        watchlist = self.config.get("watchlist", {})
        gap_pct = settings.get("gap_fade_gap_pct", self.DEFAULT_GAP_PCT)
        fade_pct = settings.get("gap_fade_fade_pct", self.DEFAULT_FADE_PCT)
        today = datetime.now().strftime("%Y-%m-%d")

        alerts = []
        for symbol, q in quotes.items():
            open_px = q.get("open", 0)
            prev_close = q.get("prev_close", 0)
            price = q.get("price", 0)
            name = q.get("name", symbol)

            if open_px <= 0 or prev_close <= 0 or price <= 0:
                continue

            entry = watchlist.get(symbol, {})
            # 只检测 star + holding
            if not (entry.get("star") and entry.get("type") == "holding"):
                continue
            level = resolve_level(entry)

            gap_from_prev = (
                (open_px - prev_close) / prev_close * 100
            )  # 正=高开, 负=低开

            # ── 高开低走 ──
            if gap_from_prev >= gap_pct and not self._already_fired(
                symbol, "fade", today
            ):
                fade = (open_px - price) / open_px * 100
                if fade >= fade_pct:
                    gap_erased = price <= prev_close
                    self._mark_fired(symbol, "fade", today)
                    gap_str = f"+{gap_from_prev:.1f}%"
                    fade_str = f"-{fade:.1f}%"
                    erased_str = " 缺口完全回吐" if gap_erased else ""
                    display = f"{symbol} {name} 高开低走: 高开{gap_str} 回落{fade_str}{erased_str} | 现价{price:.2f}"
                    msg = f"{symbol} {name} 高开{gap_str} 回落{fade_str}{erased_str} 现价{price:.2f}"
                    alerts.append(
                        {
                            "symbol": symbol,
                            "title": f"{name} 高开低走",
                            "message": msg,
                            "display": display,
                            "_kind": "gap_fade",
                            "_level": level,
                            "_price": price,
                            "_name": name,
                            "_change_pct": q.get("change_pct", 0),
                            "_stealth": msg,
                        }
                    )
                    logger.info(
                        f"GapFade↓: {symbol} {name} 高开{gap_str} 回落{fade_str}{erased_str}"
                    )

            # ── 低开高走 ──
            elif gap_from_prev <= -gap_pct and not self._already_fired(
                symbol, "recover", today
            ):
                recover = (price - open_px) / abs(open_px) * 100
                if recover >= fade_pct:
                    gap_recovered = price >= prev_close
                    self._mark_fired(symbol, "recover", today)
                    gap_str = f"{gap_from_prev:.1f}%"  # 已含负号
                    recover_str = f"+{recover:.1f}%"
                    recovered_str = " 缺口完全收复" if gap_recovered else ""
                    display = f"{symbol} {name} 低开高走: 低开{gap_str} 反弹{recover_str}{recovered_str} | 现价{price:.2f}"
                    msg = f"{symbol} {name} 低开{gap_str} 反弹{recover_str}{recovered_str} 现价{price:.2f}"
                    alerts.append(
                        {
                            "symbol": symbol,
                            "title": f"{name} 低开高走",
                            "message": msg,
                            "display": display,
                            "_kind": "gap_recover",
                            "_level": level,
                            "_price": price,
                            "_name": name,
                            "_change_pct": q.get("change_pct", 0),
                            "_stealth": msg,
                        }
                    )
                    logger.info(
                        f"GapRecover↑: {symbol} {name} 低开{gap_str} 反弹{recover_str}{recovered_str}"
                    )

        return alerts

    def update_config(self, config: dict):
        self.config = config


def check_mainline_alerts() -> list[dict]:
    """Read sector_daily + tag_meta from DB, run mainline detection, return alerts.

    Same algorithm as sector_index_engine.detect_mainline but returns alert dicts
    instead of writing to sector_alerts table.
    """
    from src.sim_trading.db import get_config_connection, get_connection

    today_str = datetime.now().strftime("%Y-%m-%d")

    # tag_meta is in config.db, sector_daily is in trading.db
    cfg_conn = None
    trading_conn = None
    try:
        cfg_conn = get_config_connection()
        trading_conn = get_connection()
        # Load tag indices from config DB
        tags = cfg_conn.execute(
            "SELECT tag, star, watch, baseline_value, created_at FROM tag_meta"
        ).fetchall()
        if not tags:
            return []

        alerts = []
        rules = dict(_MAINLINE_DEFAULTS)
        cum_threshold = rules["cumulative_gain_pct"]
        slope_threshold = rules["slope_threshold"]
        r2_min = rules["r_squared_min"]
        lookback = rules["lookback_days"]
        min_days = rules["min_days_since_create"]

        for row in tags:
            tag_name = row["tag"]
            star = bool(row["star"])
            watch = bool(row["watch"])

            if not watch:
                continue

            # Get last N days of index values (sector_daily is in trading.db)
            daily_rows = trading_conn.execute(
                "SELECT date, index_value FROM sector_daily"
                " WHERE index_id = ? AND date <= ?"
                " ORDER BY date DESC LIMIT ?",
                (tag_name, today_str, lookback),
            ).fetchall()

            if len(daily_rows) < min_days:
                continue

            # Reverse to chronological order (oldest first)
            daily_rows = list(reversed(daily_rows))
            values = [r["index_value"] for r in daily_rows]
            baseline = values[0]
            if baseline == 0:
                continue

            cum_gain = (values[-1] / baseline - 1) * 100

            # Linear regression on daily returns
            if len(values) < 3:
                slope, r_squared = 0.0, 0.0
            else:
                returns = [
                    (values[i] / values[i - 1] - 1) * 100 for i in range(1, len(values))
                ]
                slope, r_squared = _linear_regression(returns)

            # Mainline: cumulative >= threshold AND slope >= threshold AND R² >= min
            if (
                cum_gain >= cum_threshold
                and slope >= slope_threshold
                and r_squared >= r2_min
            ):
                alerts.append(
                    {
                        "symbol": f"tag:{tag_name}",
                        "title": f"{tag_name} 主线行情",
                        "_kind": "MAINLINE",
                        "_level": 1 if star else 2,
                        "_change_pct": round(cum_gain, 1),
                        "_name": f"{tag_name}指数",
                        "message": f"{tag_name} 主线行情确认",
                        "display": (
                            f"\U0001f525 {tag_name}指数 主线行情"
                            f" 累涨{cum_gain:.1f}% 斜率{slope:.3f} R\u00b2={r_squared:.2f}"
                        ),
                        "_stealth": f"{tag_name} 主线确认",
                    }
                )
                logger.info(
                    "mainline detected: %s cum=%.1f%% slope=%.3f R2=%.2f",
                    tag_name,
                    cum_gain,
                    slope,
                    r_squared,
                )
            # Approaching: cumulative >= 75% threshold AND slope > 0
            elif cum_gain >= cum_threshold * 0.75 and slope > 0:
                alerts.append(
                    {
                        "symbol": f"tag:{tag_name}",
                        "title": f"{tag_name} 接近主线",
                        "_kind": "MAINLINE",
                        "_level": 2,  # approaching is always L2
                        "_change_pct": round(cum_gain, 1),
                        "_name": f"{tag_name}指数",
                        "message": f"{tag_name} 接近主线",
                        "display": (
                            f"\u26a1 {tag_name}指数 接近主线 累涨{cum_gain:.1f}%"
                        ),
                        "_stealth": f"{tag_name} 接近主线",
                    }
                )
                logger.info(
                    "approaching mainline: %s cum=%.1f%% slope=%.3f",
                    tag_name,
                    cum_gain,
                    slope,
                )

        return alerts
    except Exception as e:
        logger.warning("check_mainline_alerts failed: %s", e)
        return []
    finally:
        if cfg_conn is not None:
            cfg_conn.close()
        if trading_conn is not None:
            trading_conn.close()


def write_alert_events(alerts: list[dict]):
    """将告警事件写入 SQLite alert_events 表，供 web 端读取展示。

    单一数据源：notifier 计算，web 只读。确保 terminal 和 web 告警一致。
    INSERT OR IGNORE 利用 UNIQUE(ts, symbol, message) 零成本去重。
    """
    if not alerts:
        return

    from src.sim_trading.db import get_connection

    ts_base = int(time.time() * 1000)
    t = datetime.now().strftime("%H:%M:%S")
    today = datetime.now().strftime("%Y-%m-%d")

    rows = []
    for i, a in enumerate(alerts):
        symbol = a.get("symbol", "")
        kind = a.get("_kind", "")
        change_pct = a.get("_change_pct", 0)
        name = a.get("title", "").split(" ")[0] if a.get("title") else symbol

        # 中文可读格式
        if kind == "STALE":
            display = a.get("display", a.get("message", ""))
        elif kind in ("gap_fade", "gap_recover"):
            display = a.get("display", a.get("message", ""))
        elif kind == "l2_strategy":
            display = a.get("message", f"{symbol} L2 signal")
        elif kind == "threshold":
            # 触价显示方向图标：↑高于阈值 或 ↓低于阈值
            msg = a.get("message", "")
            # 判断是高于还是低于
            if "!" in msg:
                # 需要根据价格判断方向，这里简化处理
                display = f"{symbol} {name} 触价告警 {a.get('_price', 0):.2f}"
            else:
                display = f"{symbol} {name} 触价告警 {a.get('_price', 0):.2f}"
        elif kind == "portfolio":
            display = f"组合盈亏 {change_pct:+.1f}%"
        elif kind == "DRIFT":
            display = a.get("display", a.get("message", ""))
        elif kind == "MAINLINE":
            display = a.get("display", a.get("message", ""))
        elif kind == "trade_plan":
            display = a.get("display", a.get("message", ""))
        elif kind == "tick_monitor":
            display = a.get("display", a.get("message", ""))
        elif kind == "ai_investment":
            display = a.get("display", a.get("title", a.get("message", "")))
        else:
            # title 格式: "股票名 ↑+4.5% → 217.45"，直接用
            title = a.get("title", "")
            if title:
                display = f"{symbol} {title}"
            else:
                icon = "↑" if change_pct > 0 else "↓"
                direction = "涨幅" if change_pct > 0 else "跌幅"
                p = a.get("_price", 0)
                display = (
                    f"{symbol} {name} {icon}{direction} {abs(change_pct):.1f}% → {p:.2f}"
                    if p
                    else f"{symbol} {name} {icon}{direction} {abs(change_pct):.1f}%"
                )

        message = a.get("_stealth", a.get("message", ""))
        rows.append(
            (
                ts_base + i,
                today,
                t,
                symbol,
                kind,
                a.get("_level", 2),
                message,
                display,
                change_pct,
            )
        )

    conn = None
    try:
        conn = get_connection()
        for row in rows:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO alert_events "
                "(ts, date, time, symbol, kind, level, message, display, change_pct) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
            if cursor.rowcount == 0:
                continue
            (
                alert_ts,
                alert_date,
                alert_time,
                symbol,
                kind,
                level,
                message,
                display,
                change_pct,
            ) = row
            record_db_change_best_effort(
                conn,
                db_name="trading.db",
                table="alert_events",
                action="create",
                key=f"{symbol}:{alert_ts}",
                source="stock_notifier",
                actor=make_actor(actor_type="system", actor_id="stock_notifier"),
                before=None,
                after={
                    "ts": alert_ts,
                    "date": alert_date,
                    "time": alert_time,
                    "symbol": symbol,
                    "kind": kind,
                    "level": level,
                    "message": message,
                    "display": display,
                    "change_pct": change_pct,
                },
                hash_text_fields=True,
            )
        conn.commit()
    except Exception as e:
        logger.warning(f"写入 alert_events 到 SQLite 失败: {e}")
    finally:
        if conn:
            conn.close()


# ══════════════════════════════════════════
# 5. AI investment events → alert_events (single notifier entry point)
# ══════════════════════════════════════════

AI_INVESTMENT_EVENT_BATCH = 20


def _map_ai_investment_row_to_alert(row: dict) -> tuple[dict | None, str]:
    """Build one alert dict for write_alert_events / stealth_dispatch; return notify_status to persist.

    Returns (None, status) when the row should be marked suppressed without an alert.
    """
    scope = (row.get("delivery_scope") or "web_only").strip()
    if scope == "suppressed":
        return None, "suppressed"

    sev = (row.get("severity") or "normal").strip()
    sym = (row.get("symbol") or "").strip()
    title = (row.get("title") or "AI").strip()
    summary = (row.get("summary") or "").strip()
    eid = row.get("id") or ""
    verdict = (row.get("verdict") or "").strip()
    message = f"[AI {eid}] {summary}" if summary else f"[AI {eid}] {title}"
    if len(message) > 900:
        message = message[:897] + "..."

    line = f"{sym} {title}".strip() if sym else title
    if verdict:
        line = f"{line} — {verdict[:120]}"

    if scope == "web_only":
        level = 3
        nstatus = "web_only"
    elif scope == "daily_only":
        level = 3
        nstatus = "web_only"
    elif scope == "feishu_high":
        if sev in ("critical", "high"):
            level = 1
        else:
            level = 2
        nstatus = "sent"
    elif scope == "feishu_normal":
        level = 2
        nstatus = "sent"
    else:
        level = 3
        nstatus = "web_only"

    alert = {
        "title": title,
        "message": message,
        "symbol": sym,
        "display": line[:500],
        "_kind": "ai_investment",
        "_level": level,
        "_change_pct": 0.0,
        "_stealth": message[:500],
    }
    return alert, nstatus


def _update_ai_investment_notify_status(event_id: str, status: str) -> None:
    from src.sim_trading.db import get_connection

    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE ai_investment_events
            SET notify_status = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, now_iso, event_id),
        )
        conn.commit()
    finally:
        conn.close()


def consume_pending_ai_investment_events() -> int:
    """Drain pending rows from ai_investment_events into alert_events and update notify_status.

    Intended to be called periodically from the notifier main loop (not from other daemons).
    Returns the number of rows finalized (including suppressed without an alert).
    """
    from src.sim_trading.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT * FROM ai_investment_events
            WHERE notify_status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
            """,
            (AI_INVESTMENT_EVENT_BATCH,),
        ).fetchall()
    finally:
        conn.close()

    if not rows:
        return 0

    done = 0
    for row in rows:
        r = dict(row)
        eid = r.get("id")
        if not eid:
            continue
        try:
            alert, nstatus = _map_ai_investment_row_to_alert(r)
            if alert is None:
                _update_ai_investment_notify_status(eid, nstatus)
                done += 1
                continue

            write_alert_events([alert])
            lvl = alert.get("_level", 3)
            if lvl == 1:
                stealth_dispatch([alert], sound="default")
            elif lvl == 2:
                stealth_dispatch([alert], sound="")
            _update_ai_investment_notify_status(eid, nstatus)
            done += 1
        except Exception as e:
            logger.warning("ai_investment_events id=%s failed: %s", eid, e)
            try:
                _update_ai_investment_notify_status(eid, "failed")
            except Exception as e2:
                logger.warning("ai_investment_events id=%s mark failed: %s", eid, e2)
            done += 1

    return done


# ══════════════════════════════════════════
# 5a. L2 strategy signal consumption
# ══════════════════════════════════════════

PER_STOCK_DAILY_CAP = 8  # max L2 alerts per stock per day (safety net)
_l2_watermark_unavailable = False


def _load_l2_signal_watermark_from_db() -> int:
    """Return latest known L2 signal timestamp so restarts do not replay history."""
    global _l2_watermark_unavailable
    conn = None
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        state = conn.execute(
            "SELECT value_json FROM tick_monitor_state WHERE key = ?",
            ("stock_notifier_l2_last_consumed",),
        ).fetchone()
        if state:
            _l2_watermark_unavailable = False
            return int(state["value_json"])
        row = conn.execute("SELECT COALESCE(MAX(ts), 0) AS max_ts FROM signals").fetchone()
        watermark = int(row["max_ts"] or 0) if row else 0
        _save_l2_signal_watermark_to_db(watermark, conn=conn)
        _l2_watermark_unavailable = False
        return watermark
    except Exception as e:
        _l2_watermark_unavailable = True
        logger.warning(f"Failed to load L2 signal watermark; L2 consumption paused: {e}")
        return 0
    finally:
        if conn:
            conn.close()


def _save_l2_signal_watermark_to_db(watermark: int, conn=None) -> None:
    owns_conn = conn is None
    try:
        if conn is None:
            from src.sim_trading.db import get_connection

            conn = get_connection()
        conn.execute(
            """
            INSERT OR REPLACE INTO tick_monitor_state (key, value_json, updated_at_ms)
            VALUES (?, ?, ?)
            """,
            ("stock_notifier_l2_last_consumed", str(int(watermark)), int(time.time() * 1000)),
        )
        if owns_conn:
            conn.commit()
    except Exception as e:
        logger.warning(f"Failed to save L2 signal watermark: {e}")
    finally:
        if owns_conn and conn:
            conn.close()


def check_l2_signals() -> list[dict]:
    """读取 trading.db 中未处理的 L2 信号，转换为 alert 格式。

    L2 daemon 写信号 → notifier 消费 → 统一 dispatch。
    用 lastConsumed 时间戳避免重复处理。
    Per-stock daily cap: 超过 PER_STOCK_DAILY_CAP 的信号不写入 alert_events。
    """
    global _l2_watermark_unavailable
    if _l2_watermark_unavailable:
        check_l2_signals._last_consumed = _load_l2_signal_watermark_from_db()
        if _l2_watermark_unavailable:
            return []

    conn = None
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            """
            SELECT ts, strategy, code, direction, notify, detail, display
            FROM signals
            WHERE ts > ?
            ORDER BY ts
            """,
            (check_l2_signals._last_consumed,),
        ).fetchall()
        new_signals = [dict(row) for row in rows]
    except Exception as e:
        logger.debug(f"Failed to read L2 signals from DB: {e}")
        return []
    finally:
        if conn:
            conn.close()

    if not new_signals:
        return []

    # 更新消费位点
    check_l2_signals._last_consumed = max(s.get("ts", 0) for s in new_signals)
    _save_l2_signal_watermark_to_db(check_l2_signals._last_consumed)

    # 转换为 notifier alert 格式, applying per-stock daily cap
    alerts = []
    for s in new_signals:
        strategy = s.get("strategy", "")
        display = s.get("display", "")
        code = s.get("code", "")
        should_notify = bool(s.get("notify", False))

        # Per code+strategy daily dedup: same signal for same stock only once per day
        # Key uses (code, strategy) — NOT display, which contains dynamic values
        # (change_pct, net_amount, etc.) that vary every tick, causing duplicate alerts.
        dedup_key = (code, strategy)
        if dedup_key in check_l2_signals._seen_today:
            continue
        check_l2_signals._seen_today.add(dedup_key)

        # Per-stock daily cap check (exempt notify=True / L1 signals)
        count = check_l2_signals._daily_counts.get(code, 0)
        if count >= PER_STOCK_DAILY_CAP and not should_notify:
            continue
        check_l2_signals._daily_counts[code] = count + 1

        alerts.append(
            {
                "symbol": code,
                "title": f"L2 {strategy}",
                "message": display,
                "_kind": "l2_strategy",
                "_change_pct": 0,
                # message is "{stock_name} {cn_name}" — first word is the stock name
                "_name": code,
                "_stealth": display,
                "_notify": should_notify,
            }
        )

    return alerts


# Initialize consumption watermark and daily counters
check_l2_signals._last_consumed = _load_l2_signal_watermark_from_db()
check_l2_signals._daily_counts = {}  # {code: count} — reset daily at 08:00


def _load_seen_today_from_db() -> set:
    """Load today's already-consumed (symbol, strategy) pairs from signals table.

    This ensures dedup survives daemon/notifier restarts — same signal
    for the same stock won't be written twice even after process restart.
    Uses signals table (has `strategy` column) instead of alert_events
    (which only has `kind='l2_strategy'` without strategy name).

    IMPORTANT: Only loads signals with ts <= _last_consumed watermark,
    so unconsumed signals are NOT marked as "seen" — they will be
    processed normally on next check_l2_signals() call.
    """
    seen = set()
    conn = None
    try:
        from src.sim_trading.db import get_connection
        import datetime as _dt

        today_str = _dt.date.today().strftime("%Y-%m-%d")
        watermark = check_l2_signals._last_consumed
        if not watermark:
            return seen
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT code, strategy FROM signals WHERE date = ? AND ts <= ?",
            (today_str, watermark),
        ).fetchall()
        for code, strategy in rows:
            seen.add((code, strategy))
    except Exception as e:
        logger.warning(f"Load consumed L2 signals failed: {e}")
    finally:
        if conn:
            conn.close()
    return seen


check_l2_signals._seen_today = _load_seen_today_from_db()


# ══════════════════════════════════════════
# 5c. tick_monitor signal consumption
# ══════════════════════════════════════════


def check_tick_monitor_signals() -> tuple[list[dict], list[dict]]:
    """从 trading.db 读取未处理的 tick_monitor 信号（tick_monitor 直接写 DB）。

    tick_monitor 写 DB → notifier 消费 → stealth_dispatch:
      - Feishu: 批量推送（3min per-symbol cooldown）
      - macOS: 弹窗 + 声音（无额外 cooldown）
      - Web: write_alert_events

    Returns:
        (alerts_to_dispatch, alerts_web_only):
            - alerts_to_dispatch: L1 alerts → stealth_dispatch
            - alerts_web_only: L3 alerts → write to DB only
    """
    try:
        from src.tools.tick_monitor import get_pending_tick_signals
        return get_pending_tick_signals()
    except Exception as e:
        logger.warning(f"check_tick_monitor_signals failed: {e}")
        return [], []


def stealth_dispatch(alerts: list[dict], *, sound: str = ""):
    """Batch alerts into 1~2 stealth notifications.

    Rules:
    - 0 alerts: do nothing
    - 1~2 alerts: 1 notification, each alert a line
    - 3+ alerts: 1 notification, top 2 + "+N more"
    - Portfolio summary is always a separate notification
    - No sound by default (discreet); sound="default" for critical only
    - Alerts are clustered via AlertClusterer to reduce noise
    """
    if not alerts:
        return 0

    # ── 智能聚类降噪 ──
    try:
        clusterer = AlertClusterer()
        alerts = clusterer.cluster(alerts)
    except Exception as e:
        logger.warning(f"AlertClusterer failed, using raw alerts: {e}")

    # Separate portfolio-level from per-stock alerts
    portfolio_alerts = [a for a in alerts if a.get("_kind") == "portfolio"]
    stock_alerts = [a for a in alerts if a.get("_kind") != "portfolio"]

    # Separate tick_monitor alerts (handled separately with batch Feishu)
    tick_alerts = [a for a in stock_alerts if a.get("_kind") == "tick_monitor"]
    other_alerts = [a for a in stock_alerts if a.get("_kind") != "tick_monitor"]

    # Apply per-symbol cooldown to Feishu batch ONLY, NOT macOS
    now_ts = time.time()
    feishu_batch = []
    for a in tick_alerts:
        symbol = a.get("symbol", "")
        if not symbol:
            continue
        last_ts = _tick_symbol_cooldown.get(symbol, 0)
        if now_ts - last_ts >= _TICK_SYMBOL_COOLDOWN_SEC:
            feishu_batch.append(a)
            _tick_symbol_cooldown[symbol] = now_ts
    # drop symbols older than 2 hours to prevent memory growth
    cutoff = now_ts - 7200
    for sym in list(_tick_symbol_cooldown.keys()):
        if _tick_symbol_cooldown[sym] < cutoff:
            del _tick_symbol_cooldown[sym]

    # Send batch Feishu for cooled-down tick_alerts
    feishu_ok = False
    if feishu_batch:
        try:
            from src.tools.stock_monitor import feishu_send_tick_batch

            feishu_ok = feishu_send_tick_batch(feishu_batch)
        except Exception as e:
            logger.warning(f"feishu_send_tick_batch failed: {e}")
            feishu_ok = False

    # All tick_alerts (including cooled-down ones) go to macOS popup
    if feishu_ok:
        for a in feishu_batch:
            a["_skip_feishu"] = True
        other_alerts.extend(feishu_batch)  # only add to macOS if Feishu succeeded

    # Note: tick_alerts written via main loop's write_alert_events(all_alerts)
    # tick_web_only written separately in main loop

    sent = 0

    # ── Per-stock alerts → 1 notification ──
    if other_alerts:
        # Sort by severity: threshold > pnl > big_move > l2_strategy
        priority = {"threshold": 0, "pnl": 1, "big_move": 2, "l2_strategy": 3}
        other_alerts.sort(key=lambda a: priority.get(a.get("_kind", ""), 9))

        lines = []
        for a in other_alerts[:2]:
            lines.append(a.get("_stealth", a["message"]))
        if len(other_alerts) > 2:
            lines.append(f"+{len(other_alerts) - 2} more")

        # Critical if any threshold breach or change > 8%
        has_critical = any(
            a.get("_kind") == "threshold" or abs(a.get("_change_pct", 0)) >= 8
            for a in other_alerts
        )

        top = other_alerts[0]

        # STALE / system alerts have empty symbol → skip stock_info card
        top_symbol = top.get("symbol", "")
        if top_symbol:
            # Extract name: prefer _name, then _stealth prefix, then title prefix
            alert_name = top.get("_name", "")
            if not alert_name:
                stealth = top.get("_stealth", "")
                if stealth and not stealth.startswith("["):
                    alert_name = stealth.split()[0] if stealth.split() else ""
            if not alert_name:
                title = top.get("title", "")
                if title and not title.startswith("["):
                    alert_name = title.split()[0] if title.split() else ""
            stock_info = {
                "name": alert_name,
                "code": top_symbol,
                "price": top.get("_price", ""),
                "change_pct": top.get("_change_pct"),
                "level": top.get("_level"),
                "_kind": top.get("_kind"),
                "_skip_feishu": top.get("_skip_feishu", False),
                "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        else:
            stock_info = None

        notify(
            _notify_title(is_summary=False),
            "\n".join(lines),
            sound="default" if has_critical else sound,
            change_pct=top.get("_change_pct"),
            level=top.get("_level"),
            stock_info=stock_info,
        )
        sent += 1

    # ── Portfolio summary → separate notification (no sound) ──
    if portfolio_alerts:
        a = portfolio_alerts[0]
        notify(
            _notify_title(is_summary=True),
            a.get("_stealth", a["message"]),
            sound="",
            is_portfolio=True,
        )
        sent += 1

    return sent


def stealth_dispatch_open_close(alerts: list[dict]):
    """Dispatch open/close notifications in stealth mode."""
    for a in alerts:
        title = a.get("title", "")
        if "开盘" in title:
            notify("Sprint Started", a.get("_stealth", a["message"]), sound="")
        elif "收盘" in title:
            notify("Daily Report", a.get("_stealth", a["message"]), sound="")
        else:
            notify(_notify_title(), a.get("_stealth", a["message"]), sound="")


# ══════════════════════════════════════════
# 5b. Status line
# ══════════════════════════════════════════


def print_status(checked: int, alert_count: int, next_sec: int, trading: bool):
    """Print a compact single-line status, overwriting the previous one."""
    now = datetime.now().strftime("%H:%M:%S")
    if trading:
        line = f"[{now}] checked {checked} stocks | alerts today: {alert_count} | next: {next_sec}s"
    else:
        line = f"[{now}] non-trading hours | alerts today: {alert_count} | next check: {next_sec}s"
    # \r + \033[K = overwrite current line
    print(f"\r\033[K{line}", end="", flush=True)


# ══════════════════════════════════════════
# 5b. Market open/close notifications
# ══════════════════════════════════════════


def _count_watchlist(watchlist: dict) -> tuple[int, int]:
    """Count holdings and watching stocks (non-hidden).

    Returns (num_holdings, num_watching).
    """
    holdings = 0
    watching = 0
    for entry in watchlist.values():
        if entry.get("hidden", False):
            continue
        if entry.get("type") == "holding" and entry.get("cost") and entry.get("shares"):
            holdings += 1
        else:
            watching += 1
    return holdings, watching


def _build_close_summary(
    quotes: dict,
    config: dict,
    hkd_cny_rate: float | None,
) -> str:
    """Build a multi-line market close summary message.

    Computes:
    - Total portfolio daily P&L (CNY, %)
    - Top 3 gainers and top 3 losers among holdings
    - Stocks that hit above/below thresholds during the session
    """
    watchlist = config.get("watchlist", {})

    # ── Collect holding data ──
    holdings_data: list[dict] = []
    total_daily_pnl = 0.0
    total_market_value = 0.0

    for symbol, quote in quotes.items():
        entry = watchlist.get(symbol, {})
        if entry.get("type") != "holding":
            continue
        cost = entry.get("cost")
        shares = entry.get("shares")
        if not cost or not shares or cost <= 0 or shares <= 0:
            continue

        price = quote.get("price", 0)
        change_pct = quote.get("change_pct", 0)
        chg_amt = quote.get("chg_amt", 0)
        name = quote.get("name", symbol)

        if price <= 0:
            continue

        is_hk = is_hk_symbol(symbol)
        fx = hkd_cny_rate if is_hk else 1.0

        daily_pnl_native = chg_amt * shares
        if fx is not None and fx > 0:
            daily_pnl_cny = daily_pnl_native * fx
            market_value_cny = price * shares * fx
        else:
            daily_pnl_cny = daily_pnl_native  # fallback: treat as 1:1
            market_value_cny = price * shares

        total_daily_pnl += daily_pnl_cny
        total_market_value += market_value_cny

        holdings_data.append(
            {
                "symbol": symbol,
                "name": name,
                "change_pct": change_pct,
                "daily_pnl_cny": daily_pnl_cny,
            }
        )

    # ── Threshold hits (from config.db alert_rules) ──
    threshold_hits: list[str] = []
    _alert_data: dict[str, dict] = {}
    conn = None
    try:
        from src.sim_trading.db import get_config_connection

        conn = get_config_connection()
        rows = conn.execute("SELECT symbol, above, below FROM alert_rules").fetchall()
        _alert_data = {
            row["symbol"]: {
                "above": row["above"],
                "below": row["below"],
            }
            for row in rows
        }
    except Exception:
        _alert_data = {}
    finally:
        if conn:
            conn.close()

    for symbol, quote in quotes.items():
        price = quote.get("price", 0)
        name = quote.get("name", symbol)
        if price <= 0:
            continue
        _ae = _alert_data.get(symbol, {})
        above = _ae.get("above")
        below = _ae.get("below")
        if above is not None and price >= above:
            threshold_hits.append(f"  {name} 突破上限 {above}（现价 {price:.2f}）")
        if below is not None and price <= below:
            threshold_hits.append(f"  {name} 跌破下限 {below}（现价 {price:.2f}）")

    # ── Build message lines ──
    lines: list[str] = []

    if holdings_data and total_market_value > 0:
        portfolio_pct = (total_daily_pnl / total_market_value) * 100
        sign = "+" if total_daily_pnl >= 0 else ""
        pct_sign = "+" if portfolio_pct >= 0 else ""
        lines.append(
            f"持仓盈亏: {sign}{total_daily_pnl:,.0f}元 ({pct_sign}{portfolio_pct:.2f}%)"
        )
    else:
        lines.append("持仓盈亏: 无持仓数据")

    # Top gainers / losers (sorted by change_pct)
    if holdings_data:
        sorted_by_change = sorted(
            holdings_data, key=lambda x: x["change_pct"], reverse=True
        )

        gainers = [h for h in sorted_by_change if h["change_pct"] > 0][:3]
        losers = [h for h in reversed(sorted_by_change) if h["change_pct"] < 0][:3]

        if gainers:
            parts = [f"{h['name']}({h['change_pct']:+.1f}%)" for h in gainers]
            lines.append(f"领涨: {', '.join(parts)}")
        if losers:
            parts = [f"{h['name']}({h['change_pct']:+.1f}%)" for h in losers]
            lines.append(f"领跌: {', '.join(parts)}")

    if threshold_hits:
        lines.append("触发阈值:")
        lines.extend(threshold_hits)

    return "\n".join(lines)


def check_market_open_close(
    *,
    sent_open: bool,
    sent_close: bool,
    config: dict,
    quotes: dict | None,
    hkd_cny_rate: float | None,
) -> tuple[bool, bool, list[dict]]:
    """Check if market open/close notifications should be sent.

    Only fires for A-share market (9:30 open, 15:01 close).
    Returns updated (sent_open, sent_close, alerts_to_send).
    """
    now = datetime.now()
    if now.weekday() >= 5:
        return sent_open, sent_close, []

    t = now.hour * 100 + now.minute
    watchlist = config.get("watchlist", {})
    alerts: list[dict] = []

    # ── Market open notification: 09:30 ~ 09:45 window ──
    if not sent_open and 930 <= t <= 945:
        num_holdings, num_watching = _count_watchlist(watchlist)
        alerts.append(
            {
                "title": "开盘",
                "message": f"A股开盘 | 关注: {num_holdings}只持仓, {num_watching}只自选",
                "_stealth": f"持仓{num_holdings}只 自选{num_watching}只",
            }
        )
        sent_open = True

    # ── Market close notification: 15:01 ~ 15:15 window ──
    if not sent_close and 1501 <= t <= 1515 and quotes:
        summary = _build_close_summary(quotes, config, hkd_cny_rate)
        close_alert = {
            "title": "收盘",
            "message": summary,
            "_stealth": summary,  # close summary is already compact enough
            "symbol": "MARKET",  # 特殊标记，表示市场收盘
            "_kind": "market_close",
            "_change_pct": 0,
        }
        alerts.append(close_alert)
        # 保存到数据库，供 web 端显示
        write_alert_events([close_alert])
        sent_close = True

    return sent_open, sent_close, alerts


# ══════════════════════════════════════════
# 6. Trade Plan Engine
# ══════════════════════════════════════════

def _get_trading_conn() -> sqlite3.Connection:
    from src.sim_trading.db import get_connection, init_trading_db

    init_trading_db()
    return get_connection()


def _read_trade_plan_audit_snapshot(
    conn: sqlite3.Connection, plan_ids: list[str]
) -> list[dict]:
    if not plan_ids:
        return []
    placeholders = ",".join("?" * len(plan_ids))
    rows = conn.execute(
        f"""
        SELECT id, name, symbol, status, scope, created_at, orders_json, updated_at
        FROM trade_plans
        WHERE id IN ({placeholders})
        ORDER BY id
        """,
        tuple(plan_ids),
    ).fetchall()
    result: list[dict] = []
    for row in rows:
        try:
            orders = json.loads(row["orders_json"]) if row["orders_json"] else []
        except json.JSONDecodeError:
            orders = []
        result.append(
            {
                "id": row["id"],
                "name": row["name"],
                "symbol": row["symbol"],
                "status": row["status"],
                "scope": row["scope"] or "real",
                "created_at": row["created_at"],
                "orders": orders,
                "updated_at": row["updated_at"],
            }
        )
    return result


def _record_trade_plan_audit(
    conn: sqlite3.Connection,
    *,
    action: str,
    key: str,
    before: dict | None,
    after: dict | None,
    metadata: dict | None = None,
) -> None:
    event = build_audit_event_v2(
        event_id=uuid.uuid4().hex,
        ts_ms=int(time.time() * 1000),
        source="stock_notifier",
        actor=make_actor(actor_type="system", actor_id="stock_notifier"),
        action=action,
        entity="trade_plans",
        key=key,
        db_name="trading.db",
        before=before,
        after=after,
        metadata=metadata or {},
    )
    insert_trading_outbox(conn, event)


class TradePlanEngine:
    """检查交易计划条件，触发通知 + 模拟执行。

    每 tick 调用 check()，返回触发的告警列表（格式兼容 write_alert_events）。
    触发后自动更新 trading.db trade_plans 表（标记 triggered=True）并写入 trade_plan_events 表。

    A 股指标条件单: order 中含 "indicators" 字段时，自动从 akshare 拉取日线计算指标。
    每日每只 A 股最多拉取一次 kline（缓存到 _ashare_ind_cache）。
    仅在 15:05-15:20 时间窗口检测指标条件（收盘数据才有意义）。
    """

    # A 股 kline 缓存刷新间隔 (秒) — 盘中每 30 分钟重新拉 kline
    KLINE_REFRESH_INTERVAL = 1800

    def __init__(self):
        self._plans: dict = {}
        self._pending_plan_events: list[tuple] = []
        self._consecutive_tracker: dict[
            str, dict
        ] = {}  # {plan_id: {cond_id: {"count": N, "last_date": "YYYY-MM-DD"}}}
        self._last_mtime: float = 0.0
        self._ashare_kline_cache: dict[str, object] = {}  # {symbol: kline DataFrame}
        self._ashare_kline_ts: dict[str, float] = {}  # {symbol: last fetch timestamp}
        self._ashare_kline_date: str = ""  # 缓存日期
        self._reload_plans()

    def _reload_plans(self):
        """Load or reload plans from trading.db (checks updated_at for hot-reload)."""
        conn = None
        try:
            conn = _get_trading_conn()
            # Ensure scope column exists (idempotent migration)
            try:
                conn.execute("SELECT scope FROM trade_plans LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    conn.execute("ALTER TABLE trade_plans ADD COLUMN scope TEXT NOT NULL DEFAULT 'real'")
                except sqlite3.OperationalError:
                    pass
            rows = conn.execute(
                "SELECT id, name, symbol, status, scope, created_at, orders_json, updated_at "
                "FROM trade_plans"
            ).fetchall()
        except Exception as e:
            logger.warning(f"TradePlan: failed to load from DB: {e}")
            self._plans = {}
            return
        finally:
            if conn:
                conn.close()

        # Use max updated_at as mtime proxy
        mtime = max((r["updated_at"] or 0 for r in rows), default=0)
        if mtime == self._last_mtime:
            return
        self._last_mtime = mtime

        plans: dict = {}
        for r in rows:
            plan_id = r["id"]
            try:
                orders = json.loads(r["orders_json"]) if r["orders_json"] else []
            except json.JSONDecodeError:
                orders = []
            plans[plan_id] = {
                "name": r["name"],
                "symbol": r["symbol"],
                "status": r["status"],
                "scope": r["scope"] or "real",
                "created_at": r["created_at"],
                "orders": orders,
            }
        self._plans = plans

    def _save_plans(self):
        """Write plans back to trading.db trade_plans table."""
        conn = None
        try:
            conn = _get_trading_conn()
            now_ts = int(time.time())
            # Ensure scope column exists (idempotent migration)
            try:
                conn.execute("SELECT scope FROM trade_plans LIMIT 1")
            except sqlite3.OperationalError:
                try:
                    conn.execute("ALTER TABLE trade_plans ADD COLUMN scope TEXT NOT NULL DEFAULT 'real'")
                except sqlite3.OperationalError:
                    pass
            plan_ids = sorted(self._plans.keys())
            before = _read_trade_plan_audit_snapshot(conn, plan_ids)
            conn.execute("BEGIN")
            pending_events = getattr(self, "_pending_plan_events", [])
            for event in pending_events:
                cursor = conn.execute(
                    "INSERT OR IGNORE INTO trade_plan_events "
                    "(ts, date, plan_id, event_type, condition_id, label, price, shares, message) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    event,
                )
                if cursor.rowcount == 0:
                    continue
                (
                    event_ts,
                    event_date,
                    plan_id,
                    event_type,
                    condition_id,
                    label,
                    price,
                    shares,
                    message,
                ) = event
                record_db_change_best_effort(
                    conn,
                    db_name="trading.db",
                    table="trade_plan_events",
                    action="create",
                    key=f"{plan_id}:{condition_id}:{event_ts}",
                    source="stock_notifier_trade_plan",
                    actor=make_actor(actor_type="system", actor_id="stock_notifier"),
                    before=None,
                    after={
                        "ts": event_ts,
                        "date": event_date,
                        "plan_id": plan_id,
                        "event_type": event_type,
                        "condition_id": condition_id,
                        "label": label,
                        "price": price,
                        "shares": shares,
                        "message": message,
                    },
                    hash_text_fields=True,
                )
            for plan_id, plan in self._plans.items():
                conn.execute(
                    "INSERT OR REPLACE INTO trade_plans (id, name, symbol, status, scope, created_at, orders_json, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        plan_id,
                        plan.get("name", ""),
                        plan.get("symbol", ""),
                        plan.get("status", "active"),
                        plan.get("scope", "real"),
                        plan.get("created_at", ""),
                        json.dumps(plan.get("orders", []), ensure_ascii=False),
                        now_ts,
                    ),
                )
            if plan_ids:
                after = _read_trade_plan_audit_snapshot(conn, plan_ids)
                _record_trade_plan_audit(
                    conn,
                    action="save",
                    key=",".join(plan_ids),
                    before={"trade_plans": before},
                    after={"trade_plans": after},
                    metadata={"plan_count": len(plan_ids)},
                )
            conn.commit()
            if hasattr(self, "_pending_plan_events"):
                self._pending_plan_events.clear()
            self._last_mtime = now_ts
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error(f"TradePlan: failed to save to DB: {e}")
        finally:
            if conn:
                conn.close()

    def _get_ashare_indicators(
        self, symbol: str, live_price: float = 0, live_volume: float = 0
    ) -> dict:
        """获取 A 股日线技术指标，盘中用实时价格预估。

        - kline 缓存每 30 分钟刷新一次（避免 akshare 限速）
        - 盘中 (9:30-15:00): 用 live_price 追加当日 bar，计算预估指标
        - 收盘后 (15:00+): 直接用 kline 最后一根 bar（当日完整数据）
        - 非交易时段: 返回上次缓存的指标或空 dict
        """
        now = datetime.now()
        if now.weekday() >= 5:
            return {}
        hhmm = now.hour * 100 + now.minute
        # 仅交易时段 + 收盘后 20 分钟内检测 (9:25 - 15:20)
        if hhmm < 925 or hhmm > 1520:
            return {}

        today = now.strftime("%Y-%m-%d")
        # 日期变化 → 清空 kline 缓存
        if self._ashare_kline_date != today:
            self._ashare_kline_cache.clear()
            self._ashare_kline_ts.clear()
            self._ashare_kline_date = today

        # 拉取/刷新 kline（每 30 分钟一次）
        ts_now = now.timestamp()
        last_ts = self._ashare_kline_ts.get(symbol, 0)
        if symbol not in self._ashare_kline_cache or (
            ts_now - last_ts > self.KLINE_REFRESH_INTERVAL
        ):
            try:
                from src.tools.indicator_alert_engine import fetch_kline_akshare
                import time

                kline = fetch_kline_akshare(symbol, days=60)
                if kline is None:
                    logger.warning(
                        f"TradePlan: A-share kline fetch failed for {symbol}"
                    )
                    return {}
                self._ashare_kline_cache[symbol] = kline
                self._ashare_kline_ts[symbol] = ts_now
                time.sleep(1.5)  # akshare 限速
            except Exception as e:
                logger.warning(f"TradePlan: A-share kline error for {symbol}: {e}")
                return {}

        kline = self._ashare_kline_cache.get(symbol)
        if kline is None:
            return {}

        try:
            from src.tools.indicator_alert_engine import compute_indicators

            # 盘中 (9:30-15:00): 用实时价格作为当日收盘预估
            is_trading = 930 <= hhmm <= 1500
            if is_trading and live_price > 0:
                ind = compute_indicators(
                    kline, live_price=live_price, live_volume=live_volume
                )
            else:
                ind = compute_indicators(kline)

            if ind is None:
                return {}

            return {
                "rsi": ind.get("rsi"),
                "macd_hist_list": [
                    ind.get("macd_hist_prev", 0),
                    ind.get("macd_hist", 0),
                ],
                "vol_ratio": ind.get("vol_ratio"),
                "macd_golden_cross": ind.get("macd_golden_cross", False),
                "macd_death_cross": ind.get("macd_death_cross", False),
                "macd_bull_divergence": ind.get("macd_bull_divergence", False),
                "ma5_turn_up": ind.get("ma5_turn_up", False),
            }
        except Exception as e:
            logger.warning(
                f"TradePlan: A-share indicator compute error for {symbol}: {e}"
            )
            return {}

    def _plan_has_indicator_orders(self, plan: dict) -> bool:
        """Check if any order in the plan has indicator conditions."""
        for order in plan.get("orders", []):
            if order.get("triggered"):
                continue
            if order.get("indicators"):
                return True
        return False

    def check(self, quotes: dict) -> list[dict]:
        """Check all active plans against current quotes.

        Returns list of alert dicts compatible with write_alert_events().
        """
        self._reload_plans()
        alerts: list[dict] = []
        dirty = False
        today = datetime.now().strftime("%Y-%m-%d")

        for plan_id, plan in self._plans.items():
            if plan.get("status") != "active":
                continue
            symbol = plan.get("symbol", "")
            q = quotes.get(symbol)
            if not q:
                continue
            price = q.get("price", 0)
            if price <= 0:
                continue
            amount = q.get("amount", 0) or q.get("_amount", 0)
            change = q.get("change_pct", 0) or q.get("change", 0)
            name = q.get("name", symbol)

            # 获取指标: HK 股先从 quotes 取 (L2 daemon)，无则 akshare 拉取; A 股直接 akshare
            ind = q.get("indicators", {})
            if not ind and not symbol.startswith("KR"):
                if self._plan_has_indicator_orders(plan):
                    ind = self._get_ashare_indicators(
                        symbol,
                        live_price=price,
                        live_volume=q.get("volume", 0) or 0,
                    )

            # 条件单 (统一处理 buy/sell/止损/trailing)
            for order in plan.get("orders", []):
                if order.get("triggered"):
                    continue
                if not self._check_order_conditions(
                    plan_id, order, price, amount, today, indicators=ind
                ):
                    if order.pop("_ts_dirty", False):
                        dirty = True
                    continue
                side = order.get("side", "sell")
                shares = order.get("shares", 0) or 0
                label = order.get("label", "")
                if side == "buy":
                    prefix = f"买入 {shares}股"
                    event_type = "buy_triggered"
                else:
                    prefix = f"卖出 {shares}股"
                    event_type = "sell_triggered"
                alert = self._make_alert(
                    plan_id,
                    plan,
                    order["id"],
                    f"{prefix}: {label}",
                    price,
                    name,
                    change,
                    event_type=event_type,
                    shares=shares,
                    side=side,
                )
                alerts.append(alert)
                order["triggered"] = True
                order["triggered_at"] = datetime.now().isoformat()
                dirty = True
                self._write_plan_event(
                    plan_id, event_type, order["id"], label, price, shares
                )
                logger.info(
                    f"PLAN {side.upper()} {plan['name']}: {label} @ {price:.2f}"
                )

        if dirty:
            self._save_plans()
        return alerts

    def _check_order_conditions(
        self,
        plan_id: str,
        order: dict,
        price: float,
        amount: float,
        today: str,
        **kwargs,
    ) -> bool:
        """Check if an order's conditions are met. Supports trailing + indicator conditions."""
        op = order.get("op", ">=")
        target = order.get("price", 0)
        side = order.get("side", "sell")

        # ── Trailing order logic ──
        ts = order.get("trailing")
        if ts and ts.get("pct"):
            pct = ts["pct"]
            wm = ts.get("watermark") or 0
            is_active = ts.get("active", False)

            if not is_active:
                activated = (price >= target) if op == ">=" else (price <= target)
                if activated:
                    is_active = True
                    wm = price
                    logger.info(
                        f"Order trailing activated: {plan_id}:{order['id']} @ {price:.4f}"
                    )

            if is_active:
                if side == "sell":
                    if price > wm:
                        wm = price
                    if wm > 0 and price <= wm * (1 - pct / 100):
                        ts["active"] = is_active
                        ts["watermark"] = round(wm, 4)
                        return True
                else:
                    if wm == 0 or price < wm:
                        wm = price
                    if wm > 0 and price >= wm * (1 + pct / 100):
                        ts["active"] = is_active
                        ts["watermark"] = round(wm, 4)
                        return True

            if is_active != ts.get("active") or wm != (ts.get("watermark") or 0):
                ts["active"] = is_active
                ts["watermark"] = round(wm, 4)
                order["_ts_dirty"] = True

            return False

        # ── Normal price check ──
        if op == ">=":
            if price < target:
                return False
        else:
            if price > target:
                return False

        vol_min = order.get("volume_min")
        if vol_min and amount < vol_min:
            return False

        cons_days = order.get("consecutive_days")
        if cons_days:
            key = f"{plan_id}:{order['id']}"
            if key not in self._consecutive_tracker:
                self._consecutive_tracker[key] = {"count": 0, "last_date": ""}
            tracker = self._consecutive_tracker[key]

            if (op == ">=" and price >= target) or (op == "<=" and price <= target):
                if tracker["last_date"] != today:
                    tracker["count"] += 1
                    tracker["last_date"] = today
            else:
                tracker["count"] = 0
                tracker["last_date"] = ""

            if tracker["count"] < cons_days:
                return False

        # ── Indicator conditions (AND with price/volume) ──
        ind_cond = order.get("indicators")
        if ind_cond:
            indicators = kwargs.get("indicators", {})
            if not self._check_indicator_conditions(ind_cond, indicators):
                return False

        return True

    def _check_indicator_conditions(self, cond: dict, indicators: dict) -> bool:
        """Check technical indicator conditions (AND logic).

        Args:
            cond: indicator conditions from order["indicators"]
            indicators: {rsi, macd_hist, macd_hist_list, vol_ratio} from L2 daemon

        Returns True if ALL conditions met. Returns False if indicators unavailable (safe default).
        """
        if not indicators:
            return False  # 指标不可用 → 不触发（安全默认）

        if "rsi_above" in cond:
            rsi = indicators.get("rsi")
            if rsi is None or rsi <= cond["rsi_above"]:
                return False

        if "rsi_below" in cond:
            rsi = indicators.get("rsi")
            if rsi is None or rsi >= cond["rsi_below"]:
                return False

        if "macd_hist_narrowing_days" in cond:
            hist = indicators.get("macd_hist_list", [])
            n = cond["macd_hist_narrowing_days"]
            if len(hist) < n + 1:
                return False
            recent = hist[-(n + 1) :]
            for i in range(1, len(recent)):
                if abs(recent[i]) >= abs(recent[i - 1]):
                    return False

        if cond.get("macd_golden_cross"):
            hist = indicators.get("macd_hist_list", [])
            if len(hist) < 2 or not (hist[-2] <= 0 < hist[-1]):
                return False

        if cond.get("macd_death_cross"):
            hist = indicators.get("macd_hist_list", [])
            if len(hist) < 2 or not (hist[-2] >= 0 > hist[-1]):
                return False

        if "vol_ratio_above" in cond:
            vol_ratio = indicators.get("vol_ratio")
            if vol_ratio is None or vol_ratio <= cond["vol_ratio_above"]:
                return False

        # ── Boolean signals (from A-share compute_indicators) ──
        if cond.get("macd_bull_divergence"):
            if not indicators.get("macd_bull_divergence"):
                return False

        if cond.get("ma5_turn_up"):
            if not indicators.get("ma5_turn_up"):
                return False

        # ── Confluence: require N or more boolean signals to be true ──
        confluence_min = cond.get("confluence_min")
        if confluence_min:
            count = sum(
                1
                for k in ("macd_golden_cross", "macd_bull_divergence", "ma5_turn_up")
                if indicators.get(k)
            )
            rsi = indicators.get("rsi")
            if rsi is not None and rsi < 35:
                count += 1
            vol_ratio = indicators.get("vol_ratio")
            if vol_ratio is not None and vol_ratio >= 1.5:
                count += 1
            if count < confluence_min:
                return False

        return True

    def _make_alert(
        self,
        plan_id: str,
        plan: dict,
        cond_id: str,
        label: str,
        price: float,
        name: str,
        change: float,
        event_type: str = "",
        shares: int = 0,
        side: str = "sell",
    ) -> dict:
        """Create alert dict compatible with write_alert_events + stealth_dispatch."""
        symbol = plan.get("symbol", "")
        plan_name = plan.get("name", plan_id)

        # Use label's side info if present (e.g., "卖出 100股: 止盈"), otherwise construct from side
        if "买入" in label:
            action_text = (
                f"买入 {shares} 股 @ {price:.2f}" if shares > 0 else f"@ {price:.2f}"
            )
        elif "卖出" in label:
            action_text = (
                f"卖出 {shares} 股 @ {price:.2f}" if shares > 0 else f"@ {price:.2f}"
            )
        elif shares > 0:
            action_text = (
                f"{'买入' if side == 'buy' else '卖出'} {shares} 股 @ {price:.2f}"
            )
        elif event_type == "sl_triggered":
            action_text = f"止损 @ {price:.2f}"
        else:
            action_text = f"@ {price:.2f}"

        return {
            "symbol": symbol,
            "title": f"{name} {label}",
            "message": f"[PLAN] {name} {label} {action_text}",
            "display": f"📋 {plan_name} | {label} | {action_text}",
            "_kind": "trade_plan",
            "_level": 1,  # L1: 交易计划 = 需要立即行动
            "_change_pct": change,
            "_name": name,
            "_stealth": f"{symbol} {label} {action_text}",
            "_plan_id": plan_id,
            "_condition_id": cond_id,
            "_event_type": event_type,
        }

    def _write_plan_event(
        self,
        plan_id: str,
        event_type: str,
        condition_id: str,
        label: str,
        price: float,
        shares: int,
    ):
        """Queue trade_plan_events rows; _save_plans persists them atomically."""
        if not hasattr(self, "_pending_plan_events"):
            self._pending_plan_events = []
        self._pending_plan_events.append(
            (
                int(time.time() * 1000),
                datetime.now().strftime("%Y-%m-%d"),
                plan_id,
                event_type,
                condition_id,
                label,
                price,
                shares,
                f"{label} @ {price:.2f}",
            )
        )


# ══════════════════════════════════════════
# 6b. Watch Drift Tracker
# ══════════════════════════════════════════


class WatchDriftTracker:
    """Tracks cumulative drift from watch_price for stocks and tag indices.

    Notifies when drift crosses tier boundaries (e.g. +/-5%, +/-10%, +/-15%...).
    Each tier fires only once per day (reset at 08:00 with everything else).
    """

    def __init__(self, alert_config_path):
        self._alert_config_path = alert_config_path
        self._notified_tiers = {}  # {symbol_or_tag: set of triggered tier values}
        self._retrace_cooldown: dict[str, float] = {}  # {key: last_fire_ts}
        self._defaults = {"watch_drift_pct": 5, "watch_drift_enabled": True}

    def reset(self):
        """Daily reset at 08:00."""
        self._notified_tiers.clear()
        self._retrace_cooldown.clear()
        if hasattr(self, "_cached_step"):
            del self._cached_step

    def check_stocks(self, quotes):
        """Check individual stock drift from watch_price. Returns list of alert dicts.

        quotes = {symbol: {"price": float, "name": str, ...}}
        """
        from src.sim_trading.db import get_config_connection

        alerts = []
        try:
            conn = get_config_connection()
            rows = conn.execute(
                "SELECT symbol, name, watch_price, tags, star, list_type, hidden "
                "FROM monitor_watchlist WHERE watch_price IS NOT NULL AND watch_price > 0"
            ).fetchall()
            conn.close()
        except Exception as e:
            logger.warning(f"WatchDriftTracker: failed to read monitor_watchlist: {e}")
            return alerts

        for row in rows:
            symbol, name, wp, tags_json, star, list_type, hidden = row
            if hidden:
                continue
            q = quotes.get(symbol)
            if not q or not q.get("price"):
                continue
            price = q["price"]
            drift_pct = (price - wp) / wp * 100
            step = self._get_step(symbol)
            tier = self._calc_tier(drift_pct, step)
            if tier and tier not in self._notified_tiers.get(symbol, set()):
                self._notified_tiers.setdefault(symbol, set()).add(tier)
                self._fill_lower_tiers(symbol, tier, drift_pct, step)
                level = 1 if star else (2 if list_type == "holding" else 3)
                direction = "涨" if drift_pct > 0 else "跌"
                alerts.append(
                    {
                        "symbol": symbol,
                        "title": f"{name} drift",
                        "_kind": "DRIFT",
                        "_level": level,
                        "_change_pct": round(drift_pct, 1),
                        "_name": name,
                        "message": f"{name} 距关注{direction}{abs(drift_pct):.1f}%",
                        "display": f"{name}({symbol}) 距关注价{wp:.2f}{direction}{abs(drift_pct):.1f}%，现价{price:.2f}",
                        "_stealth": f"{name} 距关注{direction}{abs(drift_pct):.1f}%",
                    }
                )
            else:
                level = 1 if star else (2 if list_type == "holding" else 3)
                retrace_alert = self._check_retrace(
                    symbol, drift_pct, step,
                    "涨" if drift_pct > 0 else "跌",
                    name, wp, price, is_index=False, level=level,
                )
                if retrace_alert:
                    alerts.append(retrace_alert)
        return alerts

    def check_indices(self, index_values):
        """Check tag index drift from baseline. index_values = {tag: current_value}.

        Reads tag_meta to get baseline and star/watch status.
        """
        from src.sim_trading.db import get_config_connection

        alerts = []
        try:
            conn = get_config_connection()
            tags = conn.execute(
                "SELECT tag, star, watch, baseline_value FROM tag_meta WHERE watch = 1"
            ).fetchall()
            conn.close()
        except Exception as e:
            logger.warning(f"WatchDriftTracker: failed to read tag_meta: {e}")
            return alerts

        for row in tags:
            tag, star, watch, baseline = row
            val = index_values.get(tag)
            if not val or not baseline:
                continue
            drift_pct = (val - baseline) / baseline * 100
            key = f"tag:{tag}"
            step = self._get_step(key)
            tier = self._calc_tier(drift_pct, step)
            if tier and tier not in self._notified_tiers.get(key, set()):
                self._notified_tiers.setdefault(key, set()).add(tier)
                self._fill_lower_tiers(key, tier, drift_pct, step)
                level = 1 if star else 2
                direction = "涨" if drift_pct > 0 else "跌"
                alerts.append(
                    {
                        "symbol": key,
                        "title": f"{tag}指数 drift",
                        "_kind": "DRIFT",
                        "_level": level,
                        "_change_pct": round(drift_pct, 1),
                        "_name": f"{tag}指数",
                        "message": f"{tag}指数 距创建{direction}{abs(drift_pct):.1f}%",
                        "display": f"{tag}指数 距创建{direction}{abs(drift_pct):.1f}%，当前{val:.1f}",
                        "_stealth": f"{tag}指数 距基线{direction}{abs(drift_pct):.1f}%",
                    }
                )
            else:
                level = 1 if star else 2
                retrace_alert = self._check_retrace(
                    key, drift_pct, step,
                    "涨" if drift_pct > 0 else "跌",
                    f"{tag}指数", baseline, val, is_index=True, level=level,
                )
                if retrace_alert:
                    alerts.append(retrace_alert)
        return alerts

    def _fill_lower_tiers(self, key, tier, drift_pct, step):
        """Auto-fill all lower same-direction tiers when a new tier is crossed."""
        sign = 1 if drift_pct > 0 else -1
        for lower_tier in range(step, abs(tier) + 1, step):
            self._notified_tiers.setdefault(key, set()).add(sign * lower_tier)

    def _get_step(self, key):
        """Get drift step % for a key (stock code or tag:name). Cached after first read."""
        if not hasattr(self, "_cached_step"):
            try:
                from src.sim_trading.db import get_config_connection

                conn = get_config_connection()
                row = conn.execute(
                    "SELECT value FROM monitor_settings WHERE key = ?", ("watch_drift_pct",)
                ).fetchone()
                conn.close()
                self._cached_step = row["value"] if row else self._defaults["watch_drift_pct"]
            except Exception:
                self._cached_step = self._defaults["watch_drift_pct"]
        return self._cached_step

    def _calc_tier(self, drift_pct, step):
        """Return the tier value if drift crosses a new step boundary, else None.

        E.g. step=5: drift 12.3% -> tier = 10 (crossed 10% tier)
             drift 4.9% -> None (hasn't crossed 5% tier yet)
        """
        if step <= 0:
            return None
        tier_num = int(abs(drift_pct) / step)
        if tier_num == 0:
            return None
        tier_value = tier_num * step * (1 if drift_pct > 0 else -1)
        return tier_value

    def _check_retrace(self, key, drift_pct, step, direction, name, wp_or_baseline,
                       price_or_val, is_index=False, level=2):
        if abs(drift_pct) < 0.01:
            return None
        same_dir_tiers = {t for t in self._notified_tiers.get(key, set())
                          if isinstance(t, int) and (t > 0) == (drift_pct >= 0)}
        if not same_dir_tiers:
            return None

        highest = max(same_dir_tiers, key=abs)
        current_tier = self._calc_tier(drift_pct, step)
        retrace_tier_key = f"retrace:{abs(highest)}"

        if (current_tier is None or abs(current_tier) < abs(highest)):
            if retrace_tier_key in self._notified_tiers.get(key, set()):
                return None
            # P1: Add retrace cooldown to prevent rapid oscillation spam
            cooldown_key = f"{key}:{retrace_tier_key}"
            last_fired = self._retrace_cooldown.get(cooldown_key, 0)
            if time.time() - last_fired < 300:  # 5 min
                return None
            self._retrace_cooldown[cooldown_key] = time.time()
            self._notified_tiers.setdefault(key, set()).add(retrace_tier_key)

            retrace_dir = "回落" if drift_pct >= 0 else "反弹"
            if is_index:
                return {
                    "symbol": key,
                    "title": f"{name} drift {retrace_dir}",
                    "_kind": "DRIFT",
                    "_level": level,
                    "_change_pct": round(drift_pct, 1),
                    "_name": name,
                    "message": f"{name} 距基线{direction}{abs(drift_pct):.1f}% ({retrace_dir}自{abs(highest):.0f}%)",
                    "display": f"{name} 距基线{direction}{abs(drift_pct):.1f}%，{retrace_dir}自{abs(highest):.0f}%档，当前{price_or_val:.1f}",
                    "_stealth": f"{name} 距基线{direction}{abs(drift_pct):.1f}% ({retrace_dir}自{abs(highest):.0f}%)",
                }
            else:
                return {
                    "symbol": key,
                    "title": f"{name} drift {retrace_dir}",
                    "_kind": "DRIFT",
                    "_level": level,
                    "_change_pct": round(drift_pct, 1),
                    "_name": name,
                    "message": f"{name} 距关注{direction}{abs(drift_pct):.1f}% ({retrace_dir}自{abs(highest):.0f}%)",
                    "display": f"{name}({key}) 距关注价{wp_or_baseline:.2f}{direction}{abs(drift_pct):.1f}%，{retrace_dir}自{abs(highest):.0f}%档，现价{price_or_val:.2f}",
                    "_stealth": f"{name} 距关注{direction}{abs(drift_pct):.1f}% ({retrace_dir}自{abs(highest):.0f}%)",
                }
        return None


class WatchDriftPatternEngine(PatternEngine):
    """WatchDriftTracker 的 PatternEngine 适配器。

    将 check_stocks() + check_indices() 两个调用合并为单一 check()，
    内部自行完成 sector_daily DB 查询，对外接口统一。
    """

    def __init__(self, alert_config_path):
        self._tracker = WatchDriftTracker(alert_config_path)

    def reset(self):
        self._tracker.reset()

    def check(self, quotes: dict) -> list[dict]:
        alerts = self._tracker.check_stocks(quotes)
        try:
            from src.sim_trading.db import get_connection as _gc
            import datetime as _dt

            today_str = _dt.date.today().strftime("%Y-%m-%d")
            conn = _gc()
            rows = conn.execute(
                "SELECT index_id, index_value FROM sector_daily WHERE date = ?",
                (today_str,),
            ).fetchall()
            conn.close()
            index_values = {r[0]: r[1] for r in rows}
            alerts.extend(self._tracker.check_indices(index_values))
        except Exception:
            pass
        return alerts


# ══════════════════════════════════════════
# 6c. PanicSellEngine — 放量下跌恐慌盘检测
# ══════════════════════════════════════════


class PanicSellEngine(PatternEngine):
    """检测大盘放量下跌 + 个股恐慌盘砸出，触发 L1/L2 告警。

    双重确认：
      大盘: AMO1(两市) > market_amo1_min  AND  (上证跌幅>=drop_pct OR 深证跌幅>=drop_pct)
      个股: AMO1(持仓股) > stock_amo1_min  AND  该股跌幅 >= stock_drop_pct

    市场先行逻辑：大盘条件不满足时不检测个股。
    每日每只股票最多告警1次（cooldown_hours=24）。
    """

    def __init__(self, config_path: Path | None = None):
        # 默认阈值
        self._market_amo1_min = 1.3
        self._market_drop_pct = 1.0
        self._stock_amo1_min = 2.0
        self._stock_drop_pct = 2.0
        self._cooldown_hours = 24

        cfg = _read_monitor_settings([
            "panic_market_amo1_min",
            "panic_market_drop_pct",
            "panic_stock_amo1_min",
            "panic_stock_drop_pct",
            "panic_cooldown_hours",
        ])
        self._market_amo1_min = cfg.get("panic_market_amo1_min", self._market_amo1_min)
        self._market_drop_pct = cfg.get("panic_market_drop_pct", self._market_drop_pct)
        self._stock_amo1_min = cfg.get("panic_stock_amo1_min", self._stock_amo1_min)
        self._stock_drop_pct = cfg.get("panic_stock_drop_pct", self._stock_drop_pct)
        self._cooldown_hours = cfg.get("panic_cooldown_hours", self._cooldown_hours)

        # cooldown: {(date_str, symbol): last_alert_ts_ms}
        self._cooldown: dict[tuple[str, str], int] = {}
        self._watchlist: dict = {}

    def update_config(self, config: dict):
        self._watchlist = config.get("watchlist", {})

    def reset(self):
        # 每日 08:00 reset 时不清理 cooldown（已按 date 区分）
        pass

    def _load_market_amo(self) -> tuple[float | None, float | None, float, float]:
        """读取 market_turnover DB 的 AMO 值和指数涨跌幅。

        Returns: (market_amo1, market_amo2, sh_pct, sz_pct)
        """
        conn = None
        try:
            from src.sim_trading.db import get_connection

            conn = get_connection()
            row = conn.execute(
                "SELECT amo1, amo2, sh_pct, sz_pct FROM market_turnover ORDER BY ts DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None, None, 0.0, 0.0
            return row["amo1"], row["amo2"], row["sh_pct"] or 0.0, row["sz_pct"] or 0.0
        except Exception as e:
            logger.warning(f"PanicSellEngine: failed to read market_turnover from DB: {e}")
            return None, None, 0.0, 0.0
        finally:
            if conn:
                conn.close()

    def _is_cooldown(self, symbol: str) -> bool:
        today = datetime.now().strftime("%Y-%m-%d")
        key = (today, symbol)
        if key in self._cooldown:
            last = self._cooldown[key]
            elapsed_h = (time.time() * 1000 - last) / 3_600_000
            return elapsed_h < self._cooldown_hours
        return False

    def _mark_alerted(self, symbol: str):
        today = datetime.now().strftime("%Y-%m-%d")
        self._cooldown[(today, symbol)] = int(time.time() * 1000)

    def check(self, quotes: dict) -> list[dict]:
        # 1. 读取大盘 AMO 和指数涨跌幅
        market_amo1, _, sh_pct, sz_pct = self._load_market_amo()
        if market_amo1 is None:
            return []

        # 2. 大盘条件检查
        market_dropped = (
            sh_pct <= -self._market_drop_pct or sz_pct <= -self._market_drop_pct
        )
        if not market_dropped:
            return []  # 大盘未放量下跌，跳过

        if market_amo1 < self._market_amo1_min:
            return []  # 市场量能不足，跳过

        # 大盘条件满足，检测个股
        alerts = []
        today_str = datetime.now().strftime("%Y-%m-%d")

        for symbol, q in quotes.items():
            # 只看持仓股（type=holding）或 ★星标自选股
            entry = self._watchlist.get(symbol, {})
            stype = entry.get("type", "")
            is_star = entry.get("star", False)
            if stype != "holding" and not is_star:
                continue

            # cooldown 检查
            if self._is_cooldown(symbol):
                continue

            # 个股 AMO 和涨跌幅
            # quotes 中的数据来自 price_snapshots 表，每个 service 有 amo1
            # merge_data 已从 price_snapshots 读取完整字段
            amo1 = q.get("amo1")
            if amo1 is None:
                # fallback：从 price_snapshots 直接读
                conn = None
                try:
                    from src.sim_trading.db import get_connection

                    conn = get_connection()
                    row = conn.execute(
                        "SELECT amo1 FROM price_snapshots WHERE code = ? ORDER BY ts DESC LIMIT 1",
                        (symbol,),
                    ).fetchone()
                    if row:
                        amo1 = row["amo1"]
                except Exception:
                    pass
                finally:
                    if conn:
                        conn.close()
            if amo1 is None or amo1 < self._stock_amo1_min:
                continue

            pct = q.get("change_pct", 0) or 0
            if pct >= -self._stock_drop_pct:
                continue

            # 触发！
            self._mark_alerted(symbol)
            name = q.get("name", symbol)
            level = 1 if is_star else 2

            alerts.append(
                {
                    "symbol": symbol,
                    "title": f"放量恐慌 {pct:.1f}%",
                    "message": f"[PANIC] {name} AMO1={amo1:.1f}x 跌幅{pct:.1f}%",
                    "display": f"🚨 {name} 放量下跌 | AMO1={amo1:.1f}x | {pct:.1f}% | 大盘AMO={market_amo1:.1f}x",
                    "_kind": "panic_sell",
                    "_level": level,
                    "_change_pct": pct,
                    "_name": name,
                    "_stealth": f"{name} 放量恐慌 跌幅{pct:.1f}%",
                    "amo1": amo1,
                    "market_amo1": market_amo1,
                }
            )

        return alerts


# ══════════════════════════════════════════
# 7. Main loop
# ══════════════════════════════════════════


def run():
    """Main notification daemon loop."""
    # ── Graceful shutdown ──
    running = True

    def _handle_signal(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Startup banner ──
    config = read_monitor_config()
    if not config.get("watchlist"):
        print("ERROR: cannot read watchlist from DB or JSON")
        sys.exit(1)

    settings = config.get("settings", {})
    watchlist = config.get("watchlist", {})
    has_hk = any(is_hk_symbol(s) for s in watchlist)
    num_holdings = sum(
        1
        for e in watchlist.values()
        if e.get("type") == "holding" and e.get("cost") and e.get("shares")
    )

    print("Stock Notifier started (delta mode)")
    print(
        f"  watchlist : {len(watchlist)} stocks ({sum(1 for s in watchlist if is_hk_symbol(s))} HK)"
    )
    print(f"  holdings  : {num_holdings} with cost/shares (P&L tracking)")
    l1 = get_policy(1, settings)
    print(
        f"  trigger   : L1 +/-{l1['trigger_pct']}% / L2 +/-{get_policy(2, settings)['trigger_pct']}% (首次触发)"
    )
    print(
        f"  delta     : L1 +/-{l1['delta_pct']}% / L2 +/-{get_policy(2, settings)['delta_pct']}% (再次触发)"
    )
    print(f"  portfolio : +/-{settings.get('portfolio_delta_pct', 2)}% (组合变化)")
    print("  data src  : trading.db")
    print(f"  Ctrl+C to stop\n")

    # ── Ensure alert_events table exists ──
    from src.sim_trading.db import init_db

    init_db()

    # ── Leader election ──
    from src.tools.monitor_lock import MonitorLock

    lock = MonitorLock()
    if not lock.try_acquire():
        holder = lock.get_lock_holder()
        if holder:
            print(f"[NOTIFIER] 锁被 {holder[0]} (pid={holder[1]}) 持有，退出")
        else:
            print("[NOTIFIER] 锁被未知进程持有，退出")
        sys.exit(1)
    print(f"[NOTIFIER] 成功获取锁 {lock.hostname}")

    # ── State ──
    last_mtime = 0.0
    daily_alerts = 0
    # 如果当前已过 08:00，说明今天的重置已经（或应该已经）执行过，
    # 不需要再次触发每日重置（避免重启进程丢失当日 delta 追踪状态）。
    _now = datetime.now()
    last_alert_date = _now.date() if _now.hour >= 8 else None
    last_checked_count = len(watchlist)
    engine = DeltaAlertEngine(config)
    plan_engine = TradePlanEngine()
    watchdog = DataFreshnessWatchdog(poll_interval=settings.get("poll_interval", 30))
    last_heartbeat = int(time.time())
    last_ai_investment_poll = 0.0
    AI_INVESTMENT_POLL_INTERVAL = 5.0

    # ── 注册通用形态引擎（新增形态只需在此 register 一行）──
    register_pattern_engine(GapFadeEngine(config))
    register_pattern_engine(WatchDriftPatternEngine(None))
    register_pattern_engine(PanicSellEngine(None))
    # A股日线技术指标: 不再全局告警，改为交易计划条件单按需检测
    # (TradePlanEngine._get_ashare_indicators 在 15:05-15:20 窗口内自动拉取)
    sent_open_today = False
    sent_close_today = False
    sent_summary_today = False
    sent_morning_briefing_today = False
    mainline_checked_today = False
    latest_quotes: dict | None = None  # last merged quotes (for close summary)
    latest_hkd_cny_rate: float | None = None
    market_snapshot: dict | None = None  # latest DB market snapshot (for watchdog)
    last_indicator_refresh: float = 0.0  # 指标缓存上次刷新时间戳
    INDICATOR_REFRESH_INTERVAL = 1800  # 30 分钟刷新一次

    while running:
        # Reset daily at 08:00 (before market open)
        # last_alert_date=None on fresh start → always triggers reset once
        today = datetime.now().date()
        now_hour = datetime.now().hour
        if (last_alert_date is None or today != last_alert_date) and now_hour >= 8:
            daily_alerts = 0
            sent_open_today = False
            sent_close_today = False
            sent_summary_today = False
            sent_morning_briefing_today = False
            mainline_checked_today = False
            latest_quotes = None
            latest_hkd_cny_rate = None
            last_alert_date = today
            engine.reset()  # clear delta tracking for new day
            for _eng in _pattern_engines:
                _eng.reset()
            watchdog.reset()  # clear staleness tracking for new day
            check_l2_signals._daily_counts = {}  # reset per-stock L2 cap
            check_l2_signals._seen_today = set()  # reset code+strategy dedup
            # 归档昨日数据 + 清空（新交易日重新开始）
            _archive_and_reset(today)
            # Reload config at day boundary
            fresh_config = read_monitor_config()
            if fresh_config.get("watchlist"):
                config = fresh_config
                watchlist = config.get("watchlist", {})
                settings = config.get("settings", {})
                has_hk = any(is_hk_symbol(s) for s in watchlist)
                engine.config = config
                for _eng in _pattern_engines:
                    _eng.update_config(config)
                watchdog.update_poll_interval(settings.get("poll_interval", 30))

        trading = is_any_market_open(has_hk)
        check_interval = TRADING_CHECK_SEC if trading else NON_TRADING_CHECK_SEC

        # Check DB timestamp
        current_mtime = _get_latest_db_ts()

        if current_mtime > 0 and current_mtime != last_mtime:
            last_mtime = current_mtime

            # Reload config each time (cheap, picks up threshold changes)
            fresh_config = read_monitor_config()
            if fresh_config.get("watchlist"):
                config = fresh_config
                watchlist = config.get("watchlist", {})
                settings = config.get("settings", {})
                has_hk = any(is_hk_symbol(s) for s in watchlist)
                engine.config = config
                for _eng in _pattern_engines:
                    _eng.update_config(config)

            market = _read_market_snapshot_from_db()
            if market is not None:
                market_snapshot = market  # keep for watchdog
                quotes = merge_data(market, config)
                last_checked_count = len(quotes)

                # Keep latest data for close summary (even outside trading check)
                if quotes:
                    latest_quotes = quotes
                    latest_hkd_cny_rate = 0.92  # 固定汇率值

                if trading and quotes:
                    # 集合竞价时段不发送告警（价格不稳定）
                    in_auction = is_in_auction_period()

                    hkd_cny_rate = 0.92  # 固定汇率值
                    all_alerts = (
                        [] if in_auction else engine.check(quotes, hkd_cny_rate)
                    )

                    # ── L2 strategy signals (from daemon) ──
                    l2_alerts = [] if in_auction else check_l2_signals()
                    if l2_alerts:
                        for a in l2_alerts:
                            if "_level" not in a:
                                # _notify=True → L1（高优先级弹窗+声音），否则 L3（仅 web）
                                a["_level"] = 1 if a.get("_notify") else 3
                        l2_web_only = [a for a in l2_alerts if not a.get("_notify")]
                        l2_notify = [a for a in l2_alerts if a.get("_notify")]
                        if l2_web_only:
                            write_alert_events(l2_web_only)
                        # l2_notify 加入 all_alerts（后面统一写入+分发）
                        all_alerts.extend(l2_notify)

                    # ── tick_monitor signals (from tick_monitor daemon) ──
                    tick_alerts_dispatch, tick_web_only = (
                        [] if in_auction else check_tick_monitor_signals()
                    )
                    if tick_web_only:
                        write_alert_events(tick_web_only)
                    # tick alerts 加入 all_alerts 统一写入+分发（不移除）
                    if tick_alerts_dispatch:
                        all_alerts.extend(tick_alerts_dispatch)

                    # ── Trade plan conditions ──
                    plan_alerts = [] if in_auction else plan_engine.check(quotes)
                    if plan_alerts:
                        write_alert_events(plan_alerts)
                        all_alerts.extend(plan_alerts)

                    # ── 指标缓存刷新（盘中每 30 分钟 + 收盘后一次）──
                    ts_now = time.time()
                    if ts_now - last_indicator_refresh > INDICATOR_REFRESH_INTERVAL:
                        try:
                            from src.tools.indicator_alert_engine import (
                                refresh_indicator_cache,
                            )

                            refresh_indicator_cache(
                                watchlist=watchlist, live_quotes=quotes
                            )
                            last_indicator_refresh = ts_now
                        except Exception as _ie:
                            logger.warning(f"indicator_cache refresh failed: {_ie}")

                    # ── 注册表形态引擎（统一驱动，隔离异常）──
                    if not in_auction:
                        for _eng in _pattern_engines:
                            try:
                                _eng_alerts = _eng.check(quotes)
                                if _eng_alerts:
                                    all_alerts.extend(_eng_alerts)
                            except Exception as _e:
                                logger.warning(
                                    f"{type(_eng).__name__} check failed: {_e}"
                                )

                    if all_alerts:
                        print()
                        # 统一写入一次（去重在 write_alert_events 内处理）
                        write_alert_events(all_alerts)
                        # 按级别分流 macOS 通知
                        l1_alerts = [a for a in all_alerts if a.get("_level") == 1]
                        l2_alerts_dispatch = [
                            a
                            for a in all_alerts
                            if a.get("_level") == 2 or a.get("_kind") == "portfolio"
                        ]
                        # L3 = web_only, 已写入 alert_events，不弹窗
                        if l1_alerts:
                            stealth_dispatch(l1_alerts, sound="default")
                        if l2_alerts_dispatch:
                            stealth_dispatch(l2_alerts_dispatch, sound="")
                        daily_alerts += len(l1_alerts) + len(l2_alerts_dispatch)
                        for a in all_alerts:
                            logger.info(f"Alert: {a['title']} - {a['message']}")

                print_status(last_checked_count, daily_alerts, check_interval, trading)
            else:
                print_status(last_checked_count, daily_alerts, check_interval, trading)
        else:
            # No new data -- reuse last checked count for consistent display
            print_status(last_checked_count, daily_alerts, check_interval, trading)

        # ── Data Freshness Watchdog (runs every iteration, not just on mtime change) ──
        wd_alerts = watchdog.check(_get_latest_db_ts(), market_snapshot, has_hk)
        if wd_alerts:
            print()
            write_alert_events(wd_alerts)
            wd_l1 = [a for a in wd_alerts if a.get("_level") == 1]
            wd_l2 = [a for a in wd_alerts if a.get("_level") == 2]
            if wd_l1:
                stealth_dispatch(wd_l1, sound="default")
            if wd_l2:
                stealth_dispatch(wd_l2, sound="")
            daily_alerts += len(wd_l1) + len(wd_l2)
            for a in wd_alerts:
                logger.info(f"Watchdog: {a['message']}")

        # ── Market open/close notifications (time-based, independent of mtime) ──
        sent_open_today, sent_close_today, oc_alerts = check_market_open_close(
            sent_open=sent_open_today,
            sent_close=sent_close_today,
            config=config,
            quotes=latest_quotes,
            hkd_cny_rate=latest_hkd_cny_rate,
        )
        if oc_alerts:
            print()
            stealth_dispatch_open_close(oc_alerts)
            daily_alerts += len(oc_alerts)
            for a in oc_alerts:
                logger.info(f"Alert: {a['title']} - {a['message']}")

        # ── Mainline detection (once per day, after 15:30 when sector_daily has data) ──
        if not mainline_checked_today:
            now_t = datetime.now()
            hhmm = now_t.hour * 100 + now_t.minute
            if now_t.weekday() < 5 and hhmm >= 1530:
                mainline_checked_today = True
                try:
                    ml_alerts = check_mainline_alerts()
                    if ml_alerts:
                        print()
                        write_alert_events(ml_alerts)
                        ml_l1 = [a for a in ml_alerts if a.get("_level") == 1]
                        ml_l2 = [a for a in ml_alerts if a.get("_level") == 2]
                        if ml_l1:
                            stealth_dispatch(ml_l1, sound="default")
                        if ml_l2:
                            stealth_dispatch(ml_l2, sound="")
                        daily_alerts += len(ml_l1) + len(ml_l2)
                        for a in ml_alerts:
                            logger.info(f"Mainline: {a['message']}")
                except Exception as e:
                    logger.error(f"Mainline detection failed: {e}")

                # Compute today's custom index values for historical record
                # Run in background thread — takes ~50min, must not block main loop
                # (blocking would cause daily summary window 16:05-16:15 to be missed)
                def _run_sector_indices():
                    try:
                        from src.tools.sector_index_engine import compute_custom_indices

                        compute_custom_indices()
                        logger.info("sector indices computed for today")
                    except Exception as e:
                        logger.warning(f"sector index computation failed: {e}")

                import threading

                threading.Thread(
                    target=_run_sector_indices, daemon=True, name="sector-indices"
                ).start()

        # ── Morning briefing generation (8:25-8:35 before A-share open) ──
        if not sent_morning_briefing_today:
            now_t = datetime.now()
            hhmm = now_t.hour * 100 + now_t.minute
            if now_t.weekday() < 5 and 825 <= hhmm <= 835:
                sent_morning_briefing_today = True
                logger.info("Triggering morning briefing generation...")
                try:
                    from src.tools.daily_summary_generator import (
                        generate_morning_briefing,
                    )

                    generate_morning_briefing()
                    logger.info("Morning briefing generated")
                except Exception as e:
                    logger.error(f"Morning briefing generation failed: {e}")

        # ── Daily summary generation (16:05-16:15 after HK close) ──
        if not sent_summary_today:
            now_t = datetime.now()
            hhmm = now_t.hour * 100 + now_t.minute
            if now_t.weekday() < 5 and 1605 <= hhmm <= 1615:
                sent_summary_today = True
                logger.info("Triggering daily summary generation...")
                try:
                    from src.tools.daily_summary_generator import generate_daily_summary

                    generate_daily_summary()
                    notify("Daily Report", "Signal digest ready", sound="")
                except Exception as e:
                    logger.error(f"Daily summary generation failed: {e}")

        # ── AI investment events (DB queue → alert_events, notifier-only dispatch) ──
        ts_poll = time.time()
        if ts_poll - last_ai_investment_poll >= AI_INVESTMENT_POLL_INTERVAL:
            last_ai_investment_poll = ts_poll
            try:
                consume_pending_ai_investment_events()
            except Exception as e:
                logger.warning("consume_pending_ai_investment_events failed: %s", e)

        # Heartbeat refresh
        now = int(time.time())
        if now - last_heartbeat >= 30:
            if not lock.refresh_heartbeat():
                print("[NOTIFIER] 锁丢失，退出")
                running = False
                break
            last_heartbeat = now

        # Sleep in small increments for responsive shutdown
        slept = 0.0
        while slept < check_interval and running:
            time.sleep(min(0.5, check_interval - slept))
            slept += 0.5

    # ── Shutdown ──
    print(f"\n\nNotifier stopped. Total alerts today: {daily_alerts}")


# ══════════════════════════════════════════
# AlertClusterer — Kimi 智能告警聚类降噪
# ══════════════════════════════════════════


class AlertClusterer:
    """用 LLM 对告警事件做语义聚类，合并同类告警减少飞书轰炸。

    不替代现有的 (code, message) dedup 和 cooldown 机制，
    而是在 stealth_dispatch 前对已去重的告警做二次聚类。

    使用方式:
        clusterer = AlertClusterer()
        clusters = clusterer.cluster(alerts)
        # clusters 是合并后的告警列表，可直接传给 stealth_dispatch
    """

    def __init__(self, config_path: Path | None = None):
        self._enabled = True
        self._min_count = 3  # 最少告警数才触发聚类（太少没必要）

        cfg = _read_monitor_settings(["cluster_enabled", "cluster_min_count"])
        self._enabled = bool(cfg.get("cluster_enabled", 1 if self._enabled else 0))
        self._min_count = int(cfg.get("cluster_min_count", self._min_count))

    @property
    def enabled(self) -> bool:
        return self._enabled

    def cluster(self, alerts: list[dict]) -> list[dict]:
        """对告警列表做语义聚类，返回合并后的告警列表。

        当告警数 < min_count 时不聚类，直接返回原列表。
        """
        if not self._enabled:
            return alerts
        if len(alerts) < self._min_count:
            return alerts

        try:
            clustered = self._cluster_via_llm(alerts)
            if clustered is None:
                return alerts
            return clustered
        except Exception as e:
            logger.warning(f"AlertClusterer 聚类失败，fallback 到原始告警: {e}")
            return alerts

    def _cluster_via_llm(self, alerts: list[dict]) -> list[dict] | None:
        """调用 LLM 做语义聚类。

        将告警列表发给 LLM，要求合并同类告警并输出聚类结果。
        使用 json_schema 保证输出格式。
        """
        # 构建告警列表文本（包含 _level 供 LLM 判断 L1）
        alert_lines = []
        for i, a in enumerate(alerts):
            symbol = a.get("symbol", "?")
            title = a.get("title", "")
            message = a.get("message", "")
            name = a.get("_name", symbol)
            lvl = a.get("_level", "")
            lvl_tag = f" L{lvl}" if lvl else ""
            alert_lines.append(f"[{i}] {symbol} {name}{lvl_tag} | {title} | {message}")

        alert_text = "\n".join(alert_lines)

        system_msg = {
            "role": "system",
            "content": (
                "你是告警降噪专家。给定一组股票监控告警事件，请将属于同一只股票、"
                "同一类事件（如同一资金行为导致的大单翻转+量价背离）的告警合并为"
                "一条摘要。\n\n"
                "合并规则：\n"
                "1. 同股票 + 语义相关的告警合并为一条（如大单翻转3次+量价背离→主力分歧）\n"
                "2. 不同股票的告警保持独立\n"
                "3. 无法归类的告警保留原文\n"
                "4. 合并后每条告警标题概括核心问题，正文包含原始告警数量\n"
                "5. L1 高优告警（标记为 L1）不得被合并到普通告警中，保持独立发送"
            ),
        }

        user_msg = {
            "role": "user",
            "content": (
                f"请对以下 {len(alerts)} 条告警做语义聚类，合并同类告警：\n\n{alert_text}"
            ),
        }

        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": "alert_clusters",
                "strict": True,
                "schema": {
                    "type": "object",
                    "properties": {
                        "clusters": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "symbol": {"type": "string"},
                                    "title": {
                                        "type": "string",
                                        "description": "合并后的告警标题",
                                    },
                                    "message": {
                                        "type": "string",
                                        "description": "合并后的告警正文",
                                    },
                                    "original_count": {
                                        "type": "integer",
                                        "description": "包含的原始告警数",
                                    },
                                    "original_indices": {
                                        "type": "array",
                                        "items": {"type": "integer"},
                                        "description": "原始告警的索引列表",
                                    },
                                    "is_l1": {
                                        "type": "boolean",
                                        "description": "是否包含L1高优告警",
                                    },
                                },
                                "required": ["symbol", "title", "message",
                                             "original_count", "is_l1"],
                                "additionalProperties": False,
                            },
                        }
                    },
                    "required": ["clusters"],
                    "additionalProperties": False,
                },
            },
        }

        try:
            from src.utils.llm_clients import LLMClientFactory
            import json as _json

            client = LLMClientFactory.create_client()
            result = client.get_completion(
                [system_msg, user_msg],
                response_format=response_format,
            )
            if not result:
                return None

            parsed = _json.loads(result)
            raw_clusters = parsed.get("clusters", [])
            if not raw_clusters:
                return alerts

            # 构建合并后的告警列表
            merged_alerts = []
            seen_indices = set()

            for c in raw_clusters:
                indices = c.get("original_indices", [])
                # 过滤越界索引
                valid_indices = [i for i in indices if 0 <= i < len(alerts)]
                seen_indices.update(valid_indices)

                # 从原始告警聚合关键字段
                orig_alerts = [alerts[i] for i in valid_indices]
                cluster_levels = {a.get("_level") for a in orig_alerts}
                effective_level = 1 if 1 in cluster_levels else (min(cluster_levels) if cluster_levels else 2)
                max_change = max((a.get("_change_pct", 0) for a in orig_alerts), default=0)
                first = orig_alerts[0] if orig_alerts else {}

                merged_alerts.append({
                    "symbol": c["symbol"],
                    "title": f"[聚合] {c['title']}",
                    "message": c["message"],
                    "_kind": first.get("_kind", "alert_cluster"),
                    "_level": effective_level,
                    "_change_pct": max_change,
                    "_price": first.get("_price", ""),
                    "_name": first.get("_name", c["symbol"]),
                    "_stealth": c["message"],
                    "_cluster_size": c.get("original_count", 0),
                })

            # 保留未被聚类的告警
            for i, a in enumerate(alerts):
                if i not in seen_indices:
                    merged_alerts.append(a)

            logger.info(
                f"AlertClusterer: {len(alerts)} → {len(merged_alerts)} "
                f"(合并 {len(alerts) - len(merged_alerts)} 条)"
            )
            return merged_alerts

        except Exception as e:
            logger.error(f"AlertClusterer LLM 调用失败: {e}")
            return None


if __name__ == "__main__":
    lock_path = f"/tmp/stock_notifier.{os.getuid()}.lock"
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"另一 stock_notifier 已在运行，退出。锁文件: {lock_path}")
        os.close(lock_fd)
        sys.exit(1)
    try:
        run()
    finally:
        os.close(lock_fd)
