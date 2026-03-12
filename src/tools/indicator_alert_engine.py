"""技术指标引擎 — A 股 + 港股通用。

功能:
  1. compute_indicators() — 从日 K 线计算 RSI/MACD/MA/Vol 等指标
  2. fetch_kline_akshare() — 从 akshare 获取 A 股/港股日 K 线
  3. refresh_indicator_cache() — 批量计算全 watchlist 指标，写入 SQLite indicator_cache 表
  4. AStockIndicatorAlertEngine — 收盘后告警（已弃用，改为交易计划条件单）

数据源: akshare (A 股 stock_zh_a_hist + 港股 stock_hk_hist)，无 Futu 依赖。
"""

import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

logger = logging.getLogger("l2_daemon.indicator_alert")

# ---------------------------------------------------------------------------
# 指标计算
# ---------------------------------------------------------------------------

def _calc_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def _calc_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=signal, adjust=False).mean()
    hist = (dif - dea) * 2
    return dif, dea, hist


def _calc_ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(period).mean()


def compute_indicators(kline: pd.DataFrame, live_price: float | None = None,
                       live_volume: float | None = None) -> dict | None:
    """从日K线 DataFrame 计算全套技术指标。

    kline 需要含 close/high/low/volume 列，按日期升序排列。
    live_price: 盘中实时价格，追加为当日 bar 的 close（用于盘中预估指标）。
    live_volume: 盘中实时成交量，追加为当日 bar 的 volume。
    返回 dict 或 None (数据不足)。
    """
    if kline is None or len(kline) < 26:
        return None

    # 盘中模式: 追加一根 live bar（用实时价格做今日收盘预估）
    if live_price is not None and live_price > 0:
        live_row = pd.DataFrame([{
            "close": live_price,
            "high": live_price,
            "low": live_price,
            "volume": live_volume if live_volume and live_volume > 0 else kline["volume"].iloc[-1],
        }])
        kline = pd.concat([kline, live_row], ignore_index=True)

    close = kline["close"].astype(float)
    volume = kline["volume"].astype(float)

    rsi = _calc_rsi(close, 14)
    dif, dea, hist = _calc_macd(close)
    ma5 = _calc_ma(close, 5)
    ma10 = _calc_ma(close, 10)
    ma20 = _calc_ma(close, 20)
    vol_ma20 = volume.rolling(20).mean()

    # 最新值
    latest = {
        "close": float(close.iloc[-1]),
        "rsi": float(rsi.iloc[-1]) if not np.isnan(rsi.iloc[-1]) else 50.0,
        "dif": float(dif.iloc[-1]),
        "dea": float(dea.iloc[-1]),
        "macd_hist": float(hist.iloc[-1]),
        "macd_hist_prev": float(hist.iloc[-2]) if len(hist) >= 2 else 0.0,
        "ma5": float(ma5.iloc[-1]) if not np.isnan(ma5.iloc[-1]) else 0.0,
        "ma5_prev": float(ma5.iloc[-2]) if len(ma5) >= 2 and not np.isnan(ma5.iloc[-2]) else 0.0,
        "ma5_prev2": float(ma5.iloc[-3]) if len(ma5) >= 3 and not np.isnan(ma5.iloc[-3]) else 0.0,
        "ma10": float(ma10.iloc[-1]) if not np.isnan(ma10.iloc[-1]) else 0.0,
        "ma20": float(ma20.iloc[-1]) if not np.isnan(ma20.iloc[-1]) else 0.0,
        "vol": float(volume.iloc[-1]),
        "vol_ma20": float(vol_ma20.iloc[-1]) if not np.isnan(vol_ma20.iloc[-1]) else 0.0,
        "vol_ratio": float(volume.iloc[-1] / vol_ma20.iloc[-1]) if vol_ma20.iloc[-1] > 0 else 0.0,
    }

    # MACD 金叉/死叉: 今天 DIF > DEA 且昨天 DIF <= DEA
    dif_prev = float(dif.iloc[-2]) if len(dif) >= 2 else 0.0
    dea_prev = float(dea.iloc[-2]) if len(dea) >= 2 else 0.0
    latest["macd_golden_cross"] = (latest["dif"] > latest["dea"]) and (dif_prev <= dea_prev)
    latest["macd_death_cross"] = (latest["dif"] < latest["dea"]) and (dif_prev >= dea_prev)

    # MACD 底背离: 近 20 日内价格创新低但 MACD hist 不创新低
    latest["macd_bull_divergence"] = False
    if len(close) >= 20 and len(hist) >= 20:
        recent_close = close.iloc[-20:]
        recent_hist = hist.iloc[-20:]
        # 找到价格最低点和 MACD hist 最低点
        price_min_idx = recent_close.idxmin()
        hist_min_idx = recent_hist.idxmin()
        # 底背离: 当前价格接近近期最低 (< 最低 + 3%), 但 hist 高于最低
        price_near_low = close.iloc[-1] <= recent_close.min() * 1.03
        hist_above_low = hist.iloc[-1] > recent_hist.min() * 0.7  # hist 抬高 30%+
        if price_near_low and hist_above_low and recent_hist.min() < 0:
            latest["macd_bull_divergence"] = True

    # MA5 拐头向上: 前天 MA5 > 昨天 MA5 (下行), 今天 MA5 > 昨天 MA5 (上行)
    latest["ma5_turn_up"] = (
        latest["ma5_prev2"] > latest["ma5_prev"] > 0 and
        latest["ma5"] > latest["ma5_prev"]
    )

    return latest


# ---------------------------------------------------------------------------
# 数据获取
# ---------------------------------------------------------------------------

def fetch_kline_akshare(symbol: str, days: int = 80) -> pd.DataFrame | None:
    """从 akshare 获取日 K 线（A 股 + 港股）。

    A 股: stock_zh_a_hist (symbol=000792)
    港股: stock_hk_hist (symbol=09988, 去掉 HK 前缀)
    返回 DataFrame (close/high/low/volume), 升序; 失败返回 None。
    """
    try:
        import akshare as ak

        end = datetime.now().strftime("%Y%m%d")
        start = (datetime.now() - timedelta(days=days + 30)).strftime("%Y%m%d")

        if symbol.startswith("HK"):
            # 港股: 去掉 HK 前缀
            hk_code = symbol[2:]
            df = ak.stock_hk_hist(
                symbol=hk_code,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )
        else:
            # A 股
            df = ak.stock_zh_a_hist(
                symbol=symbol,
                period="daily",
                start_date=start,
                end_date=end,
                adjust="qfq",
            )

        if df is None or len(df) < 20:
            return None

        df = df.rename(columns={
            "日期": "date", "开盘": "open", "最高": "high",
            "最低": "low", "收盘": "close", "成交量": "volume",
        })
        df = df.sort_values("date").reset_index(drop=True)
        return df.tail(days)
    except Exception as e:
        logger.warning(f"akshare fetch {symbol} failed: {e}")
        return None


# ---------------------------------------------------------------------------
# 指标缓存 (SQLite)
# ---------------------------------------------------------------------------

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sim_trading.db"

_CREATE_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS indicator_cache (
    symbol TEXT NOT NULL,
    date TEXT NOT NULL,
    data_json TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, date)
)
"""


def _ensure_cache_table():
    import sqlite3
    con = sqlite3.connect(str(DB_PATH))
    con.execute(_CREATE_CACHE_TABLE)
    con.commit()
    con.close()


def refresh_indicator_cache(watchlist: dict | None = None, live_quotes: dict | None = None):
    """批量计算全 watchlist 指标，写入 indicator_cache 表。

    由 Notifier 每 30 分钟调用一次（盘中用 live_quotes 做预估）。
    Args:
        watchlist: {symbol: {name, type, ...}} — None 时从 monitor_config.json 读取
        live_quotes: {symbol: {price, volume, ...}} — 盘中实时行情，用于 live_price 预估
    """
    import sqlite3

    if watchlist is None:
        cfg_path = Path(__file__).resolve().parent.parent / "data" / "monitor_config.json"
        try:
            with open(cfg_path, "r") as f:
                cfg = json.load(f)
            watchlist = cfg.get("watchlist", {})
        except Exception:
            return

    # 过滤掉 KR
    symbols = [s for s in watchlist if not s.startswith("KR")]
    if not symbols:
        return

    _ensure_cache_table()
    today = datetime.now().strftime("%Y-%m-%d")
    now_str = datetime.now().isoformat()
    rows = []

    for symbol in symbols:
        try:
            kline = fetch_kline_akshare(symbol, days=60)
            if kline is None:
                continue

            # 盘中用实时价格做预估
            lp = 0.0
            lv = 0.0
            if live_quotes and symbol in live_quotes:
                q = live_quotes[symbol]
                lp = q.get("price", 0) or 0
                lv = q.get("volume", 0) or 0

            ind = compute_indicators(kline, live_price=lp if lp > 0 else None,
                                     live_volume=lv if lv > 0 else None)
            if ind is None:
                continue

            # 序列化 (bool → int for JSON)
            for k, v in ind.items():
                if isinstance(v, (bool, np.bool_)):
                    ind[k] = bool(v)

            rows.append((symbol, today, json.dumps(ind, ensure_ascii=False), now_str))
            time.sleep(1.5)  # akshare 限速
        except Exception as e:
            logger.warning(f"indicator_cache: {symbol} failed: {e}")
            continue

    if rows:
        con = sqlite3.connect(str(DB_PATH))
        con.executemany(
            "INSERT OR REPLACE INTO indicator_cache (symbol, date, data_json, updated_at) VALUES (?, ?, ?, ?)",
            rows,
        )
        con.commit()
        con.close()
        logger.info(f"indicator_cache: refreshed {len(rows)}/{len(symbols)} symbols")


def read_indicator_cache() -> dict[str, dict]:
    """从 SQLite 读取今日指标缓存。返回 {symbol: indicators_dict}。"""
    import sqlite3
    try:
        con = sqlite3.connect(str(DB_PATH))
        con.row_factory = sqlite3.Row
        today = datetime.now().strftime("%Y-%m-%d")
        cur = con.execute("SELECT symbol, data_json FROM indicator_cache WHERE date = ?", (today,))
        result = {}
        for row in cur:
            try:
                result[row["symbol"]] = json.loads(row["data_json"])
            except Exception:
                pass
        con.close()
        return result
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# 告警引擎
# ---------------------------------------------------------------------------

# 在 stock_notifier 中 PatternEngine 定义。这里 import 时可能有循环依赖，
# 所以用 duck typing: 只要实现 check/reset/update_config 就行。

class AStockIndicatorAlertEngine:
    """A股日线技术指标告警引擎。

    每日 A 股收盘后 (15:05-15:20) 执行一次:
    1. 获取 watchlist 中所有 A 股的 60 日 K 线 (akshare)
    2. 计算 RSI/MACD/MA/Vol 指标
    3. 检测规则，满足条件时生成告警
    4. 告警接入 PatternEngine 注册表，统一写入 + 分发

    告警级别: star → L1(弹窗+声音), holding → L2(静默弹窗), watching → L3(仅web)
    """

    # 检测时间窗口: 15:05 - 15:20 (A股收盘后)
    CHECK_START = 1505
    CHECK_END = 1520

    def __init__(self, config: dict):
        self.config = config
        self._checked_today: dict[str, str] = {}  # {symbol: date} 每日每股只检测一次
        self._indicator_cache: dict[str, dict] = {}  # 缓存当日指标快照

    def reset(self):
        self._checked_today.clear()
        self._indicator_cache.clear()

    def update_config(self, config: dict):
        self.config = config

    def check(self, quotes: dict) -> list[dict]:
        """PatternEngine 接口: 每 tick 调用，但只在收盘后时间窗口执行。"""
        now = datetime.now()
        hhmm = now.hour * 100 + now.minute

        # 仅工作日 15:05-15:20 执行
        if now.weekday() >= 5 or hhmm < self.CHECK_START or hhmm > self.CHECK_END:
            return []

        today = now.strftime("%Y-%m-%d")
        watchlist = self.config.get("watchlist", {})
        alerts = []

        # 仅检查 A 股 (不以 HK/KR 开头)
        a_stocks = [s for s in watchlist if not s.startswith("HK") and not s.startswith("KR")]

        for symbol in a_stocks:
            if self._checked_today.get(symbol) == today:
                continue

            entry = watchlist[symbol]
            name = entry.get("name", symbol)

            # 获取日 K 线
            kline = fetch_kline_akshare(symbol)
            if kline is None:
                logger.warning(f"indicator_alert: {symbol} kline fetch failed, skip")
                self._checked_today[symbol] = today
                continue

            # 计算指标
            ind = compute_indicators(kline)
            if ind is None:
                self._checked_today[symbol] = today
                continue

            self._indicator_cache[symbol] = ind
            self._checked_today[symbol] = today

            # 推导通知级别
            level = self._resolve_level(entry)

            # ── 检测规则 ──
            price = quotes.get(symbol, {}).get("price") or ind["close"]
            change_pct = quotes.get(symbol, {}).get("change_pct", 0)

            # 1. RSI 超卖 (< 30)
            if ind["rsi"] < 30:
                alerts.append(self._make_alert(
                    symbol, name, level, change_pct, price,
                    kind="ind_rsi_oversold",
                    signal=f"RSI超卖",
                    detail=f"RSI={ind['rsi']:.1f} 进入超卖区, 现价{price:.2f}",
                ))

            # 2. RSI 超买 (> 70) — 对持仓标的预警
            if ind["rsi"] > 70 and entry.get("type") == "holding":
                alerts.append(self._make_alert(
                    symbol, name, level, change_pct, price,
                    kind="ind_rsi_overbought",
                    signal=f"RSI超买",
                    detail=f"RSI={ind['rsi']:.1f} 超买区, 现价{price:.2f}",
                ))

            # 3. MACD 金叉
            if ind["macd_golden_cross"]:
                alerts.append(self._make_alert(
                    symbol, name, level, change_pct, price,
                    kind="ind_macd_golden",
                    signal="MACD金叉",
                    detail=f"DIF上穿DEA, MACD柱={ind['macd_hist']:.3f}, 现价{price:.2f}",
                ))

            # 4. MACD 死叉 — 对持仓预警
            if ind["macd_death_cross"] and entry.get("type") == "holding":
                alerts.append(self._make_alert(
                    symbol, name, level, change_pct, price,
                    kind="ind_macd_death",
                    signal="MACD死叉",
                    detail=f"DIF下穿DEA, MACD柱={ind['macd_hist']:.3f}, 现价{price:.2f}",
                ))

            # 5. MACD 底背离
            if ind["macd_bull_divergence"]:
                alerts.append(self._make_alert(
                    symbol, name, level, change_pct, price,
                    kind="ind_macd_divergence",
                    signal="MACD底背离",
                    detail=f"价格接近新低但MACD柱抬高, RSI={ind['rsi']:.1f}, 现价{price:.2f}",
                ))

            # 6. 放量 (成交量 > 2x MA20)
            if ind["vol_ratio"] >= 2.0:
                alerts.append(self._make_alert(
                    symbol, name, level, change_pct, price,
                    kind="ind_volume_surge",
                    signal="放量",
                    detail=f"成交量={ind['vol_ratio']:.1f}倍MA20, 现价{price:.2f}",
                ))

            # 7. MA5 拐头向上 (下行转上行)
            if ind["ma5_turn_up"]:
                alerts.append(self._make_alert(
                    symbol, name, level, change_pct, price,
                    kind="ind_ma5_turn_up",
                    signal="MA5拐头",
                    detail=f"MA5由跌转升({ind['ma5_prev']:.2f}→{ind['ma5']:.2f}), 现价{price:.2f}",
                ))

            # 8. 综合开仓信号: RSI<35 + MACD底背离或金叉 + 放量
            buy_signals = []
            if ind["rsi"] < 35:
                buy_signals.append(f"RSI={ind['rsi']:.0f}")
            if ind["macd_bull_divergence"]:
                buy_signals.append("底背离")
            if ind["macd_golden_cross"]:
                buy_signals.append("MACD金叉")
            if ind["vol_ratio"] >= 1.5:
                buy_signals.append(f"量比{ind['vol_ratio']:.1f}x")
            if ind["ma5_turn_up"]:
                buy_signals.append("MA5拐头")

            if len(buy_signals) >= 2:
                # 多信号共振 — 强制 L1 提醒
                alerts.append(self._make_alert(
                    symbol, name, min(level, 1), change_pct, price,
                    kind="ind_buy_confluence",
                    signal="开仓信号共振",
                    detail=f"{'＋'.join(buy_signals)}, 现价{price:.2f}",
                ))

            # akshare 限速: 每只股票间隔 1.5s
            time.sleep(1.5)

        if alerts:
            logger.info(f"indicator_alert: {len(alerts)} alerts for {len(a_stocks)} A-stocks")

        return alerts

    @staticmethod
    def _resolve_level(entry: dict) -> int:
        if entry.get("hidden"):
            return 4
        if entry.get("star"):
            return 1
        if entry.get("type") == "holding":
            return 2
        return 3

    @staticmethod
    def _make_alert(symbol, name, level, change_pct, price, kind, signal, detail):
        display = f"{symbol} {name} 【{signal}】{detail}"
        msg = f"{name} {signal} {price:.2f}"
        return {
            "symbol": symbol,
            "title": f"{name} {signal}",
            "message": msg,
            "display": display,
            "_kind": kind,
            "_level": level,
            "_change_pct": change_pct,
            "_price": price,
            "_stealth": f"Indicator: {name} {signal}",
        }

    def get_cache(self) -> dict:
        """返回当日指标缓存，供外部查询。"""
        return dict(self._indicator_cache)
