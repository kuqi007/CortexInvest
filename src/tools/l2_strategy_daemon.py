#!/usr/bin/env python3
"""
L2 Strategy Daemon — 独立进程，3s 轮询检测 L2 策略信号

读取 config.db（持仓列表 + L2 策略参数），
通过 Futu OpenD 获取实时 L2 数据，检测信号后写入 trading.db。

Notifier 消费 trading.db 信号，转换为 macOS 通知 + trading.db:alert_events。

设计原则:
  - OpenD 不可用时静默退出（Futu 可选）
  - 信号原子写入（tmp → rename）
  - 交易时段 3s，非交易时段 60s

Usage:
    poetry run python src/tools/l2_strategy_daemon.py
"""

import fcntl
import json
import os
import signal
import sys
import time
from datetime import datetime
from pathlib import Path

# ── Project root & import path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.l2_strategy_engine import L2StrategyEngine
from src.tools.stock_notifier import is_any_market_open
from src.tools.stock_monitor import is_hk_symbol
from src.utils.logging_config import setup_logger
from src.sim_trading.db import (
    get_config_connection,
    get_connection,
    init_config_db,
    init_trading_db,
)
from src.sim_trading.l2_signal_direction import infer_l2_signal_direction
from src.sim_trading.signal_rules import load_signal_rules

logger = setup_logger("l2_daemon")

from src.utils.config_reader import read_monitor_config

# ── Poll intervals ──
TRADING_POLL_SEC = 3
NON_TRADING_POLL_SEC = 60

MAX_SIGNALS = 200


def load_configs() -> tuple[dict, dict]:
    """Load monitor config and L2 strategy config from config.db."""
    monitor = read_monitor_config()
    if not monitor.get("watchlist"):
        logger.error("Cannot read watchlist from DB")
        sys.exit(1)

    l2_config = load_l2_strategy_config()

    return monitor, l2_config


def load_l2_strategy_config() -> dict:
    """Load L2 strategy config rows from config.db."""
    conn = None
    try:
        init_config_db()
        conn = get_config_connection()
        rows = conn.execute(
            """
            SELECT strategy, enabled, config_json
            FROM l2_strategy_config
            ORDER BY strategy
            """
        ).fetchall()
    except Exception as e:
        logger.warning(f"Cannot read l2_strategy_config from DB, using defaults: {e}")
        return {"enabled": True, "strategies": {}, "max_signals": MAX_SIGNALS}
    finally:
        if conn is not None:
            conn.close()

    strategies = {}
    for row in rows:
        try:
            config = json.loads(row["config_json"] or "{}")
        except json.JSONDecodeError:
            logger.warning(f"Invalid l2_strategy_config row: {row['strategy']}")
            config = {}
        strategies[row["strategy"]] = {
            "enabled": bool(row["enabled"]),
            **config,
        }
    return {"enabled": True, "strategies": strategies, "max_signals": MAX_SIGNALS}


def load_realtime_rules() -> dict:
    """Load realtime signal rules from config.db."""
    return load_signal_rules()


def write_signals(
    new_signals: list[dict],
    session_snapshot: dict | None = None,
    indicators: dict | None = None,
):
    """Buffered write: accumulate signals, flush to disk every 30s or 5 signals."""
    write_signals._buffer.extend(new_signals or [])
    if session_snapshot:
        write_signals._session = session_snapshot
    if indicators:
        write_signals._indicators = indicators

    now = time.time()
    buf_len = len(write_signals._buffer)
    elapsed = now - write_signals._last_flush

    # Flush when: 5+ signals buffered OR 30s elapsed
    if buf_len >= 5 or elapsed >= 30:
        _flush_signals_to_disk()


def _flush_signals_to_disk():
    """Flush buffered L2 signals/session state to trading.db."""
    buf = list(write_signals._buffer)

    session = write_signals._session or {}
    indicators = write_signals._indicators or {}
    conn = None
    try:
        init_trading_db()
        conn = get_connection()
        conn.execute("BEGIN IMMEDIATE")
        for signal in buf:
            ts = int(signal.get("ts") or time.time() * 1000)
            dt = datetime.fromtimestamp(ts / 1000)
            conn.execute(
                """
                INSERT OR IGNORE INTO signals
                    (ts, date, time, strategy, code, direction, notify, detail, display)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    dt.strftime("%Y-%m-%d"),
                    signal.get("time") or dt.strftime("%H:%M:%S"),
                    signal.get("strategy", ""),
                    signal.get("code", ""),
                    signal.get("direction") or infer_l2_signal_direction(signal),
                    1 if signal.get("notify") else 0,
                    json.dumps(signal.get("detail", {}), ensure_ascii=False),
                    signal.get("display", ""),
                ),
            )

        snapshot_ts = int(time.time() * 1000)
        snapshot_dt = datetime.fromtimestamp(snapshot_ts / 1000)
        for code, ctx in session.items():
            if not isinstance(ctx, dict):
                continue
            payload = dict(ctx)
            if code in indicators:
                payload["indicators"] = indicators[code]
            conn.execute(
                """
                INSERT OR IGNORE INTO session_snapshots
                    (ts, date, time, code, session_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot_ts,
                    snapshot_dt.strftime("%Y-%m-%d"),
                    snapshot_dt.strftime("%H:%M:%S"),
                    code,
                    json.dumps(payload, ensure_ascii=False),
                ),
            )
        conn.commit()
        write_signals._buffer = []
        write_signals._last_flush = time.time()
    except Exception as e:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        logger.warning(f"Flush L2 signals to DB failed: {e}")
    finally:
        if conn is not None:
            conn.close()


# Buffer state (module-level, attached to function for testability)
write_signals._buffer: list[dict] = []
write_signals._session: dict = {}
write_signals._indicators: dict = {}
write_signals._last_flush: float = time.time()


def run():
    """Main daemon loop"""
    running = True

    def _handle_signal(_signum, _frame):
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # ── Load configs ──
    monitor_config, l2_config = load_configs()

    if not l2_config.get("enabled", True):
        print("L2 strategy engine is disabled in config. Exiting.")
        return

    watchlist = monitor_config.get("watchlist", {})
    has_hk = any(is_hk_symbol(s) for s in watchlist)
    hk_holdings = [
        code
        for code, info in watchlist.items()
        if code.startswith("HK")
        and info.get("type") == "holding"
        and not info.get("hidden", False)
    ]

    if not hk_holdings:
        print("No HK holdings found in watchlist. L2 daemon has nothing to monitor.")
        return

    # ── Initialize engine ──
    engine = L2StrategyEngine(l2_config, watchlist)

    # ── Leader election ──
    from src.tools.monitor_lock import MonitorLock

    lock = MonitorLock()
    if not lock.try_acquire():
        holder = lock.get_lock_holder()
        if holder:
            print(f"[L2] 锁被 {holder[0]} (pid={holder[1]}) 持有，退出")
        else:
            print("[L2] 锁被未知进程持有，退出")
        return
    print(f"[L2] 成功获取锁 {lock.hostname}")

    # ── Initialize signal archiver (sim trading data collection) ──
    try:
        from src.sim_trading.signal_archiver import SignalArchiver

        archiver = SignalArchiver()
        archiver_enabled = True
    except Exception as e:
        archiver = None
        archiver_enabled = False
        logger.warning(f"Signal archiver not available: {e}")

    # ── Initialize real-time sim engine (v2: pass daily_tracker + futu config) ──
    try:
        from src.sim_trading.realtime_engine import RealtimeSimEngine

        rt_rules = load_realtime_rules()
        daily_tracker = getattr(engine, "_daily_indicators", None)
        futu_cfg = rt_rules.get("futu_trade", {})
        rt_engine = RealtimeSimEngine(
            rt_rules,
            daily_tracker=daily_tracker,
            futu_trade=futu_cfg.get("enabled", False),
            futu_host=futu_cfg.get("host", "127.0.0.1"),
            futu_port=futu_cfg.get("port", 11111),
        )
        rt_enabled = True
    except Exception as e:
        rt_engine = None
        rt_enabled = False
        logger.warning(f"RT sim engine not available: {e}")

    # ── Startup banner ──
    print("L2 Strategy Daemon started")
    print(f"  HK holdings : {len(hk_holdings)} stocks")
    strategies = l2_config.get("strategies", {})
    enabled = [k for k, v in strategies.items() if v.get("enabled", True)]
    print(f"  strategies  : {', '.join(enabled)}")
    print("  signals sink: trading.db")
    print(f"  archiver    : {'enabled' if archiver_enabled else 'disabled'}")
    print(f"  sim engine  : {'enabled' if rt_enabled else 'disabled'}")
    if rt_enabled and rt_engine:
        futu_status = "ON" if rt_engine._futu_enabled else "OFF"
        print(f"  futu trade  : {futu_status}")
    print(f"  Ctrl+C to stop\n")

    # ── State ──
    total_signals = 0
    last_date = datetime.now().date()
    prev_save_date = None  # Track which date we last saved for daily_pnl
    last_heartbeat = int(time.time())

    while running:
        # Daily reset
        today = datetime.now().date()
        if today != last_date:
            _flush_signals_to_disk()  # Flush buffer before daily reset
            # Save previous day's equity to daily_pnl before resetting
            if rt_enabled and rt_engine and prev_save_date is not None:
                try:
                    rt_engine.save_daily_pnl(prev_save_date.strftime("%Y-%m-%d"))
                except Exception as e:
                    logger.warning(f"save_daily_pnl error: {e}")
            prev_save_date = last_date
            last_date = today
            total_signals = 0
            engine.reset_daily()
            if rt_enabled and rt_engine:
                rt_engine.daily_reset()
            # Reload config
            monitor_config, l2_config = load_configs()
            watchlist = monitor_config.get("watchlist", {})
            engine = L2StrategyEngine(l2_config, watchlist)
            logger.info("Daily reset complete, config reloaded")

        trading = is_any_market_open(has_hk)
        poll_interval = TRADING_POLL_SEC if trading else NON_TRADING_POLL_SEC

        if trading:
            signals, session = engine.poll_once()
            if signals or session:
                daily_tracker = getattr(engine, "_daily_indicators", None)
                ind_snapshot = (
                    daily_tracker.snapshot_indicators() if daily_tracker else {}
                )
                write_signals(signals, session, ind_snapshot)
                total_signals += len(signals)
                for s in signals:
                    logger.info(f"Signal: [{s['strategy']}] {s['display']}")

            # Archive signals + price snapshots + session context to SQLite
            if archiver_enabled and archiver:
                for _arch_fn in (
                    archiver.archive_signals,
                    archiver.sample_prices,
                    archiver.snapshot_session,
                ):
                    try:
                        _arch_fn()
                    except Exception as _arch_err:
                        logger.debug(f"Archiver {_arch_fn.__name__} error: {_arch_err}")

            # Real-time sim engine tick (after archiver so signals are in DB)
            if rt_enabled and rt_engine:
                try:
                    rt_engine.tick()
                except Exception as e:
                    logger.warning(f"RT sim engine tick failed: {e}")

            # Status line
            rt_pos = (
                len(rt_engine._pos_mgr.positions) if rt_enabled and rt_engine else 0
            )
            now = datetime.now().strftime("%H:%M:%S")
            print(
                f"\r\033[K[{now}] L2 daemon | signals: {total_signals} | sim: {rt_pos} pos | next: {poll_interval}s",
                end="",
                flush=True,
            )
        else:
            now = datetime.now().strftime("%H:%M:%S")
            print(
                f"\r\033[K[{now}] L2 daemon | non-trading | signals today: {total_signals} | next: {poll_interval}s",
                end="",
                flush=True,
            )

        # Heartbeat refresh
        now = int(time.time())
        if now - last_heartbeat >= 30:
            if not lock.refresh_heartbeat():
                print("[L2] 锁丢失，退出")
                running = False
                break
            last_heartbeat = now

        # Sleep in small increments for responsive shutdown
        slept = 0.0
        while slept < poll_interval and running:
            time.sleep(min(0.5, poll_interval - slept))
            slept += 0.5

    # ── Shutdown ──
    _flush_signals_to_disk()
    engine.close()
    print(f"\n\nL2 Strategy Daemon stopped. Total signals today: {total_signals}")


if __name__ == "__main__":
    (PROJECT_ROOT / "run").mkdir(parents=True, exist_ok=True)
    lock_path = str(PROJECT_ROOT / "run" / "l2_strategy_daemon.lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"另一 l2_strategy_daemon 已在运行，退出。锁文件: {lock_path}")
        os.close(lock_fd)
        sys.exit(0)
    try:
        run()
    finally:
        os.close(lock_fd)
