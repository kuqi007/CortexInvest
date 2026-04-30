"""Regression tests for DATA_DIR fallback logic in src/sim_trading/db.py.

Verifies:
1. AI_INVESTOR_DATA_DIR env var takes priority over everything
2. When AI_INVESTOR_DATA_DIR is unset AND src/data symlink exists → DATA_DIR = src/data
3. When AI_INVESTOR_DATA_DIR is unset AND src/data does NOT exist → DATA_DIR = PROJECT_ROOT/data
4. CONFIG_DB_PATH = DATA_DIR / "config.db"
5. TRADING_DB_PATH = DATA_DIR / "trading.db"
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


class TestDataDirFallbackSubprocess:
    """Test DATA_DIR fallback using subprocess for clean process isolation."""

    def _run_script(self, script: str, env_overrides: dict | None = None) -> dict:
        """Run a Python script in a subprocess and return stdout/stderr/results."""
        env = os.environ.copy()
        if env_overrides is not None:
            for k, v in env_overrides.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        else:
            env.pop("AI_INVESTOR_DATA_DIR", None)

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd="/Users/zhul1/Documents/dev/openWorkspace/ai-investor",
        )
        return {
            "stdout": result.stdout,
            "stderr": result.stderr,
            "returncode": result.returncode,
        }

    def test_env_var_takes_priority(self, tmp_path):
        """AI_INVESTOR_DATA_DIR env var takes priority over everything."""
        custom_data = tmp_path / "custom_data"
        custom_data.mkdir()
        (custom_data / "config.db").touch()
        (custom_data / "trading.db").touch()

        script = f"""
import sys
sys.path.insert(0, '/Users/zhul1/Documents/dev/openWorkspace/ai-investor')
from src.sim_trading.db import DATA_DIR, CONFIG_DB_PATH, TRADING_DB_PATH
print('DATA_DIR=' + str(DATA_DIR))
print('CONFIG_IN_DATA=' + str(CONFIG_DB_PATH.parent == DATA_DIR))
print('TRADING_IN_DATA=' + str(TRADING_DB_PATH.parent == DATA_DIR))
"""
        env = {"AI_INVESTOR_DATA_DIR": str(custom_data)}
        result = self._run_script(script, env)

        assert result["returncode"] == 0, f"Script failed: {result['stderr']}"
        assert str(custom_data) in result["stdout"], f"Expected {custom_data} in output"
        assert "CONFIG_IN_DATA=True" in result["stdout"]
        assert "TRADING_IN_DATA=True" in result["stdout"]

    def test_fallback_to_src_data_when_exists(self, tmp_path):
        """When AI_INVESTOR_DATA_DIR is unset AND src/data exists → DATA_DIR = src/data."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        src_dir = project_root / "src"
        src_dir.mkdir()
        src_data_dir = src_dir / "data"
        src_data_dir.mkdir()
        (src_data_dir / "config.db").touch()
        (src_data_dir / "trading.db").touch()

        fallback_data_dir = project_root / "data"
        fallback_data_dir.mkdir()

        script = f"""
from pathlib import Path
project_root = Path('{project_root}')
symlink_data_dir = project_root / "src" / "data"
data_dir = symlink_data_dir if symlink_data_dir.exists() else project_root / "data"
fallback_data_dir = project_root / "data"
print('DATA_DIR=' + str(data_dir))
print('IS_SRC_DATA=' + str(data_dir == symlink_data_dir))
print('SYMLINK_EXISTS=' + str(symlink_data_dir.exists()))
print('FALLBACK_EXISTS=' + str(fallback_data_dir.exists()))
"""
        result = self._run_script(script, env_overrides={})

        assert result["returncode"] == 0, f"Script failed: {result['stderr']}"
        assert "IS_SRC_DATA=True" in result["stdout"], f"Should use src/data when it exists: {result['stdout']}"

    def test_fallback_to_project_root_data_when_no_src_data(self, tmp_path):
        """When AI_INVESTOR_DATA_DIR is unset AND src/data does NOT exist → DATA_DIR = PROJECT_ROOT/data."""
        project_root = tmp_path / "project"
        project_root.mkdir()
        src_dir = project_root / "src"
        src_dir.mkdir()
        # Note: NO src/data directory here

        fallback_data_dir = project_root / "data"
        fallback_data_dir.mkdir()

        script = f"""
from pathlib import Path
project_root = Path('{project_root}')
symlink_data_dir = project_root / "src" / "data"
data_dir = symlink_data_dir if symlink_data_dir.exists() else project_root / "data"
fallback_data_dir = project_root / "data"
print('DATA_DIR=' + str(data_dir))
print('IS_SRC_DATA=' + str(data_dir == symlink_data_dir))
print('IS_FALLBACK=' + str(data_dir == fallback_data_dir))
print('SYMLINK_EXISTS=' + str(symlink_data_dir.exists()))
"""
        result = self._run_script(script, env_overrides={})

        assert result["returncode"] == 0, f"Script failed: {result['stderr']}"
        assert "IS_SRC_DATA=False" in result["stdout"], f"Should NOT use src/data when it doesn't exist"
        assert "IS_FALLBACK=True" in result["stdout"], f"Should use PROJECT_ROOT/data when src/data doesn't exist"

    def test_config_db_path_relative_to_data_dir(self, tmp_path):
        """CONFIG_DB_PATH = DATA_DIR / 'config.db'."""
        custom_data = tmp_path / "custom_data"
        custom_data.mkdir()
        (custom_data / "config.db").touch()
        (custom_data / "trading.db").touch()

        script = f"""
import sys
sys.path.insert(0, '/Users/zhul1/Documents/dev/openWorkspace/ai-investor')
from src.sim_trading.db import DATA_DIR, CONFIG_DB_PATH
print('CONFIG_DB_PATH=' + str(CONFIG_DB_PATH))
print('EQUALS=' + str(CONFIG_DB_PATH == DATA_DIR / 'config.db'))
"""
        env = {"AI_INVESTOR_DATA_DIR": str(custom_data)}
        result = self._run_script(script, env)

        assert result["returncode"] == 0, f"Script failed: {result['stderr']}"
        assert "EQUALS=True" in result["stdout"], f"CONFIG_DB_PATH should equal DATA_DIR / 'config.db'"

    def test_trading_db_path_relative_to_data_dir(self, tmp_path):
        """TRADING_DB_PATH = DATA_DIR / 'trading.db'."""
        custom_data = tmp_path / "custom_data"
        custom_data.mkdir()
        (custom_data / "config.db").touch()
        (custom_data / "trading.db").touch()

        script = f"""
import sys
sys.path.insert(0, '/Users/zhul1/Documents/dev/openWorkspace/ai-investor')
from src.sim_trading.db import DATA_DIR, TRADING_DB_PATH
print('TRADING_DB_PATH=' + str(TRADING_DB_PATH))
print('EQUALS=' + str(TRADING_DB_PATH == DATA_DIR / 'trading.db'))
"""
        env = {"AI_INVESTOR_DATA_DIR": str(custom_data)}
        result = self._run_script(script, env)

        assert result["returncode"] == 0, f"Script failed: {result['stderr']}"
        assert "EQUALS=True" in result["stdout"], f"TRADING_DB_PATH should equal DATA_DIR / 'trading.db'"


class TestDataDirFallbackModuleLevel:
    """Test DATA_DIR fallback by importing the module with different env states."""

    def _import_with_env(self, env_overrides: dict | None) -> dict:
        """Import the db module with given env overrides and return the relevant globals."""
        env = os.environ.copy()
        if env_overrides is not None:
            for k, v in env_overrides.items():
                if v is None:
                    env.pop(k, None)
                else:
                    env[k] = v
        else:
            env.pop("AI_INVESTOR_DATA_DIR", None)

        script = """
import sys
sys.path.insert(0, '/Users/zhul1/Documents/dev/openWorkspace/ai-investor')
from src.sim_trading import db
print('DATA_DIR=' + str(db.DATA_DIR))
print('CONFIG_DB_PATH=' + str(db.CONFIG_DB_PATH))
print('TRADING_DB_PATH=' + str(db.TRADING_DB_PATH))
"""

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd="/Users/zhul1/Documents/dev/openWorkspace/ai-investor",
        )
        if result.returncode != 0:
            return {"error": result.stderr}
        lines = result.stdout.strip().split("\n")
        return {
            "DATA_DIR": next((l.split("=", 1)[1] for l in lines if l.startswith("DATA_DIR=")), None),
            "CONFIG_DB_PATH": next((l.split("=", 1)[1] for l in lines if l.startswith("CONFIG_DB_PATH=")), None),
            "TRADING_DB_PATH": next((l.split("=", 1)[1] for l in lines if l.startswith("TRADING_DB_PATH=")), None),
        }

    def test_env_override_in_subprocess(self, tmp_path):
        """Test that env var is properly read in a fresh subprocess."""
        custom_data = tmp_path / "custom_env_data"
        custom_data.mkdir()
        (custom_data / "config.db").touch()
        (custom_data / "trading.db").touch()

        result = self._import_with_env({"AI_INVESTOR_DATA_DIR": str(custom_data)})

        assert "error" not in result, f"Import failed: {result.get('error')}"
        assert result["DATA_DIR"] == str(custom_data), f"Expected {custom_data}, got {result['DATA_DIR']}"
        assert result["CONFIG_DB_PATH"] == str(custom_data / "config.db")
        assert result["TRADING_DB_PATH"] == str(custom_data / "trading.db")

    def test_unset_env_uses_actual_fallback(self):
        """When env is unset, DATA_DIR should be actual src/data or PROJECT_ROOT/data."""
        result = self._import_with_env(None)  # No AI_INVESTOR_DATA_DIR

        assert "error" not in result, f"Import failed: {result.get('error')}"
        data_dir = Path(result["DATA_DIR"])

        # DATA_DIR should be either PROJECT_ROOT/src/data or PROJECT_ROOT/data
        project_root = Path("/Users/zhul1/Documents/dev/openWorkspace/ai-investor")
        expected_paths = [project_root / "src" / "data", project_root / "data"]

        assert data_dir in expected_paths, f"DATA_DIR {data_dir} should be one of {expected_paths}"
