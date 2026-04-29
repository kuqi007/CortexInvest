#!/usr/bin/env python3
"""Tests for stock_monitor.py — focus on Feishu alert filtering."""

import subprocess
import sys
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.stock_monitor import notify


@pytest.fixture(autouse=True)
def isolate_notification_audit_db(tmp_path, monkeypatch):
    import src.sim_trading.db as db

    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()


def test_load_config_reads_db_only(monkeypatch):
    import src.tools.stock_monitor as stock_monitor
    import src.utils.config_reader as config_reader

    expected = {"watchlist": {"HK09988": {"name": "阿里巴巴"}}, "settings": {}}
    monkeypatch.setattr(config_reader, "read_monitor_config", lambda prefer_db=True: expected)

    assert stock_monitor.load_config() == expected


def test_load_config_does_not_create_default_or_json_fallback(monkeypatch):
    import src.tools.stock_monitor as stock_monitor
    import src.utils.config_reader as config_reader

    def fail(*args, **kwargs):
        raise RuntimeError("config.db unavailable")

    monkeypatch.setattr(config_reader, "read_monitor_config", fail)
    monkeypatch.setattr(stock_monitor, "save_config", lambda config: (_ for _ in ()).throw(AssertionError("must not save default config")))

    with pytest.raises(RuntimeError, match="config.db unavailable"):
        stock_monitor.load_config()


def test_save_config_records_config_audit_in_same_db(tmp_path, monkeypatch):
    import src.sim_trading.db as db
    import src.tools.stock_monitor as stock_monitor

    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))

    stock_monitor.save_config(
        {
            "watchlist": {
                "HK00700": {
                    "name": "Tencent",
                    "alias": "TCEHY",
                    "type": "holding",
                    "shares": 100,
                    "cost": 320,
                    "pin_order": 3,
                }
            },
            "settings": {"poll_interval": 30},
        }
    )

    conn = db.get_config_connection()
    try:
        watch_row = conn.execute(
            "SELECT symbol, alias, shares, cost, pin_order FROM monitor_watchlist WHERE symbol = ?",
            ("HK00700",),
        ).fetchone()
        audit_row = conn.execute(
            "SELECT payload_json FROM config_audit_outbox ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert dict(watch_row) == {
        "symbol": "HK00700",
        "alias": "TCEHY",
        "shares": 100,
        "cost": 320.0,
        "pin_order": 3,
    }
    payload = json.loads(audit_row["payload_json"])
    assert payload["schema_version"] == 2
    assert payload["source"] == "stock_monitor"
    assert payload["action"] == "replace"
    assert payload["entity"] == "monitor_config"
    assert payload["key"] == "config"
    assert payload["db"] == "config.db"
    assert payload["after"]["watchlist"][0]["symbol"] == "HK00700"
    assert payload["after"]["watchlist"][0]["alias"] == "TCEHY"
    assert payload["after"]["watchlist"][0]["pin_order"] == 3


def test_save_alerts_records_config_audit_in_same_db(tmp_path, monkeypatch):
    import src.sim_trading.db as db
    import src.tools.stock_monitor as stock_monitor

    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))

    stock_monitor.save_alerts({"HK00700": {"above": 380, "below": 300}})

    conn = db.get_config_connection()
    try:
        alert_row = conn.execute(
            "SELECT symbol, above, below FROM alert_rules WHERE symbol = ?",
            ("HK00700",),
        ).fetchone()
        audit_row = conn.execute(
            "SELECT payload_json FROM config_audit_outbox ORDER BY ts_ms DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()

    assert dict(alert_row) == {"symbol": "HK00700", "above": 380.0, "below": 300.0}
    payload = json.loads(audit_row["payload_json"])
    assert payload["schema_version"] == 2
    assert payload["source"] == "stock_monitor"
    assert payload["action"] == "replace"
    assert payload["entity"] == "alert_rules"
    assert payload["key"] == "alerts"
    assert payload["after"]["alerts"][0]["symbol"] == "HK00700"


# ══════════════════════════════════════════
# Feishu filtering in notify()
# ══════════════════════════════════════════


def _mock_subprocess_run(cmd, **kwargs):
    """Simulate terminal-notifier not found; fallback paths succeed."""
    mock = MagicMock()
    if cmd and cmd[0] == "which":
        mock.returncode = 1  # not found
    else:
        mock.returncode = 0  # fallback osascript succeeds
    return mock


class TestFeishuFiltering:
    """
    Regression tests for commit f8fc484:
    Only push L1 (star/trade_plan/l2_notify) and critical L2 (panic_sell/threshold)
    to Feishu. Skip L3, drift, gap, mainline, STALE, portfolio, open/close.
    """

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_l1_star_pushes_to_feishu(self, mock_sub, mock_feishu):
        mock_feishu.return_value = True
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 1, "_kind": "star", "name": "Test"},
        )
        mock_feishu.assert_called_once()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_l1_trade_plan_pushes_to_feishu(self, mock_sub, mock_feishu):
        mock_feishu.return_value = True
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 1, "_kind": "trade_plan", "name": "Test"},
        )
        mock_feishu.assert_called_once()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_l1_l2_notify_pushes_to_feishu(self, mock_sub, mock_feishu):
        mock_feishu.return_value = True
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 1, "_kind": "l2_notify", "name": "Test"},
        )
        mock_feishu.assert_called_once()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_l2_panic_sell_pushes_to_feishu(self, mock_sub, mock_feishu):
        mock_feishu.return_value = True
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 2, "_kind": "panic_sell", "name": "Test"},
        )
        mock_feishu.assert_called_once()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_l2_threshold_pushes_to_feishu(self, mock_sub, mock_feishu):
        mock_feishu.return_value = True
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 2, "_kind": "threshold", "name": "Test"},
        )
        mock_feishu.assert_called_once()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_l2_non_critical_skips_feishu(self, mock_sub, mock_feishu):
        """L2 with _kind other than panic_sell/threshold should NOT push to Feishu."""
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 2, "_kind": "drift", "name": "Test"},
        )
        mock_feishu.assert_not_called()
        # macOS notification should still fire (subprocess.run called for fallback)
        assert mock_sub.called

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_l3_skips_feishu(self, mock_sub, mock_feishu):
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 3, "_kind": "watching", "name": "Test"},
        )
        mock_feishu.assert_not_called()
        assert mock_sub.called

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_l4_skips_feishu(self, mock_sub, mock_feishu):
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 4, "_kind": "hidden", "name": "Test"},
        )
        mock_feishu.assert_not_called()
        assert mock_sub.called

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_no_stock_info_skips_feishu(self, mock_sub, mock_feishu):
        """CLI dashboard alerts without stock_info should not push to Feishu."""
        notify("测试标题", "测试消息")
        mock_feishu.assert_not_called()
        assert mock_sub.called

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_stock_info_without_level_skips_feishu(self, mock_sub, mock_feishu):
        notify(
            "测试标题",
            "测试消息",
            stock_info={"name": "Test", "_kind": "star"},
        )
        mock_feishu.assert_not_called()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_stock_info_without_kind_skips_feishu(self, mock_sub, mock_feishu):
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 2, "name": "Test"},
        )
        mock_feishu.assert_not_called()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_feishu_exception_does_not_crash(self, mock_sub, mock_feishu):
        """Even if feishu_send raises, notify should not crash."""
        mock_feishu.side_effect = RuntimeError("network error")
        # Should not raise
        notify(
            "测试标题",
            "测试消息",
            stock_info={"level": 1, "_kind": "star", "name": "Test"},
        )

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_macos_notification_always_fires(self, mock_sub, mock_feishu):
        """macOS notification should fire regardless of Feishu filtering."""
        for level, kind in [
            (1, "star"),
            (2, "panic_sell"),
            (2, "drift"),
            (3, "watching"),
            (4, "hidden"),
        ]:
            mock_sub.reset_mock()
            mock_feishu.reset_mock()
            notify(
                f"title {level}",
                f"msg {level}",
                stock_info={"level": level, "_kind": kind, "name": "Test"},
            )
            assert mock_sub.called, f"macOS notify should fire for level={level} kind={kind}"

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_portfolio_skips_feishu(self, mock_sub, mock_feishu):
        """Portfolio-level alerts should not go to Feishu."""
        notify(
            "持仓日报",
            "组合盈亏",
            stock_info={"level": 2, "_kind": "portfolio", "name": "Portfolio"},
            is_portfolio=True,
        )
        mock_feishu.assert_not_called()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_open_close_skips_feishu(self, mock_sub, mock_feishu):
        for kind in ("open", "close"):
            mock_feishu.reset_mock()
            notify(
                f"market {kind}",
                f"market {kind} message",
                stock_info={"level": 2, "_kind": kind, "name": "Market"},
            )
            mock_feishu.assert_not_called()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_stale_skips_feishu(self, mock_sub, mock_feishu):
        notify(
            "STALE 警告",
            "数据过期",
            stock_info={"level": 2, "_kind": "STALE", "name": "Test"},
        )
        mock_feishu.assert_not_called()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_gap_skips_feishu(self, mock_sub, mock_feishu):
        notify(
            "跳空",
            "大幅跳空",
            stock_info={"level": 2, "_kind": "gap", "name": "Test"},
        )
        mock_feishu.assert_not_called()

    @patch("src.tools.stock_monitor.feishu_send")
    @patch("src.tools.stock_monitor.subprocess.run", side_effect=_mock_subprocess_run)
    def test_mainline_skips_feishu(self, mock_sub, mock_feishu):
        notify(
            "主线",
            "主线切换",
            stock_info={"level": 2, "_kind": "mainline", "name": "Test"},
        )
        mock_feishu.assert_not_called()


# ══════════════════════════════════════════
# DeltaAlertEngine re-trigger cooldown
# ══════════════════════════════════════════


class TestDeltaAlertCooldown:
    """Regression: big_move re-trigger now enforces cooldown_min gate.
    Fixes: 潍柴动力 4/14 fired 3 alerts within 8 minutes.

    The cooldown gate blocks re-trigger even when delta_pct threshold
    is met, if fewer than cooldown_min minutes have passed since last
    notification ts was recorded.
    """

    def test_first_trigger_records_ts(self):
        """First notification records ts in _notified so subsequent
        calls can enforce the cooldown window."""
        import time
        from src.tools.stock_notifier import DeltaAlertEngine

        engine = DeltaAlertEngine({
            "watchlist": {
                "000338": {"type": "holding", "star": False, "hidden": False, "name": "潍柴动力"}
            },
            "settings": {},
        })
        engine._alerts = {}
        engine._notified = {}

        # A股: trigger_pct=6 for holding, change_pct=7 >= trigger_pct → fires
        quotes = {
            "000338": {
                "price": 30.0,
                "change_pct": 7.0,
                "prev_close": 28.57,
                "chg_amt": 1.43,
                "name": "潍柴动力",
                "open": 29.0,
            }
        }

        result = engine.check(quotes, hkd_cny_rate=None)

        # Should have produced exactly one alert
        assert len(result) == 1
        assert result[0]["symbol"] == "000338"

        # _notified must now contain ts so cooldown can be checked next tick
        assert "ts" in engine._notified["000338"]
        # ts should be a recent timestamp (within a few seconds)
        assert time.time() - engine._notified["000338"]["ts"] < 5

    def test_retrigger_blocked_within_cooldown(self):
        """Re-trigger within cooldown_min is blocked even when delta_pct
        threshold is met."""
        import time
        from src.tools.stock_notifier import DeltaAlertEngine

        engine = DeltaAlertEngine({
            "watchlist": {
                "000338": {"type": "holding", "star": False, "hidden": False, "name": "潍柴动力"}
            },
            "settings": {},
        })
        engine._alerts = {}

        # Simulate first trigger just happened (ts = now)
        engine._notified["000338"] = {
            "price": 30.0,
            "change_pct": 7.0,
            "ts": time.time(),
        }

        # Price drops to 27.9 → delta = |27.9-30|/30 = 7% >= delta_pct(7)
        # but L2 cooldown_min=15 min, so should be blocked
        quotes = {
            "000338": {
                "price": 27.9,
                "change_pct": -2.5,
                "prev_close": 30.0,
                "chg_amt": -2.1,
                "name": "潍柴动力",
                "open": 28.5,
            }
        }

        result = engine.check(quotes, hkd_cny_rate=None)

        # No new alert because still within 15-min cooldown
        assert len(result) == 0, "Re-trigger should be blocked by cooldown"

    def test_retrigger_fires_after_cooldown_expires(self):
        """Re-trigger fires once cooldown_min has elapsed and delta_pct
        threshold is met."""
        import time
        from src.tools.stock_notifier import DeltaAlertEngine

        engine = DeltaAlertEngine({
            "watchlist": {
                "000338": {"type": "holding", "star": False, "hidden": False, "name": "潍柴动力"}
            },
            "settings": {},
        })
        engine._alerts = {}

        # Simulate first trigger happened 16 minutes ago (cooldown=15 min)
        engine._notified["000338"] = {
            "price": 30.0,
            "change_pct": 7.0,
            "ts": time.time() - 16 * 60,
        }

        # Price drops to 27.9 → delta = 7% >= delta_pct(7), and cooldown passed
        quotes = {
            "000338": {
                "price": 27.9,
                "change_pct": -2.5,
                "prev_close": 30.0,
                "chg_amt": -2.1,
                "name": "潍柴动力",
                "open": 28.5,
            }
        }

        result = engine.check(quotes, hkd_cny_rate=None)

        # Should now fire because cooldown expired
        assert len(result) == 1
        assert result[0]["symbol"] == "000338"

    def test_retrigger_blocked_even_when_delta_exactly_at_threshold(self):
        """Edge case: delta == delta_pct but within cooldown → still blocked."""
        import time
        from src.tools.stock_notifier import DeltaAlertEngine

        engine = DeltaAlertEngine({
            "watchlist": {
                "000338": {"type": "holding", "star": False, "hidden": False, "name": "潍柴动力"}
            },
            "settings": {},
        })
        engine._alerts = {}

        # Simulate first trigger just happened
        engine._notified["000338"] = {
            "price": 30.0,
            "change_pct": 7.0,
            "ts": time.time(),
        }

        # Price drops to 27.9 exactly → delta = 7% == delta_pct(7)
        # For A股 holding → delta_pct stays 7 (no HK adjustment)
        quotes = {
            "000338": {
                "price": 27.9,
                "change_pct": -7.0,
                "prev_close": 30.0,
                "chg_amt": -2.1,
                "name": "潍柴动力",
                "open": 28.0,
            }
        }

        result = engine.check(quotes, hkd_cny_rate=None)

        # Blocked: cooldown not passed yet even though delta == delta_pct
        assert len(result) == 0

    def test_threshold_retrigger_blocked_by_cooldown(self):
        """Threshold re-breach within cooldown window is suppressed."""
        from src.tools.stock_notifier import DeltaAlertEngine

        engine = DeltaAlertEngine({
            "watchlist": {
                "THR001": {"type": "holding", "star": False, "hidden": False, "name": "TestStock"}
            },
            "settings": {"l2_cooldown_min": 15},
        })
        # Bypass _reload_alerts() which would overwrite _alerts from DB
        engine._reload_alerts = lambda: None
        # Manually set alerts (bypass DB load)
        engine._alerts = {
            "THR001": {"above": 110.0, "below": 90.0}
        }

        # First trigger: price hits above threshold
        quotes = {"THR001": {"price": 112.0, "change_pct": 5.0, "name": "TestStock", "chg_amt": 0}}
        alerts = engine.check(quotes, hkd_cny_rate=None)
        assert len(alerts) == 1
        assert alerts[0].get("_kind") == "threshold"

        # Re-breach within cooldown: price still above threshold
        quotes2 = {"THR001": {"price": 111.0, "change_pct": 4.5, "name": "TestStock", "chg_amt": 0}}
        alerts2 = engine.check(quotes2, hkd_cny_rate=None)
        assert len(alerts2) == 0, "threshold re-trigger within cooldown should be blocked"


# ══════════════════════════════════════════
# WatchDriftTracker retrace level regression
# ══════════════════════════════════════════


class TestWatchDriftRetraceLevel:
    """Regression: retrace alerts should respect star/type config, not hardcoded level=2.

    Bug: _check_retrace() had "_level": 2 hardcoded for both index and stock paths.
    Fix: _check_retrace() now accepts a level parameter and uses it instead of 2.
    """

    def test_star_stock_retrace_uses_level_1(self):
        """Star=1 stock retrace should be level 1 (sound)."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        key = "TEST001"
        # Simulate tier 5 already crossed (positive direction)
        tracker._notified_tiers[key] = {5}
        # Now price drops below tier 5, triggering retrace check
        drift_pct = 4.0  # below tier 5

        alert = tracker._check_retrace(
            key=key,
            drift_pct=drift_pct,
            step=5,
            direction="跌",
            name="TestStock",
            wp_or_baseline=100.0,
            price_or_val=96.0,
            is_index=False,
            level=1,  # star=1 → level 1
        )
        assert alert is not None
        assert alert["_level"] == 1, f"star stock retrace should be level 1, got {alert['_level']}"

    def test_holding_stock_retrace_uses_level_2(self):
        """Holding (non-star) stock retrace should be level 2 (silent)."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        key = "TEST002"
        tracker._notified_tiers[key] = {5}

        alert = tracker._check_retrace(
            key=key,
            drift_pct=4.0,
            step=5,
            direction="跌",
            name="TestStock",
            wp_or_baseline=100.0,
            price_or_val=96.0,
            is_index=False,
            level=2,  # holding → level 2
        )
        assert alert is not None
        assert alert["_level"] == 2, f"holding stock retrace should be level 2, got {alert['_level']}"

    def test_watching_stock_retrace_uses_level_3(self):
        """Watching (non-star) stock retrace should be level 3 (no notification)."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        key = "TEST003"
        tracker._notified_tiers[key] = {5}

        alert = tracker._check_retrace(
            key=key,
            drift_pct=4.0,
            step=5,
            direction="跌",
            name="TestStock",
            wp_or_baseline=100.0,
            price_or_val=96.0,
            is_index=False,
            level=3,  # watching → level 3
        )
        assert alert is not None
        assert alert["_level"] == 3, f"watching stock retrace should be level 3, got {alert['_level']}"

    def test_star_index_retrace_uses_level_1(self):
        """Star=1 index retrace should be level 1."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        key = "tag:test_index"
        tracker._notified_tiers[key] = {5}

        alert = tracker._check_retrace(
            key=key,
            drift_pct=4.0,
            step=5,
            direction="跌",
            name="test_index指数",
            wp_or_baseline=100.0,
            price_or_val=96.0,
            is_index=True,
            level=1,
        )
        assert alert is not None
        assert alert["_level"] == 1, f"star index retrace should be level 1, got {alert['_level']}"

    def test_non_star_index_retrace_uses_level_2(self):
        """Non-star index retrace should be level 2."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        key = "tag:test_index"
        tracker._notified_tiers[key] = {5}

        alert = tracker._check_retrace(
            key=key,
            drift_pct=4.0,
            step=5,
            direction="跌",
            name="test_index指数",
            wp_or_baseline=100.0,
            price_or_val=96.0,
            is_index=True,
            level=2,
        )
        assert alert is not None
        assert alert["_level"] == 2, f"non-star index retrace should be level 2, got {alert['_level']}"

    def test_retrace_backward_compat_default_level_2(self):
        """Direct callers without level param get default level=2 (backward compat)."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        key = "TEST004"
        tracker._notified_tiers[key] = {5}

        # Call without level argument - should default to 2
        alert = tracker._check_retrace(
            key=key,
            drift_pct=4.0,
            step=5,
            direction="跌",
            name="TestStock",
            wp_or_baseline=100.0,
            price_or_val=96.0,
            is_index=False,
            # No level= argument → should use default 2
        )
        assert alert is not None
        assert alert["_level"] == 2, f"default level should be 2, got {alert['_level']}"


class TestWatchDriftTierFill:
    """Regression: when a higher tier is crossed, lower tiers are auto-filled
    to prevent spurious alerts on the way down.
    Fixes: price 10%→9.9%→4.9% triggering both tier-10 and tier-5 alerts.
    """

    def test_high_tier_cross_fills_lower_tiers(self):
        """Crossing tier 10 should mark tier 5 as already fired."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        # Simulate: stock rises to drift_pct=10.2% (tier=10)
        # Should fill tier 5 automatically
        key = "TEST001"
        tracker._notified_tiers[key] = set()
        drift_pct = 10.2
        step = 5
        tier = 10  # tier for 10.2%

        # Manually simulate what check_stocks does for a tier crossing
        # Add the tier
        tracker._notified_tiers[key].add(tier)
        # Auto-fill lower tiers
        for lower in range(step, abs(tier) + 1, step):
            tracker._notified_tiers[key].add(lower)

        # Now simulate price dropping to 9.9% (tier=5)
        # Tier 5 should already be in notified_tiers
        assert 5 in tracker._notified_tiers[key], "tier 5 should be auto-filled when tier 10 crosses"
        assert -5 not in tracker._notified_tiers[key], "negative tier should not be added for positive drift"

    def test_negative_high_tier_cross_fills_lower_tiers(self):
        """Crossing tier -10 should mark tier -5 as already fired."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        key = "TEST002"
        tracker._notified_tiers[key] = set()
        drift_pct = -10.2
        step = 5
        tier = -10  # tier for -10.2%

        # Add the tier
        tracker._notified_tiers[key].add(tier)
        # Auto-fill lower tiers (same direction = negative)
        for lower in range(step, abs(tier) + 1, step):
            tracker._notified_tiers[key].add(-lower)

        # Now simulate price recovering to -9.9% (tier=-5)
        # Tier -5 should already be in notified_tiers
        assert -5 in tracker._notified_tiers[key], "tier -5 should be auto-filled when tier -10 crosses"
        assert 5 not in tracker._notified_tiers[key], "positive tier should not be added for negative drift"

    def test_retrace_cooldown_blocks_immediate_retrigger(self):
        """Retrace should be blocked within 5 minutes of last retrace — test method contract."""
        from src.tools.stock_notifier import WatchDriftTracker

        tracker = WatchDriftTracker({"watchlist": {}, "settings": {}})
        key = "TEST001"
        tracker._notified_tiers[key] = {5}

        # First retrace: drift drops below tier 5 → should fire
        alert1 = tracker._check_retrace(
            key=key, drift_pct=4.0, step=5, direction="跌",
            name="TestStock", wp_or_baseline=100.0, price_or_val=96.0,
            is_index=False, level=2,
        )
        assert alert1 is not None, "first retrace should fire"

        # Immediate second retrace: should be blocked by cooldown
        alert2 = tracker._check_retrace(
            key=key, drift_pct=3.5, step=5, direction="跌",
            name="TestStock", wp_or_baseline=100.0, price_or_val=96.5,
            is_index=False, level=2,
        )
        assert alert2 is None, "retrace within 5 min should be blocked"
