#!/usr/bin/env python3
"""
Market data poller — 轻量守护脚本

定时从东方财富 API 抓取实时行情，写入 src/data/market_data.json，
供 Next.js 前端直接读取展示。

用法:
    poetry run python src/tools/market_data_poller.py
"""

import json
import socket
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# 东方财富 push API 公开 token（所有 quant 库共用）
EM_UT = "fa5fd1943c7b386f172d6893dbfba10b"

from src.sim_trading.db import init_db, get_connection, get_config_connection
from src.tools.futu_enricher import FutuL2Enricher
from src.tools.stock_monitor import (
    fetch_realtime_eastmoney,
    fetch_realtime_sina,
    fetch_realtime_yahoo,
    is_kr_symbol,
)
from src.utils.logging_config import setup_logger

logger = setup_logger("market_data_poller")

# Futu L2 增强器 — 全局单例，懒连接，失败不影响主流程
_futu_enricher = FutuL2Enricher()

CONFIG_PATH = PROJECT_ROOT / "src" / "data" / "monitor_config.json"
OUTPUT_PATH = PROJECT_ROOT / "src" / "data" / "market_data.json"
DB_PATH = PROJECT_ROOT / "src" / "data" / "sim_trading.db"

# 确保数据库 schema 包含所有表（包括新增的 market_amo_history）
init_db()

# ── AMO History (模块级状态，poll_once 之间保持) ──
# _amo_history[code] = [amount_n, ..., amount_1]  # 最近的 N 天成交额(元)，最多12天
_amo_history: dict[str, list[float]] = {}
# market_amo_history[date_str] = total_amount(亿元)
_market_amo_history: list[tuple[str, float]] = []  # [(date, amount), ...]

_AMO_DAYS_1 = 6  # AMO1 短周期
_AMO_DAYS_2 = 12  # AMO2 中周期
AMO_MIN_AMOUNT = 1_000_000  # 最小成交额(元)，低于此值不计入 AMO


def _load_amo_history_from_db(codes: list[str]) -> dict[str, list[float]]:
    """从 daily_kline 加载历史成交额(amount = volume × close × 100)，最多12天。

    注意：daily_kline.volume 的单位是"手"（1手=100股），但实时API的 vol 是"股"。
    为保持单位一致，所有历史成交额都用 vol × close × 100（元）计算，
    与实时 amount = vol(股) × price（元） 的单位相同。

    Returns: {code: [most_recent_amount, ..., oldest_amount]}  单位：元
    """
    history: dict[str, list[float]] = {}
    if not codes:
        return history
    try:
        conn = sqlite3.connect(str(DB_PATH))
        placeholders = ",".join("?" * len(codes))
        rows = conn.execute(
            f"""
            SELECT code, date, volume, close
            FROM daily_kline
            WHERE code IN ({placeholders})
            ORDER BY date DESC
            LIMIT ?
            """,
            codes + [len(codes) * _AMO_DAYS_2],
        ).fetchall()
        conn.close()

        # 按 code 分组，每个取最多12条（最近12天）
        by_code: dict[str, list[tuple[str, float]]] = {}
        for code, date, volume, close in rows:
            if code not in by_code:
                by_code[code] = []
            # daily_kline.volume 单位是"手"（1手=100股），×100 转为股再乘价格
            if volume and close and volume > 0 and close > 0:
                amount = volume * close * 100  # 元
                by_code[code].append((date, amount))

        # 排序并取最近12天
        for code, items in by_code.items():
            items.sort(key=lambda x: x[0], reverse=True)
            history[code] = [amt for _, amt in items[:_AMO_DAYS_2]]
    except Exception as e:
        logger.warning(f"load_amo_history_from_db 失败: {e}")
    return history


def _update_amo_for_stock(code: str, amount_today: float) -> tuple[float, float]:
    """更新单只股票的 AMO 历史，返回 (amo1, amo2)。

    _amo_history[code][0] = 今天（从DB加载，或本次盘中插入），
    [1:] = 历史数据（由远及近）。

    AMO1 = 今天 / 前5天均值；AMO2 = 今天 / 前11天均值。
    """
    global _amo_history
    if code not in _amo_history:
        _amo_history[code] = []

    # 只在今天不在 DB 历史时插入（盘中实时更新）
    if amount_today >= AMO_MIN_AMOUNT:
        if not _amo_history[code] or _amo_history[code][0] != amount_today:
            _amo_history[code].insert(0, amount_today)

    # keep max 12 days
    if len(_amo_history[code]) > _AMO_DAYS_2:
        _amo_history[code] = _amo_history[code][:_AMO_DAYS_2]

    # 计算 AMO：只用历史数据 [1:] 算均值
    hist = _amo_history[code]
    hist_past = hist[1:]  # 排除今天
    n = len(hist_past)
    if n == 0:
        return 1.0, 1.0
    avg5 = sum(hist_past[:_AMO_DAYS_1]) / min(n, _AMO_DAYS_1)
    avg12 = sum(hist_past) / max(n, 1)
    avg5 = max(avg5, 1)
    avg12 = max(avg12, 1)
    return amount_today / avg5, amount_today / avg12


# ── 大盘 AMO（两市合计成交额，单位：亿元 × 1e8 = 元）──
_market_amo_12d: list[float] = []  # 最近12天每日两市合计成交额(元)，内存缓存


def _load_market_amo_from_db() -> list[float]:
    """从 SQLite 加载最近最多12天的大盘成交额历史。

    Returns: [最新, 次新, ..., 最旧]，与 _amo_history 格式一致。
    hist[0] = 最新（今日），hist[1:] = 历史，用于 AMO 计算。
    """
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT total_yuan FROM market_amo_history ORDER BY date DESC LIMIT 12"
        ).fetchall()
        conn.close()
        if not rows:
            return []
        return [float(r[0]) for r in rows]
    except Exception:
        return []


def _save_market_amo_to_db(date_str: str, total_yuan: float):
    """Upsert 今日大盘成交额到 SQLite。"""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            "INSERT OR REPLACE INTO market_amo_history (date, total_yuan) VALUES (?, ?)",
            (date_str, total_yuan),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"大盘AMO写入SQLite失败: {e}")


def _update_market_amo(total_yi: float):
    """更新大盘 AMO 历史（内存缓存 + SQLite 持久化）。total_yi: 两市合计成交额（亿元）。"""
    global _market_amo_12d
    amount_yuan = total_yi * 1e8
    today_str = datetime.now().strftime("%Y-%m-%d")
    _save_market_amo_to_db(today_str, amount_yuan)
    if _market_amo_12d:
        _market_amo_12d[0] = amount_yuan
    else:
        _market_amo_12d = [amount_yuan]


def _get_market_amo1() -> float:
    hist = _market_amo_12d
    if len(hist) < 2:
        return 1.0
    avg6 = sum(hist[1 : _AMO_DAYS_1 + 1]) / min(len(hist) - 1, _AMO_DAYS_1)
    return hist[0] / max(avg6, 1)


def _get_market_amo2() -> float:
    hist = _market_amo_12d
    if len(hist) < 2:
        return 1.0
    avg12 = sum(hist[1:]) / max(len(hist) - 1, 1)
    return hist[0] / max(avg12, 1)


def load_watchlist_from_db() -> tuple[dict, dict]:
    """从 DB 读取 watchlist 和 settings，单一数据源。

    Returns:
        (watchlist, settings) — watchlist: {symbol: {name, type, ...}}
    """
    watchlist: dict = {}
    settings: dict = {}
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        # watchlist
        rows = conn.execute(
            "SELECT symbol, name, list_type, cost, shares, lot, "
            "hidden, star, tags, watch_price FROM monitor_watchlist"
        ).fetchall()
        for row in rows:
            entry: dict = {
                "name": row["name"] or "",
                "type": row["list_type"] or "watching",
            }
            if row["cost"]:
                entry["cost"] = row["cost"]
            if row["shares"]:
                entry["shares"] = row["shares"]
            if row["lot"]:
                entry["lot"] = row["lot"]
            if row["hidden"]:
                entry["hidden"] = bool(row["hidden"])
            if row["star"]:
                entry["star"] = bool(row["star"])
            if row["tags"]:
                try:
                    tags = json.loads(row["tags"])
                    if isinstance(tags, list):
                        entry["tags"] = tags
                except json.JSONDecodeError:
                    entry["tags"] = row["tags"]
            if row["watch_price"]:
                entry["watch_price"] = row["watch_price"]
            watchlist[row["symbol"]] = entry
        # settings
        setting_rows = conn.execute(
            "SELECT key, value FROM monitor_settings"
        ).fetchall()
        for row in setting_rows:
            try:
                settings[row["key"]] = int(row["value"])
            except (TypeError, ValueError):
                try:
                    settings[row["key"]] = float(row["value"])
                except (TypeError, ValueError):
                    settings[row["key"]] = row["value"]
        conn.close()
    except Exception as e:
        logger.error(f"load_watchlist_from_db 失败，回退 JSON: {e}")
        # 降级到 JSON（应急 fallback，避免 poller 停摆）
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg.get("watchlist", {}), cfg.get("settings", {})
        except Exception:
            return {}, {}
    return watchlist, settings


def _backfill_missing_names(stocks: list[dict], watchlist: dict) -> bool:
    """用行情抓取结果填充 watchlist 中名称缺失的股票。

    只写 DB（单一数据源）。返回 True 表示有更新。
    """
    updates: dict[str, str] = {}

    for s in stocks:
        code = s.get("code", "")
        name = s.get("name", "").strip()
        if not code or not name:
            continue
        existing_name = (watchlist.get(code, {}).get("name") or "").strip()
        if not existing_name or existing_name == code:
            updates[code] = name

    if not updates:
        return False

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn = get_config_connection()
        cur = conn.cursor()
        for code, name in updates.items():
            cur.execute(
                "UPDATE monitor_watchlist SET name = ?, updated_at = ? "
                "WHERE symbol = ? "
                "AND (name IS NULL OR name = '' OR name = symbol)",
                (name, now, code),
            )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning(f"backfill names DB write failed: {e}")

    logger.info(f"自动填充股票名称: {updates}")
    return True


def fetch_realtime_with_fallback(symbols: list[str]) -> tuple[list[dict], bool]:
    """优先东方财富，失败回退新浪（价格能刷新，但无量比/换手率）

    Returns:
        (stocks, is_sina_fallback) — is_sina_fallback=True 时 turnover/vol_ratio 为 0
    """
    stocks = fetch_realtime_eastmoney(symbols)
    if stocks:
        return stocks, False

    logger.warning("东方财富不可达，回退新浪行情")
    sina_quotes = fetch_realtime_sina(symbols)
    if not sina_quotes:
        return [], True

    # 转换新浪格式 → 东方财富格式
    results = []
    for sym in symbols:
        q = sina_quotes.get(sym)
        if not q:
            continue
        prev = q.get("prev_close", 0)
        price = q.get("price", 0)
        if price <= 0 and prev > 0:
            price = prev  # 停牌：用昨收价填充，涨跌=0
        elif price <= 0:
            continue  # 无任何价格数据，跳过
        pct = q.get("change_pct", 0)
        # 港股盘前: price==prevClose 且 pct==0，跳过以保留昨日收盘涨跌
        # 仅限港股；A 股 price==prevClose 可能是停牌，应保留
        if pct == 0 and prev > 0 and abs(price - prev) < 0.001 and sym.startswith("HK"):
            continue
        chg = price - prev if prev > 0 and price > 0 else 0
        results.append(
            {
                "code": sym,
                "name": q.get("name", ""),
                "price": price,
                "pct": pct,
                "change": round(chg, 3),
                "volume": q.get("volume", 0),
                "amount": q.get("amount", 0),
                "amplitude": round((q.get("high", 0) - q.get("low", 0)) / prev * 100, 2)
                if prev > 0
                else 0,
                "turnover": 0,  # 新浪无换手率，poll_once 会从旧数据继承
                "vol_ratio": 0,  # 新浪无量比，poll_once 会从旧数据继承
                "high": q.get("high", 0),
                "low": q.get("low", 0),
                "open": q.get("open", 0),
                "prev_close": prev,
            }
        )
    return results, True


def build_services(stocks: list[dict], watchlist: dict) -> list[dict]:
    """将东方财富原始数据组装为前端 Service 格式

    只写行情数据，不写 config 字段（type/cost/shares/hidden/above/below/pnl）。
    config 字段由 web /api/metrics 实时从 monitor_config.json 合并，
    确保 UI 端操作（隐藏、改持仓等）立即生效，不依赖 poller 周期。
    """
    services = []
    for s in stocks:
        code = s.get("code", "")
        price = s.get("price", 0)
        prev = s.get("prev_close", 0)
        # 停牌股: price=0 但有昨收，用昨收价填充，涨跌=0
        if (not price or price <= 0) and prev > 0:
            price = prev
        services.append(
            {
                "id": code,
                "name": s.get("name", ""),
                "price": price,
                "change": s.get("pct", 0) if price != prev else 0,
                "chgAmt": s.get("change", 0) if price != prev else 0,
                "vol": s.get("volume", 0),
                "amount": s.get("amount", 0),
                "amp": s.get("amplitude", 0),
                "turnover": s.get("turnover", 0),
                "volRatio": s.get("vol_ratio", 0),
                "high": s.get("high", 0),
                "low": s.get("low", 0),
                "open": s.get("open", 0),
                "prevClose": s.get("prev_close", 0),
            }
        )
    return services


SINA_INDEX_URL = "https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006,s_sh000688"
SINA_HEADERS = {"Referer": "https://finance.sina.com.cn"}


def fetch_hk_index_data() -> dict:
    """从腾讯财经获取港股指数（恒生 + 恒生科技），返回 {hkIndex, hkIndexPct, hkTech, hkTechPct, hkTurnover}"""
    result = {}
    try:
        resp = requests.get(
            "https://qt.gtimg.cn/q=hkHSI,hkHSTECH",
            timeout=5,
        )
        resp.encoding = "gbk"
    except Exception as e:
        logger.debug(f"获取港股指数失败: {e}")
        return result

    for line in resp.text.strip().split("\n"):
        if "=" not in line or '="";' in line:
            continue
        m = line.split("=")
        if len(m) < 2:
            continue
        raw = m[1].strip().strip('"')
        fields = raw.split("~")
        if len(fields) < 35:
            continue
        try:
            price = float(fields[3]) if fields[3] else 0
            chg_ratio = float(fields[32]) if fields[32] else 0
            # turnover 在字段 36，单位是"万元"，转亿元
            raw_turnover = float(fields[36]) if fields[36] else 0
            turnover_yi = raw_turnover / 10000  # 万元 → 亿元
        except (ValueError, IndexError):
            continue
        code_full = fields[2]  # "HSI" 或 "HSTECH"
        if code_full == "HSI":
            result["hkIndex"] = round(price, 2) if price else 0
            result["hkIndexPct"] = round(chg_ratio, 2) if chg_ratio else 0
            result["hkTurnover"] = round(turnover_yi, 0) if turnover_yi else 0
        elif code_full == "HSTECH":
            result["hkTech"] = round(price, 2) if price else 0
            result["hkTechPct"] = round(chg_ratio, 2) if chg_ratio else 0
    return result


def fetch_market_turnover() -> dict | None:
    """从新浪获取沪深两市实时成交额（单次请求，<50ms）

    Returns:
        {sh, sz, total (亿), shPct, szPct, verdict}
    """
    import re

    try:
        resp = requests.get(SINA_INDEX_URL, headers=SINA_HEADERS, timeout=5)
        resp.encoding = "gbk"
    except Exception as e:
        logger.warning(f"获取两市成交额失败: {e}")
        return None

    result = {}
    for line in resp.text.strip().split("\n"):
        m = re.match(r'var hq_str_s_(\w+)="(.*)";', line.strip())
        if not m:
            continue
        code = m.group(1)
        fields = m.group(2).split(",")
        if len(fields) < 6:
            continue
        # 简化格式: 名称,点位,涨跌点,涨跌幅%,成交量(万手),成交额(万元)
        # 支持: sh000001(上证), sz399001(深证), sz399006(创业板), sh000688(科创50)
        entry = {
            "name": fields[0],
            "price": float(fields[1]),
            "pct": float(fields[3]),
            "amount": float(fields[5]) / 10000,  # 万元 → 亿元
        }
        if code == "sh000001":
            result["sh"] = entry
        elif code == "sz399001":
            result["sz"] = entry
        elif code == "sz399006":
            result["chiNext"] = entry
        elif code == "sh000688":
            result["kc50"] = entry

    if "sh" not in result or "sz" not in result:
        return None

    sh_yi = result["sh"]["amount"]
    sz_yi = result["sz"]["amount"]
    total = sh_yi + sz_yi

    # 缩放量判断
    if total >= 20000:
        verdict = "extreme_high"
    elif total >= 15000:
        verdict = "high"
    elif total >= 12000:
        verdict = "above_avg"
    elif total >= 10000:
        verdict = "normal"
    elif total >= 8000:
        verdict = "below_avg"
    elif total >= 6000:
        verdict = "low"
    else:
        verdict = "extreme_low"

    turnover_dict = {
        "sh": round(sh_yi),
        "sz": round(sz_yi),
        "total": round(total),
        "shIndex": result["sh"]["price"],
        "szIndex": result["sz"]["price"],
        "shPct": result["sh"]["pct"],
        "szPct": result["sz"]["pct"],
        "verdict": verdict,
    }
    if "chiNext" in result:
        turnover_dict["chiNext"] = round(result["chiNext"]["price"], 2)
        turnover_dict["chiNextPct"] = round(result["chiNext"]["pct"], 2)
    if "kc50" in result:
        turnover_dict["kc50"] = round(result["kc50"]["price"], 2)
        turnover_dict["kc50Pct"] = round(result["kc50"]["pct"], 2)
    return turnover_dict


def poll_once() -> bool:
    """执行一次抓取+写入，返回是否成功

    即使个股行情抓取失败，也尝试写入大盘数据（成交额），
    确保 dashboard 至少能看到市场概览。
    """
    global _amo_history, _market_amo_history, _market_amo_12d

    # 每轮从 DB 刷新大盘 AMO 历史，避免外部回填后内存缓存过期
    _market_amo_12d = _load_market_amo_from_db()
    if _market_amo_12d:
        logger.debug(f"大盘AMO历史: {len(_market_amo_12d)}天")

    watchlist, settings = load_watchlist_from_db()
    symbols = list(watchlist.keys())

    if not symbols:
        logger.warning("watchlist 为空，跳过本轮")
        return False

    # ── AMO 历史：每轮从 DB 刷新，避免 kline_fetcher 更新后内存缓存过期 ──
    _amo_history = _load_amo_history_from_db(symbols)
    if not _amo_history:
        logger.debug(f"AMO历史: DB 无数据，依赖盘中增量")

    # 分离 KR 股票（Yahoo Finance），其余走东方财富/新浪
    kr_symbols = [s for s in symbols if is_kr_symbol(s)]
    em_symbols = [s for s in symbols if not is_kr_symbol(s)]

    # 追加 A 股指数：创业板、科创50
    INDEX_CODES = ["399006", "sh000688"]
    em_with_index = em_symbols + INDEX_CODES
    stocks, is_sina_fallback = fetch_realtime_with_fallback(em_with_index)

    # 追加 Yahoo Finance 数据（KR 股票）
    if kr_symbols:
        kr_stocks = fetch_realtime_yahoo(kr_symbols)
        stocks = stocks + kr_stocks

    # 两市成交额（新浪源，独立于东方财富，不受其故障影响）
    turnover = fetch_market_turnover()

    if not stocks:
        # 个股数据失败，但尝试 partial update（保留旧 services，更新成交额）
        logger.warning("个股行情获取失败（东方财富不可达），尝试更新大盘数据")
        index_results = []  # fetch_realtime_with_fallback 返回空，没有指数数据
        try:
            existing = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        except Exception:
            existing = {"services": []}
        existing["ts"] = int(time.time() * 1000)
        existing["settings"] = settings
        existing["_source"] = {
            "primary": "unavailable",
            "is_fallback": True,
            "futu_connected": False,
        }
        if turnover:
            # 市场 AMO 计算：成交额(亿元) × 1e8 = 元
            total_yi = float(turnover.get("total", 0))
            _update_market_amo(total_yi)
            turnover["amo1"] = round(_get_market_amo1(), 3)
            turnover["amo2"] = round(_get_market_amo2(), 3)
            # 港股指数兜底
            hk_idx = fetch_hk_index_data()
            if hk_idx:
                turnover.update(hk_idx)
            existing["marketTurnover"] = turnover
        tmp = OUTPUT_PATH.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        tmp.replace(OUTPUT_PATH)
        if turnover:
            logger.info(
                f"大盘数据已更新: 两市 {turnover['total']:,}亿 ({turnover['verdict']})"
            )
        return False

    # 自动填充缺失的股票名称（只在首次抓到名称时写入，后续 no-op）
    _backfill_missing_names(stocks, watchlist)

    # 指数代码集合（不在 watchlist 中），需要分离出来单独写入 marketTurnover
    index_codes_set = set(INDEX_CODES)
    index_results = [s for s in stocks if s.get("code", "") in index_codes_set]
    stock_results = [s for s in stocks if s.get("code", "") not in index_codes_set]
    services = build_services(stock_results, watchlist)

    # ── AMO 计算：每只股票 amount = vol × close（个股），更新历史后算 AMO1/AMO2 ──
    for svc in services:
        code = svc["id"]
        vol = svc.get("vol", 0) or 0
        price = svc.get("price", 0) or 0
        # 成交额 = 成交量 × 当前价（近似，实际应为均价，此处用现价估算，单位：元）
        amount_today = vol * price
        if amount_today > AMO_MIN_AMOUNT:
            amo1, amo2 = _update_amo_for_stock(code, amount_today)
            svc["amo1"] = round(amo1, 3)
            svc["amo2"] = round(amo2, 3)
        else:
            # 成交额太小（ETF/极低流动性），AMO 置 1（无量价参考价值）
            svc["amo1"] = 1.0
            svc["amo2"] = 1.0

    # 新浪降级时继承旧数据中的量比/换手率（新浪不提供这两个字段）
    if is_sina_fallback:
        try:
            old_data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            old_map = {s["id"]: s for s in old_data.get("services", []) if s.get("id")}
            carried = 0
            for svc in services:
                old = old_map.get(svc["id"])
                if old:
                    if old.get("turnover"):
                        svc["turnover"] = old["turnover"]
                    if old.get("volRatio"):
                        svc["volRatio"] = old["volRatio"]
                    carried += 1
            if carried:
                logger.info(f"新浪降级: 从旧数据继承量比/换手率 ({carried} 只)")
        except Exception:
            pass

    # Futu L2 增强（可选，失败时 l2_data = {}，不影响后续）
    l2_data, hk_index = _futu_enricher.enrich(services)
    if l2_data:
        for svc in services:
            extra = l2_data.get(svc["id"])
            if extra:
                svc.update(extra)
        logger.info(f"L2 增强: {len(l2_data)}/{len(services)} 只")

    # 将港股指数数据写入 marketTurnover（优先用 futu，失败时用腾讯兜底）
    if turnover:  # turnover 为 None 时跳过（fetch_market_turnover 完全失败）
        hk_idx = hk_index.copy() if hk_index else {}
        # 腾讯财经兜底获取港股指数
        if not hk_idx.get("hkIndex"):
            sina_hk = fetch_hk_index_data()
            if sina_hk:
                hk_idx.update(sina_hk)
                logger.info(
                    f"港股指数(腾讯兜底): hkIndex={sina_hk.get('hkIndex')} hkTech={sina_hk.get('hkTech')}"
                )
        if hk_idx:
            for k, v in hk_idx.items():
                turnover[k] = v
            logger.info(
                f"港股指数: hkIndex={hk_idx.get('hkIndex')} hkTech={hk_idx.get('hkTech')}"
            )

    # 合并旧数据中缺失的 service（盘前 price=0 被跳过的股票保留昨日收盘价）
    new_ids = {s["id"] for s in services}
    try:
        old_data = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
        for old_svc in old_data.get("services", []):
            if old_svc.get("id") and old_svc["id"] not in new_ids:
                services.append(old_svc)
    except Exception:
        pass

    # ── 市场 AMO：成交额(亿元) × 1e8 = 元 ──
    if turnover:
        total_yi = float(turnover.get("total", 0))
        _update_market_amo(total_yi)
        turnover["amo1"] = round(_get_market_amo1(), 3)
        turnover["amo2"] = round(_get_market_amo2(), 3)

    # 提取指数数据写入 marketTurnover
    if turnover:
        for idx in index_results:
            code = idx.get("code", "")
            price = idx.get("price", 0) or 0
            pct = idx.get("pct", 0) or 0
            if code == "399006":
                turnover["chiNext"] = round(price, 2) if price else 0
                turnover["chiNextPct"] = round(pct, 2) if pct else 0
            elif code == "sh000688":
                turnover["kc50"] = round(price, 2) if price else 0
                turnover["kc50Pct"] = round(pct, 2) if pct else 0

    payload = {
        "services": services,
        "ts": int(time.time() * 1000),
        "_updated_by": socket.gethostname(),
        "settings": settings,
        "marketTurnover": turnover,
        # 数据源元数据，供 Data Freshness Watchdog 检测降级
        "_source": {
            "primary": "sina" if is_sina_fallback else "eastmoney",
            "is_fallback": is_sina_fallback,
            "futu_connected": bool(l2_data),
        },
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUTPUT_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    tmp.replace(OUTPUT_PATH)

    now = datetime.now().strftime("%H:%M:%S")
    logger.info(f"[{now}] 已更新 {len(services)} 只标的 -> {OUTPUT_PATH.name}")
    return True


def main():
    from src.tools.monitor_lock import MonitorLock, HEARTBEAT_INTERVAL

    lock = MonitorLock()
    if not lock.try_acquire():
        holder = lock.get_lock_holder()
        if holder:
            print(f"[POLLER] 锁被 {holder[0]} (pid={holder[1]}) 持有，退出")
        else:
            print("[POLLER] 锁被未知进程持有，退出")
        sys.exit(1)
    print(f"[POLLER] 成功获取锁 {lock.hostname}")

    from src.tools.stock_notifier import is_any_market_open
    from src.tools.trading_calendar import is_trading_day

    watchlist, settings = load_watchlist_from_db()
    interval = settings.get("poll_interval", 30)
    has_hk = any(s.startswith("HK") for s in watchlist)
    IDLE_INTERVAL = 300  # 休市时 5 分钟检查一次

    print("Market Data Poller 启动")
    print(f"  标的数: {len(watchlist)}")
    print(f"  港股: {'是' if has_hk else '否'}")
    print(f"  轮询间隔: {interval}s (休市 {IDLE_INTERVAL}s)")
    print(f"  输出文件: {OUTPUT_PATH}")
    print("  按 Ctrl+C 退出\n")

    # 启动时立即执行一次（确保有初始数据）
    poll_once()

    try:
        while True:
            time.sleep(interval)
            # 刷新锁
            if not lock.refresh_heartbeat():
                print("[POLLER] 锁丢失，退出")
                break
            # 每轮重新读取 DB，这样 watchlist 变化能自动生效
            watchlist, settings = load_watchlist_from_db()
            interval = settings.get("poll_interval", 30)
            has_hk = any(s.startswith("HK") for s in watchlist)

            # 休市判断：非交易日或交易时段外，降低轮询频率
            if not is_any_market_open(has_hk):
                cn_trading = is_trading_day("CN")
                hk_trading = is_trading_day("HK") if has_hk else False
                if not cn_trading and not hk_trading:
                    logger.info("非交易日，跳过轮询")
                else:
                    logger.info("交易时段外，跳过轮询")
                interval = IDLE_INTERVAL
                continue

            poll_once()
            # Checkpoint trading.db WAL so OneDrive can sync
            try:
                chk_conn = get_connection()
                chk_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                chk_conn.close()
            except Exception:
                pass
    except KeyboardInterrupt:
        print("\nPoller 已停止")


if __name__ == "__main__":
    main()
