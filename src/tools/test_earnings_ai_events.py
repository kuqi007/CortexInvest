"""Tests for earnings → ai_investment_events (Phase 2 skeleton)."""

import json
from datetime import date
import pytest

import src.sim_trading.db as db
from src.tools.earnings_ai_events import (
    emit_holdings_star_earnings_ai_events,
    infer_earnings_market,
    symbols_match,
)


@pytest.fixture(autouse=True)
def _disable_pre_earnings_network_by_default(monkeypatch):
    """避免各用例隐式拉财务/akshare；需要预判的用例再单独 patch。"""
    monkeypatch.setattr(
        "src.tools.earnings_ai_events._pre_earnings_scan_for_emit",
        lambda *_a, **_k: {},
    )


@pytest.mark.parametrize(
    "a,b,expect",
    [
        ("000001", "000001", True),
        ("SH600000", "600000", True),
        ("HK00700", "HK00700", True),
        ("HK00700", "00700", True),
        ("000001", "000002", False),
    ],
)
def test_symbols_match(a, b, expect):
    assert symbols_match(a, b) is expect


@pytest.mark.parametrize(
    "sym,market",
    [
        ("HK00700", "HK"),
        ("00700", "HK"),
        ("600519", "CN"),
        ("SH600519", "CN"),
    ],
)
def test_infer_earnings_market(sym, market):
    assert infer_earnings_market(sym) == market


def test_countdown_includes_pre_earnings_when_scan_returns(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    def fake_scan(cal_sym, matched, name):
        return {
            "pre_earnings": {
                "verdict": "bullish",
                "score": 3.2,
                "reasons_sample": ["净利润增长 25.0%（强劲）", "x"],
                "symbol_used": "000001",
                "metrics_nonempty": True,
                "source": "unit",
            }
        }

    monkeypatch.setattr(
        "src.tools.earnings_ai_events._pre_earnings_scan_for_emit",
        fake_scan,
    )

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "000001",
            "平安",
            None,
            "holding",
            10.0,
            100,
            100,
            0,
            0,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('000001', '2026-06-06', '平安银行', 'test', '2026-06-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 6, 1)
    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 1

    conn = db.get_connection()
    row = conn.execute(
        "SELECT metrics_json, recommendation_json FROM ai_investment_events"
    ).fetchone()
    conn.close()

    m = json.loads(row["metrics_json"])
    assert m["pre_earnings"]["verdict"] == "bullish"
    assert m["pre_earnings"]["score"] == 3.2
    rec = json.loads(row["recommendation_json"])
    assert rec["pre_earnings_verdict"] == "bullish"
    assert rec["pre_earnings_score"] == 3.2


def test_emit_t5_creates_pending_web_only(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "000001",
            "平安",
            None,
            "holding",
            10.0,
            100,
            100,
            0,
            0,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('000001', '2026-06-06', '平安银行', 'test', '2026-06-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 6, 1)
    n = emit_holdings_star_earnings_ai_events(ref_date=ref)
    assert n == 1

    conn = db.get_connection()
    row = conn.execute(
        "SELECT delivery_scope, notify_status, dedupe_key, severity FROM ai_investment_events"
    ).fetchone()
    conn.close()

    assert row["delivery_scope"] == "web_only"
    assert row["notify_status"] == "pending"
    assert row["dedupe_key"] == "earnings:T5:000001:2026-06-06"
    assert row["severity"] == "normal"

    n2 = emit_holdings_star_earnings_ai_events(ref_date=ref)
    assert n2 == 0


def test_countdown_enriches_trading_sessions_and_history(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    monkeypatch.setattr(
        "src.tools.earnings_ai_events.is_trading_day",
        lambda _m, _ds=None: True,
    )

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "000001",
            "平安",
            None,
            "holding",
            10.0,
            100,
            100,
            0,
            0,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('000001', '2026-06-06', '平安银行', 'test', '2026-06-01T00:00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO earnings_history (symbol, report_date, eps, revenue, created_at)
        VALUES ('000001', '2025-06-30', 0.8, 80.0, '2025-07-01T00:00:00')
        """
    )
    conn.execute(
        """
        INSERT INTO earnings_history (symbol, report_date, eps, revenue, created_at)
        VALUES ('000001', '2025-09-30', 1.0, 100.0, '2025-10-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 6, 1)
    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 1

    conn = db.get_connection()
    row = conn.execute(
        "SELECT metrics_json, reasons_json FROM ai_investment_events LIMIT 1"
    ).fetchone()
    conn.close()

    metrics = json.loads(row["metrics_json"])
    assert metrics["earnings_market"] == "CN"
    assert metrics["trading_sessions_until_report"] == 5
    assert metrics["history_eps_period_over_period_pct"] == pytest.approx(25.0)
    assert metrics["history_revenue_period_over_period_pct"] == pytest.approx(25.0)

    reasons = json.loads(row["reasons_json"])
    assert any("EPS" in r for r in reasons)
    assert any("营收" in r for r in reasons)


def test_session_milestone_when_calendar_does_not_match(tmp_path, monkeypatch):
    """自然日未命中 T5，但交易日口径视为 T5 → earnings_countdown_session。"""
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    monkeypatch.setattr(
        "src.tools.earnings_ai_events.trading_sessions_until_report",
        lambda _ref, _rd, _m: 5,
    )

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "600588",
            "用友",
            None,
            "holding",
            15.0,
            100,
            100,
            0,
            0,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('600588', '2026-07-15', '用友网络', 'test', '2026-06-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 6, 1)
    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 1

    conn = db.get_connection()
    row = conn.execute(
        "SELECT event_type, dedupe_key FROM ai_investment_events"
    ).fetchone()
    conn.close()

    assert row["event_type"] == "earnings_countdown_session"
    assert row["dedupe_key"] == "earnings:sessT5:600588:2026-07-15"

    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 0


def test_star_non_holding_included(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "600519",
            "茅台",
            None,
            "watching",
            None,
            None,
            100,
            0,
            1,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('600519', '2026-03-06', '贵州茅台', 'test', '2026-03-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 3, 1)
    n = emit_holdings_star_earnings_ai_events(ref_date=ref)
    assert n == 1


def test_holding_t1_uses_feishu_normal(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "000002",
            "万科",
            None,
            "holding",
            10.0,
            100,
            100,
            0,
            0,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('000002', '2026-07-02', '万科A', 'test', '2026-07-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 7, 1)
    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 1

    conn = db.get_connection()
    row = conn.execute(
        "SELECT delivery_scope, dedupe_key FROM ai_investment_events"
    ).fetchone()
    conn.close()

    assert row["delivery_scope"] == "feishu_normal"
    assert row["dedupe_key"] == "earnings:T1:000002:2026-07-02"


def test_star_only_t1_stays_web_only(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "300750",
            "宁德",
            None,
            "watching",
            None,
            None,
            100,
            0,
            1,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('300750', '2026-08-02', '宁德时代', 'test', '2026-08-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 8, 1)
    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 1

    conn = db.get_connection()
    scope = conn.execute(
        "SELECT delivery_scope FROM ai_investment_events"
    ).fetchone()[0]
    conn.close()

    assert scope == "web_only"


def test_post_window_holding_feishu(tmp_path, monkeypatch):
    """预计披露日已过 3 个自然日 → earnings_post_window，持仓走 feishu_normal。"""
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "688001",
            "华兴",
            None,
            "holding",
            20.0,
            100,
            100,
            0,
            0,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('688001', '2026-09-05', '华兴源创', 'test', '2026-09-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 9, 8)
    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 1

    conn = db.get_connection()
    row = conn.execute(
        """
        SELECT delivery_scope, event_type, dedupe_key, severity
        FROM ai_investment_events
        """
    ).fetchone()
    conn.close()

    assert row["event_type"] == "earnings_post_window"
    assert row["delivery_scope"] == "feishu_normal"
    assert row["dedupe_key"] == "earnings:post:688001:2026-09-05"
    assert row["severity"] == "high"

    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 0


def test_post_window_star_web_only(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "300001",
            "特锐德",
            None,
            "watching",
            None,
            None,
            100,
            0,
            1,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('300001', '2026-10-01', '特锐德', 'test', '2026-10-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 10, 3)
    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 1

    conn = db.get_connection()
    scope = conn.execute("SELECT delivery_scope FROM ai_investment_events").fetchone()[0]
    conn.close()

    assert scope == "web_only"


def test_post_window_out_of_range_skipped(tmp_path, monkeypatch):
    """披露已超过 8 个自然日 → 不在 POST_REPORT_WINDOW 内，不写事件。"""
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "600000",
            "浦发",
            None,
            "holding",
            8.0,
            100,
            100,
            0,
            0,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('600000', '2026-01-01', '浦发银行', 'test', '2026-01-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 1, 10)
    assert emit_holdings_star_earnings_ai_events(ref_date=ref) == 0


def test_watchlist_without_star_or_holding_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    db.init_trading_db()

    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, alias, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, pin_order, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "300750",
            "宁德",
            None,
            "watching",
            None,
            None,
            100,
            0,
            0,
            0,
            "[]",
            0,
            1777376520000,
            1777376520000,
        ),
    )
    conn.commit()
    conn.close()

    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO earnings_calendar (symbol, report_date, name, source, updated_at)
        VALUES ('300750', '2026-04-05', '宁德', 'test', '2026-04-01T00:00:00')
        """
    )
    conn.commit()
    conn.close()

    ref = date(2026, 4, 1)
    n = emit_holdings_star_earnings_ai_events(ref_date=ref)
    assert n == 0
