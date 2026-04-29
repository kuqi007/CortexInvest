from datetime import datetime

import src.sim_trading.db as db
import src.tools.trading_calendar as tc


def test_calendar_cache_loads_from_db_without_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    today = datetime.now().strftime("%Y-%m-%d")
    now_ms = int(datetime.now().timestamp() * 1000)
    month_key = today[:7]
    conn = db.get_connection()
    conn.execute(
        """
        INSERT INTO trading_calendar_cache (date, calendar_json, updated_at_ms)
        VALUES (?, ?, ?)
        """,
        (
            f"HK|||{month_key}",
            '{"market":"HK","month":"%s","days":{"%s":"WHOLE"}}' % (month_key, today),
                now_ms,
        ),
    )
    conn.commit()
    conn.close()
    tc._cache = {}
    tc._cache_date = ""

    assert tc._load_cache_from_db() is True

    assert tc._cache == {("HK", month_key): {today: "WHOLE"}}
    assert tc._cache_date == today


def test_calendar_cache_saves_to_db_not_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    cache_file = tmp_path / "trading_calendar_cache.json"
    db.init_trading_db()
    tc._cache = {("CN", "2026-04"): {"2026-04-28": "WHOLE"}}
    tc._cache_date = "2026-04-28"

    tc._save_cache_to_db()

    assert not cache_file.exists()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT calendar_json FROM trading_calendar_cache WHERE date = ?",
        ("CN|||2026-04",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert '"2026-04-28":"WHOLE"' in row["calendar_json"].replace(" ", "")
