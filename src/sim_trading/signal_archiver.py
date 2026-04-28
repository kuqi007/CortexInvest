"""Signal Archiver — 价格快照采样辅助。

与 l2_strategy_daemon 共生运行:
- L2 daemon 已直接写入 signals/session_snapshots
- 每 30s 从 price_snapshots DB 采样价格快照

可独立运行: poetry run python src/sim_trading/signal_archiver.py
"""

import logging
import time
from datetime import datetime

from .db import get_connection, init_db
from .l2_signal_direction import infer_l2_signal_direction

logger = logging.getLogger("signal_archiver")

SIGNAL_CHECK_SEC = 5
PRICE_SAMPLE_SEC = 30


def _infer_direction(signal: dict) -> str:
    """Compatibility wrapper for existing tests/imports."""
    return infer_l2_signal_direction(signal)


class SignalArchiver:
    """Compatibility archiver for price sampling."""

    def __init__(self):
        init_db()
        self._last_signal_ts = 0  # 信号水位标记
        self._last_price_sample = 0.0  # 上次价格采样时间

    def _load_watermark(self):
        """从 DB 加载最新信号 ts 作为水位标记。"""
        conn = get_connection()
        row = conn.execute("SELECT MAX(ts) as max_ts FROM signals").fetchone()
        conn.close()
        if row and row["max_ts"]:
            self._last_signal_ts = row["max_ts"]
            logger.info(f"Signal watermark loaded: {self._last_signal_ts}")

    def archive_signals(self) -> int:
        """L2 daemon writes signals directly to DB; kept for loop compatibility."""
        return 0

    def sample_prices(self) -> int:
        """Sample current HK stock prices from price_snapshots DB. Returns sample count."""
        now = time.time()
        if now - self._last_price_sample < PRICE_SAMPLE_SEC:
            return 0

        self._last_price_sample = now
        ts = int(now * 1000)
        today = datetime.now().strftime("%Y-%m-%d")

        conn = None
        try:
            conn = get_connection()
            rows = conn.execute(
                """SELECT code, name, price, volume, amount, change_pct
                   FROM price_snapshots
                   WHERE (code, ts) IN (
                       SELECT code, MAX(ts) FROM price_snapshots GROUP BY code
                   )
                   AND code LIKE 'HK%'"""
            ).fetchall()
            conn.close()
        except Exception:
            if conn:
                conn.close()
            return 0

        if not rows:
            return 0

        conn = get_connection()
        saved = 0
        for row in rows:
            code = row["code"]
            price = float(row["price"] or 0)
            if price <= 0:
                continue

            try:
                conn.execute(
                    """INSERT OR IGNORE INTO price_snapshots
                       (ts, date, code, price, volume, amount, change_pct)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ts,
                        today,
                        code,
                        price,
                        float(row["volume"] or 0),
                        float(row["amount"] or 0),
                        float(row["change_pct"] or 0),
                    ),
                )
                saved += 1
            except Exception as e:
                logger.debug(f"Price sample error: {e}")

        conn.commit()
        conn.close()

        if saved > 0:
            logger.info(f"Sampled {saved} HK prices from DB")

        return saved

    def snapshot_session(self) -> int:
        """L2 daemon writes session snapshots directly to DB; kept for loop compatibility."""
        return 0

    def run(self):
        """主循环: 5s 检查信号 + 30s 采样价格 + 5min session 快照。"""
        self._load_watermark()
        logger.info("Signal Archiver started")
        logger.info("  signals/session: written directly by l2_strategy_daemon")

        try:
            while True:
                try:
                    self.archive_signals()
                    self.sample_prices()
                    self.snapshot_session()
                except Exception as e:
                    logger.warning(f"Archiver error: {e}")
                time.sleep(SIGNAL_CHECK_SEC)
        except KeyboardInterrupt:
            logger.info("Signal Archiver stopped")

    def backfill(self) -> tuple[int, int]:
        """Run one compatibility pass. Returns (signal_count, price_count)."""
        self._last_signal_ts = 0  # 清除水位，强制全量归档
        sig_count = self.archive_signals()
        price_count = self.sample_prices()
        return sig_count, price_count


def run():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    archiver = SignalArchiver()
    archiver.run()


if __name__ == "__main__":
    run()
