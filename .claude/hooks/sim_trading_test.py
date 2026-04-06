#!/usr/bin/env python3
"""PostToolUse hook: auto-run sim_trading tests when a sim_trading source file is edited."""

import json
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = str(Path(__file__).resolve().parents[2])
LOG_FILE = "/tmp/claude_hook.log"
LOG_MAX = 1_048_576  # 1 MB


def _log(msg: str) -> None:
    try:
        from datetime import datetime
        stamp = datetime.now().strftime("%H:%M:%S")
        line = f"{stamp} {msg}\n"
        if Path(LOG_FILE).exists() and Path(LOG_FILE).stat().st_size > LOG_MAX:
            Path(LOG_FILE).write_text("")
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass


try:
    data = json.load(sys.stdin)
    fp = data.get("tool_input", {}).get("file_path", "")
    if "sim_trading/" in fp and fp.endswith(".py") and "test_" not in fp:
        _log(f"[hook] sim_trading changed: {fp}")
        r = subprocess.run(
            ["uv", "run", "pytest", "src/sim_trading/test_sim_trading.py",
             "-q", "--tb=short", "-m", "smoke"],
            capture_output=True, text=True, cwd=PROJECT_DIR, timeout=60,
        )
        if r.stdout:
            for line in r.stdout.strip().split("\n")[-3:]:
                _log(f"[hook] {line}")
        if r.returncode != 0:
            _log(f"[hook] SMOKE FAIL (exit={r.returncode}) — run full tests")
        else:
            _log("[hook] smoke passed")
except Exception as e:
    _log(f"[hook] error: {e}")
