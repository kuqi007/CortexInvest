"""Shared L2 signal direction inference."""

from __future__ import annotations


def infer_l2_signal_direction(signal: dict) -> str:
    """Infer L2 signal direction from strategy name and detail payload."""
    strategy = signal.get("strategy", "")
    detail = signal.get("detail", {}) or {}
    if "bearish" in strategy or "sell" in strategy:
        return "bearish"
    if "bullish" in strategy or strategy in ("momentum_alert", "volume_accel_alert"):
        return "bullish"
    bullish_strategies = {
        "macd_golden_cross",
        "ma_bullish_align",
        "volume_breakout",
        "breakout_pullback",
        "macd_bottom_divergence",
    }
    bearish_strategies = {
        "macd_death_cross",
        "ma_bearish_align",
        "macd_top_divergence",
        "support_breakdown",
        "rsi_extreme_overbought",
        "volume_divergence_top",
    }
    if strategy in bullish_strategies:
        return "bullish"
    if strategy in bearish_strategies:
        return "bearish"
    direction = detail.get("direction", "")
    if direction in ("bullish", "bearish"):
        return direction
    if direction == "BUY":
        return "bullish"
    if direction == "SELL":
        return "bearish"
    imbalance = detail.get("imbalance", detail.get("curr_imbalance", 0))
    if imbalance > 0:
        return "bullish"
    if imbalance < 0:
        return "bearish"
    dominant = detail.get("dominant_direction")
    if dominant:
        return dominant
    if strategy == "volume_price_divergence":
        return "bearish"
    return "neutral"
