#!/usr/bin/env python3
"""Tests for stock_monitor.py — focus on Feishu alert filtering."""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.tools.stock_monitor import notify


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
