"""Tests for ai_investment_events consumption in stock_notifier."""

import uuid
from unittest.mock import MagicMock

import pytest

import src.sim_trading.db as db
from src.tools.stock_notifier import consume_pending_ai_investment_events


def _insert_event(conn, **kwargs) -> str:
    eid = kwargs.get("id") or str(uuid.uuid4())
    dedupe = kwargs.get("dedupe_key") or str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO ai_investment_events (
            id, event_date, symbol, name, source, event_type, severity, delivery_scope,
            verdict, confidence, dedupe_key, title, summary, reasons_json,
            notify_status, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            eid,
            kwargs.get("event_date", "2026-05-01"),
            kwargs.get("symbol", "000001"),
            kwargs.get("name"),
            kwargs.get("source", "unit_test"),
            kwargs.get("event_type", "signal"),
            kwargs.get("severity", "normal"),
            kwargs.get("delivery_scope", "web_only"),
            kwargs.get("verdict", "observe"),
            float(kwargs.get("confidence", 0.5)),
            dedupe,
            kwargs.get("title", "标题"),
            kwargs.get("summary", "摘要"),
            kwargs.get("reasons_json", "[]"),
            kwargs.get("notify_status", "pending"),
            kwargs.get("created_at", "2026-05-01T10:00:00"),
            kwargs.get("updated_at", "2026-05-01T10:00:00"),
        ),
    )
    return eid


def test_consume_suppressed_no_alert(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    monkeypatch.setattr("src.tools.stock_notifier.stealth_dispatch", MagicMock())

    conn = db.get_connection()
    eid = _insert_event(conn, delivery_scope="suppressed")
    conn.commit()
    conn.close()

    assert consume_pending_ai_investment_events() == 1

    conn = db.get_connection()
    st = conn.execute(
        "SELECT notify_status FROM ai_investment_events WHERE id = ?", (eid,)
    ).fetchone()[0]
    n_alerts = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
    conn.close()

    assert st == "suppressed"
    assert n_alerts == 0


def test_consume_web_only_writes_alert_and_skips_stealth(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    mock_sd = MagicMock()
    monkeypatch.setattr("src.tools.stock_notifier.stealth_dispatch", mock_sd)

    conn = db.get_connection()
    eid = _insert_event(conn, delivery_scope="web_only", title="AI 提示", summary="内容")
    conn.commit()
    conn.close()

    assert consume_pending_ai_investment_events() == 1
    mock_sd.assert_not_called()

    conn = db.get_connection()
    st = conn.execute(
        "SELECT notify_status FROM ai_investment_events WHERE id = ?", (eid,)
    ).fetchone()[0]
    row = conn.execute(
        "SELECT kind, level, symbol FROM alert_events WHERE message LIKE ?",
        (f"%{eid}%",),
    ).fetchone()
    conn.close()

    assert st == "web_only"
    assert row is not None
    assert row["kind"] == "ai_investment"
    assert row["level"] == 3


def test_consume_feishu_high_calls_feishu_only_not_stealth(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    mock_feishu = MagicMock(return_value=True)
    mock_sd = MagicMock()
    monkeypatch.setattr("src.tools.stock_monitor.feishu_send", mock_feishu)
    monkeypatch.setattr("src.tools.stock_notifier.stealth_dispatch", mock_sd)

    conn = db.get_connection()
    eid = _insert_event(
        conn,
        delivery_scope="feishu_high",
        severity="critical",
        title="风险",
        summary="注意",
    )
    conn.commit()
    conn.close()

    assert consume_pending_ai_investment_events() == 1
    mock_feishu.assert_called_once()
    mock_sd.assert_not_called()

    conn = db.get_connection()
    st = conn.execute(
        "SELECT notify_status FROM ai_investment_events WHERE id = ?", (eid,)
    ).fetchone()[0]
    conn.close()
    assert st == "sent"


def test_consume_feishu_send_failure_marks_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    monkeypatch.setattr("src.tools.stock_monitor.feishu_send", MagicMock(return_value=False))
    monkeypatch.setattr("src.tools.stock_notifier.stealth_dispatch", MagicMock())

    conn = db.get_connection()
    eid = _insert_event(
        conn,
        delivery_scope="feishu_normal",
        title="测",
        summary="试",
    )
    conn.commit()
    conn.close()

    assert consume_pending_ai_investment_events() == 1

    conn = db.get_connection()
    st = conn.execute(
        "SELECT notify_status FROM ai_investment_events WHERE id = ?", (eid,)
    ).fetchone()[0]
    ac = conn.execute("SELECT COUNT(*) FROM alert_events").fetchone()[0]
    conn.close()

    assert st == "failed"
    assert ac == 1
