#!/usr/bin/env python3
"""
Tick Monitor — 短线盯盘 Daemon

基于 Futu OpenD get_rt_ticker() 逐笔成交数据，监控指定股票的
买入/卖出价格触发条件，通过飞书通知提醒。

设计原则:
  - 轻量级: 只监控 2-3 只短线股票，3-5 秒轮询
  - 独立进程: 不依赖 l2_strategy_engine，直接调用 Futu API
  - 复用 feishu_send() 通知通道
  - 配置从 trading.db trade_plans 中 scope="tick_monitor" 的计划读取

配置格式 (trade_plans.orders_json):
  "HK03296_tick": {
    "name": "华勤技术短线盯盘",
    "symbol": "HK03296",
    "status": "active",
    "scope": "tick_monitor",
    "created_at": "2026-04-24",
    "orders": [
      {
        "id": "buy_trigger",
        "side": "buy",
        "op": "<=",
        "price": 85,
        "shares": 100,
        "label": "回踩85买入",
        "triggered": false
      },
      {
        "id": "sell_trigger",
        "side": "sell",
        "op": ">=",
        "price": 95,
        "shares": 100,
        "label": "反弹95卖出",
        "triggered": false
      }
    ]
  }

触发规则:
  - buy + op="<=" → 最新 tick price <= trigger price → 触发
  - buy + op=">=" → 最新 tick price >= trigger price → 触发
  - sell + op="<=" → 最新 tick price <= trigger price → 触发
  - sell + op=">=" → 最新 tick price >= trigger price → 触发

"""

import json
import os
import signal
import socket
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.stock_notifier import is_any_market_open
from src.sim_trading.db import get_connection, init_trading_db
from src.utils.futu_codes import to_futu_code
from src.utils.logging_config import setup_logger

logger = setup_logger("tick_monitor")

# ── 生产者侧冷却 (防止同一 order 频繁重复写入) ──
_order_cooldown: dict[str, float] = {}  # key: "plan_id.order_id", value: last trigger time

# DB table SQL for tick_monitor_events
_TICK_EVENTS_TABLE_SQL = (
    "CREATE TABLE IF NOT EXISTS tick_monitor_events ("
    "    id INTEGER PRIMARY KEY AUTOINCREMENT,"
    "    ts INTEGER NOT NULL,"
    "    date TEXT NOT NULL,"
    "    time TEXT NOT NULL,"
    "    symbol TEXT NOT NULL,"
    "    plan_name TEXT NOT NULL,"
    "    order_id TEXT NOT NULL,"
    "    side TEXT NOT NULL,"
    "    op TEXT NOT NULL DEFAULT '<=',"
    "    label TEXT,"
    "    trigger_price REAL NOT NULL,"
    "    tick_price REAL NOT NULL,"
    "    tick_time TEXT NOT NULL,"
    "    direction TEXT,"
    "    volume REAL,"
    "    title TEXT,"
    "    message TEXT,"
    "    level INTEGER DEFAULT 1,"
    "    dispatched INTEGER DEFAULT 0,"
    "    UNIQUE(ts, symbol, order_id)"
    ")"
)

_TICK_EVENTS_INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_tick_monitor_dispatched "
    "ON tick_monitor_events(dispatched, ts)"
)

OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
RECONNECT_COOLDOWN = 60
POLL_INTERVAL = 5  # 秒
BACKOFF_AFTER_FAIL = 30  # 连续失败后的退避间隔
MAX_CONSECUTIVE_FAIL = 3  # 超过此次连续失败后进入退避


# ══════════════════════════════════════════
# Futu OpenD 连接管理
# ══════════════════════════════════════════


class FutuConnection:
    """轻量级 Futu OpenD 连接，懒连接 + 自动重连 + 失效检测"""

    def __init__(self, host: str = OPEND_HOST, port: int = OPEND_PORT):
        self._host = host
        self._port = port
        self._ctx = None
        self._last_fail_time = 0
        self._consecutive_fail = 0

    def _is_port_open(self) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((self._host, self._port)) == 0
        sock.close()
        return result

    def connect(self) -> bool:
        if self._ctx is not None:
            return True
        if time.time() - self._last_fail_time < RECONNECT_COOLDOWN:
            return False
        if not self._is_port_open():
            self._last_fail_time = time.time()
            logger.debug("OpenD not running, tick monitor skipped")
            return False
        try:
            from futu import OpenQuoteContext

            self._ctx = OpenQuoteContext(host=self._host, port=self._port)
            self._consecutive_fail = 0
            logger.info("Tick Monitor connected to OpenD")
            return True
        except Exception as e:
            self._last_fail_time = time.time()
            logger.warning(f"Tick Monitor connection failed: {e}")
            return False

    def close(self):
        if self._ctx:
            try:
                self._ctx.close()
            except Exception:
                pass
            self._ctx = None

    def get_latest_tick(self, code: str) -> dict | None:
        """获取某只股票的最新逐笔成交数据，返回最新一笔 tick"""
        if not self.connect():
            return None

        from futu import RET_OK

        futu_code = to_futu_code(code)
        try:
            ret, data = self._ctx.get_rt_ticker(futu_code, num=10)
            if ret != RET_OK or data.empty:
                self._consecutive_fail += 1
                if self._consecutive_fail >= MAX_CONSECUTIVE_FAIL:
                    logger.warning(
                        f"get_rt_ticker failed {self._consecutive_fail} times, "
                        "forcing reconnect"
                    )
                    self.close()
                return None

            self._consecutive_fail = 0
            # 取最新一笔
            latest = data.iloc[0]
            return {
                "sequence": int(latest.get("sequence", 0)),
                "price": float(latest.get("price", 0)),
                "volume": int(latest.get("volume", 0)),
                "turnover": float(latest.get("turnover", 0)),
                "direction": str(latest.get("ticker_direction", "")),
                "time": str(latest.get("time", "")),
            }
        except Exception as e:
            logger.debug(f"get_rt_ticker({code}) error: {e}")
            self.close()  # 强制下次重连
            return None


# ══════════════════════════════════════════
# 配置加载
# ══════════════════════════════════════════

_plans_cache: dict[str, dict] | None = None


def load_tick_monitor_plans() -> dict[str, dict]:
    """从 trading.db 加载 scope='tick_monitor' 且 active 的计划。"""
    global _plans_cache
    conn = None
    try:
        init_trading_db()
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, name, symbol, status, scope, created_at, orders_json
            FROM trade_plans
            WHERE scope = 'tick_monitor' AND status = 'active'
            ORDER BY id
            """
        ).fetchall()
    except Exception as e:
        logger.warning(f"Failed to load tick monitor plans from trading.db: {e}")
        return _plans_cache or {}
    finally:
        if conn is not None:
            conn.close()

    tick_plans = {}
    for row in rows:
        try:
            orders = json.loads(row["orders_json"] or "[]")
        except json.JSONDecodeError:
            logger.warning(f"Invalid orders_json for tick monitor plan {row['id']}")
            continue
        tick_plans[row["id"]] = {
            "name": row["name"],
            "symbol": row["symbol"],
            "status": row["status"],
            "scope": row["scope"],
            "created_at": row["created_at"],
            "orders": orders if isinstance(orders, list) else [],
        }

    _plans_cache = tick_plans
    return tick_plans


# ══════════════════════════════════════════
# ══════════════════════════════════════════
# 触发检测
# ══════════════════════════════════════════


def check_trigger(order: dict, tick_price: float) -> bool:
    """检查订单是否被触发"""
    op = order.get("op", "<=")
    price = order.get("price")
    if price is None:
        return False

    if op == "<=":
        return tick_price <= price
    elif op == ">=":
        return tick_price >= price
    elif op == "<":
        return tick_price < price
    elif op == ">":
        return tick_price > price
    elif op == "==":
        return abs(tick_price - price) < 0.001
    return False


# ══════════════════════════════════════════
# 信号写入 (stock_notifier 统一分发)
# ══════════════════════════════════════════


def _ensure_tick_events_table():
    """确保 tick_monitor_events 表和索引存在。"""
    try:
        from src.sim_trading.db import get_connection
        conn = get_connection()
        # Create table (ignore error if already exists)
        conn.executescript(_TICK_EVENTS_TABLE_SQL)
        # Add op column if missing (for existing tables before this schema update)
        try:
            conn.execute("ALTER TABLE tick_monitor_events ADD COLUMN op TEXT NOT NULL DEFAULT '<='")
        except Exception:
            pass  # column already exists
        # Create index (ignore error if already exists)
        conn.executescript(_TICK_EVENTS_INDEX_SQL)
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to create tick_monitor_events table: {e}")


def _write_signals(alerts: list[dict]) -> bool:
    """将触发信号直接写入 trading.db:tick_monitor_events，供 stock_notifier 消费。"""
    if not alerts:
        return True
    conn = None
    try:
        from src.sim_trading.db import get_connection
        _ensure_tick_events_table()

        conn = get_connection()
        ts_base = int(time.time() * 1000)
        today = datetime.now().strftime("%Y-%m-%d")
        t = datetime.now().strftime("%H:%M:%S")

        rows = []
        for i, alert in enumerate(alerts):
            rows.append((
                ts_base + i,
                today,
                t,
                alert.get("symbol", ""),
                alert.get("plan_name", ""),
                alert.get("order_id", ""),
                alert.get("side", "buy"),
                alert.get("op", "<="),
                alert.get("label", ""),
                alert.get("trigger_price", 0),
                alert.get("tick_price", 0),
                alert.get("time", t),
                alert.get("direction", ""),
                alert.get("volume", 0),
                alert.get("title", ""),
                alert.get("message", ""),
                alert.get("_level", 1),
            ))

        conn.executemany(
            "INSERT OR IGNORE INTO tick_monitor_events "
            "(ts, date, time, symbol, plan_name, order_id, side, op, label, trigger_price, tick_price, tick_time, direction, volume, title, message, level) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
        logger.debug(f"Written {len(alerts)} signals to tick_monitor_events DB")
        return True
    except Exception as e:
        logger.warning(f"Failed to write tick_monitor_events: {e}")
        return False
    finally:
        if conn:
            conn.close()


def get_pending_tick_signals() -> tuple[list[dict], list[dict]]:
    """从 trading.db 读取未处理的 tick_monitor 信号，返回并标记已分发。

    Returns:
        (alerts_to_dispatch, alerts_web_only): 按 level 分流
    """
    try:
        from src.sim_trading.db import get_connection
        conn = get_connection()

        # BEGIN IMMEDIATE 保证 SELECT→UPDATE 原子性
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, ts, symbol, plan_name, order_id, side, op, label, trigger_price, "
            "tick_price, tick_time, direction, volume, title, message, level "
            "FROM tick_monitor_events WHERE dispatched = 0 ORDER BY ts"
        ).fetchall()

        if not rows:
            conn.commit()
            conn.close()
            return [], []

        # 标记为已分发
        ids = [r[0] for r in rows]
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"UPDATE tick_monitor_events SET dispatched = 1 WHERE id IN ({placeholders})", ids)
        conn.commit()
        conn.close()

        alerts_to_dispatch = []
        alerts_web_only = []

        for r in rows:
            (id_, ts, symbol, plan_name, order_id, side, op, label,
             trigger_price, tick_price, tick_time, direction,
             volume, title, message, level) = r

            side_str = "买入" if side == "buy" else "卖出"
            op_str = "≤" if op == "<=" else "≥" if op == ">=" else op

            stealth = f"{symbol} {side_str}触发 {label} {op_str}{trigger_price} → {tick_price}"
            display = f"🎯 {plan_name} | {label} | {side_str}价{op_str}{trigger_price} 现价{tick_price}"

            alert = {
                "symbol": symbol,
                "side": side,
                "title": title or f"🎯 短线盯盘触发: {plan_name}",
                "message": message,
                "display": display,
                "_kind": "tick_monitor",
                "_level": level,
                "_change_pct": 0.0,
                "_name": plan_name,
                "_stealth": stealth,
                "_price": tick_price,
                "_tick_time": tick_time,
                "_direction": direction,
                "_volume": volume,
                "_trigger_price": trigger_price,
                "_tick_price": tick_price,
            }

            if level == 1:
                alerts_to_dispatch.append(alert)
            else:
                alerts_web_only.append(alert)

        return alerts_to_dispatch, alerts_web_only

    except Exception as e:
        logger.warning(f"Failed to get_pending_tick_signals: {e}")
        return [], []


def send_tick_notification(
    plan_name: str,
    symbol: str,
    order: dict,
    tick: dict,
) -> bool:
    """将逐笔触发通知写入 trading.db:tick_monitor_events，由 stock_notifier 统一分发。"""
    side = order.get("side", "buy")
    op = order.get("op", "<=")
    trigger_price = order.get("price", 0)
    label = order.get("label", "")
    tick_price = tick.get("price", 0)
    order_id = order.get("id", "")

    side_str = "买入" if side == "buy" else "卖出"
    op_str = "≤" if op == "<=" else "≥" if op == ">=" else op

    # 构造通知文本
    msg = (
        f"{side_str}信号 | {symbol}\n"
        f"触发条件: 价格 {op_str} {trigger_price}\n"
        f"当前价格: **{tick_price}**\n"
        f"方向: {tick.get('direction', '')} | 成交量: {tick.get('volume', 0)}\n"
        f"时间: {tick.get('time', datetime.now().strftime('%H:%M:%S'))}"
    )

    # tick_monitor 的信号统一经由 stock_notifier 的 stealth_dispatch 分发
    # _kind="tick_monitor" 标识来源，_level=1 表示高优先级（需弹窗+声音）
    alert = {
        "symbol": symbol,
        "plan_name": plan_name,
        "order_id": order_id,
        "side": side,
        "op": op,
        "label": label,
        "trigger_price": trigger_price,
        "tick_price": tick_price,
        "time": tick.get("time", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "direction": tick.get("direction", ""),
        "volume": tick.get("volume", 0),
        "title": f"🎯 短线盯盘触发: {plan_name}",
        "message": msg,
        "_kind": "tick_monitor",
        "_level": 1,  # L1: 高优先级弹窗+声音
        "_change_pct": 0.0,
        "_name": plan_name,
        "_stealth": f"{symbol} {side_str}触发 {label} {op_str}{trigger_price} → {tick_price}",
    }

    return _write_signals([alert])


# ══════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════


def _sigterm_handler(signum, frame):
    logger.info("Tick Monitor received SIGTERM, exiting")
    sys.exit(0)


def _get_latest_price(symbol: str) -> dict | None:
    """从 price_snapshots 读取最新行情（DB 是权威数据源）"""
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        row = conn.execute(
            "SELECT price, change_pct FROM price_snapshots "
            "WHERE code = ? AND ts = (SELECT MAX(ts) FROM price_snapshots WHERE code = ?)",
            (symbol, symbol),
        ).fetchone()
        conn.close()
        if row and row[0]:
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            return {
                "price": float(row[0]),
                "change_pct": float(row[1]) if row[1] else 0.0,
                "time": now,
                "direction": "",
                "volume": 0,
            }
    except Exception as e:
        logger.debug(f"_get_latest_price({symbol}) failed: {e}")
    return None


def run_tick_monitor(poll_interval: int = POLL_INTERVAL):
    """Tick Monitor 主循环"""
    logger.info("Tick Monitor 启动")
    signal.signal(signal.SIGTERM, _sigterm_handler)

    fail_count = 0

    try:
        while True:
            # 交易时段检查
            if not is_any_market_open(has_hk=True):
                logger.debug("Market closed, sleeping 60s")
                time.sleep(60)
                continue

            plans = load_tick_monitor_plans()
            if not plans:
                time.sleep(poll_interval)
                continue

            now = time.time()
            any_tick = False

            for plan_id, plan in plans.items():
                symbol = (plan.get("symbol") or "").strip().upper()
                plan_name = plan.get("name", plan_id)
                orders = plan.get("orders", [])

                if not symbol or not orders:
                    continue

                # 从 DB 读取最新价格
                tick = _get_latest_price(symbol)
                if tick is None:
                    continue

                any_tick = True
                tick_price = tick.get("price", 0)

                for order in orders:
                    order_id = order.get("id", "")
                    if not order_id:
                        continue

                    if check_trigger(order, tick_price):
                        # 生产者侧 60s 冷却，防止同一 order 频繁重复写入
                        order_key = f"{plan_id}.{order_id}"
                        now = time.time()
                        last = _order_cooldown.get(order_key, 0)
                        if now - last < 60:
                            logger.debug(f"Cooldown active for {order_key}, skipping")
                            continue
                        _order_cooldown[order_key] = now

                        logger.info(
                            f"🎯 TRIGGERED: {plan_name} {order_id} "
                            f"price={tick_price} op={order.get('op')} target={order.get('price')}"
                        )

                        # 发送通知
                        success = send_tick_notification(plan_name, symbol, order, tick)
                        if success:
                            logger.info(f"Notification sent for {plan_id}.{order_id}")
                        else:
                            logger.warning(f"Notification failed for {plan_id}.{order_id}")

            # 动态退避：如果所有 tick 都失败，延长睡眠
            if not any_tick and plans:
                fail_count += 1
                if fail_count >= MAX_CONSECUTIVE_FAIL:
                    sleep_time = min(BACKOFF_AFTER_FAIL, poll_interval * 6)
                    logger.warning(f"All ticks failed {fail_count} times, backing off to {sleep_time}s")
                    time.sleep(sleep_time)
                    continue
            else:
                fail_count = 0

            time.sleep(poll_interval)

    except KeyboardInterrupt:
        logger.info("Tick Monitor 收到中断信号，退出")
    finally:
        logger.info("Tick Monitor 已停止")


# ══════════════════════════════════════════
# CLI
# ══════════════════════════════════════════


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Tick Monitor — 短线盯盘 Daemon")
    parser.add_argument("--interval", type=int, default=POLL_INTERVAL, help="轮询间隔秒数 (默认 5)")
    parser.add_argument("--dry-run", action="store_true", help="干跑模式: 不发送通知，只打印日志")
    args = parser.parse_args()

    poll_interval = args.interval

    if args.dry_run:
        logger.info("🧪 干跑模式: 通知将被打印但不发送")
        # TODO: 干跑模式下 mock feishu_send

    # Daemon singleton lock
    (PROJECT_ROOT / "run").mkdir(parents=True, exist_ok=True)
    lock_path = str(PROJECT_ROOT / "run" / "tick_monitor.lock")
    lock_fd = os.open(lock_path, os.O_CREAT | os.O_RDWR)
    try:
        import fcntl

        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print(f"另一 tick_monitor 已在运行，退出。锁文件: {lock_path}")
        os.close(lock_fd)
        sys.exit(1)

    try:
        run_tick_monitor(poll_interval=poll_interval)
    finally:
        os.close(lock_fd)


if __name__ == "__main__":
    main()
