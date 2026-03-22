#!/usr/bin/env python3
"""
Washout Bottom Calculator — 洗盘底部最佳买入点计算器

公式：
    最佳买入区间 ≈ 高点日收盘价 × 0.80  ~ 高点日最高价 × 0.75

逻辑：
    主力洗盘通常从高点回落约 20-25%，达到黄金坑区间。
    当股价回撤到 最高价×0.75 或 收盘价×0.80 附近时，视为最佳买入点。

Usage:
    poetry run python -m src.tools.washout_calculator --code 002438
    poetry run python -m src.tools.washout_calculator --code 301408 --days 60
    poetry run python -m src.tools.washout_calculator --code HK02722
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ── Data Fetcher ────────────────────────────────────────────────────────────────

def _fetch_kline_tencents(code: str, days: int = 90) -> list[dict]:
    """Fetch daily K-line from Tencent Finance (fallback when akshare unavailable)."""
    # Calculate date range — empty start/end causes "bad params" on Tencent API
    end_date = datetime.today()
    start_date = end_date - timedelta(days=days + 30)  # extra buffer for non-trading days
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")

    if code.startswith("HK"):
        c = code[2:]
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_day"
               f"&param=hk{c},day,{start_str},{end_str},{days},qfq")
    elif code.startswith("KR"):
        return []
    else:
        # A-share: 00xxx/002xxx=sz(深圳), 60xxx/68xxx=sh(上海), 30xxx=sz(深圳创业板)
        if code.startswith(("60", "68")):
            prefix = "sh"
        else:
            prefix = "sz"  # 00xxx, 002xxx, 30xxx, 90xxx (BK) etc.
        url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?_var=kline_day"
               f"&param={prefix}{code},day,{start_str},{end_str},{days},qfq")

    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        text = resp.text
        # Parse JSON from response: var kline_day = {...}
        m = re.search(r'=(\{.*\})', text)
        if not m:
            return []
        data = json.loads(m.group(1))
        qfq = data.get("data", {}).get(code.lower() if code.startswith("HK") else f"{prefix}{code}", {})
        if isinstance(qfq, dict):
            # HK: "day", A-share: "qfqday"
            day_data = qfq.get("day") or qfq.get("qfqday") or []
        elif isinstance(qfq, list):
            day_data = qfq[0].get("day") or qfq[0].get("qfqday") or [] if qfq else []
        else:
            return []

        result = []
        for d in day_data:
            if len(d) < 5:
                continue
            result.append({
                "date": d[0],
                "open": float(d[1]),
                "close": float(d[2]),
                "high": float(d[3]),
                "low": float(d[4]),
                "vol": float(d[5]) if len(d) > 5 else 0,
            })
        return result
    except Exception as e:
        print(f"[WARN] Tencent kline fetch failed for {code}: {e}", file=sys.stderr)
        return []


def _fetch_kline_akshare(code: str, days: int = 90) -> list[dict]:
    """Fetch daily K-line from akshare (primary source for A-shares)."""
    try:
        import akshare as ak
        if code.startswith("HK"):
            c = code[2:]
            df = ak.stock_hk_daily(symbol=f"00{c}")
            df = df.tail(days)
        elif code.startswith("KR"):
            return []
        else:
            # A-share
            symbol_map = {k: v for v, k in {
                "000001": "000001", "399001": "399001", "399006": "399006"
            }.items()}
            df = ak.stock_zh_a_hist(symbol=code, period="daily", adjust="qfq")
            df = df.tail(days)

        result = []
        for _, row in df.iterrows():
            date_val = row.iloc[0]
            if hasattr(date_val, 'strftime'):
                date_str = date_val.strftime("%Y-%m-%d")
            else:
                date_str = str(date_val)[:10]
            result.append({
                "date": date_str,
                "open": float(row.iloc[1]),
                "close": float(row.iloc[2]),
                "high": float(row.iloc[3]),
                "low": float(row.iloc[4]),
                "vol": float(row.iloc[5]) if len(row) > 5 else 0,
            })
        return result
    except Exception:
        return []


def fetch_kline(code: str, days: int = 90) -> list[dict]:
    """Try akshare first, fallback to Tencent Finance."""
    data = _fetch_kline_akshare(code, days)
    if not data:
        data = _fetch_kline_tencents(code, days)
    return data


# ── Technical Indicators ─────────────────────────────────────────────────────────

def _sma(prices: list[float], period: int) -> list[float]:
    """Simple moving average."""
    if len(prices) < period:
        return []
    result = []
    for i in range(period - 1, len(prices)):
        result.append(sum(prices[i - period + 1:i + 1]) / period)
    return result


def _stddev(prices: list[float], period: int) -> list[float]:
    """Rolling standard deviation."""
    if len(prices) < period:
        return []
    result = []
    for i in range(period - 1, len(prices)):
        window = prices[i - period + 1:i + 1]
        mean = sum(window) / period
        variance = sum((p - mean) ** 2 for p in window) / period
        result.append(variance ** 0.5)
    return result


def calc_ma(klines: list[dict], periods: tuple[int, ...] = (5, 10, 20, 60)) -> dict[int, float]:
    """Calculate MA for given periods. Returns {period: ma_value} for last bar."""
    closes = [k["close"] for k in klines]
    result = {}
    for p in periods:
        sma = _sma(closes, p)
        if sma:
            result[p] = round(sma[-1], 3)
    return result


def calc_boll(klines: list[dict], period: int = 20, std_mult: float = 2.0) -> dict:
    """Calculate Bollinger Bands for last bar. Returns {mid, upper, lower, bandwidth, pct_b}."""
    closes = [k["close"] for k in klines]
    if len(closes) < period:
        return {}

    sma_vals = _sma(closes, period)
    std_vals = _stddev(closes, period)
    if not sma_vals or not std_vals:
        return {}

    mid = sma_vals[-1]
    std = std_vals[-1]
    upper = round(mid + std_mult * std, 3)
    lower = round(mid - std_mult * std, 3)
    bandwidth = round((upper - lower) / mid * 100, 3) if mid else 0

    # %B: (close - lower) / (upper - lower), >1 above upper band, <0 below lower band
    price = closes[-1]
    band_range = upper - lower
    pct_b = round((price - lower) / band_range, 3) if band_range else 0

    return {
        "mid": round(mid, 3),
        "upper": upper,
        "lower": lower,
        "bandwidth": bandwidth,
        "pct_b": pct_b,
    }


def _rsi(prices: list[float], period: int = 14) -> list[float]:
    """Relative Strength Index."""
    if len(prices) < period + 1:
        return []
    deltas = [prices[i] - prices[i - 1] for i in range(1, len(prices))]
    gains = [d if d > 0 else 0 for d in deltas]
    losses = [-d if d < 0 else 0 for d in deltas]

    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return [100.0]

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return [rsi]


def calc_rsi(klines: list[dict], periods: tuple[int, ...] = (6, 12, 24)) -> dict:
    """Calculate RSI for multiple periods using Wilder's EMA smoothing.
    Matches 东方财富 RSI exactly (alpha = 1/period).
    Returns {period: {value, history}}."""
    closes = [k["close"] for k in klines]
    result = {}
    for period in periods:
        if len(closes) < period + 1:
            continue
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains  = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        rsi_vals = []
        for i in range(period, len(gains)):
            avg_gain = avg_gain + (gains[i]  - avg_gain) / period
            avg_loss = avg_loss + (losses[i] - avg_loss) / period
            rs = avg_gain / avg_loss if avg_loss > 0 else 999
            rsi_vals.append(100.0 - (100.0 / (1 + rs)))
        if rsi_vals:
            result[period] = {
                "value":  round(rsi_vals[-1], 1),
                "history": [round(v, 1) for v in rsi_vals[-5:]],
            }
    return result


def detect_panic_selling(klines: list[dict], lookback: int = 20) -> dict:
    """Detect panic selling signals: volume spike + sharp drop in recent days."""
    if len(klines) < 5:
        return {}

    klines = klines[-lookback:]

    # Average volume for comparison
    avg_vol = sum(k["close"] * 100 for k in klines[:-1]) / (len(klines) - 1)  # approximate

    # Calculate volume in yuan (close * approximate volume)
    volumes = []
    for k in klines:
        # approximate: use close as proxy for price, raw vol field is in shares
        vol_yuan = k["close"] * float(k.get("vol", 0))
        volumes.append(vol_yuan)

    if not volumes or sum(volumes[:-1]) == 0:
        return {}

    avg_vol_yuan = sum(volumes[:-1]) / (len(volumes) - 1)

    # Recent 3 days
    recent = klines[-3:]
    recent_vols = volumes[-3:]

    # Check for volume surge + price drop
    last = recent[-1]
    price_chg_pct = (last["close"] - klines[-4]["close"]) / klines[-4]["close"] * 100 if len(klines) >= 4 else 0

    # Panic criteria: volume > 2x average + price drop > 3%
    vol_ratio = volumes[-1] / avg_vol_yuan if avg_vol_yuan > 0 else 0
    is_panic = vol_ratio > 2.0 and price_chg_pct < -3.0

    # 3-day consecutive decline
    if len(recent) >= 3:
        d1 = (recent[0]["close"] - klines[-4]["close"]) / klines[-4]["close"] * 100 if len(klines) >= 4 else 0
        d2 = (recent[1]["close"] - recent[0]["close"]) / recent[0]["close"] * 100
        d3 = price_chg_pct
        consecutive_drop = d1 < -1 and d2 < -1 and d3 < -1
    else:
        consecutive_drop = False

    # Bottom fishing: large drop + volume spike (potential institutional bottom)
    bottom_fishing = vol_ratio > 1.5 and price_chg_pct < -2.0

    return {
        "vol_ratio": round(vol_ratio, 2),
        "price_drop_pct": round(price_chg_pct, 2),
        "is_panic": is_panic,
        "consecutive_drop_3d": consecutive_drop,
        "bottom_fishing": bottom_fishing,
    }


def analyze_ma_pattern(ma: dict[int, float], price: float) -> dict:
    if not ma:
        return {}

    sorted_ma = sorted(ma.keys())  # [5, 10, 20, 60]
    result = {
        "above_ma5": ma.get(5) and price > ma[5],
        "above_ma10": ma.get(10) and price > ma[10],
        "above_ma20": ma.get(20) and price > ma[20],
        "above_ma60": ma.get(60) and price > ma[60],
        "bullish_align": False,
        "bearish_align": False,
    }

    # 多头排列: MA5 > MA10 > MA20 > MA60 (short > medium > long)
    if len(sorted_ma) >= 2:
        ma_vals = {p: ma[p] for p in sorted_ma if p in ma}
        ma_list = [ma_vals[p] for p in sorted_ma if p in ma_vals]
        if len(ma_list) >= 2:
            result["bullish_align"] = all(ma_list[i] > ma_list[i+1] for i in range(len(ma_list)-1))
            result["bearish_align"] = all(ma_list[i] < ma_list[i+1] for i in range(len(ma_list)-1))

    # 距各均线距离%
    result["dist_ma20_pct"] = round((price - ma[20]) / ma[20] * 100, 2) if ma.get(20) else None
    result["dist_ma60_pct"] = round((price - ma[60]) / ma[60] * 100, 2) if ma.get(60) else None

    return result


# ── Calculator ────────────────────────────────────────────────────────────────

def find_washout_levels(klines: list[dict], lookback: int = 60) -> dict:
    """
    Find the most recent significant high and calculate washout buy zones.

    Returns:
        {
          "high_date": "2026-02-26",
          "high_price": 53.57,
          "close_at_high": 52.88,
          "target_close": 42.30,    # close × 0.80
          "target_high": 40.18,     # high × 0.75
          "current_price": 42.10,
          "drop_pct": 20.5,
          "recent_low": {"date": "...", "price": ...},
          "zone_valid": True/False,
          "ma": {5: ..., 10: ..., 20: ..., 60: ...},
          "boll": {mid, upper, lower, bandwidth, pct_b},
          "ma_pattern": {...},
        }
    """
    if not klines:
        return {}

    # Take last `lookback` days
    klines = klines[-lookback:]

    # Find the highest high in the period
    max_high = max(klines, key=lambda x: x["high"])
    high_date = max_high["date"]
    high_price = max_high["high"]

    # Find the close on that high date (the close price of the high day)
    high_day_kline = next((k for k in klines if k["date"] == high_date), None)
    close_at_high = high_day_kline["close"] if high_day_kline else high_price

    # Calculate target zones
    target_close = round(close_at_high * 0.80, 2)
    target_high = round(high_price * 0.75, 2)
    target_avg = round((target_close + target_high) / 2, 2)

    # Current price (last close)
    current_price = klines[-1]["close"]
    current_date = klines[-1]["date"]

    # Find lowest low after the high date
    post_high = [k for k in klines if k["date"] > high_date]
    if post_high:
        low_kline = min(post_high, key=lambda x: x["low"])
        recent_low_date = low_kline["date"]
        recent_low_price = low_kline["low"]
    else:
        recent_low_date = None
        recent_low_price = None

    # Drop from high
    drop_pct = round((high_price - current_price) / high_price * 100, 2)

    # Zone validity: if current price is within ±5% of target zone, it's valid
    if target_avg > 0:
        zone_valid = target_close <= current_price <= target_high * 1.05 or \
                     abs(current_price - target_avg) / target_avg < 0.05
    else:
        zone_valid = False

    # ── Technical indicators ──────────────────────────────────────────────
    ma = calc_ma(klines)
    boll = calc_boll(klines)
    ma_pattern = analyze_ma_pattern(ma, current_price)
    rsi = calc_rsi(klines)
    panic = detect_panic_selling(klines)

    return {
        "code": None,
        "high_date": high_date,
        "high_price": high_price,
        "close_at_high": close_at_high,
        "target_close": target_close,
        "target_high": target_high,
        "target_avg": target_avg,
        "current_price": current_price,
        "current_date": current_date,
        "drop_pct": drop_pct,
        "recent_low": {"date": recent_low_date, "price": recent_low_price},
        "zone_valid": zone_valid,
        "days_since_high": (datetime.strptime(current_date, "%Y-%m-%d") -
                            datetime.strptime(high_date, "%Y-%m-%d")).days if high_date else None,
        "ma": ma,
        "boll": boll,
        "ma_pattern": ma_pattern,
        "rsi": rsi,
        "panic": panic,
    }


# ── Formatter ────────────────────────────────────────────────────────────────

def format_result(code: str, r: dict) -> str:
    if not r:
        return f"[{code}] 无法获取K线数据"

    lines = [
        f"\n{'='*50}",
        f"  股票代码: {code}",
        f"{'='*50}",
        f"  近期高点: {r['high_date']}  最高价: {r['high_price']}  收盘: {r['close_at_high']}",
        f"  当前价格: {r['current_price']}  ({r['current_date']})",
        f"  从高点回落: {r['drop_pct']}%",
        f"  距高点天数: {r['days_since_high']}天",
        "",
        f"  【洗盘底部预测】",
        f"  ├ 目标区间A (收盘×0.80): {r['target_close']}",
        f"  ├ 目标区间B (最高×0.75): {r['target_high']}",
        f"  └ 核心区间: {r['target_close']} ~ {r['target_high']}",
        "",
    ]

    if r["recent_low"]["date"]:
        lines.append(
            f"  近期最低: {r['recent_low']['date']}  最低价: {r['recent_low']['price']}"
        )
        zone_str = "✅ 已在区间内" if r["zone_valid"] else "❌ 未到区间"
        lines.append(f"  区间状态: {zone_str}")
    else:
        lines.append("  近期最低: 数据不足")

    # Signal interpretation
    cur = r["current_price"]
    tgt = r["target_avg"]
    lines.append("")
    if cur <= r["target_close"]:
        lines.append(f"  📌 信号: 【最佳买入区间】现价 {cur} ≤ 目标 {r['target_close']}")
    elif cur <= r["target_high"]:
        lines.append(f"  📌 信号: 【接近买入区间】现价 {cur} 接近目标 {r['target_high']}")
    elif r["recent_low"]["price"] and r["recent_low"]["price"] <= r["target_high"]:
        lines.append(f"  📌 信号: 【已超跌】最低 {r['recent_low']['price']} 已跌破目标区间")
    else:
        pct_to = round((cur - tgt) / tgt * 100, 1)
        lines.append(f"  📌 信号: 距目标区间 {pct_to}%")

    # ── MA Analysis ────────────────────────────────────────────────────────
    ma = r.get("ma", {})
    if ma:
        ma_keys = sorted(ma.keys())
        ma_str = "  ".join(f"MA{p}:{ma[p]}" for p in ma_keys)
        lines.append("")
        lines.append(f"  【均线】 {ma_str}")
        mp = r.get("ma_pattern", {})
        if mp:
            above = []
            below = []
            for p in sorted(ma_keys, reverse=True):
                if mp.get(f"above_ma{p}"):
                    above.append(f"MA{p}")
                else:
                    below.append(f"MA{p}")
            if above:
                lines.append(f"  ├ 价格在{'/'.join(above)} 上方")
            if below:
                lines.append(f"  └ 价格在{'/'.join(below)} 下方")
            if mp.get("dist_ma20_pct") is not None:
                d = mp["dist_ma20_pct"]
                arrow = "▲" if d > 0 else "▼"
                lines.append(f"  ├ 距MA20: {arrow}{abs(d)}%")
            if mp.get("dist_ma60_pct") is not None:
                d = mp["dist_ma60_pct"]
                arrow = "▲" if d > 0 else "▼"
                lines.append(f"  └ 距MA60: {arrow}{abs(d)}%")

            if mp.get("bearish_align"):
                lines.append(f"  └ 均线: 【空头排列】短>中>长")
            elif mp.get("bullish_align"):
                lines.append(f"  └ 均线: 【多头排列】短<中<长")

    # ── BOLL Analysis ───────────────────────────────────────────────────────
    boll = r.get("boll", {})
    if boll:
        cur = r["current_price"]
        mid = boll["mid"]
        upper = boll["upper"]
        lower = boll["lower"]

        lines.append("")
        lines.append(f"  【BOLL】 中轨:{mid} 上轨:{upper} 下轨:{lower}")

        bandwidth = boll.get("bandwidth", 0)
        pct_b = boll.get("pct_b", 0)

        # Distance to mid and lower band
        dist_mid_pct = round((cur - mid) / mid * 100, 2)
        dist_lower_pct = round((cur - lower) / lower * 100, 2)
        dist_upper_pct = round((cur - upper) / upper * 100, 2)
        lines.append(f"  ├ 距中轨: {'+' if dist_mid_pct >= 0 else ''}{dist_mid_pct}%")
        lines.append(f"  ├ 距下轨: {'+' if dist_lower_pct >= 0 else ''}{dist_lower_pct}%")
        lines.append(f"  └ 距上轨: {'+' if dist_upper_pct >= 0 else ''}{dist_upper_pct}%")

        # Position relative to mid band
        if cur > upper:
            pos_signal = "🔴 突破上轨（极端超买）"
        elif cur > mid:
            pos_signal = "🟢 在中轨上方（偏强）"
        elif cur > lower:
            pos_signal = "🟡 在中轨下方（偏弱）"
        else:
            pos_signal = "🔴 跌破下轨（极端超卖）"
        lines.append(f"  ├ 价格位置: {pos_signal}")

        # Bandwidth interpretation (squeeze detection)
        if bandwidth < 5:
            bw_signal = "⚠️ 极度收敛（可能变盘）"
        elif bandwidth < 10:
            bw_signal = "📊 收敛（观望）"
        elif bandwidth > 20:
            bw_signal = "📈 扩张（趋势中）"
        else:
            bw_signal = "📊 正常"
        lines.append(f"  └ 带宽: {bandwidth}% ({bw_signal})")

        # %B interpretation
        if pct_b < 0:
            boll_signal = "🔴 跌破下轨（超卖）"
        elif pct_b < 0.2:
            boll_signal = "🟡 接近下轨（低位）"
        elif pct_b > 0.8:
            boll_signal = "🔴 接近上轨（超买）"
        elif pct_b > 1.0:
            boll_signal = "🔴 突破上轨"
        else:
            boll_signal = "🟢 中轨附近"
        lines.append(f"  └ %B:{pct_b} ({boll_signal})")

    # ── RSI Analysis ───────────────────────────────────────────────────────
    rsi = r.get("rsi", {})
    if rsi:
        lines.append("")
        for period in sorted(rsi.keys()):
            rsi_data = rsi[period]
            rsi_val = rsi_data.get("value", 0)
            rsi_history = rsi_data.get("history", [])
            history_str = " / ".join(str(v) for v in rsi_history) if rsi_history else "无"
            label = f"RSI({period})"

            if rsi_val < 20:
                signal = "🔴 极度超卖"
            elif rsi_val < 30:
                signal = "🟡 超卖"
            elif rsi_val < 40:
                signal = "🟡 偏弱"
            elif rsi_val < 60:
                signal = "🟢 中性"
            elif rsi_val < 70:
                signal = "🟡 偏热"
            else:
                signal = "🔴 超买"

            if period == 6:
                lines.append(f"  【{label}】 当前:{rsi_val}  近5期:{history_str}")
                lines.append(f"  └ 信号: {signal}")
            else:
                lines.append(f"  【{label}】 {rsi_val} ({signal})")

    # ── Panic Selling Detection ─────────────────────────────────────────────
    panic = r.get("panic", {})
    if panic:
        lines.append("")
        lines.append(f"  【量价异动】 量比:{panic.get('vol_ratio', 0):.1f}x  跌幅:{panic.get('price_drop_pct', 0):+.1f}%")
        if panic.get("is_panic"):
            lines.append(f"  └ 🚨 恐慌抛售信号! (量>2x均量+跌幅>3%)")
        elif panic.get("bottom_fishing"):
            lines.append(f"  └ 🟡 底部吸筹嫌疑 (量>1.5x+跌幅>2%)")
        elif panic.get("consecutive_drop_3d"):
            lines.append(f"  └ 🟡 三连跌（谨慎）")
        else:
            lines.append(f"  └ ✅ 无恐慌信号")

    lines.append("")
    return "\n".join(lines)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="洗盘底部最佳买入点计算器")
    parser.add_argument("--code", required=True, help="股票代码，如 002438 或 HK09988")
    parser.add_argument("--days", type=int, default=90, help="回溯天数，默认90")
    parser.add_argument("--json", action="store_true", help="输出JSON格式")
    args = parser.parse_args()

    code = args.code.upper()

    klines = fetch_kline(code, args.days)
    if not klines:
        print(f"[ERROR] 无法获取 {code} 的K线数据", file=sys.stderr)
        sys.exit(1)

    result = find_washout_levels(klines, args.days)
    result["code"] = code

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(format_result(code, result))


if __name__ == "__main__":
    main()
