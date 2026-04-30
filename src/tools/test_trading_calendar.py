from datetime import datetime
from unittest.mock import patch

import src.sim_trading.db as db
import src.tools.trading_calendar as tc


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_data(**markets):
    """Return a mock _fetch_trading_days that returns controlled data.

    markets: kwarg like CN={"2026-04-28": "WHOLE", "2026-04-29": "MORNING"}
    """
    def mock_fetch(market: str, start: str, end: str):
        return markets.get(market.upper(), {})
    return mock_fetch


# ─────────────────────────────────────────────────────────────────────────────
# is_trading_day + get_trade_type — core logic via mocked _fetch_trading_days
# ─────────────────────────────────────────────────────────────────────────────

def test_is_trading_day_returns_true_for_whole(monkeypatch):
    """WHOLE = 全天交易 → is_trading_day returns True."""
    mock = _mock_data(CN={"2026-04-28": "WHOLE"})
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        assert tc.is_trading_day("CN", "2026-04-28") is True


def test_is_trading_day_returns_true_for_morning(monkeypatch):
    """MORNING = 上午交易 → is_trading_day returns True."""
    mock = _mock_data(CN={"2026-04-28": "MORNING"})
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        assert tc.is_trading_day("CN", "2026-04-28") is True


def test_is_trading_day_returns_true_for_afternoon(monkeypatch):
    """AFTERNOON = 下午交易 → is_trading_day returns True."""
    mock = _mock_data(CN={"2026-04-28": "AFTERNOON"})
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        assert tc.is_trading_day("CN", "2026-04-28") is True


def test_is_trading_day_returns_false_for_missing_date(monkeypatch):
    """Date NOT in calendar → is_trading_day returns False."""
    mock = _mock_data(CN={"2026-04-28": "WHOLE"})  # only 04-28 exists
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        assert tc.is_trading_day("CN", "2026-04-29") is False


def test_is_trading_day_returns_false_for_empty_calendar(monkeypatch):
    """Empty calendar (no data at all) → is_trading_day returns False.

    When _fetch_trading_days returns {} (no trading days in range) AND
    _load_cache_from_db finds nothing (fully empty cache), the weekday
    fallback kicks in — so 2026-04-28 (Tuesday) would return True.
    This test verifies the cache-lookup path returns False for a date
    that is genuinely absent from an explicitly empty calendar.
    """
    # Mock _fetch_trading_days to return a specific date explicitly marked
    # as NOT a trading day (using an empty string as sentinel).
    # This tests the cache path: cal.get(date_str) returns None → False.
    def empty_cal_mock(market, start, end):
        return {}  # Futu reachable but returns no days

    with patch.object(tc, "_fetch_trading_days", empty_cal_mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        # Directly seed the cache with an explicit "not a trading day" entry
        # for the query month so the cache lookup path is exercised.
        tc._cache[("CN", "2026-04")] = {}  # empty = date not found
        assert tc.is_trading_day("CN", "2026-04-28") is False


def test_get_trade_type_returns_whole(monkeypatch):
    """get_trade_type returns WHOLE when cached."""
    mock = _mock_data(CN={"2026-04-28": "WHOLE"})
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        assert tc.get_trade_type("CN", "2026-04-28") == "WHOLE"


def test_get_trade_type_returns_morning(monkeypatch):
    """get_trade_type returns MORNING when cached."""
    mock = _mock_data(CN={"2026-04-28": "MORNING"})
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        assert tc.get_trade_type("CN", "2026-04-28") == "MORNING"


def test_get_trade_type_returns_afternoon(monkeypatch):
    """get_trade_type returns AFTERNOON when cached."""
    mock = _mock_data(CN={"2026-04-28": "AFTERNOON"})
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        assert tc.get_trade_type("CN", "2026-04-28") == "AFTERNOON"


def test_get_trade_type_returns_none_when_not_in_calendar(monkeypatch):
    """get_trade_type returns None when date is not in calendar."""
    mock = _mock_data(CN={"2026-04-28": "WHOLE"})
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        assert tc.get_trade_type("CN", "2026-04-29") is None


def test_cn_and_hk_calendars_are_separate(monkeypatch):
    """CN and HK calendars are stored and queried independently."""
    mock = _mock_data(
        CN={"2026-04-28": "WHOLE"},
        HK={"2026-04-28": "MORNING"},
    )
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        # CN has WHOLE on 04-28
        assert tc.is_trading_day("CN", "2026-04-28") is True
        assert tc.get_trade_type("CN", "2026-04-28") == "WHOLE"
        # HK has MORNING on 04-28 (different type)
        assert tc.is_trading_day("HK", "2026-04-28") is True
        assert tc.get_trade_type("HK", "2026-04-28") == "MORNING"
        # HK does NOT have 04-29 → should be False
        assert tc.is_trading_day("HK", "2026-04-29") is False
        assert tc.get_trade_type("HK", "2026-04-29") is None


def test_month_boundary_apr_and_may_in_same_response(monkeypatch):
    """A single _fetch_trading_days response can span two calendar months."""
    # Simulate Futu returning a flat range covering Apr 28 – May 5
    mock = _mock_data(
        CN={
            "2026-04-28": "WHOLE",
            "2026-04-29": "WHOLE",
            "2026-04-30": "WHOLE",
            "2026-05-04": "WHOLE",  # May 1/2/3 are weekend → skip
            "2026-05-05": "MORNING",
        },
    )
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        # April dates
        assert tc.is_trading_day("CN", "2026-04-28") is True
        assert tc.is_trading_day("CN", "2026-04-29") is True
        assert tc.is_trading_day("CN", "2026-04-30") is True
        # May dates
        assert tc.is_trading_day("CN", "2026-05-04") is True
        assert tc.get_trade_type("CN", "2026-05-04") == "WHOLE"
        assert tc.get_trade_type("CN", "2026-05-05") == "MORNING"
        # Day not in response
        assert tc.is_trading_day("CN", "2026-05-01") is False


def test_weekday_fallback_only_when_cache_completely_empty(monkeypatch):
    """Weekday fallback fires only when cache has no entry for the month at all.

    When _fetch_trading_days returns empty dict (Futu unavailable), the cache
    stays empty → is_trading_day falls back to weekday check.
    """
    # Return empty dict = Futu unreachable / no data
    mock = _mock_data(CN={})
    with patch.object(tc, "_fetch_trading_days", mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        # 2026-04-28 is a Tuesday (weekday) → should return True via fallback
        assert tc.is_trading_day("CN", "2026-04-28") is True
        # 2026-05-02 is a Saturday → should return False
        assert tc.is_trading_day("CN", "2026-05-02") is False


def test_weekday_fallback_respects_cache_not_empty(monkeypatch):
    """When the cache is populated, weekday fallback must NOT fire.

    Even if _fetch_trading_days returns empty on a subsequent call,
    the already-cached data takes precedence.
    """
    # First call populates cache via mock returning data
    populate_mock = _mock_data(CN={"2026-04-28": "WHOLE"})
    with patch.object(tc, "_fetch_trading_days", populate_mock), \
         patch.object(tc, "_load_cache_from_db", return_value=False):
        tc._cache = {}
        tc._cache_date = ""
        # First call: cache is populated
        assert tc.is_trading_day("CN", "2026-04-28") is True

    # Second call: mock returns empty (Futu unavailable) but cache should still serve
    empty_mock = _mock_data(CN={})
    with patch.object(tc, "_fetch_trading_days", empty_mock):
        # _cache_date still matches today → cache is reused → weekday fallback NOT triggered
        assert tc.is_trading_day("CN", "2026-04-28") is True


# ─────────────────────────────────────────────────────────────────────────────
# DB cache round-trip (existing tests — preserved)
# ─────────────────────────────────────────────────────────────────────────────

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
