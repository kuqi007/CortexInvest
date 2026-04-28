#!/usr/bin/env python3
"""
Daily Summary Generator — post-market LLM-powered signal digest

Aggregates L2 strategy signals, alert events, and market data after market close,
then calls LLM to produce a structured daily report for the web dashboard.

Output: trading.db:daily_summaries

Usage:
    poetry run python -c "from src.tools.daily_summary_generator import generate_daily_summary; generate_daily_summary()"
"""

import json
import os
import sqlite3
import time
import requests
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from dotenv import load_dotenv

# ── Project root & import path ──
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Load .env at module level
load_dotenv(PROJECT_ROOT / ".env")
import sys

sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.llm_clients import LLMClientFactory
from src.utils.logging_config import setup_logger
from src.sim_trading.db import TRADING_DB_PATH

logger = setup_logger("daily_summary")

# ── Data file paths ──
DATA_DIR = PROJECT_ROOT / "src" / "data"
# Config read from DB (primary) or JSON (backup)
from src.utils.config_reader import read_monitor_config

CONFIG_DB_PATH = DATA_DIR / "config.db"
TRADING_DB_PATH = DATA_DIR / "trading.db"

# Rate limiting flag - set to True when API daily limit is reached
_morning_api_rate_limited = False


def _read_market_data_from_db() -> dict:
    """Read latest price snapshots from trading.db.

    Returns dict compatible with old market_data.json format:
    {"services": [{"id": code, "name": ..., "price": ..., "change": ...}]}
    """
    conn = None
    try:
        conn = sqlite3.connect(TRADING_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT code, name, price, change_pct, volume, amount FROM price_snapshots "
            "WHERE (code, ts) IN ("
            "  SELECT code, MAX(ts) FROM price_snapshots GROUP BY code"
            ")"
        ).fetchall()
        services = []
        for r in rows:
            services.append({
                "id": r["code"],
                "name": r["name"] or r["code"],
                "price": r["price"] or 0,
                "change": r["change_pct"] or 0,
                "volume": r["volume"] or 0,
                "amount": r["amount"] or 0,
            })
        return {"services": services}
    except Exception as e:
        logger.warning(f"读取 price_snapshots 失败: {e}")
        return {"services": []}
    finally:
        if conn:
            conn.close()


def _load_trade_plans_from_db() -> dict:
    """Read trade plans from trading.db in the legacy prompt shape."""
    conn = None
    try:
        conn = sqlite3.connect(TRADING_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, name, symbol, status, scope, created_at, orders_json
            FROM trade_plans
            ORDER BY id
            """
        ).fetchall()
    except Exception as e:
        logger.warning(f"读取 trade_plans 失败: {e}")
        return {"plans": {}}
    finally:
        if conn:
            conn.close()

    plans: dict[str, dict] = {}
    for row in rows:
        try:
            orders = json.loads(row["orders_json"] or "[]")
        except json.JSONDecodeError:
            logger.warning(f"trade_plans.orders_json 无效: {row['id']}")
            continue
        plans[row["id"]] = {
            "name": row["name"],
            "symbol": row["symbol"],
            "status": row["status"],
            "scope": row["scope"],
            "created_at": row["created_at"],
            "orders": orders if isinstance(orders, list) else [],
        }
    return {"plans": plans}


def _load_l2_signals_from_db(date_str: str) -> list[dict]:
    """Read L2 signals for one date from trading.db."""
    conn = None
    try:
        conn = sqlite3.connect(TRADING_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ts, date, time, strategy, code, direction, notify, detail, display
            FROM signals
            WHERE date = ?
            ORDER BY ts
            """,
            (date_str,),
        ).fetchall()
    except Exception as e:
        logger.warning(f"读取 L2 signals 失败: {e}")
        return []
    finally:
        if conn:
            conn.close()

    signals = []
    for row in rows:
        try:
            detail = json.loads(row["detail"] or "{}")
        except json.JSONDecodeError:
            detail = {}
        signals.append(
            {
                "ts": row["ts"],
                "date": row["date"],
                "time": row["time"],
                "strategy": row["strategy"],
                "code": row["code"],
                "direction": row["direction"],
                "notify": bool(row["notify"]),
                "detail": detail,
                "display": row["display"],
            }
        )
    return signals


def _read_alert_rules_from_db() -> dict:
    """Read alert rules from config.db.

    Returns dict compatible with old alert_config.json format:
    {"rules": [{"symbol": ..., "above": ..., "below": ...}]}
    """
    conn = None
    try:
        conn = sqlite3.connect(CONFIG_DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symbol, above, below FROM alert_rules"
        ).fetchall()
        rules = []
        for r in rows:
            rule: dict[str, Any] = {"symbol": r["symbol"]}
            if r["above"] is not None:
                rule["above"] = r["above"]
            if r["below"] is not None:
                rule["below"] = r["below"]
            rules.append(rule)
        return {"rules": rules}
    except Exception as e:
        logger.warning(f"读取 alert_rules 失败: {e}")
        return {"rules": []}
    finally:
        if conn:
            conn.close()


# ── Morning Briefing Functions ──


def _call_eastmoney_api(query: str) -> dict:
    """调用东方财富 API 获取市场数据或新闻.

    Returns:
        Empty dict if API key not set or rate limited.
    """
    # Try to get API key from env or ~/.eastmoney_api
    api_key = os.environ.get("EASTMONEY_APIKEY")
    if not api_key:
        api_file = os.path.expanduser("~/.eastmoney_api")
        if os.path.exists(api_file):
            with open(api_file) as f:
                api_key = f.read().strip()

    if not api_key:
        logger.warning("EASTMONEY_APIKEY not set, skipping API call")
        return {}

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/query"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key,
    }
    try:
        response = requests.post(
            url,
            json={"toolQuery": query},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()

        # Check for rate limit (status 113 = daily limit reached)
        if result.get("status") == 113:
            global _morning_api_rate_limited
            _morning_api_rate_limited = True
            logger.warning(f"API rate limit reached: {result.get('message')}")
            return {"_rate_limited": True}

        return result
    except Exception as e:
        logger.warning(f"Eastmoney API call failed for '{query}': {e}")
        return {}


def _call_news_search_api(query: str) -> dict:
    """调用东方财富新闻搜索 API.

    Returns:
        Empty dict if API key not set or error.
    """
    api_key = os.environ.get("EASTMONEY_APIKEY")
    if not api_key:
        api_file = os.path.expanduser("~/.eastmoney_api")
        if os.path.exists(api_file):
            with open(api_file) as f:
                api_key = f.read().strip()

    if not api_key:
        logger.warning("EASTMONEY_APIKEY not set, skipping news search")
        return {}

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/news-search"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key,
    }
    try:
        response = requests.post(
            url,
            json={"query": query},
            headers=headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.warning(f"News search API call failed for '{query}': {e}")
        return {}


def _fetch_us_markets() -> dict:
    """获取美股走势 (通过新闻搜索)."""
    markets = {}

    # Get today's date for fresh news
    today = datetime.now()
    date_str = today.strftime("%Y年%m月%d日")

    # Search for overnight US market news with date
    data = _call_news_search_api(f"{date_str} 隔夜美股 道琼斯 纳斯达克 标普500")
    try:
        # Parse news to extract market movements
        # Look for headlines mentioning specific indices and their changes
        inner_data = (
            (data.get("data") or {}).get("data", {})
            if isinstance(data.get("data"), dict)
            else {}
        )
        llm_response = inner_data.get("llmSearchResponse", {})
        news_list = llm_response.get("data", [])

        # Extract market info from news headlines
        for item in news_list[:5]:
            title = item.get("title", "")
            content = item.get("content", "")

            # Try to find Dow Jones, Nasdaq, S&P500 mentions
            # Look for patterns like "道琼斯涨X%" or "纳斯达克跌X%"
            import re

            dow_match = re.search(r"道琼斯.*?([+-]?\d+\.?\d*)%", content)
            nasdaq_match = re.search(r"纳斯达克.*?([+-]?\d+\.?\d*)%", content)
            sp_match = re.search(r"标普.*?([+-]?\d+\.?\d*)%", content)

            if dow_match and "dow" not in markets:
                markets["dow"] = {
                    "name": "道琼斯",
                    "change_pct": round(float(dow_match.group(1)), 2),
                }
            if nasdaq_match and "nasdaq" not in markets:
                markets["nasdaq"] = {
                    "name": "纳斯达克",
                    "change_pct": round(float(nasdaq_match.group(1)), 2),
                }
            if sp_match and "sp500" not in markets:
                markets["sp500"] = {
                    "name": "标普500",
                    "change_pct": round(float(sp_match.group(1)), 2),
                }

    except Exception as e:
        logger.warning(f"Failed to parse US markets from news: {e}")

    return markets


def _fetch_asia_markets() -> dict:
    """获取韩/日股市走势 (通过新闻搜索)."""
    markets = {}

    # Get today's date for fresh news
    today = datetime.now()
    date_str = today.strftime("%Y年%m月%d日")

    # Search for Japan and Korea market news with date
    data = _call_news_search_api(f"{date_str} 日本股市 日经225 韩国KOSPI 今日收盘")
    try:
        import re

        inner_data = (
            (data.get("data") or {}).get("data", {})
            if isinstance(data.get("data"), dict)
            else {}
        )
        llm_response = inner_data.get("llmSearchResponse", {})
        news_list = llm_response.get("data", [])

        for item in news_list[:5]:
            content = item.get("content", "")

            # Look for Nikkei (日经) and KOSPI (韩国/综合) mentions
            nikkei_match = re.search(r"日经.*?([+-]?\d+\.?\d*)%", content)
            kospi_match = re.search(r"韩国.*?([+-]?\d+\.?\d*)%", content)

            if nikkei_match and "nikkei" not in markets:
                markets["nikkei"] = {
                    "name": "日经225",
                    "change_pct": round(float(nikkei_match.group(1)), 2),
                }
            if kospi_match and "kospi" not in markets:
                markets["kospi"] = {
                    "name": "韩国KOSPI",
                    "change_pct": round(float(kospi_match.group(1)), 2),
                }

    except Exception as e:
        logger.warning(f"Failed to parse Asia markets from news: {e}")

    return markets


def _search_global_news() -> list[dict]:
    """搜索国际局势新闻 (使用 news-search API)."""
    # Get today's date for fresh news
    today = datetime.now()
    date_str = today.strftime("%Y年%m月%d日")

    news_items = []
    keywords = [
        f"{date_str} 美联储议息",
        f"{date_str} 国际局势",
        f"{date_str} 隔夜美股",
    ]

    for keyword in keywords:
        time.sleep(1.5)  # Rate limit protection
        data = _call_news_search_api(keyword)
        try:
            # Parse news-search response format
            # data.data.data.llmSearchResponse.data[]
            inner_data = (
                (data.get("data") or {}).get("data", {})
                if isinstance(data.get("data"), dict)
                else {}
            )
            llm_response = inner_data.get("llmSearchResponse", {})
            news_list = llm_response.get("data", [])

            for item in news_list[:3]:
                title = item.get("title", "")
                content = item.get("content", "")
                if title and len(title) > 10:
                    # Extract first 200 chars of content as summary
                    summary = content[:200] + "..." if len(content) > 200 else content
                    news_items.append(
                        {
                            "title": title[:100],
                            "summary": summary,
                            "source": item.get("source", "东方财富"),
                            "time": item.get("date", ""),
                            "url": item.get("jumpUrl", ""),
                        }
                    )
        except (KeyError, TypeError) as e:
            logger.warning(f"Failed to parse news for '{keyword}': {e}")

    # Deduplicate
    seen = set()
    unique_news = []
    for item in news_items:
        if item["title"] not in seen:
            seen.add(item["title"])
            unique_news.append(item)
            if len(unique_news) >= 6:
                break

    return unique_news


def _read_morning_briefing() -> dict | None:
    """Read morning briefing from trading.db."""
    today_str = datetime.now().strftime("%Y-%m-%d")
    conn = None
    try:
        conn = sqlite3.connect(TRADING_DB_PATH)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT content_json FROM morning_briefings WHERE date = ?",
            (today_str,),
        ).fetchone()
        if row:
            return json.loads(row["content_json"])
    except Exception:
        pass
    finally:
        if conn:
            conn.close()
    return None


def generate_morning_briefing() -> dict | None:
    """Generate morning briefing with overnight US/Asia market movements.

    Output: trading.db:morning_briefings
    Skips generation if already generated today.

    Returns:
        The generated briefing dict, or None on failure/already exists.
    """
    today_str = datetime.now().strftime("%Y-%m-%d")
    existing = _read_morning_briefing()
    if existing and existing.get("generated_at", "").startswith(today_str):
        logger.info(
            f"Morning briefing already generated today ({today_str}), skipping"
        )
        return existing
    logger.info("Generating morning briefing...")

    try:
        # 1. 获取美股走势
        us_markets = _fetch_us_markets()

        # 2. 获取韩/日股市
        asia_markets = _fetch_asia_markets()

        # 3. 搜索国际局势新闻
        global_news = _search_global_news()

        # 4. 构建 briefing
        briefing = {
            "generated_at": datetime.now().isoformat(),
            "us_markets": us_markets,
            "asia_markets": asia_markets,
            "global_news": global_news,
        }

        # 5. 写入 DB
        conn = None
        try:
            conn = sqlite3.connect(TRADING_DB_PATH)
            conn.execute(
                "INSERT OR REPLACE INTO morning_briefings (date, generated_at, content_json) VALUES (?, ?, ?)",
                (today_str, briefing["generated_at"], json.dumps(briefing, ensure_ascii=False)),
            )
            conn.commit()
            logger.info("Morning briefing written to trading.db:morning_briefings")
        except Exception as e:
            logger.error(f"Morning briefing write to DB failed: {e}")
        finally:
            if conn:
                conn.close()
        return briefing

    except Exception as e:
        logger.error(f"Morning briefing generation failed: {e}")
        return None


def _detect_market(signals: list[dict], watchlist: dict) -> str:
    """Detect primary market from signal source, fallback to watchlist."""
    if signals:
        hk_sigs = sum(1 for s in signals if s.get("code", "").startswith("HK"))
        return "HK" if hk_sigs > len(signals) // 2 else "A"
    hk_count = sum(1 for k in watchlist if k.startswith("HK"))
    return "HK" if hk_count >= len(watchlist) - hk_count else "A"


def _aggregate_signals(signals: list[dict]) -> dict:
    """Aggregate L2 signals by stock code.

    Returns: {code: {strategies: Counter, directions: [], displays: [], count: int}}
    """
    by_stock: dict = defaultdict(
        lambda: {
            "strategies": Counter(),
            "directions": [],
            "displays": [],
            "count": 0,
            "notify_count": 0,
        }
    )

    for s in signals:
        code = s.get("code", "")
        if not code:
            continue
        entry = by_stock[code]
        entry["count"] += 1
        strategy = s.get("strategy", "unknown")
        entry["strategies"][strategy] += 1
        if s.get("notify"):
            entry["notify_count"] += 1
        # Collect display text for key signals (L1 / notify=True)
        if s.get("notify") and s.get("display"):
            entry["displays"].append(s["display"])

    return dict(by_stock)


def _aggregate_alerts(events: list[dict]) -> dict:
    """Aggregate non-L2 alert events by stock code.

    L2 signals are already counted from trading.db:signals,
    so we exclude kind=l2_strategy to avoid double counting.

    Returns: {code: {kinds: Counter, count: int}}
    """
    by_stock: dict = defaultdict(lambda: {"kinds": Counter(), "count": 0})
    for e in events:
        code = e.get("symbol", "")
        if not code:
            continue
        # Skip L2 signals — already aggregated from trading.db:signals
        if e.get("kind") == "l2_strategy":
            continue
        by_stock[code]["count"] += 1
        by_stock[code]["kinds"][e.get("kind", "unknown")] += 1
    return dict(by_stock)


def _build_per_stock(
    market_data: dict,
    config: dict,
    signal_agg: dict,
    alert_agg: dict,
    l2_digest_map: dict | None = None,
) -> list[dict]:
    """Build per-stock summary combining market data + signals + alerts."""
    watchlist = config.get("watchlist", {})
    services = {s["id"]: s for s in market_data.get("services", []) if s.get("id")}

    # HKD/CNY 汇率固定值（汇率功能已移除）
    hkd_cny_rate = 0.92

    per_stock = []
    # Combine all codes from watchlist, signals, and market data
    all_codes = set(watchlist.keys()) | set(signal_agg.keys())

    for code in sorted(all_codes):
        svc = services.get(code, {})
        entry = watchlist.get(code, {})
        sig = signal_agg.get(code, {})
        alrt = alert_agg.get(code, {})

        name = entry.get("name") or svc.get("name", code)
        change = svc.get("change", 0)
        price = svc.get("price", 0)
        signal_count = sig.get("count", 0)
        alert_count = alrt.get("count", 0)

        is_star = entry.get("star", False)
        if signal_count == 0 and alert_count == 0 and not is_star:
            continue

        # Determine direction: prefer l2_digest direction_score, fallback to signal counts
        strategies = sig.get("strategies", Counter())
        digest = (l2_digest_map or {}).get(code, {})
        ds = digest.get("direction_score", 0)
        if ds > 2:
            direction = "偏多"
        elif ds < -2:
            direction = "偏空"
        elif ds != 0:
            # Weak signal from digest
            direction = "偏多" if ds > 0 else "偏空" if ds < 0 else "中性"
        else:
            # Fallback: composite signal counts
            bullish = strategies.get("composite_bullish", 0)
            bearish = strategies.get("composite_bearish", 0)
            if bullish > bearish:
                direction = "偏多"
            elif bearish > bullish:
                direction = "偏空"
            elif bullish > 0 and bearish > 0:
                direction = "多空交织"
            else:
                direction = "中性"

        # Top signal types
        key_signals = []
        for strat, cnt in strategies.most_common(5):
            # Translate strategy names to readable labels
            label_map = {
                "composite_bullish": "多头信号",
                "composite_bearish": "空头信号",
                "large_order": "大单成交",
                "tick_imbalance": "主买卖失衡",
                "volume_price_divergence": "量价背离",
                "momentum_alert": "动量确认",
                "momentum_buy": "动量买入",
                "momentum_sell": "动量卖出",
                "momentum_buy_alert": "动量确认",
                "momentum_sell_alert": "动量卖出确认",
                "volume_accel_alert": "放量加速",
                "volume_crash_alert": "放量砸盘",
                "sustained_buying": "主买持续",
                "sustained_selling": "主卖持续",
                "retail_institutional_divergence": "散户机构分歧",
                "institutional_retail_divergence": "散户机构分歧",
                "large_order_flip": "大单翻转",
                "large_order_reversal": "大单翻转",
                "late_session_activity": "尾盘异动",
                "rsi_extreme": "RSI极值",
                "rsi_overbought": "RSI超买",
                "rsi_oversold": "RSI超卖",
                "macd_cross": "MACD交叉",  # legacy fallback
                "macd_golden_cross": "MACD金叉",
                "macd_death_cross": "MACD死叉",
                "macd_top_divergence": "MACD顶背离",
                "macd_bottom_divergence": "MACD底背离",
                "ma_alignment": "均线信号",
                "ma_bullish_align": "均线多排",
                "ma_bearish_align": "均线空排",
                "bollinger_breakout": "布林突破",
                "adx_trend_start": "趋势启动",
                "volume_breakout": "放量突破",
                "candlestick_pattern": "K线形态",
                "engulfing_pattern": "吞没形态",
                "key_level_test": "关键位置",
                "breakout_pullback": "突破回踩",
                "relative_strength": "相对强弱",
                "order_book_imbalance": "盘口失衡",
                "capital_flow_spike": "资金异动",
                "support_breakdown": "破位下跌",
                "morning_evening_star": "晨暮星",
                "volume_divergence_top": "缩量创高",
                "rsi_extreme_overbought": "RSI严重超买",
                "rsi_extreme_oversold": "RSI严重超卖",
                "tick_persistence": "tick持续偏向",
                "closing_surge": "尾盘异动",
                "momentum_sell_alert": "动量卖出确认",
                "volume_accel_sell_alert": "放量砸盘确认",
                "bollinger_squeeze_breakout": "布林带突破",
            }
            label = label_map.get(strat, strat)
            key_signals.append(f"{label}x{cnt}" if cnt > 1 else label)

        # Include alert kinds
        for kind, cnt in alrt.get("kinds", Counter()).most_common(3):
            kind_label = {"big_move": "大幅异动", "threshold": "触价告警"}.get(
                kind, kind
            )
            if cnt > 1:
                key_signals.append(f"{kind_label}x{cnt}")
            elif kind_label not in key_signals:
                key_signals.append(kind_label)

        # Position size (market value in CNY)
        cost = entry.get("cost", 0) or 0
        shares = entry.get("shares", 0) or 0
        is_hk = code.startswith("HK")
        fx = hkd_cny_rate if is_hk else 1
        mkt_val = price * shares * fx if price > 0 and shares > 0 else 0
        pnl_pct = ((price - cost) / cost * 100) if cost > 0 and price > 0 else None

        per_stock.append(
            {
                "code": code,
                "name": name,
                "type": entry.get("type", "watching"),
                "star": entry.get("star", False),
                "price": price,
                "change": round(change, 2),
                "cost": cost,
                "shares": shares,
                "mkt_val": round(mkt_val, 0),
                "pnl_pct": round(pnl_pct, 1) if pnl_pct is not None else None,
                "fx": fx,
                "signalCount": signal_count,
                "alertCount": alert_count,
                "direction": direction,
                "keySignals": key_signals[:6],
            }
        )

    # Sort: star first, then holdings by market value desc, then watching by signal count
    per_stock.sort(
        key=lambda x: (
            0 if x.get("star") else 1,
            0 if x["type"] == "holding" else 1,
            -x["mkt_val"],
            -x["signalCount"],
        )
    )
    return per_stock


def _build_stats(
    signals: list[dict],
    events: list[dict],
    per_stock: list[dict],
) -> dict:
    """Build overall stats summary."""
    total_signals = len(signals)
    l1_count = sum(1 for s in signals if s.get("notify"))

    # Count bullish/bearish composite signals
    bullish = sum(1 for s in signals if s.get("strategy") == "composite_bullish")
    bearish = sum(1 for s in signals if s.get("strategy") == "composite_bearish")

    stock_count = len(per_stock)
    up_count = sum(1 for ps in per_stock if ps["change"] > 0)
    down_count = sum(1 for ps in per_stock if ps["change"] < 0)

    return {
        "totalSignals": total_signals,
        "totalAlerts": len(events),
        "l1Count": l1_count,
        "bullish": bullish,
        "bearish": bearish,
        "stockCount": stock_count,
        "upCount": up_count,
        "downCount": down_count,
    }


def _build_llm_prompt(
    stats: dict,
    per_stock: list[dict],
    l1_displays: list[str],
    l2_digest_map: dict | None = None,
    trade_plans: dict | None = None,
    include_morning_briefing: bool = True,
) -> list[dict]:
    """Construct messages for LLM daily report generation."""
    system = """你是一位资深量化工程师，专注 A 股和港股。根据今日 L2 策略信号、微观结构数据和告警数据，生成简洁的持仓信号日报。

格式要求（严格遵守，不要偏离）：

## 市场整体评估
（3-5句：今日多空氛围、资金面整体方向、板块分化。用具体数字描述，例如"持仓中X只大单净买入、X只tick偏买超过20%"、"整体主力资金净流入约X亿"等）

## 重点关注
（优先选★重点标记和持仓市值大的标的，每只写2-3句深度分析。★重点股必须出现在此节）

1. **代码 名称** — ...
   在分析中自然引用微观数据，例如：
   "今日大单净买入0.19亿，tick偏买12.4%，主力资金净流入7.57亿，三维共振偏多。但大单翻转3次提示盘中多空拉锯激烈。"
   如果某只股票没有提供微观数据，写"无微观数据"，绝不可编造。
   重点解读：
   - 多维度矛盾（大单方向 vs tick方向 vs 资金流向不一致时，分析原因）
   - tick偏买/偏卖超过20%的异常值
   - 量价背离频次高（≥3次）的风险信号
   - 综合方向评分极端值（>+4 或 <-4）

## 逐股速览
- **代码 名称** — 一句话总结，自然穿插1-2个关键数据（如"tick偏卖28%+主力流出0.51亿，空头格局"）
（重要：只覆盖有微观数据的标的。如果某股票在"持仓标的"或"重点自选"中没有微观数据行，则**绝对不能**出现在逐股速览中，也不可编造数据填充。如果用户消息末尾标注"今日无数据"，则整节替换为"今日无微观数据"一句话即可）

## 操作建议
1. ...
（2-3条建议，每条必须基于具体微观数据得出，不要泛泛而谈）

写作规则：
- 标题用 ## 不用 ###，标题上不要加 **加粗**
- 正文中股票名称可以用 **加粗**
- **严禁模糊表述**：不可写"资金流入显著"、"大单活跃"等空话，必须写"大单净买0.19亿"、"tick偏买12.4%"这样的具体数字
- **严禁编造数据**：只能使用本文中提供的具体数据，绝不可为没有数据的标的编造tick、大单、资金流等数字。如果某只股票标注了"无微观数据"，必须写"无微观数据"，绝不可自己捏造任何具体数字（如tick偏买12%、大单净买0.19亿等都是禁止的）
- 数据要像专业研报一样自然融入文字中，不要堆砌成表格
- 当大单方向与资金流向矛盾时（如大单净卖但主力净流入），需解读原因（算法拆单、对倒等）
- 标注[★重点]的股票（包括持仓和自选）是用户最关注的，必须在"重点关注"中详细分析
- 持仓市值大的股票对组合影响大，也应优先关注
- 持仓标的标签含成本和盈亏%，据此给出是否加仓/减仓/止损建议
- 如有条件单数据，在操作建议中结合条件单距离给出提醒（如"阿里距买入条件单125仅3.5%"）
- ★重点自选标的同样需要深度分析微观数据，不能只给一句话
- 风格：专业简洁，像给基金经理写的晨会纪要
- **输出格式**: 你必须输出 JSON，report 字段包含完整的 Markdown 报告
- **数据来源约束**：每只标的的"微观:"行列出了该标的的全部微观数据。如果该行写"无微观数据"，则该标的不能出现任何tick、大单、主力资金流的具体数字。如果你在文中写了任何tick偏买/偏卖X%、大单净买/卖X亿、主力流入/流出X亿的数字，该数字必须能在对应标的的"微观:"行中找到完全一致的值。违规即视为严重错误"""

    # Build data section
    holdings = [ps for ps in per_stock if ps["type"] == "holding"]
    watching = [ps for ps in per_stock if ps["type"] != "holding"]

    lines = []

    # ── Morning briefing (隔夜外盘背景) ──
    if include_morning_briefing:
        morning = _read_morning_briefing()
        if morning:
            us = morning.get("us_markets", {})
            asia = morning.get("asia_markets", {})
            news = morning.get("global_news", [])

            us_lines = []
            for key, name in [
                ("dow", "道琼斯"),
                ("nasdaq", "纳斯达克"),
                ("sp500", "标普500"),
            ]:
                m = us.get(key, {})
                if m.get("change_pct"):
                    us_lines.append(f"{name} {m['change_pct']:+.2f}%")

            asia_lines = []
            for key, name in [("nikkei", "日经"), ("kospi", "KOSPI")]:
                m = asia.get(key, {})
                if m.get("change_pct"):
                    asia_lines.append(f"{name} {m['change_pct']:+.2f}%")

            if us_lines:
                lines.append("## 隔夜外盘")
                lines.append(f"- 美股: {', '.join(us_lines)}")
            if asia_lines:
                lines.append(f"- 亚太: {', '.join(asia_lines)}")
            if news:
                lines.append("## 国际要闻")
                for item in news[:3]:
                    lines.append(f"- {item.get('title', '')[:60]}")
            lines.append("")

    # ── Fix 5: Portfolio summary ──
    if holdings:
        # Only include holdings with valid cost for P&L calculation
        costed = [ps for ps in holdings if ps["cost"] > 0 and ps["shares"] > 0]
        total_mkt = sum(ps["mkt_val"] for ps in costed)
        total_cost = sum(ps["cost"] * ps["shares"] * ps.get("fx", 1) for ps in costed)
        total_pnl = total_mkt - total_cost if total_cost > 0 else 0
        total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
        hk_day = sum(
            ps["change"] / 100 * ps["mkt_val"]
            for ps in holdings
            if ps["code"].startswith("HK") and ps["mkt_val"] > 0
        )
        a_day = sum(
            ps["change"] / 100 * ps["mkt_val"]
            for ps in holdings
            if not ps["code"].startswith("HK") and ps["mkt_val"] > 0
        )
        h_up = sum(1 for ps in holdings if ps["change"] > 0)
        h_down = sum(1 for ps in holdings if ps["change"] < 0)
        lines.append("## 组合概况")
        lines.append(
            f"- 持仓 {len(holdings)} 只, 总市值 {total_mkt / 10000:.1f}万, 总浮盈 {total_pnl / 10000:+.1f}万 ({total_pnl_pct:+.1f}%)"
        )
        day_parts = []
        if any(ps["code"].startswith("HK") for ps in holdings):
            day_parts.append(f"港股 {hk_day / 10000:+.1f}万")
        if any(not ps["code"].startswith("HK") for ps in holdings):
            day_parts.append(f"A股 {a_day / 10000:+.1f}万")
        if day_parts:
            lines.append(f"- 今日变化: {', '.join(day_parts)}")
        lines.append(f"- 涨: {h_up}只 跌: {h_down}只")
        lines.append("")

    lines.append(f"## 今日统计")
    lines.append(f"- 总信号数: {stats['totalSignals']} | L1高优: {stats['l1Count']}")
    lines.append(f"- 多头信号: {stats['bullish']} | 空头信号: {stats['bearish']}")
    lines.append(
        f"- 监控标的: {stats['stockCount']}只 | 收涨: {stats['upCount']} 收跌: {stats['downCount']}"
    )
    lines.append(f"- 告警事件: {stats['totalAlerts']}")
    lines.append("")

    digest_map = l2_digest_map or {}

    if holdings:
        lines.append("## 持仓标的")
        for ps in holdings:
            sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else "无信号"
            # Star + position size + cost/pnl tags
            tags = []
            if ps.get("star"):
                tags.append("★重点")
            if ps["mkt_val"] > 0:
                val_wan = ps["mkt_val"] / 10000
                tags.append(f"持仓{val_wan:.1f}万")
            if ps.get("cost") and ps["cost"] > 0:
                tags.append(f"成本{ps['cost']:.2f}")
            if ps.get("pnl_pct") is not None:
                tags.append(f"盈亏{ps['pnl_pct']:+.1f}%")
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            sentiment_tag = ""
            if ps.get("sentiment") is not None:
                s = ps["sentiment"]
                sentiment_tag = f" | 新闻情感:{s:+.2f}"
            line = f"- {ps['code']} {ps['name']}{tag_str} | 涨跌:{ps['change']:+.2f}%{sentiment_tag} | 信号:{ps['signalCount']}条 | 方向:{ps['direction']} | {sigs}"
            # Append L2 microstructure digest (or explicit "无微观数据" to prevent LLM hallucination)
            d = digest_map.get(ps["code"])
            if d:
                lo_net_yi = d["lo_net_amount"] / 1e8
                cf_yi = d["cf_net_inflow"] / 1e8
                tick_pct = d["tick_imbalance"] * 100
                micro = f"  微观: 大单净额{lo_net_yi:+.2f}亿(净比{d['lo_net_ratio']:+.2f}) tick{tick_pct:+.1f}% 主力{cf_yi:+.2f}亿"
                if d["vpd_count"] or d["lor_count"]:
                    events_parts = []
                    if d["vpd_count"]:
                        events_parts.append(f"背离x{d['vpd_count']}")
                    if d["lor_count"]:
                        dir_label = (
                            "多"
                            if d["lor_direction"] == "bullish"
                            else "空"
                            if d["lor_direction"] == "bearish"
                            else "?"
                        )
                        events_parts.append(f"翻转x{d['lor_count']}({dir_label})")
                    micro += f" | {' '.join(events_parts)}"
                micro += f" | 综合:{d['direction_score']:+d}({d['direction']})"
                line += "\n" + micro
            else:
                line += "\n  微观: 无微观数据（不可编造）"
            lines.append(line)
        lines.append("")

    # Star watching stocks get full analysis like holdings
    star_watching = [ps for ps in watching if ps.get("star")]
    other_watching = [ps for ps in watching if not ps.get("star")]

    if star_watching:
        lines.append("## ★ 重点自选")
        for ps in star_watching:
            sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else "无信号"
            sentiment_tag = ""
            if ps.get("sentiment") is not None:
                s = ps["sentiment"]
                sentiment_tag = f" | 新闻情感:{s:+.2f}"
            line = f"- {ps['code']} {ps['name']} [★重点] | 涨跌:{ps['change']:+.2f}%{sentiment_tag} | 信号:{ps['signalCount']}条 | 方向:{ps['direction']} | {sigs}"
            d = digest_map.get(ps["code"])
            if d:
                lo_net_yi = d["lo_net_amount"] / 1e8
                cf_yi = d["cf_net_inflow"] / 1e8
                tick_pct = d["tick_imbalance"] * 100
                micro = f"  微观: 大单净额{lo_net_yi:+.2f}亿(净比{d['lo_net_ratio']:+.2f}) tick{tick_pct:+.1f}% 主力{cf_yi:+.2f}亿"
                if d["vpd_count"] or d["lor_count"]:
                    events_parts = []
                    if d["vpd_count"]:
                        events_parts.append(f"背离x{d['vpd_count']}")
                    if d["lor_count"]:
                        dir_label = (
                            "多"
                            if d["lor_direction"] == "bullish"
                            else "空"
                            if d["lor_direction"] == "bearish"
                            else "?"
                        )
                        events_parts.append(f"翻转x{d['lor_count']}({dir_label})")
                    micro += f" | {' '.join(events_parts)}"
                micro += f" | 综合:{d['direction_score']:+d}({d['direction']})"
                line += "\n" + micro
            else:
                line += "\n  微观: 无微观数据（不可编造）"
            lines.append(line)
        lines.append("")

    if other_watching:
        lines.append("## 自选标的")
        for ps in other_watching:
            if ps["signalCount"] > 0:
                sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else ""
                lines.append(
                    f"- {ps['code']} {ps['name']} | {ps['change']:+.2f}% | 信号:{ps['signalCount']} | {sigs}"
                )
        lines.append("")

    # ── Fix 4: Trade plans / conditional orders ──
    if trade_plans:
        plans = trade_plans.get("plans", {})
        active_plans = {k: v for k, v in plans.items() if v.get("status") == "active"}
        if active_plans:
            # Build code→price lookup from per_stock
            price_map = {ps["code"]: ps["price"] for ps in per_stock if ps.get("price")}
            plan_lines = []
            for pid, plan in active_plans.items():
                symbol = plan.get("symbol", "")
                pname = plan.get("name", pid)
                cur_price = price_map.get(symbol, 0)
                orders = plan.get("orders", [])
                untriggered = [o for o in orders if not o.get("triggered")]
                if not untriggered:
                    continue
                order_descs = []
                for o in untriggered:
                    side_cn = "买入" if o.get("side") == "buy" else "卖出"
                    op = o.get("op", ">=")
                    tgt = o.get("price", 0)
                    label = o.get("label", f"{side_cn}{op}{tgt}")
                    if cur_price > 0 and tgt > 0:
                        dist = (tgt - cur_price) / cur_price * 100
                        order_descs.append(f"{label}{op}{tgt}(距现价{dist:+.1f}%)")
                    else:
                        order_descs.append(f"{label}{op}{tgt}")
                plan_lines.append(f"- {symbol} {pname}: {', '.join(order_descs)}")
            if plan_lines:
                lines.append("## 条件单状态")
                lines.extend(plan_lines)
                lines.append("")

    if l1_displays:
        lines.append("## L1 关键事件（原文）")
        for d in l1_displays[-10:]:  # Limit to last 10
            lines.append(f"- {d}")

    # 添加微观数据白名单或空数据警告
    if digest_map:
        codes_with_micro = sorted(digest_map.keys())
        lines.append("")
        lines.append("## 有微观数据的股票代码列表")
        lines.append(
            "以下股票有 L2 微观数据（tick/大单/资金流），逐股速览只能包含这些股票："
        )
        lines.append(", ".join(codes_with_micro))
        lines.append("**严禁为不在此列表中的股票编造微观数据。**")
    else:
        lines.append("")
        lines.append("## 微观数据状态：今日无数据")
        lines.append(
            "**今日 L2 微观数据未采集（session_snapshots 为空），所有标的均无 tick/大单/资金流数据。**"
        )
        lines.append(
            "**逐股速览一节必须省略，或在标题后写'今日无微观数据，无法进行微观分析'。**"
        )
        lines.append(
            "**严禁编造任何 tick偏买/偏卖X%、大单净买/卖X亿、主力流入/流出X亿 等数字。**"
        )
        lines.append(
            "**操作建议只能基于已知的价格涨跌幅、信号类型、告警事件给出，不得引用任何编造的微观数据。**"
        )

    user_msg = "\n".join(lines)

    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user_msg},
    ]


def _build_analysis_tools(
    per_stock: list[dict],
    l2_digest_map: dict,
    market_data: dict,
) -> list[dict]:
    """构建 Kimi function calling 分析工具定义。

    让模型自主调用这些工具查询特定股票的行情/信号/微观数据，
    替代当前"全量灌 prompt"的粗放模式（可选开启）。

    工具:
    - get_stock_quote: 获取行情价格和涨跌幅
    - get_stock_signals: 获取今日 L2 信号
    - get_stock_microstructure: 获取微观结构数据（大单/tick/资金流）
    """

    # 构建快速查找索引
    stock_index = {ps["code"]: ps for ps in per_stock}

    def _get_quote(args: dict) -> str:
        code = args.get("code", "")
        ps = stock_index.get(code)
        if not ps:
            return f"未找到 {code} 的行情数据"
        import json as _j
        return _j.dumps({
            "code": code,
            "name": ps.get("name", ""),
            "price": ps.get("price", 0),
            "change_pct": ps.get("change", 0),
            "type": ps.get("type", ""),
            "star": ps.get("star", False),
            "cost": ps.get("cost", 0),
            "pnl_pct": ps.get("pnl_pct"),
        }, ensure_ascii=False)

    def _get_signals(args: dict) -> str:
        code = args.get("code", "")
        ps = stock_index.get(code)
        if not ps:
            return f"未找到 {code} 的信号数据"
        import json as _j
        return _j.dumps({
            "code": code,
            "signal_count": ps.get("signalCount", 0),
            "direction": ps.get("direction", "neutral"),
            "key_signals": ps.get("keySignals", []),
        }, ensure_ascii=False)

    def _get_microstructure(args: dict) -> str:
        code = args.get("code", "")
        d = l2_digest_map.get(code)
        if not d:
            return f"{{'code': '{code}', 'status': '无微观数据'}}"
        import json as _j
        return _j.dumps({
            "code": code,
            "lo_net_amount_yi": round(d.get("lo_net_amount", 0) / 1e8, 2),
            "tick_imbalance_pct": round(d.get("tick_imbalance", 0) * 100, 1),
            "cf_net_inflow_yi": round(d.get("cf_net_inflow", 0) / 1e8, 2),
            "vpd_count": d.get("vpd_count", 0),
            "lor_count": d.get("lor_count", 0),
            "direction_score": d.get("direction_score", 0),
            "direction": d.get("direction", "neutral"),
        }, ensure_ascii=False)

    tool_handlers = {
        "get_stock_quote": _get_quote,
        "get_stock_signals": _get_signals,
        "get_stock_microstructure": _get_microstructure,
    }

    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_stock_quote",
                "description": "获取某只股票的最新行情：价格、涨跌幅、持仓成本、浮盈亏",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "股票代码，如 00700、600519",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_stock_signals",
                "description": "获取某只股票今日的 L2 策略信号：信号数、多空方向、关键信号列表",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "股票代码",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "get_stock_microstructure",
                "description": "获取某只股票的微观结构数据：大单净额、tick偏买/卖、主力资金流向、量价背离次数",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "股票代码",
                        },
                    },
                    "required": ["code"],
                },
            },
        },
    ]

    return tools, tool_handlers


def _compute_l2_digest(date_str: str) -> list[dict]:
    """从 session_snapshots + signals 表聚合日线级 L2 微观结构指标。

    写入 daily_l2_digest 表并返回 digest 列表。
    """
    from src.sim_trading.db import get_connection

    conn = None
    digests = []
    try:
        conn = get_connection()

        # 1. 取每只股票交易时间内的最后一条 session_snapshot
        # 使用 16:00:00 作为 cutoff，避免收盘后 tick 数据被清零的问题
        rows = conn.execute(
            "SELECT code, session_json FROM session_snapshots "
            "WHERE date = ? AND time <= '16:00:00' AND ts = ("
            "  SELECT MAX(ts) FROM session_snapshots ss "
            "  WHERE ss.date = session_snapshots.date AND ss.code = session_snapshots.code "
            "  AND ss.time <= '16:00:00'"
            ") ORDER BY code",
            (date_str,),
        ).fetchall()

        if not rows:
            logger.info(f"L2 digest: no session_snapshots for {date_str}")
            return []

        # 2. 从 signals 表聚合事件频次
        sig_counts = {}
        for strategy in ("volume_price_divergence", "large_order_reversal"):
            cur = conn.execute(
                "SELECT code, COUNT(*) as cnt FROM signals "
                "WHERE date = ? AND strategy = ? GROUP BY code",
                (date_str, strategy),
            ).fetchall()
            for r in cur:
                sig_counts.setdefault(r["code"], {})[strategy] = r["cnt"]

        # large_order_reversal 最后方向
        lor_dirs = {}
        lor_rows = conn.execute(
            "SELECT code, direction FROM signals "
            "WHERE date = ? AND strategy = 'large_order_reversal' "
            "ORDER BY ts DESC",
            (date_str,),
        ).fetchall()
        for r in lor_rows:
            if r["code"] not in lor_dirs:
                lor_dirs[r["code"]] = r["direction"]

        # 3. 逐股计算 digest
        insert_rows = []
        for row in rows:
            code = row["code"]
            try:
                session = json.loads(row["session_json"])
            except (json.JSONDecodeError, TypeError):
                continue

            tick = session.get("tick", {})
            lo = session.get("large_order", {})
            cf = session.get("capital_flow", {})

            lo_buy_amt = lo.get("buy_amount", 0)
            lo_sell_amt = lo.get("sell_amount", 0)
            lo_total = lo_buy_amt + lo_sell_amt
            lo_net = lo_buy_amt - lo_sell_amt
            lo_net_ratio = round(lo_net / lo_total, 3) if lo_total > 0 else 0

            tick_imb = tick.get("imbalance", 0)
            cf_inflow = cf.get("main_net_inflow", 0)
            cf_pct = cf.get("main_net_inflow_pct", 0)

            code_sigs = sig_counts.get(code, {})
            vpd_count = code_sigs.get("volume_price_divergence", 0)
            lor_count = code_sigs.get("large_order_reversal", 0)
            lor_dir = lor_dirs.get(code)

            # 加权方向评分: 大单*3 + tick*2 + 资金流*1 + 背离*-2 + 翻转*-2
            score = 0
            score += (1 if lo_net_ratio > 0.1 else -1 if lo_net_ratio < -0.1 else 0) * 3
            score += (1 if tick_imb > 0.1 else -1 if tick_imb < -0.1 else 0) * 2
            score += (1 if cf_pct > 5 else -1 if cf_pct < -5 else 0) * 1
            score += (-1 if vpd_count >= 3 else 0) * 2
            score += (
                -1 if lor_dir == "bearish" else 1 if lor_dir == "bullish" else 0
            ) * 2

            direction = (
                "bullish" if score > 2 else "bearish" if score < -2 else "neutral"
            )

            digest = {
                "code": code,
                "lo_buy_count": lo.get("buy_count", 0),
                "lo_sell_count": lo.get("sell_count", 0),
                "lo_net_amount": round(lo_net, 0),
                "lo_net_ratio": lo_net_ratio,
                "tick_imbalance": round(tick_imb, 3),
                "tick_buy_vol": tick.get("buy_vol", 0),
                "tick_sell_vol": tick.get("sell_vol", 0),
                "cf_net_inflow": round(cf_inflow, 0),
                "cf_net_inflow_pct": round(cf_pct, 1),
                "vpd_count": vpd_count,
                "lor_count": lor_count,
                "lor_direction": lor_dir,
                "direction_score": score,
                "direction": direction,
            }
            digests.append(digest)

            insert_rows.append(
                (
                    date_str,
                    code,
                    digest["lo_buy_count"],
                    digest["lo_sell_count"],
                    digest["lo_net_amount"],
                    digest["lo_net_ratio"],
                    digest["tick_imbalance"],
                    digest["tick_buy_vol"],
                    digest["tick_sell_vol"],
                    digest["cf_net_inflow"],
                    digest["cf_net_inflow_pct"],
                    vpd_count,
                    lor_count,
                    lor_dir,
                    score,
                    direction,
                    row["session_json"],
                )
            )

        # 4. 批量写入
        if insert_rows:
            conn.executemany(
                "INSERT OR REPLACE INTO daily_l2_digest "
                "(date, code, lo_buy_count, lo_sell_count, lo_net_amount, lo_net_ratio, "
                "tick_imbalance, tick_buy_vol, tick_sell_vol, "
                "cf_net_inflow, cf_net_inflow_pct, "
                "vpd_count, lor_count, lor_direction, "
                "direction_score, direction, session_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                insert_rows,
            )
            conn.commit()
            logger.info(f"L2 digest: {len(insert_rows)} stocks written for {date_str}")

    except Exception as e:
        logger.warning(f"L2 digest computation failed: {e}")
    finally:
        if conn:
            conn.close()

    return digests


def generate_daily_summary(date_str: str | None = None) -> dict | None:
    """Generate daily summary report.

    Args:
        date_str: Override date (YYYY-MM-DD). Defaults to today.

    Returns:
        The generated summary dict, or None on failure.
    """
    today = date_str or datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Generating daily summary for {today}...")

    # ── Read data sources ──
    market_data = _read_market_data_from_db()
    config = read_monitor_config()
    if not config.get("watchlist"):
        config = {"watchlist": {}, "settings": {}}
    signals = _load_l2_signals_from_db(today)

    # 从 SQLite 读取当日 alert events
    events: list[dict] = []
    conn = None
    try:
        from src.sim_trading.db import get_connection

        conn = get_connection()
        rows = conn.execute(
            "SELECT ts, time, symbol, kind, level, message, display, change_pct "
            "FROM alert_events WHERE date = ? ORDER BY ts",
            (today,),
        ).fetchall()
        events = [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"读取 alert_events 失败: {e}")
    finally:
        if conn:
            conn.close()
    watchlist = config.get("watchlist", {})

    if not signals and not events:
        logger.warning("No signals or events found — skipping summary generation")
        return None

    # ── Read trade plans ──
    trade_plans = _load_trade_plans_from_db()

    # ── Compute L2 daily digest early (needed for direction in per_stock) ──
    l2_digests_early = _compute_l2_digest(today)
    l2_digest_map_early = {d["code"]: d for d in l2_digests_early}

    # ── Aggregate ──
    signal_agg = _aggregate_signals(signals)
    alert_agg = _aggregate_alerts(events)
    per_stock = _build_per_stock(
        market_data, config, signal_agg, alert_agg, l2_digest_map_early
    )
    stats = _build_stats(signals, events, per_stock)

    # Collect L1 display texts for LLM context
    l1_displays = []
    for s in signals:
        if s.get("notify") and s.get("display"):
            l1_displays.append(s["display"])

    market = _detect_market(signals, watchlist)

    # Use l2_digest_map computed earlier (already written to DB)
    l2_digest_map = l2_digest_map_early

    # ── Call LLM ──
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")

    messages = _build_llm_prompt(
        stats,
        per_stock,
        l1_displays,
        l2_digest_map,
        trade_plans,
        include_morning_briefing=True,
    )

    # ── Kimi 高级能力: thinking + prompt cache + json_schema ──
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "daily_report",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "report": {
                        "type": "string",
                        "description": "完整的持仓信号日报（Markdown 格式）",
                    },
                    "market_direction": {
                        "type": "string",
                        "enum": ["bullish", "bearish", "neutral"],
                        "description": "市场整体方向",
                    },
                    "key_topics": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 5,
                        "description": "今日最关键的市场话题",
                    },
                },
                "required": ["report", "market_direction", "key_topics"],
                "additionalProperties": False,
            },
        },
    }

    extra_body = {
        "thinking": {"type": "enabled"},
        "prompt_cache_key": "daily-summary-v2",
    }

    report = None
    reasoning = None
    try:
        client = LLMClientFactory.create_client()
        result = client.get_completion(
            messages,
            response_format=response_format,
            extra_body=extra_body,
            return_extra=True,
        )
        if isinstance(result, dict):
            import json as _json

            parsed = _json.loads(result["content"])
            report = parsed.get("report", "")
            reasoning = result.get("reasoning")
            # 将 market_direction 和 key_topics 融合到 report 头部
            direction = parsed.get("market_direction", "neutral")
            topics = parsed.get("key_topics", [])
            if direction or topics:
                meta_lines = []
                if direction:
                    dir_label = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}
                    meta_lines.append(f"**市场方向**: {dir_label.get(direction, direction)}")
                if topics:
                    meta_lines.append(f"**关键主题**: {', '.join(topics)}")
                if meta_lines:
                    report = "\n".join(meta_lines) + "\n\n" + report
        else:
            report = result
    except Exception as e:
        logger.error(f"LLM call failed: {e}")

    if not report:
        logger.warning("LLM returned empty response — using stats-only summary")
        report = _fallback_report(stats, per_stock)

    # ── 新闻情感分析（并行拉取持仓股新闻 → Kimi json_schema）──
    def _fetch_sentiment(ps: dict) -> dict:
        """获取单只股票的新闻情感分（不会失败）"""
        code = ps.get("code", "")
        try:
            from src.tools.news_crawler import get_stock_news, get_news_sentiment
            news = get_stock_news(code, max_news=5, date=today)
            if not news:
                return {**ps, "sentiment": None}
            score = get_news_sentiment(news, use_structured_output=True)
            return {**ps, "sentiment": score}
        except Exception as e:
            logger.debug(f"Sentiment fetch failed for {code}: {e}")
            return {**ps, "sentiment": None}

    try:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # 只对持仓股和重点自选做情感分析
        priority = [ps for ps in per_stock if ps.get("type") == "holding" or ps.get("star")]
        other = [ps for ps in per_stock if ps not in priority]

        # max_workers=1：Kimi 账号并发上限为 3，串行避免 429
        with ThreadPoolExecutor(max_workers=1) as pool:
            futures = {pool.submit(_fetch_sentiment, ps): ps for ps in priority}
            for f in as_completed(futures):
                result_ps = f.result()
                for i, ps in enumerate(per_stock):
                    if ps["code"] == result_ps["code"]:
                        per_stock[i] = result_ps
                        break

        sentiment_count = sum(1 for ps in per_stock if ps.get("sentiment") is not None)
        logger.info(f"Sentiment analysis: {sentiment_count}/{len(per_stock)} stocks")
    except Exception as e:
        logger.warning(f"Parallel sentiment fetch failed: {e}")

    # ── Build output ──
    summary = {
        "date": today,
        "generatedAt": int(time.time() * 1000),
        "market": market,
        "stats": stats,
        "perStock": [
            {
                "code": ps["code"],
                "name": ps["name"],
                "change": ps["change"],
                "signalCount": ps["signalCount"],
                "direction": ps["direction"],
                "keySignals": ps["keySignals"],
                # 新闻情感（通过 Kimi json_schema 分析）
                "sentiment": ps.get("sentiment"),
            }
            for ps in per_stock
        ],
        "report": report,
    }
    # 保存思考链供审计
    if reasoning:
        summary["report_chain"] = reasoning
        logger.info(f"Thinking chain saved ({len(reasoning)} chars)")

    # ── Write to trading.db ──
    conn = None
    try:
        conn = sqlite3.connect(TRADING_DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO daily_summaries (date, market, stats_json, per_stock_json, generated_at) VALUES (?, ?, ?, ?, ?)",
            (
                today,
                market,
                json.dumps(stats, ensure_ascii=False),
                json.dumps(summary.get("perStock", []), ensure_ascii=False),
                summary["generatedAt"],
            ),
        )
        conn.commit()
        logger.info(
            f"Daily summary written to trading.db:daily_summaries "
            f"({stats['totalSignals']} signals, {len(per_stock)} stocks)"
        )
    except Exception as e:
        logger.error(f"Daily summary write to DB failed: {e}")
    finally:
        if conn:
            conn.close()

    return summary


def _fallback_report(stats: dict, per_stock: list[dict]) -> str:
    """Generate a plain-text report when LLM is unavailable."""
    lines = [
        f"# 持仓信号日报",
        "",
        f"## 统计",
        f"- 总信号: {stats['totalSignals']} | L1: {stats['l1Count']}",
        f"- 多头: {stats['bullish']} | 空头: {stats['bearish']}",
        f"- 收涨: {stats['upCount']} | 收跌: {stats['downCount']}",
        "",
        f"## 逐股摘要",
    ]
    for ps in per_stock:
        if ps["signalCount"] > 0:
            sigs = ", ".join(ps["keySignals"]) if ps["keySignals"] else ""
            lines.append(
                f"- {ps['code']} {ps['name']} {ps['change']:+.2f}% | {ps['direction']} | {sigs}"
            )
    lines.append("")
    lines.append("*LLM 不可用，仅展示统计数据*")
    return "\n".join(lines)


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(PROJECT_ROOT / ".env")
    result = generate_daily_summary()
    if result:
        print(
            f"Generated: {result['date']} — {result['stats']['totalSignals']} signals"
        )
    else:
        print("No summary generated (no data)")
