"""pytest conftest — 拦截 terminal-notifier 调用，防止测试弹窗。

只 mock subprocess.run 对 terminal-notifier / osascript 的调用。
不停任何运行中的进程。
"""

import subprocess
from unittest.mock import patch

import pytest


_original_run = subprocess.run


def _safe_subprocess_run(*args, **kwargs):
    """拦截 terminal-notifier 和 osascript display notification 调用"""
    cmd = args[0] if args else kwargs.get("args", [])
    cmd_str = ""
    if isinstance(cmd, list) and len(cmd) > 0:
        cmd_str = " ".join(str(c) for c in cmd)
    elif isinstance(cmd, str):
        cmd_str = cmd
    if cmd_str and ("terminal-notifier" in cmd_str or (
        "osascript" in cmd_str and "display notification" in cmd_str
    )):
        # 返回一个成功的 CompletedProcess，不实际弹窗
        return subprocess.CompletedProcess(
            args=cmd, returncode=0, stdout=b"", stderr=b""
        )
    return _original_run(*args, **kwargs)


@pytest.fixture(scope="session", autouse=True)
def _block_notifications():
    """session 级别：拦截所有 subprocess 调用中的通知命令。"""
    with patch("subprocess.run", side_effect=_safe_subprocess_run):
        yield
