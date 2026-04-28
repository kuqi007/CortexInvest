import src.sim_trading.db as db
import src.tools.sector_index_engine as sector


def test_sector_alert_rules_load_from_db_without_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    for key, value in {
        "sector_cumulative_gain_pct": 12,
        "sector_slope_threshold": 0.12,
        "sector_r_squared_min": 0.7,
        "sector_lookback_days": 20,
        "sector_min_days_since_create": 5,
    }.items():
        conn.execute(
            "INSERT INTO monitor_settings (key, value, updated_at) VALUES (?, ?, ?)",
            (key, value, 1777376520),
        )
    conn.commit()
    conn.close()

    rules = sector._load_alert_rules_from_db()

    assert rules == {
        "cumulative_gain_pct": 12,
        "slope_threshold": 0.12,
        "r_squared_min": 0.7,
        "lookback_days": 20,
        "min_days_since_create": 5,
    }


def test_sector_period_thresholds_load_from_db_without_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    conn.execute(
        "INSERT INTO monitor_settings (key, value, updated_at) VALUES (?, ?, ?)",
        ("sector_period_5_gain", 9, 1777376520),
    )
    conn.execute(
        "INSERT INTO monitor_settings (key, value, updated_at) VALUES (?, ?, ?)",
        ("sector_period_5_drop", -6, 1777376520),
    )
    conn.commit()
    conn.close()

    thresholds = sector._load_period_thresholds_from_db()

    assert thresholds[5] == (9, -6)
