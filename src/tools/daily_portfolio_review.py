#!/usr/bin/env python3
"""
Daily Portfolio Review — post-market portfolio analysis + tomorrow investment advice.

Workflow:
  1. GET /api/positions  → current holdings (symbol, name, cost, shares)
  2. GET /api/metrics   → today's closing data (price, change, volume, indicators)
  3. Generate tomorrow's investment advice per holding
  4. Send summary via Feishu

Usage:
    cd /Users/zhul1/Documents/aiWorkspace/ai-investor
    poetry run python src/tools/daily_portfolio_review.py

Environment:
    FEISHU_APP_ID, FEISHU_APP_SECRET, FEISHU_USER_OPEN_ID
    API_BASE_URL  (default: http://localhost:3120)
"""

from __future__ import annotations

import json
import os
import sys
import time
import requests
from datetime import datetime, timedelta
from pathlib import Path

# ── Project root & import path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.logging_config import setup_logger

logger = setup_logger("daily_portfolio_review")

# ── Config ──
API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:3120")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_USER_OPEN_ID = os.environ.get("FEISHU_USER_OPEN_ID", "ou_553029ec877f28bdf3217b38bef62c8f")

# ── Feishu helpers ──

_feishu_token: str | None = None
_feishu_token_expires_at: float = 0


def _feishu_get_token() -> str | None:
    global _feishu_token, _feishu_token_expires_at
    if _feishu_token and time.time() < _feishu_token_expires_at - 60:
        return _feishu_token

    url = "https://open.feishu.cn/open-apis/auth/v2/tenantAccessTokenInternal"
    try:
        resp = requests.post(
            url,
            json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            logger.warning(f"Feishu token failed: {data}")
            return None
        _feishu_token = data["tenant_access_token"]
        _feishu_token_expires_at = time.time() + data.get("expire", 7200)
        return _feishu_token
    except Exception as e:
        logger.warning(f"Feishu token request failed: {e}")
        return None


def _feishu_send(title: str, message: str) -> bool:
    """Send a Feishu text message."""
    token = _feishu_get_token()
    if not token:
        return False

    url = "https://open.feishu.cn/open-apis/im/v1/messages"
    params = {"receive_id_type": "open_id"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "receive_id": FEISHU_USER_OPEN_ID,
        "msg_type": "text",
        "content": json.dumps({"text": f"{title}\n\n{message}"}),
    }

    try:
        resp = requests.post(url, params=params, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        result = resp.json()
        if result.get("code") != 0:
            logger.warning(f"Feishu send failed: {result}")
            return False
        logger.info(f"Feishu sent: [{title}]")
        return True
    except Exception as e:
        logger.warning(f"Feishu send exception: {e}")
        return False


# ── API helpers ──

def _api_get(path: str, params: dict | None = None) -> dict | None:
    """GET {API_BASE_URL}/api/{path}, returns parsed JSON or None."""
    url = f"{API_BASE_URL}{path}"
    for attempt in range(3):
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning(f"API GET {path} attempt {attempt + 1} failed: {e}")
            if attempt < 2:
                time.sleep(1)
    return None


# ── Technical analysis ──

def _interpret_rsi(rsi: float) -> str:
    if rsi > 75:
        return "RSI严重超买"
    elif rsi > 65:
        return "RSI偏高"
    elif rsi < 25:
        return "RSI严重超卖"
    elif rsi < 35:
        return "RSI偏低"
    return "正常"


def _interpret_macd(dif: float, dea: float, hist: float) -> str:
    if hist > 0 and dif > dea:
        return "MACD多头"
    elif hist < 0 and dif < dea:
        return "MACD空头"
    elif abs(hist) < 0.01:
        return "MACD收敛"
    return "MACD中性"


def _interpret_ma(ma5: float, ma10: float, ma20: float, close: float) -> str:
    if close > ma5 > ma10 > ma20:
        return "均线多头排列"
    elif close < ma5 < ma10 < ma20:
        return "均线空头排列"
    elif close > ma5:
        return "价格站上MA5"
    elif close < ma5:
        return "价格跌破MA5"
    return "均线纠缠"


def _interpret_vol(vol_ratio: float) -> str:
    if vol_ratio > 2.0:
        return f"成交量放大({vol_ratio:.1f}x)"
    elif vol_ratio > 1.5:
        return f"成交量温和放大({vol_ratio:.1f}x)"
    elif vol_ratio < 0.5:
        return f"成交量萎缩({vol_ratio:.1f}x)"
    return f"成交量正常({vol_ratio:.1f}x)"


# ── Advice generation ──

def _generate_advice(
    holding: dict,
    metrics_entry: dict | None,
    indicators: dict | None,
) -> dict:
    """Generate per-stock advice dict."""

    symbol = holding["symbol"]
    name = holding.get("name", symbol)
    cost = holding.get("cost") or 0
    shares = holding.get("shares") or 0
    price = (metrics_entry.get("price") or 0) if metrics_entry else 0
    change_pct = (metrics_entry.get("change") or 0) if metrics_entry else 0
    volume = (metrics_entry.get("volume") or 0) if metrics_entry else 0

    pnl_pct = 0.0
    if cost > 0 and price > 0:
        pnl_pct = (price - cost) / cost * 100

    # Signals
    signals = []
    verdict = "观望"
    action = "持有观察"

    if indicators:
        rsi = indicators.get("rsi", 50)
        dif = indicators.get("dif", 0)
        dea = indicators.get("dea", 0)
        hist = indicators.get("hist", 0)
        ma5 = indicators.get("ma5", 0)
        ma10 = indicators.get("ma10", 0)
        ma20 = indicators.get("ma20", 0)
        vol_ratio = indicators.get("volume_ratio", 1.0)

        rsi_sig = _interpret_rsi(rsi)
        macd_sig = _interpret_macd(dif, dea, hist)
        ma_sig = _interpret_ma(ma5, ma10, ma20, price)
        vol_sig = _interpret_vol(vol_ratio)

        if rsi_sig:
            signals.append(rsi_sig)
        if macd_sig:
            signals.append(macd_sig)
        if ma_sig:
            signals.append(ma_sig)
        if vol_sig:
            signals.append(vol_sig)

        # Overall verdict
        bullish_count = sum(
            1 for s in signals
            if any(kw in s for kw in ["多头", "站上", "放大"])
        )
        bearish_count = sum(
            1 for s in signals
            if any(kw in s for kw in ["空头", "跌破", "超买", "萎缩", "超卖"])
        )

        if bullish_count > bearish_count and change_pct > 0:
            verdict = "偏多"
            action = "加仓/持有"
        elif bearish_count > bullish_count and change_pct < 0:
            verdict = "偏空"
            action = "减仓/止损"
        elif bullish_count == bearish_count:
            verdict = "中性"
            action = "观望"
        else:
            verdict = "中性"
            action = "持有"

    # Stop-loss check
    stop_loss_pct = -5.0
    risk_action = ""
    if pnl_pct <= -8:
        risk_action = "⚠️ 止损信号"
    elif pnl_pct <= -5:
        risk_action = "⚠️ 接近止损"

    advice = {
        "symbol": symbol,
        "name": name,
        "cost": cost,
        "shares": shares,
        "price": price,
        "change_pct": change_pct,
        "pnl_pct": round(pnl_pct, 2),
        "signals": signals,
        "verdict": verdict,
        "action": action,
        "risk_alert": risk_action,
    }

    return advice


# ── Main ──

def run_daily_review() -> bool:
    """Run the full daily portfolio review."""

    logger.info("=" * 40)
    logger.info("Starting daily portfolio review")
    logger.info("=" * 40)

    # 1. Get current holdings
    logger.info("Step 1: Fetching holdings via GET /api/positions")
    pos_data = _api_get("/api/positions")
    if not pos_data or not pos_data.get("holdings"):
        logger.error("No holdings found via /api/positions")
        _feishu_send("📊 每日持仓复盘", "⚠️ 无法获取持仓数据，请检查系统")
        return False

    holdings = pos_data["holdings"]
    logger.info(f"  Found {len(holdings)} holdings")

    # 2. Get today's metrics for all stocks
    logger.info("Step 2: Fetching today's market data via GET /api/metrics")
    metrics_data = _api_get("/api/metrics")
    services = metrics_data.get("services", []) if metrics_data else []
    # Build lookup: symbol -> service entry
    metrics_lookup = {s["id"]: s for s in services}

    # 3. Get technical indicators per holding
    logger.info("Step 3: Fetching technical indicators for each holding")
    advices = []
    for h in holdings:
        symbol = h["symbol"]
        entry = metrics_lookup.get(symbol)

        indicators = None
        try:
            ind_data = _api_get("/api/indicators", {"symbol": symbol, "live_price": entry.get("price", 0)})
            if ind_data and not ind_data.get("error"):
                indicators = ind_data
        except Exception as e:
            logger.warning(f"  Indicators fetch failed for {symbol}: {e}")

        advice = _generate_advice(h, entry, indicators)
        advices.append(advice)

        # Rate-limit protection between indicator API calls
        time.sleep(0.5)

    # 4. Build summary
    total_pnl_pct = sum(a["pnl_pct"] for a in advices)
    avg_pnl_pct = total_pnl_pct / len(advices) if advices else 0
    winners = [a for a in advices if a["pnl_pct"] > 0]
    losers = [a for a in advices if a["pnl_pct"] < 0]

    # 5. Build Feishu message
    lines = [
        f"📊 每日持仓复盘 — {datetime.now().strftime('%Y-%m-%d')}",
        f"━━━━━━━━━━━━━━━━━━━━",
        f"持仓数量: {len(advices)}  |  盈利: {len(winners)}  |  亏损: {len(losers)}",
        f"今日整体涨跌幅: {'+' if avg_pnl_pct >= 0 else ''}{avg_pnl_pct:.2f}%",
        f"",
    ]

    for a in advices:
        pct_str = f"+{a['pnl_pct']:.2f}%" if a["pnl_pct"] >= 0 else f"{a['pnl_pct']:.2f}%"
        chg_str = f"+{a['change_pct']:.2f}%" if a["change_pct"] >= 0 else f"{a['change_pct']:.2f}%"
        sign = " ".join(a["signals"][:2]) if a["signals"] else ""

        action_emoji = "🟢" if a["verdict"] == "偏多" else "🔴" if a["verdict"] == "偏空" else "⚪"
        lines.append(
            f"{action_emoji} {a['name']}({a['symbol']}) {a['price']} {chg_str}"
        )
        lines.append(f"   持仓盈亏: {pct_str}  |   verdict: {a['verdict']}  |  {a['action']}")
        if a["signals"]:
            lines.append(f"   信号: {sign}")
        if a["risk_alert"]:
            lines.append(f"   {a['risk_alert']}")
        lines.append("")

    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📌 明日操作建议:")
    for a in advices:
        if a["action"] != "观望":
            lines.append(f"  • {a['name']}: {a['action']} — {a['verdict']}")

    message = "\n".join(lines)

    # 6. Send Feishu
    logger.info("Step 4: Sending Feishu notification")
    title = f"📊 每日复盘 {datetime.now().strftime('%m/%d')} | {'+' if avg_pnl_pct >= 0 else ''}{avg_pnl_pct:.2f}%"
    ok = _feishu_send(title, message)

    if ok:
        logger.info("Daily portfolio review completed successfully")
    else:
        logger.error("Feishu notification failed")

    return ok


if __name__ == "__main__":
    ok = run_daily_review()
    sys.exit(0 if ok else 1)
