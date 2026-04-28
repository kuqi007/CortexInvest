import json

import src.sim_trading.db as db
from src.sim_trading import replay_runner


def test_replay_runner_loads_rules_from_config_db_without_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO signal_rules (rule_id, rule_json, enabled, updated_at_ms)
        VALUES (?, ?, ?, ?)
        """,
        ("initial_capital", json.dumps(1_000_000), 1, 1777376520000),
    )
    conn.execute(
        """
        INSERT INTO signal_rules (rule_id, rule_json, enabled, updated_at_ms)
        VALUES (?, ?, ?, ?)
        """,
        ("cost_model", json.dumps({"commission": 0.0003}), 1, 1777376520000),
    )
    conn.execute(
        """
        INSERT INTO signal_rules (rule_id, rule_json, enabled, updated_at_ms)
        VALUES (?, ?, ?, ?)
        """,
        ("disabled_rule", json.dumps({"ignored": True}), 0, 1777376520000),
    )
    conn.commit()
    conn.close()

    rules = replay_runner._load_rules()

    assert rules == {
        "initial_capital": 1_000_000,
        "cost_model": {"commission": 0.0003},
    }


def test_replay_runner_load_rules_fails_fast_when_db_has_no_rules(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()

    try:
        replay_runner._load_rules()
    except RuntimeError as exc:
        assert "signal_rules" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
