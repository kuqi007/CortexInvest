#!/usr/bin/env python3
"""
Earnings Calendar Daemon — 财报日历守护进程

定期检查即将发布的财报，发送飞书预警，并自动复盘已发布财报。

Usage:
    uv run python src/tools/earnings_calendar_daemon.py

Daemon 行为:
    - 每天 08:00 ~ 22:00 每 30 分钟检查一次
    - 非交易时段降低检查频率（每 2 小时）
    - 使用 fcntl.flock 单例锁确保只有一个实例
"""

import fcntl
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.earnings_calendar import EarningsCalendar
from src.tools.trading_calendar import is_trading_day
from src.utils.logging_config import setup_logger

logger = setup_logger("earnings_calendar_daemon")

LOCK_FILE = PROJECT_ROOT / "data" / ".earnings_calendar_daemon.lock"
CHECK_INTERVAL_SEC = 1800   # 30 分钟（交易时段）
LONG_CHECK_INTERVAL_SEC = 7200  # 2 小时（非交易时段）
DAEMON_MODE = True


def is_trading_hours() -> bool:
    """检查是否在交易时段（A股 09:00-15:00）"""
    now = datetime.now()
    t = now.hour * 100 + now.minute
    if is_trading_day("CN"):
        return 900 <= t <= 1500
    return False


def main():
    # 单例锁
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.warning("财报日历 Daemon 已在运行，退出")
        sys.exit(0)

    logger.info("财报日历 Daemon 启动")
    ec = EarningsCalendar()
    consecutive_errors = 0

    while True:
        try:
            ec.check_and_alert()
            consecutive_errors = 0
        except Exception as e:
            consecutive_errors += 1
            logger.error(f"检查失败 ({consecutive_errors}次): {e}")

        # 根据交易时段调整间隔
        if is_trading_hours():
            sleep_sec = CHECK_INTERVAL_SEC
        else:
            sleep_sec = LONG_CHECK_INTERVAL_SEC

        logger.debug(f"下次检查: {sleep_sec // 60} 分钟后")
        time.sleep(sleep_sec)


if __name__ == "__main__":
    main()
