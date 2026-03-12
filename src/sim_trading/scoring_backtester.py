"""V2 Scoring Backtester with Optuna Optimization.

Replays daily klines through a parameterizable scoring system,
simulates entry/exit decisions, and uses Optuna to find optimal
scoring weights and thresholds.

Usage:
    # Single run with default params
    poetry run python -m src.sim_trading.scoring_backtester

    # Optuna optimization (300 trials)
    poetry run python -m src.sim_trading.scoring_backtester --optimize --trials 300

    # Run with specific param version
    poetry run python -m src.sim_trading.scoring_backtester --version v2_opt_01
"""

import argparse
import datetime
import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from src.sim_trading.db import get_connection, init_db

logger = logging.getLogger(__name__)

# ── Default Parameters (from signal_rules.json) ──

DEFAULT_WEIGHTS = {
    "macd": 20, "rsi": 15, "ma": 20,
    "capital_flow": 20, "volume_price": 15, "support": 10,
}
DEFAULT_THRESHOLDS = {
    "entry_threshold": 70,
    "exit_threshold": 40,
    "exit_consecutive_days": 2,
    "max_hold_days": 10,
    "position_pct": 0.25,
    "sl_atr_mult": 2.0,
    "tp_atr_mult": 3.0,
    "adx_min": 20,          # ADX below this = choppy, don't enter
    "rr_min": 1.5,          # min reward/risk ratio (TP distance / SL distance)
    "trailing_atr": 0.0,    # 0 = off; > 0 = trailing stop at entry + N*ATR once profitable
    "require_bullish_di": 1,  # 1 = only enter when +DI > -DI (bullish trend)
    "cooldown_days": 5,     # calendar days before re-entering same stock
}
DEFAULT_COST_MODEL = {
    "commission_rate": 0.0003,
    "commission_min": 3.0,
    "stamp_duty_rate": 0.0013,
    "exchange_fee_rate": 0.0000565,
    "settlement_fee_rate": 0.00002,
    "settlement_min": 2.0,
    "settlement_max": 100.0,
}
LOT_SIZES = {
    "HK00700": 100, "HK09988": 100, "HK01810": 200,
    "HK01211": 500, "HK06809": 100, "HK01060": 10000,
    "HK02359": 500, "HK06060": 1000, "HK01357": 2000,
    "HK00325": 100, "HK03896": 2000, "HK03986": 100,
}
DEFAULT_LOT = 100  # HK default

INITIAL_CAPITAL = 1_000_000  # HKD


# ══════════════════════════════════════════
# Offline Scorer — standalone, no Futu dependency
# ══════════════════════════════════════════

def compute_indicators(df: pd.DataFrame) -> dict:
    """Compute all technical indicators from a kline DataFrame.

    Args:
        df: DataFrame with columns [date, open, high, low, close, volume]
            sorted by date ascending, at least 60 rows.

    Returns:
        Dict of indicator Series, same format as DailyIndicatorTracker._ind
    """
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float)

    # RSI
    rsi = _calc_rsi(close, 14)

    # MACD
    ema_fast = close.ewm(span=12, adjust=False).mean()
    ema_slow = close.ewm(span=26, adjust=False).mean()
    dif = ema_fast - ema_slow
    dea = dif.ewm(span=9, adjust=False).mean()
    hist = (dif - dea) * 2

    # MAs
    mas = {f"ma{p}": close.rolling(p).mean() for p in [5, 10, 20, 60]}

    # ADX + DI
    adx, plus_di, minus_di = _calc_adx(high, low, close, 14)

    # ATR
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=14, adjust=False).mean()

    # Volume MA
    vol_ma20 = volume.rolling(20).mean()

    return {
        "close": close, "high": high, "low": low, "volume": volume,
        "rsi": rsi,
        "macd_dif": dif, "macd_dea": dea, "macd_hist": hist,
        "mas": mas, "adx": adx, "plus_di": plus_di, "minus_di": minus_di,
        "atr": atr, "vol_ma20": vol_ma20,
    }


def _calc_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1/period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(100)
    rsi = rsi.where(avg_gain > 0, 0)
    return rsi


def _calc_adx(high, low, close, period=14):
    tr = pd.concat([
        high - low,
        (high - close.shift()).abs(),
        (low - close.shift()).abs(),
    ], axis=1).max(axis=1)
    plus_dm = (high - high.shift()).clip(lower=0).where(
        high - high.shift() > low.shift() - low, 0)
    minus_dm = (low.shift() - low).clip(lower=0).where(
        low.shift() - low > high - high.shift(), 0)
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs() / di_sum.replace(0, np.nan)
    dx = dx.fillna(0)
    adx = dx.ewm(span=period, adjust=False).mean()
    return adx, plus_di, minus_di


def score_at(ind: dict, idx: int, weights: dict, cf_score: int = 8) -> dict:
    """Score a single bar given pre-computed indicators.

    Args:
        ind: indicator dict from compute_indicators()
        idx: integer index into the Series (must be >= 60)
        weights: {"macd": w, "rsi": w, "ma": w, "capital_flow": w,
                  "volume_price": w, "support": w}
        cf_score: fixed capital flow score (no L2 data in backtest)

    Returns:
        {"total": int, "macd": int, "rsi": int, ..., "atr": float}
    """
    close = ind["close"]
    if idx < 60 or idx >= len(close):
        return {"total": 0, "atr": 0}

    c = float(close.iloc[idx])
    rsi_val = float(ind["rsi"].iloc[idx]) if not pd.isna(ind["rsi"].iloc[idx]) else 50
    macd_hist_val = float(ind["macd_hist"].iloc[idx]) if not pd.isna(ind["macd_hist"].iloc[idx]) else 0
    prev_hist = float(ind["macd_hist"].iloc[idx-1]) if not pd.isna(ind["macd_hist"].iloc[idx-1]) else 0
    dif = float(ind["macd_dif"].iloc[idx]) if not pd.isna(ind["macd_dif"].iloc[idx]) else 0
    dea = float(ind["macd_dea"].iloc[idx]) if not pd.isna(ind["macd_dea"].iloc[idx]) else 0

    mas = ind["mas"]
    ma5 = float(mas["ma5"].iloc[idx]) if not pd.isna(mas["ma5"].iloc[idx]) else c
    ma10 = float(mas["ma10"].iloc[idx]) if not pd.isna(mas["ma10"].iloc[idx]) else c
    ma20 = float(mas["ma20"].iloc[idx]) if not pd.isna(mas["ma20"].iloc[idx]) else c
    ma60 = float(mas["ma60"].iloc[idx]) if not pd.isna(mas["ma60"].iloc[idx]) else c

    vol = float(ind["volume"].iloc[idx]) if not pd.isna(ind["volume"].iloc[idx]) else 0
    vol_ma20 = float(ind["vol_ma20"].iloc[idx]) if not pd.isna(ind["vol_ma20"].iloc[idx]) else 1

    adx_val = 25.0
    if "adx" in ind and idx < len(ind["adx"]):
        _a = ind["adx"].iloc[idx]
        if not pd.isna(_a):
            adx_val = float(_a)
    choppy = adx_val < 20

    w_macd = weights.get("macd", 20)
    w_rsi = weights.get("rsi", 15)
    w_ma = weights.get("ma", 20)
    w_cf = weights.get("capital_flow", 20)
    w_vp = weights.get("volume_price", 15)
    w_sup = weights.get("support", 10)

    # ── MACD (scaled to w_macd) ──
    raw_macd = 10  # neutral out of 20
    if macd_hist_val > 0 and prev_hist <= 0:
        raw_macd = 20
    elif macd_hist_val > 0 and macd_hist_val > prev_hist:
        raw_macd = 18
    elif macd_hist_val > 0:
        raw_macd = 14
    elif macd_hist_val < 0 and prev_hist >= 0:
        raw_macd = 0
    elif macd_hist_val < 0 and macd_hist_val < prev_hist:
        raw_macd = 2
    elif macd_hist_val < 0:
        raw_macd = 6
    if dif > 0 and dea > 0:
        raw_macd = min(20, raw_macd + 2)
    if choppy:
        raw_macd = max(0, raw_macd - 5)
    macd_score = raw_macd * w_macd / 20

    # ── RSI (scaled to w_rsi) ──
    if 50 <= rsi_val <= 65:
        raw_rsi = 15
    elif 45 <= rsi_val < 50:
        raw_rsi = 12
    elif 30 <= rsi_val < 45:
        raw_rsi = 10
    elif 65 < rsi_val <= 75:
        raw_rsi = 10
    elif 75 < rsi_val <= 80:
        raw_rsi = 6
    elif rsi_val > 80:
        raw_rsi = 3
    else:
        raw_rsi = 5
    if choppy:
        raw_rsi = max(0, raw_rsi - 5)
    rsi_score = raw_rsi * w_rsi / 15

    # ── MA (scaled to w_ma) ──
    bullish_align = (ma5 > ma10 > ma20 > ma60)
    bearish_align = (ma5 < ma10 < ma20 < ma60)
    above_ma20 = c > ma20
    above_ma60 = c > ma60

    if bullish_align and above_ma20:
        raw_ma = 20
    elif above_ma20 and above_ma60 and ma5 > ma10:
        raw_ma = 16
    elif above_ma20 and above_ma60:
        raw_ma = 12
    elif above_ma60:
        raw_ma = 8
    elif bearish_align:
        raw_ma = 0
    else:
        raw_ma = 5
    ma_score = raw_ma * w_ma / 20

    # ── Capital Flow (fixed, scaled to w_cf) ──
    cf_scaled = cf_score * w_cf / 20

    # ── Volume-Price (scaled to w_vp) ──
    vol_ratio = vol / vol_ma20 if vol_ma20 > 0 else 1
    price_up = c > float(close.iloc[idx-1]) if idx >= 1 else False

    if price_up and vol_ratio > 1.5:
        raw_vp = 15
    elif price_up and vol_ratio > 1.0:
        raw_vp = 12
    elif price_up and vol_ratio < 0.8:
        raw_vp = 6
    elif not price_up and vol_ratio > 1.5:
        raw_vp = 2
    elif not price_up and vol_ratio < 0.8:
        raw_vp = 7
    else:
        raw_vp = 8
    vp_score = raw_vp * w_vp / 15

    # ── Support (scaled to w_sup) ──
    start_sup = max(0, idx - 30)
    low_30 = float(ind["low"].iloc[start_sup:idx+1].min())
    support = max(low_30, ma60)
    dist = (c - support) / c if c > 0 else 0

    if dist < 0.02:
        raw_sup = 10
    elif dist < 0.05:
        raw_sup = 8
    elif dist < 0.10:
        raw_sup = 5
    else:
        raw_sup = 3
    sup_score = raw_sup * w_sup / 10

    total = macd_score + rsi_score + ma_score + cf_scaled + vp_score + sup_score

    atr_val = float(ind["atr"].iloc[idx]) if not pd.isna(ind["atr"].iloc[idx]) else c * 0.02

    return {
        "total": round(total, 1),
        "macd": round(macd_score, 1),
        "rsi": round(rsi_score, 1),
        "ma": round(ma_score, 1),
        "capital_flow": round(cf_scaled, 1),
        "volume_price": round(vp_score, 1),
        "support": round(sup_score, 1),
        "atr": round(atr_val, 4),
        "adx": round(adx_val, 2),
        "bullish_di": _safe_float(ind, "plus_di", idx) > _safe_float(ind, "minus_di", idx),
        "close": c,
    }


def _safe_float(ind: dict, key: str, idx: int, default: float = 0) -> float:
    if key not in ind or idx >= len(ind[key]):
        return default
    v = ind[key].iloc[idx]
    return float(v) if not pd.isna(v) else default


# ══════════════════════════════════════════
# V2 Backtest Engine
# ══════════════════════════════════════════

@dataclass
class Position:
    code: str
    entry_price: float
    quantity: int
    entry_date: str
    entry_idx: int
    stop_loss: float
    take_profit: float
    hold_days: int = 0
    consecutive_low_score: int = 0
    entry_cost: float = 0.0


@dataclass
class TradeRecord:
    code: str
    entry_price: float
    exit_price: float
    quantity: int
    entry_date: str
    exit_date: str
    hold_days: int
    pnl: float
    pnl_pct: float
    commission: float
    exit_reason: str


def calc_hk_cost(price: float, quantity: int, cost_model: dict = None) -> float:
    """Calculate HK round-trip trading cost for one side (buy or sell)."""
    if cost_model is None:
        cost_model = DEFAULT_COST_MODEL
    turnover = price * quantity
    commission = max(
        turnover * cost_model["commission_rate"],
        cost_model["commission_min"],
    )
    stamp_duty = math.ceil(turnover * cost_model["stamp_duty_rate"])
    exchange_fee = turnover * cost_model["exchange_fee_rate"]
    settlement = turnover * cost_model["settlement_fee_rate"]
    settlement = max(settlement, cost_model["settlement_min"])
    settlement = min(settlement, cost_model["settlement_max"])
    return commission + stamp_duty + exchange_fee + settlement


class V2BacktestEngine:
    """Replay daily scores through entry/exit logic, track PnL."""

    def __init__(self, weights: dict, thresholds: dict,
                 initial_capital: float = INITIAL_CAPITAL,
                 cost_model: dict = None):
        self.weights = weights
        self.thresholds = thresholds
        self.capital = initial_capital
        self.initial_capital = initial_capital
        self.cost_model = cost_model or DEFAULT_COST_MODEL
        self.positions: dict[str, Position] = {}  # code → Position
        self.trades: list[TradeRecord] = []
        self.equity_curve: list[tuple[str, float]] = []  # (date, equity)
        self._cooldown: dict[str, str] = {}  # code → earliest reentry date
        self._daily_new_positions = 0

    def run(self, kline_data: dict[str, pd.DataFrame],
            start_date: str, end_date: str) -> dict:
        """Run backtest over date range.

        Args:
            kline_data: {code: DataFrame with [date,open,high,low,close,volume]}
            start_date: "YYYY-MM-DD" (inclusive)
            end_date: "YYYY-MM-DD" (inclusive)

        Returns:
            Performance metrics dict.
        """
        # Pre-compute indicators for all stocks
        indicators: dict[str, dict] = {}
        date_indices: dict[str, dict[str, int]] = {}  # code → {date: idx}

        for code, df in kline_data.items():
            if len(df) < 60:
                logger.warning(f"Skipping {code}: only {len(df)} bars (need 60)")
                continue
            indicators[code] = compute_indicators(df)
            date_indices[code] = {d: i for i, d in enumerate(df["date"])}

        # Build sorted list of all trading dates in range
        all_dates = set()
        for code, di in date_indices.items():
            all_dates.update(di.keys())
        trading_dates = sorted(d for d in all_dates if start_date <= d <= end_date)

        if not trading_dates:
            logger.warning("No trading dates in range")
            return self._compute_metrics()

        logger.info(f"Backtesting {start_date} → {end_date}: "
                    f"{len(trading_dates)} days, {len(indicators)} stocks")

        entry_thr = self.thresholds["entry_threshold"]
        exit_thr = self.thresholds["exit_threshold"]
        exit_consec = self.thresholds["exit_consecutive_days"]
        max_hold = self.thresholds["max_hold_days"]
        pos_pct = self.thresholds["position_pct"]
        sl_mult = self.thresholds["sl_atr_mult"]
        tp_mult = self.thresholds["tp_atr_mult"]
        adx_min = self.thresholds.get("adx_min", 20)
        rr_min = self.thresholds.get("rr_min", 1.5)
        trailing_atr = self.thresholds.get("trailing_atr", 0.0)
        require_bullish = self.thresholds.get("require_bullish_di", 1)
        cooldown_days = self.thresholds.get("cooldown_days", 5)

        for date in trading_dates:
            self._daily_new_positions = 0

            # 1) Check exits for existing positions
            codes_to_close = []
            for code, pos in list(self.positions.items()):
                if code not in indicators or date not in date_indices.get(code, {}):
                    continue
                idx = date_indices[code][date]
                ind = indicators[code]
                c = float(ind["close"].iloc[idx])
                h = float(ind["high"].iloc[idx])
                l = float(ind["low"].iloc[idx])

                pos.hold_days += 1

                # Trailing stop: ratchet SL up when price makes new highs
                if trailing_atr > 0 and c > pos.entry_price:
                    atr_now = float(ind["atr"].iloc[idx]) if not pd.isna(ind["atr"].iloc[idx]) else c * 0.02
                    trail_sl = h - atr_now * trailing_atr
                    if trail_sl > pos.stop_loss:
                        pos.stop_loss = round(trail_sl, 4)

                # SL hit (intraday low)
                if l <= pos.stop_loss:
                    codes_to_close.append((code, pos.stop_loss, "stop_loss", date))
                    continue

                # TP hit (intraday high)
                if h >= pos.take_profit:
                    codes_to_close.append((code, pos.take_profit, "take_profit", date))
                    continue

                # Max hold days
                if pos.hold_days >= max_hold:
                    codes_to_close.append((code, c, "max_hold", date))
                    continue

                # Score-based exit (close price)
                sc = score_at(ind, idx, self.weights)
                if sc["total"] < exit_thr:
                    pos.consecutive_low_score += 1
                else:
                    pos.consecutive_low_score = 0

                if pos.consecutive_low_score >= exit_consec:
                    codes_to_close.append((code, c, "score_exit", date))

            for code, exit_price, reason, d in codes_to_close:
                self._close_position(code, exit_price, reason, d)

            # 2) Check entries (max 1 new position per day)
            if self._daily_new_positions >= 1:
                self._record_equity(date, indicators, date_indices)
                continue

            # Total invested check
            invested = sum(
                p.entry_price * p.quantity for p in self.positions.values()
            )
            if invested >= self.initial_capital * 0.80:
                self._record_equity(date, indicators, date_indices)
                continue

            # Score all stocks, pick best
            candidates = []
            for code in indicators:
                if code in self.positions:
                    continue
                if code in self._cooldown and date < self._cooldown[code]:
                    continue
                if date not in date_indices.get(code, {}):
                    continue

                idx = date_indices[code][date]
                if idx < 60:
                    continue

                sc = score_at(indicators[code], idx, self.weights)
                if sc["total"] < entry_thr:
                    continue
                if sc.get("adx", 25) < adx_min:
                    continue
                if require_bullish and not sc.get("bullish_di", True):
                    continue
                candidates.append((code, sc, idx))

            # Sort by score descending
            candidates.sort(key=lambda x: x[1]["total"], reverse=True)

            for code, sc, idx in candidates:
                if self._daily_new_positions >= 1:
                    break

                c = sc["close"]
                atr = sc["atr"]
                lot = LOT_SIZES.get(code, DEFAULT_LOT)

                # Position sizing
                budget = self.capital * pos_pct
                quantity = int(budget / (c * lot)) * lot
                if quantity <= 0:
                    continue

                notional = c * quantity
                if notional < 30000:
                    continue

                # Entry cost
                buy_cost = calc_hk_cost(c, quantity, self.cost_model)
                if self.capital < notional + buy_cost:
                    continue

                sl = max(c - atr * sl_mult, c * 0.90)  # floor at -10%
                tp = c + atr * tp_mult

                # SL sanity: must be below entry
                if sl >= c:
                    sl = c * 0.95

                # Reward/risk filter: skip if upside/downside ratio too low
                risk = c - sl
                reward = tp - c
                if risk > 0 and rr_min > 0 and reward / risk < rr_min:
                    continue

                self.capital -= (notional + buy_cost)
                self.positions[code] = Position(
                    code=code, entry_price=c, quantity=quantity,
                    entry_date=date, entry_idx=idx,
                    stop_loss=round(sl, 4), take_profit=round(tp, 4),
                    entry_cost=buy_cost,
                )
                self._daily_new_positions += 1
                # Set cooldown
                dt = datetime.datetime.strptime(date, "%Y-%m-%d")
                self._cooldown[code] = (dt + datetime.timedelta(days=cooldown_days)).strftime("%Y-%m-%d")

            self._record_equity(date, indicators, date_indices)

        # Close remaining positions at last close
        last_date = trading_dates[-1]
        for code in list(self.positions.keys()):
            if code in indicators and last_date in date_indices.get(code, {}):
                idx = date_indices[code][last_date]
                c = float(indicators[code]["close"].iloc[idx])
                self._close_position(code, c, "backtest_end", last_date)

        return self._compute_metrics()

    def _close_position(self, code: str, exit_price: float,
                        reason: str, date: str):
        pos = self.positions.pop(code, None)
        if pos is None:
            return

        # Set cooldown on close too (especially after stop_loss)
        cooldown_days = self.thresholds.get("cooldown_days", 5)
        dt = datetime.datetime.strptime(date, "%Y-%m-%d")
        self._cooldown[code] = (dt + datetime.timedelta(days=cooldown_days)).strftime("%Y-%m-%d")

        sell_cost = calc_hk_cost(exit_price, pos.quantity, self.cost_model)
        gross_pnl = (exit_price - pos.entry_price) * pos.quantity
        total_cost = pos.entry_cost + sell_cost
        net_pnl = gross_pnl - total_cost
        pnl_pct = net_pnl / (pos.entry_price * pos.quantity) if pos.entry_price > 0 else 0

        self.capital += exit_price * pos.quantity - sell_cost
        self.trades.append(TradeRecord(
            code=code, entry_price=pos.entry_price, exit_price=exit_price,
            quantity=pos.quantity, entry_date=pos.entry_date, exit_date=date,
            hold_days=pos.hold_days, pnl=round(net_pnl, 2),
            pnl_pct=round(pnl_pct, 4), commission=round(total_cost, 2),
            exit_reason=reason,
        ))

    def _record_equity(self, date: str, indicators: dict, date_indices: dict):
        mktval = 0
        for code, pos in self.positions.items():
            if code in indicators and date in date_indices.get(code, {}):
                idx = date_indices[code][date]
                c = float(indicators[code]["close"].iloc[idx])
                mktval += c * pos.quantity
            else:
                mktval += pos.entry_price * pos.quantity
        self.equity_curve.append((date, self.capital + mktval))

    def _compute_metrics(self) -> dict:
        n = len(self.trades)
        if n == 0:
            return {
                "total_trades": 0, "win_rate": 0, "total_pnl": 0,
                "sharpe": 0, "max_drawdown": 0, "calmar": 0,
                "avg_hold_days": 0, "total_commission": 0,
                "profit_factor": 0, "avg_pnl_pct": 0,
                "final_equity": self.capital,
            }

        wins = sum(1 for t in self.trades if t.pnl > 0)
        total_pnl = sum(t.pnl for t in self.trades)
        total_comm = sum(t.commission for t in self.trades)
        gross_profit = sum(t.pnl for t in self.trades if t.pnl > 0)
        gross_loss = abs(sum(t.pnl for t in self.trades if t.pnl < 0))
        avg_hold = sum(t.hold_days for t in self.trades) / n
        avg_pnl_pct = sum(t.pnl_pct for t in self.trades) / n

        # Sharpe from equity curve
        sharpe = 0
        max_dd = 0
        if len(self.equity_curve) >= 2:
            equities = [e[1] for e in self.equity_curve]
            returns = pd.Series(equities).pct_change().dropna()
            if returns.std() > 0:
                sharpe = (returns.mean() / returns.std()) * (252 ** 0.5)

            # Max drawdown
            peak = equities[0]
            for eq in equities:
                peak = max(peak, eq)
                dd = (peak - eq) / peak
                max_dd = max(max_dd, dd)

        calmar = 0
        if max_dd > 0 and len(self.equity_curve) >= 2:
            total_return = (self.equity_curve[-1][1] / self.equity_curve[0][1]) - 1
            # Annualize
            days = len(self.equity_curve)
            ann_return = total_return * (252 / days) if days > 0 else 0
            calmar = ann_return / max_dd

        return {
            "total_trades": n,
            "win_rate": round(wins / n, 3),
            "total_pnl": round(total_pnl, 2),
            "total_commission": round(total_comm, 2),
            "sharpe": round(sharpe, 3),
            "max_drawdown": round(max_dd, 4),
            "calmar": round(calmar, 3),
            "avg_hold_days": round(avg_hold, 1),
            "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else 999,
            "avg_pnl_pct": round(avg_pnl_pct, 4),
            "final_equity": round(self.equity_curve[-1][1] if self.equity_curve else self.capital, 2),
        }


# ══════════════════════════════════════════
# Data Loading
# ══════════════════════════════════════════

def load_kline_data(codes: list[str] | None = None) -> dict[str, pd.DataFrame]:
    """Load daily klines from SQLite.

    Returns:
        {code: DataFrame[date, open, high, low, close, volume]}
    """
    conn = get_connection()
    if codes:
        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(
            f"SELECT * FROM daily_kline WHERE code IN ({placeholders}) ORDER BY code, date",
            codes,
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM daily_kline ORDER BY code, date"
        ).fetchall()
    conn.close()

    if not rows:
        logger.warning("No kline data in daily_kline table. Run kline_fetcher first.")
        return {}

    # Group by code
    data: dict[str, list] = {}
    for r in rows:
        code = r["code"]
        if code not in data:
            data[code] = []
        data[code].append({
            "date": r["date"],
            "open": r["open"],
            "high": r["high"],
            "low": r["low"],
            "close": r["close"],
            "volume": r["volume"],
        })

    result = {}
    for code, records in data.items():
        df = pd.DataFrame(records)
        df = df.sort_values("date").reset_index(drop=True)
        result[code] = df

    logger.info(f"Loaded kline data: {len(result)} stocks, "
                f"{sum(len(df) for df in result.values())} total bars")
    return result


def split_train_test(kline_data: dict[str, pd.DataFrame],
                     warmup_months: int = 3,
                     train_months: int = 4,
                     test_months: int = 2) -> tuple[str, str, str, str]:
    """Determine train/test date boundaries from the data.

    Layout: [warmup (3m) | train (4m) | test (2m)]
    Warmup is needed for indicator computation (60 bars ≈ 3 months).

    Returns:
        (train_start, train_end, test_start, test_end) as "YYYY-MM-DD"
    """
    all_dates = set()
    for df in kline_data.values():
        all_dates.update(df["date"].tolist())
    dates = sorted(all_dates)

    if len(dates) < 60:
        raise ValueError(f"Not enough data: {len(dates)} dates (need >= 60)")

    first = datetime.datetime.strptime(dates[0], "%Y-%m-%d")
    last = datetime.datetime.strptime(dates[-1], "%Y-%m-%d")

    warmup_end = first + datetime.timedelta(days=warmup_months * 30)
    train_start_dt = warmup_end + datetime.timedelta(days=1)
    train_end_dt = train_start_dt + datetime.timedelta(days=train_months * 30)
    test_start_dt = train_end_dt + datetime.timedelta(days=1)
    test_end_dt = min(test_start_dt + datetime.timedelta(days=test_months * 30), last)

    train_start = train_start_dt.strftime("%Y-%m-%d")
    train_end = train_end_dt.strftime("%Y-%m-%d")
    test_start = test_start_dt.strftime("%Y-%m-%d")
    test_end = test_end_dt.strftime("%Y-%m-%d")

    logger.info(f"Split: warmup {dates[0]}~{warmup_end.strftime('%Y-%m-%d')}, "
                f"train {train_start}~{train_end}, test {test_start}~{test_end}")

    return train_start, train_end, test_start, test_end


# ══════════════════════════════════════════
# Walk-Forward Optimization (anti-overfit)
# ══════════════════════════════════════════

def build_walk_forward_windows(kline_data: dict[str, pd.DataFrame],
                               warmup_days: int = 60,
                               train_days: int = 40,
                               test_days: int = 20,
                               ) -> list[tuple[str, str, str, str]]:
    """Build rolling walk-forward windows from the data.

    Layout per window: [warmup 60d | train 40d | test 20d]
    Each window slides forward by test_days, so test periods don't overlap.

    Returns:
        List of (train_start, train_end, test_start, test_end)
    """
    all_dates = set()
    for df in kline_data.values():
        all_dates.update(df["date"].tolist())
    dates = sorted(all_dates)

    if len(dates) < warmup_days + train_days + test_days:
        raise ValueError(
            f"Not enough data: {len(dates)} days, need "
            f"{warmup_days + train_days + test_days}"
        )

    windows = []
    offset = warmup_days  # skip warmup for indicator computation
    while offset + train_days + test_days <= len(dates):
        train_start = dates[offset]
        train_end = dates[offset + train_days - 1]
        test_start = dates[offset + train_days]
        test_end_idx = min(offset + train_days + test_days - 1, len(dates) - 1)
        test_end = dates[test_end_idx]
        windows.append((train_start, train_end, test_start, test_end))
        offset += test_days  # slide forward by test window size

    return windows


def _sample_params(trial) -> tuple[dict, dict]:
    """Sample weights + thresholds from an Optuna trial."""
    w_macd = trial.suggest_int("w_macd", 10, 30)
    w_rsi = trial.suggest_int("w_rsi", 5, 25)
    w_ma = trial.suggest_int("w_ma", 10, 30)
    w_cf = trial.suggest_int("w_cf", 5, 25)
    w_vp = trial.suggest_int("w_vp", 5, 25)
    w_sup = trial.suggest_int("w_sup", 3, 15)

    weights = {
        "macd": w_macd, "rsi": w_rsi, "ma": w_ma,
        "capital_flow": w_cf, "volume_price": w_vp, "support": w_sup,
    }

    entry_thr = trial.suggest_int("entry_threshold", 55, 85)
    exit_thr = trial.suggest_int("exit_threshold", 25, 55)
    exit_consec = trial.suggest_int("exit_consecutive_days", 1, 3)
    max_hold = trial.suggest_int("max_hold_days", 5, 20)
    pos_pct = trial.suggest_float("position_pct", 0.15, 0.25, step=0.05)
    sl_mult = trial.suggest_float("sl_atr_mult", 1.5, 3.0, step=0.25)
    tp_mult = trial.suggest_float("tp_atr_mult", 2.0, 5.0, step=0.5)
    adx_min = trial.suggest_int("adx_min", 15, 30)
    rr_min = trial.suggest_float("rr_min", 1.0, 3.0, step=0.25)
    trailing_atr = trial.suggest_float("trailing_atr", 0.0, 3.0, step=0.5)
    require_bullish = trial.suggest_int("require_bullish_di", 0, 1)
    cooldown_days = trial.suggest_int("cooldown_days", 3, 10)

    thresholds = {
        "entry_threshold": entry_thr,
        "exit_threshold": exit_thr,
        "exit_consecutive_days": exit_consec,
        "max_hold_days": max_hold,
        "position_pct": pos_pct,
        "sl_atr_mult": sl_mult,
        "tp_atr_mult": tp_mult,
        "adx_min": adx_min,
        "rr_min": rr_min,
        "trailing_atr": trailing_atr,
        "require_bullish_di": require_bullish,
        "cooldown_days": cooldown_days,
    }
    return weights, thresholds


def _score_window(kline_data, weights, thresholds,
                  train_start, train_end, test_start, test_end):
    """Run one walk-forward window, return (train_metrics, test_metrics)."""
    train_engine = V2BacktestEngine(weights, thresholds)
    train_m = train_engine.run(kline_data, train_start, train_end)
    test_engine = V2BacktestEngine(weights, thresholds)
    test_m = test_engine.run(kline_data, test_start, test_end)
    return train_m, test_m


def optimize(kline_data: dict[str, pd.DataFrame], n_trials: int = 300) -> dict:
    """Walk-forward Optuna optimization.

    Instead of optimizing on a single train period (which overfits),
    we run each trial across ALL walk-forward windows and score on
    the AVERAGE test performance. This forces params to generalize.

    Args:
        kline_data: pre-loaded kline data
        n_trials: number of Optuna trials

    Returns:
        Best parameters dict with per-window and aggregate metrics.
    """
    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    windows = build_walk_forward_windows(kline_data)
    n_windows = len(windows)
    logger.info(f"Walk-forward: {n_windows} windows")
    for i, (ts, te, vs, ve) in enumerate(windows):
        logger.info(f"  Window {i+1}: train {ts}~{te}, test {vs}~{ve}")

    if n_windows < 2:
        raise ValueError("Need at least 2 walk-forward windows. Add more data.")

    def objective(trial) -> float:
        weights, thresholds = _sample_params(trial)

        # Entry must be well above exit
        if thresholds["entry_threshold"] <= thresholds["exit_threshold"] + 10:
            return -999

        window_scores = []
        total_test_trades = 0

        for train_start, train_end, test_start, test_end in windows:
            train_m, test_m = _score_window(
                kline_data, weights, thresholds,
                train_start, train_end, test_start, test_end,
            )

            n_trades = test_m["total_trades"]
            total_test_trades += n_trades

            if n_trades == 0:
                window_scores.append(-2)
                continue

            sharpe = test_m["sharpe"]
            pf = min(test_m["profit_factor"], 5)
            dd = test_m["max_drawdown"]

            # Per-window score
            ws = sharpe + 0.3 * pf
            if dd > 0.15:
                ws -= (dd - 0.15) * 10
            if dd > 0.25:
                ws -= 50
            window_scores.append(ws)

        # Need trades across windows
        if total_test_trades < 5:
            return -999

        # Aggregate: mean - std (reward consistency)
        avg = np.mean(window_scores)
        std = np.std(window_scores)
        worst = min(window_scores)

        # Combined: average performance - volatility penalty - worst-case penalty
        score = avg - 0.5 * std
        if worst < -5:
            score += worst * 0.3  # penalize catastrophic windows

        return score

    study = optuna.create_study(
        direction="maximize",
        sampler=optuna.samplers.TPESampler(seed=42),
        study_name="v2_wf_opt",
    )
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best = study.best_params
    logger.info(f"Best trial: value={study.best_value:.3f}")
    logger.info(f"Best params: {json.dumps(best, indent=2)}")

    best_weights = {
        "macd": best["w_macd"], "rsi": best["w_rsi"], "ma": best["w_ma"],
        "capital_flow": best["w_cf"], "volume_price": best["w_vp"],
        "support": best["w_sup"],
    }
    best_thresholds = {
        "entry_threshold": best["entry_threshold"],
        "exit_threshold": best["exit_threshold"],
        "exit_consecutive_days": best["exit_consecutive_days"],
        "max_hold_days": best["max_hold_days"],
        "position_pct": best["position_pct"],
        "sl_atr_mult": best["sl_atr_mult"],
        "tp_atr_mult": best["tp_atr_mult"],
        "adx_min": best["adx_min"],
        "rr_min": best["rr_min"],
        "trailing_atr": best["trailing_atr"],
        "require_bullish_di": best["require_bullish_di"],
        "cooldown_days": best["cooldown_days"],
    }

    # Run best params on every window for detailed report
    window_results = []
    all_test_trades = []
    for i, (ts, te, vs, ve) in enumerate(windows):
        train_m, test_m = _score_window(
            kline_data, best_weights, best_thresholds, ts, te, vs, ve,
        )
        # Collect test trades for display
        test_engine = V2BacktestEngine(best_weights, best_thresholds)
        test_engine.run(kline_data, vs, ve)
        all_test_trades.extend(test_engine.trades)
        window_results.append({
            "window": i + 1,
            "train_period": f"{ts} ~ {te}",
            "test_period": f"{vs} ~ {ve}",
            "train": train_m,
            "test": test_m,
        })

    # Aggregate test metrics across windows
    agg_test = _aggregate_metrics([w["test"] for w in window_results])

    # Final out-of-sample: run on the ENTIRE date range
    all_dates = set()
    for df in kline_data.values():
        all_dates.update(df["date"].tolist())
    sorted_dates = sorted(all_dates)
    full_start = sorted_dates[60]  # skip warmup
    full_end = sorted_dates[-1]
    full_engine = V2BacktestEngine(best_weights, best_thresholds)
    full_metrics = full_engine.run(kline_data, full_start, full_end)

    result = {
        "best_weights": best_weights,
        "best_thresholds": best_thresholds,
        "window_results": window_results,
        "aggregate_test": agg_test,
        "full_period": f"{full_start} ~ {full_end}",
        "full_metrics": full_metrics,
        "full_trades": full_engine.trades,
        "n_trials": n_trials,
        "n_windows": n_windows,
        "best_objective_value": round(study.best_value, 3),
    }

    _save_param_version(result)
    return result


def _aggregate_metrics(metrics_list: list[dict]) -> dict:
    """Average metrics across walk-forward windows."""
    n = len(metrics_list)
    if n == 0:
        return {}
    keys = ["total_trades", "win_rate", "total_pnl", "total_commission",
            "sharpe", "max_drawdown", "profit_factor", "avg_pnl_pct"]
    agg = {}
    for k in keys:
        vals = [m.get(k, 0) for m in metrics_list]
        agg[k] = round(sum(vals) / n, 3)
        agg[f"{k}_std"] = round(np.std(vals), 3) if len(vals) > 1 else 0
    agg["total_trades"] = sum(m.get("total_trades", 0) for m in metrics_list)
    agg["total_pnl"] = round(sum(m.get("total_pnl", 0) for m in metrics_list), 2)
    agg["total_commission"] = round(
        sum(m.get("total_commission", 0) for m in metrics_list), 2)
    return agg


def _save_param_version(result: dict):
    """Save optimized params to param_versions table."""
    import sqlite3 as _sqlite3
    for _attempt in range(3):
        try:
            return _save_param_version_inner(result)
        except _sqlite3.OperationalError:
            time.sleep(2)
    logger.warning("Failed to save param_version after retries")


def _save_param_version_inner(result: dict):
    conn = get_connection()
    version = f"v2_wf_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}"
    config = {
        "weights": result["best_weights"],
        "thresholds": result["best_thresholds"],
    }
    full = result.get("full_metrics", {})
    agg = result.get("aggregate_test", {})

    conn.execute(
        """INSERT OR REPLACE INTO param_versions
           (version, created_at, config_json, trade_rules_json,
            optimization_score, train_sharpe, test_sharpe,
            train_win_rate, test_win_rate,
            train_max_dd, test_max_dd, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            version,
            datetime.datetime.now().isoformat(),
            json.dumps(config),
            json.dumps(result["best_thresholds"]),
            result["best_objective_value"],
            full.get("sharpe", 0),
            agg.get("sharpe", 0),
            full.get("win_rate", 0),
            agg.get("win_rate", 0),
            full.get("max_drawdown", 0),
            agg.get("max_drawdown", 0),
            json.dumps({
                "method": "walk_forward",
                "n_windows": result.get("n_windows", 0),
                "n_trials": result.get("n_trials", 0),
                "full_period": result.get("full_period", ""),
                "full_trades": full.get("total_trades", 0),
                "agg_test_trades": agg.get("total_trades", 0),
            }),
        ),
    )
    conn.commit()
    conn.close()
    logger.info(f"Saved param version: {version}")


# ══════════════════════════════════════════
# CLI
# ══════════════════════════════════════════

def print_metrics(label: str, m: dict):
    print(f"\n{'='*50}")
    print(f"  {label}")
    print(f"{'='*50}")
    print(f"  Trades:       {m['total_trades']}")
    print(f"  Win Rate:     {m['win_rate']:.1%}")
    print(f"  Total PnL:    {m['total_pnl']:+,.0f} HKD")
    print(f"  Commission:   {m['total_commission']:,.0f} HKD")
    print(f"  Sharpe:       {m['sharpe']:.3f}")
    print(f"  Max Drawdown: {m['max_drawdown']:.2%}")
    print(f"  Calmar:       {m['calmar']:.3f}")
    print(f"  Profit Factor:{m['profit_factor']:.2f}")
    print(f"  Avg Hold Days:{m['avg_hold_days']:.1f}")
    print(f"  Avg PnL%:     {m['avg_pnl_pct']:.2%}")
    print(f"  Final Equity: {m['final_equity']:,.0f} HKD")
    print(f"{'='*50}")


def print_trades(trades: list[TradeRecord]):
    if not trades:
        print("  (no trades)")
        return
    print(f"  {'Code':<10} {'Entry':>8} {'Exit':>8} {'Qty':>5} "
          f"{'PnL':>10} {'PnL%':>7} {'Days':>4} {'Reason':<12}")
    print(f"  {'-'*75}")
    for t in trades:
        print(f"  {t.code:<10} {t.entry_price:>8.2f} {t.exit_price:>8.2f} "
              f"{t.quantity:>5} {t.pnl:>+10,.0f} {t.pnl_pct:>+7.2%} "
              f"{t.hold_days:>4} {t.exit_reason:<12}")


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    parser = argparse.ArgumentParser(description="V2 Scoring Backtester")
    parser.add_argument("--optimize", action="store_true",
                        help="Run Optuna optimization")
    parser.add_argument("--trials", type=int, default=300,
                        help="Optuna trials (default 300)")
    parser.add_argument("--codes", type=str,
                        help="Comma-separated codes to backtest")
    parser.add_argument("--version", type=str, default="v2_default",
                        help="Param version name")
    args = parser.parse_args()

    init_db()

    codes = args.codes.split(",") if args.codes else None
    kline_data = load_kline_data(codes)

    if not kline_data:
        print("ERROR: No kline data. Run kline_fetcher first:")
        print("  poetry run python -m src.sim_trading.kline_fetcher")
        return

    print(f"\nLoaded {len(kline_data)} stocks:")
    for code, df in sorted(kline_data.items()):
        print(f"  {code}: {len(df)} bars ({df['date'].iloc[0]} ~ {df['date'].iloc[-1]})")

    if args.optimize:
        print(f"\nStarting Walk-Forward Optuna optimization ({args.trials} trials)...")
        result = optimize(kline_data, n_trials=args.trials)

        print("\n" + "="*60)
        print("  WALK-FORWARD OPTIMIZATION COMPLETE")
        print("="*60)
        print(f"\nBest Weights: {json.dumps(result['best_weights'], indent=2)}")
        print(f"\nBest Thresholds: {json.dumps(result['best_thresholds'], indent=2)}")
        print(f"\nObjective Score: {result['best_objective_value']}")
        print(f"Walk-Forward Windows: {result['n_windows']}")

        # Per-window results
        print(f"\n{'─'*80}")
        print(f"  {'Window':>6} │ {'Test Period':<25} │ {'Trades':>6} │ "
              f"{'PnL':>10} │ {'WinRate':>7} │ {'Sharpe':>7} │ {'MaxDD':>7}")
        print(f"{'─'*80}")
        sharpes = []
        for wr in result["window_results"]:
            t = wr["test"]
            print(f"  {wr['window']:>6} │ {wr['test_period']:<25} │ "
                  f"{t['total_trades']:>6} │ {t['total_pnl']:>+10,.0f} │ "
                  f"{t['win_rate']:>7.1%} │ {t['sharpe']:>7.3f} │ "
                  f"{t['max_drawdown']:>7.2%}")
            sharpes.append(t['sharpe'])
        print(f"{'─'*80}")

        # Consistency check
        positive_windows = sum(1 for s in sharpes if s > 0)
        print(f"\n  Consistency: {positive_windows}/{len(sharpes)} windows "
              f"with Sharpe > 0 "
              f"({'GOOD' if positive_windows >= len(sharpes) * 0.6 else 'WEAK'})")

        # Aggregate test metrics
        agg = result["aggregate_test"]
        print(f"\n  Aggregate OOS: {agg['total_trades']} trades, "
              f"PnL {agg['total_pnl']:+,.0f}, "
              f"avg Sharpe {agg['sharpe']:.3f} ± {agg.get('sharpe_std', 0):.3f}, "
              f"avg WinRate {agg['win_rate']:.1%}")

        # Full-period backtest
        print_metrics(f"FULL PERIOD ({result['full_period']})",
                      result["full_metrics"])
        print("\nAll Trades (full period):")
        print_trades(result["full_trades"])

    else:
        # Single run with default params
        train_start, train_end, test_start, test_end = split_train_test(kline_data)

        print(f"\nRunning backtest with default params...")
        print(f"  Train: {train_start} ~ {train_end}")
        print(f"  Test:  {test_start} ~ {test_end}")

        # Train
        train_engine = V2BacktestEngine(DEFAULT_WEIGHTS, DEFAULT_THRESHOLDS)
        train_metrics = train_engine.run(kline_data, train_start, train_end)
        print_metrics(f"TRAIN ({train_start} ~ {train_end})", train_metrics)
        print("\nTrain Trades:")
        print_trades(train_engine.trades)

        # Test
        test_engine = V2BacktestEngine(DEFAULT_WEIGHTS, DEFAULT_THRESHOLDS)
        test_metrics = test_engine.run(kline_data, test_start, test_end)
        print_metrics(f"TEST ({test_start} ~ {test_end})", test_metrics)
        print("\nTest Trades:")
        print_trades(test_engine.trades)


if __name__ == "__main__":
    main()
