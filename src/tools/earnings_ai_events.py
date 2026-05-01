"""
Phase 2: 持仓 + star 股票 × earnings_calendar → ai_investment_events（仅 DB，供 notifier 消费）。

- 只读 config.db: monitor_watchlist（持仓 type=holding 或 star=1，排除 hidden）
- 只读 trading.db: earnings_calendar
- 倒计时：T-5 / T-3 / T-1 / T-0 各一条；持仓在 T-1/T-0 → feishu_normal。
- 跟进：披露日已过、且在过后 N 个自然日内 → earnings_post_window（持仓 → feishu_normal）。
- metrics 附带：对应市场 `earnings_market`、`trading_sessions_until_report`（(ref, report] 交易日数）；
  若 `earnings_history` 有可比 eps/营收，写入环比增幅字段。
- 触发仍按自然日窗口（与 dedupe 一致）。
- dedupe_key 幂等。
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

from src.sim_trading.db import get_connection
from src.tools.trading_calendar import is_trading_day
from src.utils.config_reader import read_monitor_config
from src.utils.logging_config import setup_logger

logger = setup_logger("earnings_ai_events")

# 距 report_date 的自然日差 → 窗口标签（与业务「交易日」近似，骨架阶段用自然日）
COUNTDOWN_DAYS = (
    (5, "T5", "normal", "T-5"),
    (3, "T3", "normal", "T-3"),
    (1, "T1", "high", "T-1"),
    (0, "T0", "high", "T-0"),
)

DELIVERY_SCOPE_DEFAULT = "web_only"
DELIVERY_SCOPE_HOLDING_EVE = "feishu_normal"
SOURCE = "earnings_calendar"

# 预计披露日过后的跟进窗口（自然日，含 report_date 次日直到第 N 天）
POST_REPORT_WINDOW_DAYS = 7


def infer_earnings_market(watch_symbol: str) -> str:
    """根据自选代码推断财报日历市场（用于交易日计数）。"""
    u = (watch_symbol or "").strip().upper()
    if u.startswith("HK"):
        return "HK"
    tail = u[2:] if u.startswith("HK") else u
    if tail.isdigit() and len(tail) <= 5:
        return "HK"
    return "CN"


def _parse_iso_date(value: str) -> Optional[date]:
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def trading_sessions_until_report(ref: date, report: date, market: str) -> int:
    """严格落在 (ref, report] 的交易日数量；report <= ref 时为 0。"""
    if report <= ref:
        return 0
    m = (market or "CN").upper()
    n = 0
    d = ref
    while True:
        d = d + timedelta(days=1)
        if d > report:
            break
        if is_trading_day(m, d.strftime("%Y-%m-%d")):
            n += 1
    return n


def symbol_lookup_variants(cal_symbol: str, matched_symbol: str) -> list[str]:
    """用于 earnings_history 查询的代码变体（列里可能是 6 位或带前缀）。"""
    seen: set[str] = set()
    out: list[str] = []
    for raw in (cal_symbol, matched_symbol, _strip_cn_prefix(cal_symbol), _strip_cn_prefix(matched_symbol)):
        s = (raw or "").strip()
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _float_or_none(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _pct_change_older_to_newer(older: float, newer: float) -> Optional[float]:
    if older == 0:
        return None
    return round((newer - older) / abs(older) * 100, 2)


def fetch_earnings_history_metrics(conn, variants: list[str]) -> dict[str, Any]:
    """最近披露中带 eps/revenue 的记录，计算上一期→最近一期增幅（若可比）。"""
    out: dict[str, Any] = {}
    if not variants:
        return out
    ph = ",".join("?" * len(variants))
    rows = conn.execute(
        f"""
        SELECT report_date, eps, revenue FROM earnings_history
        WHERE symbol IN ({ph})
        ORDER BY report_date DESC
        LIMIT 24
        """,
        variants,
    ).fetchall()

    dated: list[tuple[date, Optional[float], Optional[float]]] = []
    for r in rows:
        rd = _parse_iso_date(str(r["report_date"] or ""))
        if rd is None:
            continue
        eps = _float_or_none(r["eps"])
        rev = _float_or_none(r["revenue"])
        if eps is None and rev is None:
            continue
        dated.append((rd, eps, rev))

    if not dated:
        return out

    # 同一报告期可能多条；按日期聚合保留首个有值的 eps/revenue
    eps_series: list[tuple[date, float]] = []
    rev_series: list[tuple[date, float]] = []
    seen_eps: set[date] = set()
    seen_rev: set[date] = set()
    for rd, eps, rev in dated:
        if eps is not None and rd not in seen_eps:
            seen_eps.add(rd)
            eps_series.append((rd, eps))
        if rev is not None and rd not in seen_rev:
            seen_rev.add(rd)
            rev_series.append((rd, rev))

    eps_series.sort(key=lambda x: x[0], reverse=True)
    rev_series.sort(key=lambda x: x[0], reverse=True)

    if len(eps_series) >= 2:
        d0, e0 = eps_series[0]
        d1, e1 = eps_series[1]
        ch = _pct_change_older_to_newer(e1, e0)
        if ch is not None:
            out["history_eps_recent_date"] = d0.strftime("%Y-%m-%d")
            out["history_eps_prior_date"] = d1.strftime("%Y-%m-%d")
            out["history_eps_recent"] = e0
            out["history_eps_prior"] = e1
            out["history_eps_period_over_period_pct"] = ch

    if len(rev_series) >= 2:
        d0, r0 = rev_series[0]
        d1, r1 = rev_series[1]
        ch = _pct_change_older_to_newer(r1, r0)
        if ch is not None:
            out["history_revenue_recent_date"] = d0.strftime("%Y-%m-%d")
            out["history_revenue_prior_date"] = d1.strftime("%Y-%m-%d")
            out["history_revenue_recent"] = r0
            out["history_revenue_prior"] = r1
            out["history_revenue_period_over_period_pct"] = ch

    return out


def _enrich_metrics_and_reasons(
    conn,
    metrics: dict[str, Any],
    reasons: list[str],
    cal_sym: str,
    matched: str,
    ref: date,
    report_d: date,
) -> None:
    market = infer_earnings_market(matched)
    metrics["earnings_market"] = market
    metrics["trading_sessions_until_report"] = trading_sessions_until_report(ref, report_d, market)
    hist = fetch_earnings_history_metrics(conn, symbol_lookup_variants(cal_sym, matched))
    metrics.update(hist)
    eps_ch = hist.get("history_eps_period_over_period_pct")
    if eps_ch is not None:
        reasons.append(f"历史披露 EPS 较上一期: {eps_ch:+.1f}%")
    rev_ch = hist.get("history_revenue_period_over_period_pct")
    if rev_ch is not None:
        reasons.append(f"历史披露营收较上一期: {rev_ch:+.1f}%")


def _strip_cn_prefix(sym: str) -> str:
    s = (sym or "").strip().upper()
    for p in ("SH", "SZ", "BJ"):
        if s.startswith(p) and len(s) > len(p):
            return s[len(p) :]
    return s


def _hk_five_digits(sym: str) -> Optional[str]:
    """港股统一为 5 位数字串（不含 HK 前缀），无法识别则返回 None。"""
    s = (sym or "").strip().upper()
    if s.startswith("HK") and len(s) > 2:
        tail = s[2:]
        if tail.isdigit():
            return tail.zfill(5)
        return None
    if s.isdigit() and len(s) <= 5:
        return s.zfill(5)
    return None


def symbols_match(watch: str, cal: str) -> bool:
    """宽松匹配 A 股代码 / 港股代码。"""
    w = (watch or "").strip()
    c = (cal or "").strip()
    if w == c:
        return True
    if w.upper() == c.upper():
        return True
    hw, hc = _hk_five_digits(w), _hk_five_digits(c)
    if hw is not None and hc is not None and hw == hc:
        return True
    # A 股：去 SH/SZ 后比 6 位
    wn, cn = _strip_cn_prefix(w), _strip_cn_prefix(c)
    if wn.isdigit() and cn.isdigit() and len(wn) == 6 and len(cn) == 6:
        return wn == cn
    return False


def earnings_watch_symbols_holding_flag() -> dict[str, bool]:
    """纳入财报倒计时的代码 → 是否真实持仓（type=holding）。

    规则：未 hidden，且 (持仓 或 加星)。仅加星未持仓的为 False。
    """
    cfg = read_monitor_config()
    out: dict[str, bool] = {}
    for sym, entry in cfg.get("watchlist", {}).items():
        if not sym:
            continue
        if entry.get("hidden"):
            continue
        is_holding = entry.get("type") == "holding"
        if is_holding or entry.get("star"):
            out[sym] = is_holding
    return out


def _delivery_scope_for_countdown(is_holding: bool, window_tag: str) -> str:
    """T-1 / T-0 且真实持仓 → 飞书（normal）；其余 Web。"""
    if is_holding and window_tag in ("T1", "T0"):
        return DELIVERY_SCOPE_HOLDING_EVE
    return DELIVERY_SCOPE_DEFAULT


def _delivery_scope_post_report(is_holding: bool) -> str:
    """披露日后跟进：持仓飞书提醒一次（幂等 dedupe），自选仅 Web。"""
    if is_holding:
        return DELIVERY_SCOPE_HOLDING_EVE
    return DELIVERY_SCOPE_DEFAULT


def emit_holdings_star_earnings_ai_events(
    *,
    ref_date: Optional[date] = None,
    calendar_horizon_days: int = 45,
) -> int:
    """
    扫描 earnings_calendar 与持仓/star 交集：倒计时窗口 + 披露日后短期跟进。

    Returns:
        本次成功新插入的行数（INSERT OR IGNORE 计入新行）。
    """
    ref = ref_date or date.today()
    watch_roles = earnings_watch_symbols_holding_flag()
    if not watch_roles:
        logger.debug("earnings_ai_events: no holdings/star symbols, skip")
        return 0

    start_d = date.fromordinal(ref.toordinal() - POST_REPORT_WINDOW_DAYS)
    end_d = date.fromordinal(ref.toordinal() + calendar_horizon_days)
    start_s = start_d.strftime("%Y-%m-%d")
    end_s = end_d.strftime("%Y-%m-%d")

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT symbol, report_date, name, source
            FROM earnings_calendar
            WHERE report_date >= ? AND report_date <= ?
            ORDER BY report_date, symbol
            """,
            (start_s, end_s),
        ).fetchall()
    finally:
        conn.close()

    inserted = 0
    now_iso = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    event_day = ref.strftime("%Y-%m-%d")

    conn_ins = get_connection()
    try:
        for row in rows:
            cal_sym = (row["symbol"] or "").strip()
            report_date = (row["report_date"] or "")[:10]
            name = (row["name"] or "").strip() or cal_sym
            if not cal_sym or not report_date:
                continue

            matched = next((w for w in watch_roles if symbols_match(w, cal_sym)), None)
            if not matched:
                continue

            try:
                rd = datetime.strptime(report_date, "%Y-%m-%d").date()
            except ValueError:
                continue

            days_until = (rd - ref).days
            is_holding = bool(watch_roles.get(matched))

            window = next((w for w in COUNTDOWN_DAYS if w[0] == days_until), None)
            if window is not None:
                _days, tag, severity, label_cn = window
                delivery_scope = _delivery_scope_for_countdown(is_holding, tag)
                dedupe_key = f"earnings:{tag}:{matched}:{report_date}"
                title = f"财报窗口 {label_cn} · {name} ({matched})"
                summary = (
                    f"预计财报披露日 {report_date}（自然日倒计时 {label_cn}）。"
                    f"请关注仓位与波动；详细分析见日报/财报工具。"
                )
                reasons = [
                    f"窗口: {label_cn}",
                    f"预计发布日: {report_date}",
                    f"日历来源: {row['source'] or 'unknown'}",
                ]
                metrics = {
                    "phase": "countdown",
                    "days_until": days_until,
                    "report_date": report_date,
                    "window": tag,
                    "calendar_symbol": cal_sym,
                    "is_holding": is_holding,
                }
                event_type = "earnings_countdown"
                _enrich_metrics_and_reasons(
                    conn_ins, metrics, reasons, cal_sym, matched, ref, rd
                )
            elif -POST_REPORT_WINDOW_DAYS <= days_until <= -1:
                days_past = -days_until
                delivery_scope = _delivery_scope_post_report(is_holding)
                dedupe_key = f"earnings:post:{matched}:{report_date}"
                title = f"财报披露跟进 · {name} ({matched})"
                summary = (
                    f"预计披露日 {report_date} 已过去 {days_past} 个自然日。"
                    f"请关注正式公告、业绩对比与股价反应。"
                )
                reasons = [
                    f"预计披露日: {report_date}",
                    f"已过自然日: {days_past}",
                    f"日历来源: {row['source'] or 'unknown'}",
                ]
                metrics = {
                    "phase": "post_report",
                    "days_since_report_date": days_past,
                    "report_date": report_date,
                    "calendar_symbol": cal_sym,
                    "is_holding": is_holding,
                }
                event_type = "earnings_post_window"
                severity = "high" if is_holding else "normal"
                _enrich_metrics_and_reasons(
                    conn_ins, metrics, reasons, cal_sym, matched, ref, rd
                )
            else:
                continue

            try:
                conn_ins.execute(
                    """
                    INSERT OR IGNORE INTO ai_investment_events (
                        id, event_date, symbol, name, source, event_type, severity,
                        delivery_scope, verdict, confidence, dedupe_key,
                        source_record_id, source_run_id,
                        title, summary, reasons_json, metrics_json, recommendation_json,
                        notify_status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        event_day,
                        matched,
                        name,
                        SOURCE,
                        event_type,
                        severity,
                        delivery_scope,
                        "observe",
                        0.55,
                        dedupe_key,
                        f"{cal_sym}:{report_date}",
                        None,
                        title,
                        summary,
                        json.dumps(reasons, ensure_ascii=False),
                        json.dumps(metrics, ensure_ascii=False),
                        None,
                        "pending",
                        now_iso,
                        now_iso,
                    ),
                )
                if conn_ins.execute("SELECT changes()").fetchone()[0] >= 1:
                    inserted += 1
            except Exception as e:
                logger.warning(
                    "earnings_ai_events insert failed %s: %s", dedupe_key, e
                )
        conn_ins.commit()
    finally:
        conn_ins.close()

    if inserted:
        logger.info("earnings_ai_events: inserted %s new ai_investment_events", inserted)
    return inserted
