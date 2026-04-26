"""
Earnings Calendar — 财报日历 + 飞书预警 + 预判/复盘分析

功能：
1. 财报发布日历 — 爬取即将发布的财报日期
2. 预财报预警 — 发布前发送飞书通知 + 预判（利好/利空条件扫描）
3. 财报复盘分析 — 发布后深度解析 + LLM 解读

依赖：
- akshare: 财报公告、业绩预告、分析师预期
- src.tools.stock_monitor: feishu_send() 飞书通知
- src.tools.api: get_financial_metrics(), get_market_data()
- src.utils.llm_clients: LLM 分析（GPT/Gemini）

Usage:
    # 查看即将发布的财报
    uv run python -c "
    from src.tools.earnings_calendar import EarningsCalendar
    ec = EarningsCalendar()
    ec.print_ upcoming_earnings(days_ahead=7)
    "

    # 主动触发一次检查和预警
    uv run python src/tools/earnings_calendar.py
"""

import fcntl
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.utils.logging_config import setup_logger
from src.tools.stock_monitor import feishu_send

logger = setup_logger("earnings_calendar")

# ── Lock file (daemon 单例) ──
LOCK_FILE = PROJECT_ROOT / "data" / ".earnings_calendar.lock"

# ── 数据缓存路径 ──
DATA_DIR = PROJECT_ROOT / "src" / "data"
TRADING_DB = PROJECT_ROOT / "src" / "data" / "trading.db"

# ── 飞书推送阈值 ──
ALERT_BEFORE_DAYS = 3   # 提前 N 天预警
ALERT_AFTER_HOURS = 4   # 发布后 N 小时内自动分析

# ── LLM 分析提示词 ──
PRE_EARNINGS_PROMPT = """你是一个专业的A股分析师，负责在财报发布前根据近期数据对股票进行预判。

股票代码: {symbol}
股票名称: {name}
当前价格: {price}
近期涨跌幅: {pct_change}%
市值: {market_cap}亿
市盈率(动态): {pe_ratio}

近期财务指标:
- 净资产收益率(ROE): {roe}%
- 净利润增长率: {earnings_growth}%
- 主营业务收入增长率: {revenue_growth}%
- 资产负债率: {debt_to_equity}%
- 流动比率: {current_ratio}
- 每股经营性现金流: {free_cash_flow_per_share}元

行业: {sector}

请分析：
1. 给出该股票本次财报的预判（利好/利空/中性），并说明理由
2. 列出支持预判的关键条件（正面和负面）
3. 给出投资建议（短线/中线/长线持有或回避）

请用简洁专业的语言回复，适合推送飞书。"""

POST_EARNINGS_PROMPT = """你是一个专业的A股财报分析师，负责在财报发布后对财报进行深度解读。

股票代码: {symbol}
股票名称: {name}
财报期间: {period}
实际发布时间: {publish_date}

财报数据:
- 营业收入: {revenue}元
- 净利润: {net_income}元
- 净资产收益率(ROE): {roe}%
- 每股收益(EPS): {eps}元
- 每股经营性现金流: {free_cash_flow}元

市场预期（分析师预测）:
- 预测净利润: {forecast_net_income}元
- 预测EPS: {forecast_eps}元

公告摘要:
{announcement_summary}

请分析：
1. 实际财报 vs 预期对比（超预期/符合/低于预期）
2. 财报解读（利好/利空/中性）及核心原因
3. 对股价的短期和中长期影响
4. 给出投资建议（买入/持有/卖出）

请用简洁专业的语言回复，适合推送飞书。"""


# ═══════════════════════════════════════════════════════
# 1. 数据获取
# ═══════════════════════════════════════════════════════

def _fetch_stock_list() -> list:
    """获取A股股票列表"""
    try:
        import akshare as ak
        df = ak.stock_info_a_code_name()
        return df.to_dict("records")
    except Exception as e:
        logger.warning(f"获取股票列表失败: {e}")
        return []


def fetch_earnings_calendar(start_date: str, end_date: str, symbols: list = None) -> list:
    """
    获取指定日期范围内的财报发布时间安排
    使用 akshare 爬取巨潮资讯网的财报预约披露数据

    Args:
        start_date: 开始日期 YYYY-MM-DD
        end_date: 结束日期 YYYY-MM-DD
        symbols: 如果提供，则只检查这些股票（减少API调用）

    Returns:
        list of dict: [{symbol, name, period, report_date}, ...]
    """
    try:
        import akshare as ak
        from datetime import datetime

        today_str = datetime.now().strftime('%Y-%m-%d')

        # ── 方法1: 使用 stock_report_disclosure 获取预约披露（推荐）──
        # 获取当前可用期间的披露预约数据
        available_periods = ['2025年报', '2025半年报']
        all_records = []

        for period in available_periods:
            try:
                df = ak.stock_report_disclosure(market='沪深京', period=period)
                if df is None or df.empty:
                    continue

                # 标准化列名
                df = df.rename(columns={
                    '股票代码': 'code',
                    '股票简称': 'name',
                    '首次预约': 'scheduled_date',
                    '实际披露': 'actual_date',
                })

                # 优先使用实际披露日期，如果没有则用预约日期
                df['report_date'] = df['actual_date'].fillna(df['scheduled_date'])
                df['period'] = period

                # 只保留未来还未实际披露的
                for _, row in df.iterrows():
                    report_str = str(row.get('report_date', ''))[:10]
                    if report_str and report_str >= today_str and report_str <= end_date:
                        all_records.append({
                            'code': str(row.get('code', '')),
                            'name': str(row.get('name', '')),
                            'report_date': report_str,
                            'period': period,
                            'is_actual': str(row.get('actual_date', ''))[:10] != '' or pd.isna(row.get('actual_date')),
                        })
            except Exception as e:
                logger.debug(f"获取 {period} 财报披露数据失败: {e}")
                continue

        # ── 方法2: 补充 stock_zh_a_disclosure_report_cninfo（历史实际披露）──
        # 用于填充已实际发布的历史财报（供复盘分析用）
        try:
            df_hist = ak.stock_zh_a_disclosure_report_cninfo(symbol="")
            if df_hist is not None and not df_hist.empty:
                keywords = ["年报", "半年报", "季报", "季度", "审计", "财务报告", "经营业绩"]
                df_hist = df_hist[df_hist["公告标题"].apply(
                    lambda x: any(k in str(x) for k in keywords)
                )]
                # 过滤日期范围
                df_hist = df_hist[df_hist["公告时间"] >= today_str]
                df_hist = df_hist[df_hist["公告时间"] <= end_date]

                for _, row in df_hist.iterrows():
                    all_records.append({
                        'code': str(row.get("代码", "")),
                        'name': str(row.get("简称", "")),
                        'report_date': str(row.get("公告时间", ""))[:10],
                        'period': str(row.get("公告标题", ""))[:50],
                        'is_actual': True,
                    })
        except Exception as e:
            logger.debug(f"获取历史财报披露数据失败: {e}")

        # 按股票代码过滤（如果指定了symbols）
        if symbols:
            all_records = [r for r in all_records if r.get('code') in symbols]

        logger.info(f"获取到 {len(all_records)} 条财报披露记录 ({start_date} ~ {end_date})")
        return all_records

    except Exception as e:
        logger.warning(f"获取财报日历失败: {e}")
        return []


def fetch_recent_earnings(symbol: str, limit: int = 5) -> list:
    """
    获取个股近期财报发布历史
    用于判断财报季节性和历史表现
    """
    try:
        import akshare as ak
        df = ak.stock_zh_a_disclosure_report_cninfo(symbol=symbol)
        if df is None or df.empty:
            return []
        keywords = ["年报", "半年报", "季报", "审计", "财务报告"]
        df = df[df["公告标题"].apply(lambda x: any(k in str(x) for k in keywords))]
        return df.head(limit).to_dict("records")
    except Exception as e:
        logger.debug(f"获取 {symbol} 历史财报失败: {e}")
        return []


def fetch_profit_forecast(symbol: str) -> Optional[dict]:
    """
    获取分析师盈利预测
    用于预判财报是否超预期
    """
    try:
        import akshare as ak
        df = ak.stock_rank_forecast_cninfo()
        if df is None or df.empty:
            return None
        match = df[df["证券代码"] == symbol]
        if match.empty:
            return None
        latest = match.iloc[0]
        return {
            "forecast_eps": latest.get("预测每股收益"),
            "forecast_net_income": None,  # 分析师覆盖度有限
            "rating": latest.get("投资评级"),
            "institution": latest.get("研究机构简称"),
        }
    except Exception:
        return None


def get_stock_financial_metrics(symbol: str) -> dict:
    """获取个股财务指标（供预判用）"""
    try:
        from src.tools.api import get_financial_metrics, get_market_data
        metrics = get_financial_metrics(symbol)
        if metrics and metrics[0]:
            m = metrics[0]
            market = get_market_data(symbol)
            return {
                "roe": m.get("return_on_equity", 0) * 100,
                "earnings_growth": m.get("earnings_growth", 0) * 100,
                "revenue_growth": m.get("revenue_growth", 0) * 100,
                "debt_to_equity": m.get("debt_to_equity", 0) * 100,
                "current_ratio": m.get("current_ratio", 0),
                "free_cash_flow_per_share": m.get("free_cash_flow_per_share", 0),
                "pe_ratio": m.get("pe_ratio", 0),
                "price_to_book": m.get("price_to_book", 0),
                "market_cap": market.get("market_cap", 0) / 1e8,  # 亿元
            }
    except Exception as e:
        logger.debug(f"获取 {symbol} 财务指标失败: {e}")
    return {}


# ═══════════════════════════════════════════════════════
# 2. 预判分析（利好/利空条件扫描）
# ═══════════════════════════════════════════════════════

def analyze_pre_earnings_bullish_bearish(
    symbol: str,
    name: str,
    metrics: dict,
    forecast: Optional[dict],
    price_info: dict,
) -> dict:
    """
    基于近期数据扫描预判财报发布前的利好/利空条件

    Returns:
        dict: {
            "verdict": "bullish" | "bearish" | "neutral",
            "reasons": [str, ...],
            "score": float (-10~+10),
            "llm_summary": str (optional LLM summary),
        }
    """
    score = 0.0
    reasons = []

    # ── 正面条件 ──
    if metrics.get("earnings_growth", 0) > 20:
        score += 2.0
        reasons.append(f"净利润增长 {metrics['earnings_growth']:.1f}%（强劲）")
    elif metrics.get("earnings_growth", 0) > 0:
        score += 1.0
        reasons.append(f"净利润增长 {metrics['earnings_growth']:.1f}%（正增长）")

    if metrics.get("revenue_growth", 0) > 20:
        score += 1.5
        reasons.append(f"营收增长 {metrics['revenue_growth']:.1f}%（强劲）")

    if metrics.get("roe", 0) > 15:
        score += 1.5
        reasons.append(f"ROE {metrics['roe']:.1f}%（优秀）")
    elif metrics.get("roe", 0) > 8:
        score += 0.5

    if metrics.get("free_cash_flow_per_share", 0) > 0.5:
        score += 1.0
        reasons.append(f"每股经营现金流 {metrics['free_cash_flow_per_share']:.2f}元（健康）")

    if metrics.get("current_ratio", 0) > 2.0:
        score += 0.5
        reasons.append(f"流动比率 {metrics['current_ratio']:.2f}（偿债力强）")

    # ── 负面条件 ──
    if metrics.get("debt_to_equity", 0) > 70:
        score -= 2.0
        reasons.append(f"资产负债率 {metrics['debt_to_equity']:.1f}%（杠杆偏高）")
    elif metrics.get("debt_to_equity", 0) > 50:
        score -= 1.0
        reasons.append(f"资产负债率 {metrics['debt_to_equity']:.1f}%（中性偏高）")

    if metrics.get("earnings_growth", 0) < -20:
        score -= 2.5
        reasons.append(f"净利润下降 {abs(metrics['earnings_growth']):.1f}%（经营恶化）")
    elif metrics.get("earnings_growth", 0) < 0:
        score -= 1.0
        reasons.append(f"净利润下降 {abs(metrics['earnings_growth']):.1f}%（盈利下滑）")

    if metrics.get("current_ratio", 0) < 1.0:
        score -= 1.5
        reasons.append(f"流动比率 {metrics['current_ratio']:.2f}（短期偿债风险）")

    # ── 估值条件 ──
    pe = metrics.get("pe_ratio", 0)
    if pe < 0:
        pass  # 亏损股不评价
    elif pe < 15:
        score += 0.5
        reasons.append(f"PE {pe:.1f}（估值偏低）")
    elif pe > 50:
        score -= 1.0
        reasons.append(f"PE {pe:.1f}（估值偏高）")

    # ── 分析师预期 ──
    if forecast and forecast.get("rating"):
        rating = str(forecast["rating"])
        if rating in ["买入", "强烈推荐", "推荐"]:
            score += 1.0
            reasons.append(f"机构评级：{rating}（乐观）")
        elif rating in ["减持", "卖出", "回避"]:
            score -= 1.5
            reasons.append(f"机构评级：{rating}（谨慎）")

    # ── 近期价格走势 ──
    pct = price_info.get("pct_change", 0)
    if pct > 10:
        score += 0.5
        reasons.append(f"近期涨幅 {pct:.1f}%（趋势向上）")
    elif pct < -10:
        score -= 0.5
        reasons.append(f"近期跌幅 {abs(pct):.1f}%（趋势向下）")

    # ── 综合判定 ──
    if score >= 3.0:
        verdict = "bullish"
    elif score <= -2.0:
        verdict = "bearish"
    else:
        verdict = "neutral"

    if not reasons:
        reasons.append("数据不足以判断，默认中性")

    return {
        "verdict": verdict,
        "score": round(score, 1),
        "reasons": reasons,
        "llm_summary": None,  # 可选 LLM 补充
    }


def generate_llm_pre_earnings_analysis(
    symbol: str,
    name: str,
    metrics: dict,
    price_info: dict,
) -> Optional[str]:
    """使用 LLM 生成预判摘要"""
    try:
        from src.tools.openrouter_config import get_chat_completion
        prompt = PRE_EARNINGS_PROMPT.format(
            symbol=symbol,
            name=name,
            price=price_info.get("price", "N/A"),
            pct_change=price_info.get("pct_change", 0),
            market_cap=metrics.get("market_cap", 0),
            pe_ratio=metrics.get("pe_ratio", "N/A"),
            roe=metrics.get("roe", 0),
            earnings_growth=metrics.get("earnings_growth", 0),
            revenue_growth=metrics.get("revenue_growth", 0),
            debt_to_equity=metrics.get("debt_to_equity", 0),
            current_ratio=metrics.get("current_ratio", 0),
            free_cash_flow_per_share=metrics.get("free_cash_flow_per_share", 0),
            sector="A股",
        )
        result = get_chat_completion([{"role": "user", "content": prompt}])
        return result
    except Exception as e:
        logger.debug(f"LLM 预判分析失败: {e}")
        return None


def generate_llm_post_earnings_analysis(
    symbol: str,
    name: str,
    period: str,
    publish_date: str,
    earnings_data: dict,
    announcement_summary: str,
    forecast: Optional[dict],
) -> Optional[str]:
    """使用 LLM 生成财报复盘摘要"""
    try:
        from src.tools.openrouter_config import get_chat_completion
        prompt = POST_EARNINGS_PROMPT.format(
            symbol=symbol,
            name=name,
            period=period,
            publish_date=publish_date,
            revenue=earnings_data.get("revenue", "N/A"),
            net_income=earnings_data.get("net_income", "N/A"),
            roe=earnings_data.get("roe", 0),
            eps=earnings_data.get("eps", 0),
            free_cash_flow=earnings_data.get("free_cash_flow", 0),
            forecast_net_income=forecast.get("forecast_net_income", "N/A") if forecast else "无",
            forecast_eps=forecast.get("forecast_eps", "N/A") if forecast else "无",
            announcement_summary=announcement_summary or "暂无摘要",
        )
        result = get_chat_completion([{"role": "user", "content": prompt}])
        return result
    except Exception as e:
        logger.debug(f"LLM 财报复盘失败: {e}")
        return None


# ═══════════════════════════════════════════════════════
# 3. 飞书通知
# ═══════════════════════════════════════════════════════

def build_pre_earnings_feishu_card(
    symbol: str,
    name: str,
    period: str,
    report_date: str,
    verdict: str,
    score: float,
    reasons: list,
    llm_summary: Optional[str] = None,
) -> tuple[str, str]:
    """
    构建预财报飞书卡片内容

    Returns: (title, body)
    """
    emoji = "📈" if verdict == "bullish" else "📉" if verdict == "bearish" else "📊"
    verdict_text = {
        "bullish": "✅ 预判利好",
        "bearish": "⚠️ 预判利空",
        "neutral": "➖ 预判中性",
    }.get(verdict, "➖ 预判中性")

    title = f"{emoji} 财报预警 | {name}({symbol}) | {period}"

    body_lines = [
        f"📅 预计发布: {report_date}",
        f"🎯 {verdict_text} (评分: {score:+.1f})",
        "",
        "**关键条件:**",
    ]
    for reason in reasons[:5]:
        body_lines.append(f"• {reason}")

    if llm_summary:
        body_lines.append("")
        body_lines.append("**LLM 预判:**")
        body_lines.append(llm_summary[:500])

    return title, "\n".join(body_lines)


def build_post_earnings_feishu_card(
    symbol: str,
    name: str,
    period: str,
    verdict: str,
    llm_summary: str,
) -> tuple[str, str]:
    """构建财报复盘飞书卡片"""
    emoji = "📈" if verdict == "bullish" else "📉" if verdict == "bearish" else "📊"
    verdict_text = {
        "bullish": "✅ 利好",
        "bearish": "⚠️ 利空",
        "neutral": "➖ 中性",
    }.get(verdict, "➖ 中性")

    title = f"{emoji} 财报复盘 | {name}({symbol}) | {period}"

    body_lines = [
        f"📋 发布日期: {datetime.now().strftime('%Y-%m-%d')}",
        f"🎯 解读: {verdict_text}",
        "",
        llm_summary[:800] if llm_summary else "暂无分析",
    ]
    return title, "\n".join(body_lines)


def send_feishu_alert(title: str, body: str, symbol: str = "", change_pct: float = 0.0) -> bool:
    """发送飞书预警

    注意: feishu_send(title, message, stock_info=X) 中，如果提供 stock_info，
    它会用自己的格式重组 body，忽略 message。
    因此这里不传 stock_info，直接用 message 承载完整内容，
    通过 change_pct 控制 header 颜色。
    """
    try:
        # 传 stock_info=None 让 feishu_send 直接用 title+body，
        # change_pct 只影响 header 颜色
        return feishu_send(title, body, change_pct=change_pct, stock_info=None)
    except Exception as e:
        logger.warning(f"飞书通知发送失败: {e}")
        return False


# ═══════════════════════════════════════════════════════
# 4. 缓存管理
# ═══════════════════════════════════════════════════════

def _get_db_connection() -> sqlite3.Connection:
    """获取 trading.db 连接"""
    TRADING_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(TRADING_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _get_cached_earnings(days: int = 7) -> list:
    """获取缓存的财报日历，返回指定天数内即将发布的"""
    today = datetime.now().strftime("%Y-%m-%d")
    cutoff = (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")
    result = []
    try:
        with _get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT symbol, report_date, name, source FROM earnings_calendar WHERE report_date >= ? AND report_date <= ?",
                (today, cutoff),
            )
            for row in cursor.fetchall():
                result.append({
                    "symbol": row["symbol"],
                    "report_date": row["report_date"],
                    "name": row["name"],
                    "source": row["source"],
                })
    except Exception as e:
        logger.warning(f"读取 earnings_calendar 缓存失败: {e}")
    return result


def _update_cached_earnings(new_earnings: list):
    """更新即将发布的财报缓存到 earnings_calendar 表"""
    now = datetime.now().isoformat()
    try:
        with _get_db_connection() as conn:
            for e in new_earnings:
                symbol = e.get("symbol") or e.get("code", "")
                report_date = e.get("report_date", "")[:10] if e.get("report_date") else ""
                name = e.get("name") or e.get("简称", "")
                if not symbol or not report_date:
                    continue
                conn.execute(
                    """
                    INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(symbol, report_date) DO UPDATE SET
                        name=excluded.name,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                    """,
                    (symbol, report_date, name, "akshare", now),
                )
            conn.commit()
        logger.info(f"财报日历缓存已更新: {len(new_earnings)} 条记录")
    except Exception as e:
        logger.warning(f"更新 earnings_calendar 缓存失败: {e}")


def _mark_as_analyzed(symbol: str, period: str):
    """标记为已分析，避免重复推送"""
    now = datetime.now().isoformat()
    try:
        with _get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO earnings_history (symbol, report_date, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol, report_date) DO UPDATE SET
                    created_at=excluded.created_at
                """,
                (symbol, period, now),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"标记已分析失败: {e}")


def _is_already_alerted(symbol: str, period: str, alert_type: str) -> bool:
    """检查是否已发送过该类型的预警"""
    try:
        with _get_db_connection() as conn:
            cursor = conn.execute(
                "SELECT 1 FROM earnings_history WHERE symbol = ? AND report_date = ?",
                (symbol, period),
            )
            return cursor.fetchone() is not None
    except Exception as e:
        logger.warning(f"检查预警状态失败: {e}")
        return False


def _mark_alerted(symbol: str, period: str, alert_type: str, verdict: str = ""):
    """标记为已发送预警"""
    now = datetime.now().isoformat()
    try:
        with _get_db_connection() as conn:
            conn.execute(
                """
                INSERT INTO earnings_history (symbol, report_date, eps, revenue, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(symbol, report_date) DO UPDATE SET
                    eps=excluded.eps,
                    revenue=excluded.revenue,
                    created_at=excluded.created_at
                """,
                (symbol, period, None, None, now),
            )
            conn.commit()
    except Exception as e:
        logger.warning(f"标记预警失败: {e}")


# ═══════════════════════════════════════════════════════
# 5. 主流程
# ═══════════════════════════════════════════════════════

class EarningsCalendar:
    """
    财报日历主类

    负责：
    1. 爬取/缓存财报发布日期
    2. 在关键时间点触发预判分析和飞书预警
    3. 财报发布后自动触发复盘分析
    """

    def __init__(self):
        self.today = datetime.now().strftime("%Y-%m-%d")
        self._watchlist: list = []

    def _load_watchlist(self) -> list:
        """从 API 或配置文件读取自选股"""
        # 优先通过 API 读取（如果服务可用）
        try:
            import requests
            resp = requests.get(
                "http://localhost:3120/api/positions",
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json()
                return [s.get("symbol") or s.get("code") for s in data.get("positions", [])]
        except Exception:
            pass

        # 回退：从配置文件读取
        try:
            from src.utils.config_reader import read_monitor_config
            config = read_monitor_config()
            return list(config.get("watchlist", {}).keys())
        except Exception:
            return []

    def refresh_calendar(self, days_ahead: int = 30) -> list:
        """
        刷新财报日历缓存，返回即将发布的财报列表

        Args:
            days_ahead: 向前查询的天数

        Returns:
            list of dict: 即将发布的财报
        """
        end_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        watchlist = self._load_watchlist()

        # 优先使用自选股列表（快速逐只查询），无自选股则用全市场接口
        earnings = fetch_earnings_calendar(
            self.today, end_date,
            symbols=watchlist if watchlist else None
        )

        # 标准化字段名（新API返回 code/name/report_date/period）
        for e in earnings:
            e["symbol"] = e.get("code") or e.get("代码", "")
            e["name"] = e.get("name") or e.get("简称", "")
            e["report_date"] = e.get("report_date") or (e.get("公告时间") or "")[:10]
            e["period"] = e.get("period") or e.get("公告标题", "")[:50]

        _update_cached_earnings(earnings)
        logger.info(f"财报日历刷新: {len(earnings)} 条记录")
        return earnings

    def check_and_alert(self):
        """
        主检查函数：由 cron 或 daemon 定期调用

        逻辑：
        1. 刷新日历缓存
        2. 对每条即将发布的财报：
           - 提前 N 天 → 发预判预警（飞书）
           - 已发布 → 发复盘分析（飞书）
        """
        logger.info("开始财报日历检查...")

        # 加载自选股
        self._watchlist = self._load_watchlist()

        # 刷新日历（未来30天）
        upcoming = self.refresh_calendar(days_ahead=30)
        if not upcoming:
            logger.info("近期无待发布财报")
            return

        now = datetime.now()
        alerted_count = 0

        for entry in upcoming:
            symbol = entry.get("symbol") or entry.get("代码", "")
            name = entry.get("name") or entry.get("简称", symbol)
            period = entry.get("period") or entry.get("公告标题", "")[:30]
            report_date = entry.get("report_date") or (entry.get("公告时间") or "")[:10]

            if not symbol or not report_date:
                continue

            # ── 跳过非自选股 ──
            if self._watchlist and symbol not in self._watchlist:
                continue

            # ── 跳过已完成的 ──
            if _is_already_alerted(symbol, period, "post"):
                continue

            try:
                report_dt = datetime.strptime(report_date, "%Y-%m-%d")
            except ValueError:
                continue

            days_until = (report_dt - now).days

            # ── 情况1: 提前 N 天预警（预判） ──
            if 0 < days_until <= ALERT_BEFORE_DAYS:
                if _is_already_alerted(symbol, period, "pre"):
                    continue

                logger.info(f"预判预警: {name}({symbol}) {period} ({days_until}天后)")

                # 获取财务数据
                metrics = get_stock_financial_metrics(symbol)
                forecast = fetch_profit_forecast(symbol)
                price_info = {
                    "price": metrics.get("market_cap", 0) / 1e8 * 10,  # 估算
                    "pct_change": 0,
                }

                # 扫描利好/利空条件
                analysis = analyze_pre_earnings_bullish_bearish(
                    symbol, name, metrics, forecast, price_info
                )

                # 尝试 LLM 补充
                llm_summary = generate_llm_pre_earnings_analysis(
                    symbol, name, metrics, price_info
                )

                # 构建并发送飞书
                change_pct = 5.0 if analysis["verdict"] == "bullish" else -5.0 if analysis["verdict"] == "bearish" else 0.0
                title, body = build_pre_earnings_feishu_card(
                    symbol, name, period, report_date,
                    analysis["verdict"], analysis["score"],
                    analysis["reasons"], llm_summary,
                )

                if send_feishu_alert(title, body, symbol, change_pct=change_pct):
                    _mark_alerted(symbol, period, "pre", analysis["verdict"])
                    alerted_count += 1

            # ── 情况2: 财报已发布（复盘） ──
            elif days_until <= 0:
                if _is_already_alerted(symbol, period, "post"):
                    continue

                logger.info(f"财报复盘: {name}({symbol}) {period}")

                # 获取历史财报数据
                history = fetch_recent_earnings(symbol, limit=3)
                summary = "\n".join(
                    f"- {h.get('公告标题', '')[:50]} ({h.get('公告时间', '')[:10]})"
                    for h in history[:3]
                ) if history else "暂无历史数据"

                forecast = fetch_profit_forecast(symbol)
                metrics = get_stock_financial_metrics(symbol)

                # LLM 复盘分析
                llm_summary = generate_llm_post_earnings_analysis(
                    symbol, name, period, report_date,
                    {
                        "revenue": metrics.get("revenue", "N/A"),
                        "net_income": metrics.get("net_income", "N/A"),
                        "roe": metrics.get("roe", 0),
                        "eps": metrics.get("earnings_per_share", 0),
                        "free_cash_flow": metrics.get("free_cash_flow_per_share", 0),
                    },
                    summary,
                    forecast,
                )

                # 简单判断（基于LLM摘要关键词）
                verdict = "neutral"
                if llm_summary:
                    text = llm_summary.lower()
                    bullish_kw = ["超预期", "增长", "利好", "买入", "盈利", "强劲", "超预期"]
                    bearish_kw = ["低于预期", "下滑", "利空", "亏损", "风险", "减少"]
                    if any(k in text for k in bullish_kw):
                        verdict = "bullish"
                    elif any(k in text for k in bearish_kw):
                        verdict = "bearish"

                verdict_pct = 5.0 if verdict == "bullish" else -5.0 if verdict == "bearish" else 0.0

                # 发送飞书
                title, body = build_post_earnings_feishu_card(
                    symbol, name, period, verdict, llm_summary or "暂无分析"
                )

                if send_feishu_alert(title, body, symbol, change_pct=verdict_pct):
                    _mark_alerted(symbol, period, "post", verdict)
                    alerted_count += 1

        logger.info(f"财报日历检查完成: 发送 {alerted_count} 条预警")

    def print_upcoming_earnings(self, days_ahead: int = 7):
        """打印即将发布的财报（供命令行查看）"""
        upcoming = _get_cached_earnings(days=days_ahead)
        if not upcoming:
            print(f"\n📅 未来 {days_ahead} 天无待发布财报")
            return

        print(f"\n📅 未来 {days_ahead} 天待发布财报 ({len(upcoming)} 条):")
        print(f"{'代码':<10} {'名称':<12} {'预计日期':<12} {'来源':<10}")
        print("-" * 50)
        for e in upcoming:
            print(
                f"{e.get('symbol', ''):<10} "
                f"{e.get('name', ''):<12} "
                f"{e.get('report_date', ''):<12} "
                f"{e.get('source', ''):<10}"
            )


# ═══════════════════════════════════════════════════════
# 6. CLI 入口
# ═══════════════════════════════════════════════════════

def main():
    """命令行运行财报日历检查"""
    # 单例锁
    try:
        lock_fd = open(LOCK_FILE, "w")
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("财报日历已在运行中，跳过")
        return

    ec = EarningsCalendar()

    import argparse
    parser = argparse.ArgumentParser(description="财报日历")
    parser.add_argument("--days", "-d", type=int, default=7, help="查看未来N天")
    parser.add_argument("--refresh", "-r", action="store_true", help="强制刷新日历")
    parser.add_argument("--alert", "-a", action="store_true", help="执行预警检查")
    args = parser.parse_args()

    if args.alert:
        ec.check_and_alert()
    elif args.refresh:
        ec.refresh_calendar(days_ahead=30)
        ec.print_upcoming_earnings(days_ahead=args.days)
    else:
        ec.print_upcoming_earnings(days_ahead=args.days)


if __name__ == "__main__":
    main()
