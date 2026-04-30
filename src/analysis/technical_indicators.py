# src/analysis/technical_indicators.py
"""技术指标计算 — RSI, MACD, KDJ, MA, ATR, Bollinger Bands, Volume"""

import numpy as np


def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """计算 EMA"""
    ema_arr = [float(arr[0])]
    k = 2.0 / (period + 1)
    for price in arr[1:]:
        ema_arr.append(float(price) * k + ema_arr[-1] * (1 - k))
    return np.array(ema_arr)


def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """计算 RSI(14) — 使用 Wilder 平滑公式

    Args:
        closes: 收盘价列表
        period: RSI 周期，默认14

    Returns:
        RSI 值 (0-100)，数据不足时返回 None
    """
    closes_arr = np.array(closes, dtype=float)
    if len(closes_arr) <= period:
        return None

    deltas = np.diff(closes_arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # 首次平均使用 SMA 作为种子
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    # 后续使用 Wilder 平滑公式
    for i in range(period, len(gains)):
        avg_gain = avg_gain * (period - 1) / period + gains[i] / period
        avg_loss = avg_loss * (period - 1) / period + losses[i] / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def calc_macd(
    closes: list[float],
    fast: int = 12,
    slow: int = 26,
    signal: int = 9
) -> dict:
    """计算 MACD(12,26,9)

    Args:
        closes: 收盘价列表

    Returns:
        dict: {
            'dif': float, 'dea': float, 'histogram': float,
            'crossover': 'none'|'golden'|'death',
            'divergence': 'none'|'bullish'|'bearish'
        }
    """
    closes_arr = np.array(closes, dtype=float)
    if len(closes_arr) < slow + signal:
        return {
            'dif': 0.0, 'dea': 0.0, 'histogram': 0.0,
            'crossover': 'none', 'divergence': 'none'
        }
    ema_fast = _ema(closes_arr, fast)
    ema_slow = _ema(closes_arr, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, signal)

    # 背离检测（最近20期）
    divergence = 'none'
    if len(dif) >= 20:
        price_trend = closes_arr[-1] - closes_arr[-20]
        macd_trend = dif[-1] - dif[-20]
        if price_trend < 0 and macd_trend > 0:
            divergence = 'bullish'
        elif price_trend > 0 and macd_trend < 0:
            divergence = 'bearish'

    # 金叉死叉检测
    crossover = 'none'
    if len(dif) >= 2:
        if dif[-2] < dea[-2] and dif[-1] > dea[-1]:
            crossover = 'golden'
        elif dif[-2] > dea[-2] and dif[-1] < dea[-1]:
            crossover = 'death'

    return {
        'dif': round(float(dif[-1]), 2),
        'dea': round(float(dea[-1]), 2),
        'histogram': round(float(dif[-1] - dea[-1]), 2),
        'crossover': crossover,
        'divergence': divergence,
    }


def calc_kdj(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    n: int = 9
) -> dict:
    """计算 KDJ(9,3,3)

    Args:
        highs, lows, closes: OHLC 数据
        n: RSV 窗口，默认9

    Returns:
        dict: {'k': float, 'd': float, 'j': float,
               'k_series': list, 'd_series': list, 'j_series': list,
               'crossover': 'none'|'golden'|'death'}
    """
    highs_arr = np.array(highs, dtype=float)
    lows_arr = np.array(lows, dtype=float)
    closes_arr = np.array(closes, dtype=float)

    if len(closes_arr) < n:
        return {
            'k': 50.0, 'd': 50.0, 'j': 50.0,
            'k_series': [], 'd_series': [], 'j_series': [],
            'crossover': 'none'
        }

    # 计算 RSV 时间序列
    rsv_values = []
    for i in range(n - 1, len(closes)):
        low_n = float(min(lows_arr[i - n + 1:i + 1]))
        high_n = float(max(highs_arr[i - n + 1:i + 1]))
        rsv = (closes_arr[i] - low_n) / (high_n - low_n + 1e-9) * 100.0
        rsv_values.append(rsv)

    # K, D 递归计算
    k_series, d_series = [], []
    k, d = 50.0, 50.0
    for rsv in rsv_values:
        k = 2.0 / 3.0 * k + 1.0 / 3.0 * rsv
        d = 2.0 / 3.0 * d + 1.0 / 3.0 * k
        k_series.append(k)
        d_series.append(d)

    j_series = [3.0 * ki - 2.0 * di for ki, di in zip(k_series, d_series)]

    # 金叉死叉
    crossover = 'none'
    if len(k_series) >= 2:
        if k_series[-2] < d_series[-2] and k_series[-1] > d_series[-1]:
            crossover = 'golden'
        elif k_series[-2] > d_series[-2] and k_series[-1] < d_series[-1]:
            crossover = 'death'

    return {
        'k': round(k_series[-1], 1),
        'd': round(d_series[-1], 1),
        'j': round(j_series[-1], 1),
        'k_series': [round(v, 1) for v in k_series],
        'd_series': [round(v, 1) for v in d_series],
        'j_series': [round(v, 1) for v in j_series],
        'crossover': crossover,
    }


def calc_ma(closes: list[float], periods: tuple = (5, 10, 20, 60)) -> dict:
    """计算 MA 均线系统

    Args:
        closes: 收盘价列表
        periods: 均线周期，默认 (5, 10, 20, 60)

    Returns:
        dict: {'ma5': float, 'ma10': float, ..., 'arrangement': 'bullish'|'bearish'|'neutral'}
    """
    closes_arr = np.array(closes, dtype=float)
    result = {}
    for p in periods:
        if len(closes_arr) >= p:
            result[f'ma{p}'] = round(float(np.mean(closes_arr[-p:])), 2)
        else:
            result[f'ma{p}'] = None

    # 判断均线排列（按数值周期排序，而非字符串）
    valid_mas = {p: result.get(f'ma{p}') for p in periods if result.get(f'ma{p}') is not None}
    if len(valid_mas) < 2:
        arrangement = 'neutral'
    else:
        sorted_periods = sorted(valid_mas.keys())  # [5, 10, 20, 60] numeric sort
        if all(valid_mas[p1] > valid_mas[p2] for p1, p2 in zip(sorted_periods, sorted_periods[1:])):
            arrangement = 'bullish'
        elif all(valid_mas[p1] < valid_mas[p2] for p1, p2 in zip(sorted_periods, sorted_periods[1:])):
            arrangement = 'bearish'
        else:
            arrangement = 'neutral'
    result['arrangement'] = arrangement

    return result


def calc_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14
) -> float | None:
    """计算 ATR (Average True Range) — 使用 Wilder 平滑

    Args:
        highs, lows, closes: OHLC 数据
        period: ATR 周期，默认14

    Returns:
        ATR 值，数据不足时返回 None
    """
    highs_arr = np.array(highs, dtype=float)
    lows_arr = np.array(lows, dtype=float)
    closes_arr = np.array(closes, dtype=float)

    if len(closes) < period + 1:
        return None

    # 计算 True Range
    tr_values = []
    for i in range(1, len(closes)):
        high_low = highs_arr[i] - lows_arr[i]
        high_pc = abs(highs_arr[i] - closes_arr[i - 1])
        low_pc = abs(lows_arr[i] - closes_arr[i - 1])
        tr = max(high_low, high_pc, low_pc)
        tr_values.append(tr)

    if len(tr_values) < period:
        return None

    # 首次 ATR 使用 SMA
    atr = float(np.mean(tr_values[:period]))

    # 后续 Wilder 平滑
    for i in range(period, len(tr_values)):
        atr = atr * (period - 1) / period + tr_values[i] / period

    return round(atr, 2)


def calc_bollinger_bands(
    closes: list[float],
    period: int = 20,
    std_dev: float = 2.0
) -> dict | None:
    """计算布林带

    Args:
        closes: 收盘价列表
        period: 均线周期，默认20
        std_dev: 标准差倍数，默认2

    Returns:
        dict: {'middle': float, 'upper': float, 'lower': float, 'std': float}
        数据不足时返回 None
    """
    closes_arr = np.array(closes, dtype=float)
    if len(closes) < period:
        return None

    ma = float(np.mean(closes_arr[-period:]))
    std = float(np.sqrt(np.sum((closes_arr[-period:] - ma) ** 2) / period))
    upper = ma + std_dev * std
    lower = ma - std_dev * std

    return {
        'middle': round(ma, 2),
        'upper': round(upper, 2),
        'lower': round(lower, 2),
        'std': round(std, 2),
    }


def calc_volume_ratio(volumes: list[float]) -> dict:
    """计算成交量比率和趋势

    Args:
        volumes: 成交量列表（万）

    Returns:
        dict: {'ratio_vs_ma5': float, 'trend': 'expanding'|'contracting'|'neutral'}
    """
    volumes_arr = np.array(volumes, dtype=float)
    ma5 = float(np.mean(volumes_arr[-5:])) if len(volumes_arr) >= 5 else float(np.mean(volumes_arr))
    ratio = volumes_arr[-1] / ma5 if ma5 > 0 else 1.0

    trend = 'neutral'
    if ratio > 2.0:
        trend = 'expanding'
    elif ratio < 0.5:
        trend = 'contracting'

    return {
        'ratio_vs_ma5': round(ratio, 2),
        'trend': trend,
    }
