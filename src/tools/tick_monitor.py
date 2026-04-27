#!/usr/bin/env python3
"""
Tick Monitor — 短线盯盘 Daemon

基于 Futu OpenD get_rt_ticker() 逐笔成交数据，监控指定股票的
买入/卖出价格触发条件，通过飞书通知提醒。

设计原则:
  - 轻量级: 只监控 2-3 只短线股票，3-5 秒轮询
  - 独立进程: 不依赖 l2_strategy_engine，直接调用 Futu API
  - 复用 feishu_send() 通知通道
  - 配置从 trade_plans.json 中 scope="tick_monitor" 的计划读取

配置格式 (trade_plans.json):
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

通知冷却: 同一订单触发后 5 分钟内不再重复通知（防止抖动）
状态持久化: 冷却状态写入 tick_monitor_state.json，daemon 重启后不重发
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

from src.tools.stock_monitor import feishu_send
from src.tools.stock_notifier import is_any_market_open
from src.utils.futu_codes import to_futu_code
from src.utils.logging_config import setup_logger

logger = setup_logger("tick_monitor")

# ── 配置 ──
TRADE_PLANS_PATH = PROJECT_ROOT / "src" / "data" / "trade_plans.json"
STATE_PATH = PROJECT_ROOT / "src" / "data" / "tick_monitor_state.json"
OPEND_HOST = "127.0.0.1"
OPEND_PORT = 11111
RECONNECT_COOLDOWN = 60
POLL_INTERVAL = 5  # 秒
COOLDOWN_SEC = 300  # 同一订单触发后冷却 5 分钟
BACKOFF_AFTER_FAIL = 30  # 连续失败后的退避间隔
MAX_CONSECUTIVE_FAIL = 3  # 超过此次连续失败后进入退避

# 飞书防刷屏：同一 symbol 30 分钟内只推一次，每天全局最多 20 条
FEISHU_SYMBOL_COOLDOWN_SEC = 1800
FEISHU_DAILY_MAX = 20


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
# 配置加载 (带 mtime 缓存)
# ══════════════════════════════════════════

_plans_cache: dict[str, dict] | None = None
_plans_mtime: float = 0


def load_tick_monitor_plans() -> dict[str, dict]:
    """从 trade_plans.json 加载 scope='tick_monitor' 的计划，带缓存"""
    global _plans_cache, _plans_mtime

    if not TRADE_PLANS_PATH.exists():
        logger.warning(f"trade_plans.json not found: {TRADE_PLANS_PATH}")
        return _plans_cache or {}

    try:
        current_mtime = TRADE_PLANS_PATH.stat().st_mtime
        if _plans_cache is not None and current_mtime == _plans_mtime:
            return _plans_cache

        with open(TRADE_PLANS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        _plans_mtime = current_mtime
    except Exception as e:
        logger.warning(f"Failed to load trade_plans.json: {e}")
        return _plans_cache or {}

    plans = data.get("plans", {})
    tick_plans = {}
    for plan_id, plan in plans.items():
        scope = (plan.get("scope") or "").strip().lower()
        if scope == "tick_monitor" and plan.get("status") == "active":
            tick_plans[plan_id] = plan

    _plans_cache = tick_plans
    return tick_plans


# ══════════════════════════════════════════
# 状态持久化 (冷却状态)
# ══════════════════════════════════════════


def load_state() -> dict[str, float]:
    """加载持久化的冷却状态"""
    if not STATE_PATH.exists():
        return {}
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {k: float(v) for k, v in raw.items()}
    except Exception as e:
        logger.warning(f"Failed to load tick_monitor_state.json: {e}")
        return {}


def save_state(cooldowns: dict[str, float]) -> None:
    """原子写入冷却状态"""
    try:
        # 确保目录存在
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = STATE_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cooldowns, f, ensure_ascii=False, indent=2)
        os.replace(tmp, STATE_PATH)
        logger.debug(f"State saved: {len(cooldowns)} entries")
    except Exception as e:
        logger.warning(f"Failed to save tick_monitor_state.json: {e}")


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
# 飞书通知
# ══════════════════════════════════════════


# 全局飞书每日计数和 symbol 冷却
_feishu_symbol_cooldown: dict[str, float] = {}
_feishu_daily_count = 0
_feishu_daily_date = datetime.now().strftime("%Y-%m-%d")


def _check_feishu_cooldown(symbol: str) -> bool:
    """检查 symbol 是否可以通过飞书冷却。返回 True = 可以发送。"""
    global _feishu_daily_count, _feishu_daily_date

    # 每日重置
    today = datetime.now().strftime("%Y-%m-%d")
    if today != _feishu_daily_date:
        _feishu_daily_date = today
        _feishu_daily_count = 0
        _feishu_symbol_cooldown.clear()

    # 全局上限
    if _feishu_daily_count >= FEISHU_DAILY_MAX:
        return False

    # symbol 冷却
    now = time.time()
    last = _feishu_symbol_cooldown.get(symbol, 0)
    if now - last < FEISHU_SYMBOL_COOLDOWN_SEC:
        return False

    _feishu_symbol_cooldown[symbol] = now
    _feishu_daily_count += 1
    return True


def send_tick_notification(
    plan_name: str,
    symbol: str,
    order: dict,
    tick: dict,
) -> bool:
    """发送飞书逐笔触发通知（带防刷屏冷却）"""
    # 飞书冷却检查
    if not _check_feishu_cooldown(symbol):
        logger.info(f"Feishu cooldown for {symbol}, skip notification")
        return True  # 返回 True 表示已处理（不重复触发）

    side = order.get("side", "buy")
    op = order.get("op", "<=")
    trigger_price = order.get("price", 0)
    label = order.get("label", "")
    tick_price = tick.get("price", 0)

    side_str = "买入" if side == "buy" else "卖出"
    op_str = "≤" if op == "<=" else "≥" if op == ">=" else op

    title = f"🎯 短线盯盘触发: {plan_name}"
    message = (
        f"**{side_str}信号** | {symbol}\n"
        f"触发条件: 价格 {op_str} {trigger_price}\n"
        f"当前价格: **{tick_price}**\n"
        f"方向: {tick.get('direction', '')} | 成交量: {tick.get('volume', 0)}\n"
        f"时间: {tick.get('time', datetime.now().strftime('%H:%M:%S'))}"
    )

    stock_info = {
        "name": plan_name,
        "code": symbol,
        "price": str(tick_price),
        "change_pct": 0.0,
        "level": f"Tick {side_str}",
        "time": tick.get("time", ""),
    }

    return feishu_send(title, message, stock_info=stock_info)


# ══════════════════════════════════════════
# 主循环
# ══════════════════════════════════════════


def _sigterm_handler(signum, frame):
    logger.info("Tick Monitor received SIGTERM, exiting")
    sys.exit(0)


def run_tick_monitor(poll_interval: int = POLL_INTERVAL):
    """Tick Monitor 主循环"""
    logger.info("Tick Monitor 启动")
    signal.signal(signal.SIGTERM, _sigterm_handler)

    conn = FutuConnection()
    cooldowns = load_state()
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

                # 获取最新 tick
                tick = conn.get_latest_tick(symbol)
                if tick is None:
                    continue

                any_tick = True
                tick_price = tick.get("price", 0)

                for order in orders:
                    order_id = order.get("id", "")
                    if not order_id:
                        continue

                    # 冷却检查
                    last_trigger = cooldowns.get(order_id, 0)
                    if now - last_trigger < COOLDOWN_SEC:
                        continue

                    if check_trigger(order, tick_price):
                        logger.info(
                            f"🎯 TRIGGERED: {plan_name} {order_id} "
                            f"price={tick_price} op={order.get('op')} target={order.get('price')}"
                        )

                        # 发送通知
                        success = send_tick_notification(plan_name, symbol, order, tick)
                        if success:
                            cooldowns[order_id] = now
                            save_state(cooldowns)
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
        conn.close()
        save_state(cooldowns)
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
    lock_path = f"/tmp/tick_monitor.{os.getuid()}.lock"
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
