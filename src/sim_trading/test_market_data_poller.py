"""
Tests for market_data_poller: load_watchlist_from_db, _backfill_missing_names.

Coverage:
  1. load_watchlist_from_db: reads watchlist + settings from DB
  2. load_watchlist_from_db: fallback to JSON when DB unavailable
  3. load_watchlist_from_db: JSON out of sync does NOT affect DB reads
  4. _backfill_missing_names: only writes to DB, not JSON
  5. _backfill_missing_names: skips entries where name already filled
  6. poll_once: uses DB watchlist (regression for the JSON-drift bug)
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def tmp_db(tmp_path):
    """Temp DB with monitor_watchlist + monitor_settings tables."""
    db = tmp_path / "config.db"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
        CREATE TABLE monitor_watchlist (
            symbol TEXT PRIMARY KEY,
            name TEXT,
            list_type TEXT DEFAULT 'watching',
            cost REAL,
            shares INTEGER,
            lot INTEGER,
            hidden INTEGER DEFAULT 0,
            star INTEGER DEFAULT 0,
            tags TEXT,
            watch_price REAL,
            updated_at TEXT
        );
        CREATE TABLE monitor_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        INSERT INTO monitor_watchlist (symbol, name, list_type, cost, shares)
            VALUES ('002080', '中材科技', 'holding', 50.764, 1000);
        INSERT INTO monitor_watchlist (symbol, name, list_type)
            VALUES ('HK09988', '阿里巴巴', 'holding');
        INSERT INTO monitor_watchlist (symbol, name, list_type)
            VALUES ('000001', '平安银行', 'watching');
        INSERT INTO monitor_settings (key, value)
            VALUES ('poll_interval', '30');
    """)
    conn.commit()
    conn.close()
    return db


@pytest.fixture()
def stale_json(tmp_path):
    """monitor_config.json with only 1 entry — simulates the drift bug."""
    cfg = tmp_path / "monitor_config.json"
    cfg.write_text(json.dumps({
        "watchlist": {"KR000660": {"name": "SK하이닉스"}},
        "settings": {"poll_interval": 30},
    }), encoding="utf-8")
    return cfg


def _config_db_patch(tmp_db):
    """Return a patch for db._config_db_path_override to use tmp_db."""
    import src.sim_trading.db as db_mod
    return patch.object(db_mod, "_config_db_path_override", str(tmp_db))


# ─── 1. load_watchlist_from_db ───────────────────────────────────────────────

def test_load_watchlist_reads_from_db(tmp_db, stale_json):
    """DB has 3 entries; stale JSON has 1. Must return DB data."""
    import src.tools.market_data_poller as poller

    with _config_db_patch(tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json):
        watchlist, settings = poller.load_watchlist_from_db()

    assert len(watchlist) == 3, \
        "Should read all 3 entries from DB, not 1 from stale JSON"
    assert "002080" in watchlist
    assert "HK09988" in watchlist
    assert "000001" in watchlist
    assert "KR000660" not in watchlist, \
        "KR000660 is only in stale JSON, must not appear"


def test_load_watchlist_fields(tmp_db, stale_json):
    """Verify field mapping from DB columns to watchlist dict."""
    import src.tools.market_data_poller as poller

    with _config_db_patch(tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json):
        watchlist, _ = poller.load_watchlist_from_db()

    entry = watchlist["002080"]
    assert entry["name"] == "中材科技"
    assert entry["type"] == "holding"
    assert entry["cost"] == 50.764
    assert entry["shares"] == 1000


def test_load_settings_from_db(tmp_db, stale_json):
    """Settings should be read from monitor_settings table."""
    import src.tools.market_data_poller as poller

    with _config_db_patch(tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json):
        _, settings = poller.load_watchlist_from_db()

    assert settings.get("poll_interval") == 30


def test_load_watchlist_fallback_to_json_when_db_missing(stale_json, tmp_path):
    """When DB doesn't exist, must fall back to JSON (not crash)."""
    import src.tools.market_data_poller as poller

    missing_db = tmp_path / "nonexistent.db"
    with _config_db_patch(missing_db), \
         patch.object(poller, "CONFIG_PATH", stale_json):
        watchlist, settings = poller.load_watchlist_from_db()

    assert "KR000660" in watchlist
    assert settings.get("poll_interval") == 30


# ─── 2. _backfill_missing_names ──────────────────────────────────────────────

def test_backfill_writes_to_db_not_json(tmp_db, stale_json):
    """backfill must update DB; JSON must remain unchanged."""
    import src.tools.market_data_poller as poller

    conn = sqlite3.connect(str(tmp_db))
    conn.execute(
        "UPDATE monitor_watchlist SET name = '' WHERE symbol = '000001'"
    )
    conn.commit()
    conn.close()

    watchlist = {"000001": {"name": "", "type": "watching"}}
    stocks = [{"code": "000001", "name": "平安银行", "price": 12.5}]
    json_before = stale_json.read_text()

    with _config_db_patch(tmp_db):
        updated = poller._backfill_missing_names(stocks, watchlist)

    assert updated is True

    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT name FROM monitor_watchlist WHERE symbol = '000001'"
    ).fetchone()
    conn.close()
    assert row[0] == "平安银行", "DB should be updated"
    assert stale_json.read_text() == json_before, \
        "JSON must not be modified by backfill"


def test_backfill_skips_already_named(tmp_db):
    """Entries with existing names should not be updated."""
    import src.tools.market_data_poller as poller

    watchlist = {"002080": {"name": "中材科技", "type": "holding"}}
    stocks = [{"code": "002080", "name": "中材科技NEW", "price": 46.0}]

    with _config_db_patch(tmp_db):
        updated = poller._backfill_missing_names(stocks, watchlist)

    assert updated is False

    conn = sqlite3.connect(str(tmp_db))
    row = conn.execute(
        "SELECT name FROM monitor_watchlist WHERE symbol = '002080'"
    ).fetchone()
    conn.close()
    assert row[0] == "中材科技", "Existing name should not be overwritten"


def test_backfill_returns_false_when_no_updates_needed(tmp_db):
    """All names filled → returns False without touching DB."""
    import src.tools.market_data_poller as poller

    watchlist = {
        "002080": {"name": "中材科技"},
        "HK09988": {"name": "阿里巴巴"},
    }
    stocks = [
        {"code": "002080", "name": "中材科技", "price": 46.0},
        {"code": "HK09988", "name": "阿里巴巴", "price": 80.0},
    ]

    with _config_db_patch(tmp_db):
        result = poller._backfill_missing_names(stocks, watchlist)

    assert result is False


# ─── 3. Regression: poll_once reads DB, not JSON ─────────────────────────────

def test_poll_once_uses_db_watchlist(tmp_db, stale_json, tmp_path):
    """
    Regression test for the JSON-drift bug:
    DB has 3 stocks, stale JSON has 1.
    poll_once should fetch all 3 DB stocks from EM.
    """
    import src.tools.market_data_poller as poller

    output = tmp_path / "market_data.json"

    def fake_realtime_fallback(symbols):
        # Returns (stocks, is_sina_fallback) — the actual return type of fetch_realtime_with_fallback
        # Only return watchlist stocks; INDEX_CODES are excluded since fetch_market_turnover
        # is mocked to return None (turnover=None causes crash on index writes at line 608)
        INDEX_CODES = {"399006", "sh000688"}
        return ([
            {
                "code": s, "name": f"Name_{s}", "price": 10.0,
                "pct": 1.0, "change": 0.1, "volume": 1000,
                "amount": 10000.0, "amplitude": 1.0,
                "turnover": 1.0, "vol_ratio": 1.0,
                "high": 10.5, "low": 9.5, "open": 9.8,
                "prev_close": 9.9,
            }
            for s in symbols if s not in INDEX_CODES
        ], False)  # (stocks, is_sina_fallback)

    write_args: dict[str, list] = {}

    def capture_write(services, ts, date_str):
        write_args["services"] = services

    with _config_db_patch(tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json), \
         patch.object(poller, "_write_price_snapshots", side_effect=capture_write), \
         patch(
             "src.tools.market_data_poller.fetch_realtime_with_fallback",
             side_effect=fake_realtime_fallback,
         ), \
         patch(
             "src.tools.market_data_poller.fetch_realtime_yahoo",
             return_value=[],
         ), \
         patch(
             "src.tools.market_data_poller.fetch_market_turnover",
             return_value=None,
         ), \
         patch.object(poller._futu_enricher, "enrich", return_value=({}, {})):
        result = poller.poll_once()

    assert result is True
    ids = {s["id"] for s in write_args.get("services", [])}

    assert "002080" in ids, \
        "002080 must be fetched (was absent from stale JSON)"
    assert "HK09988" in ids
    assert "000001" in ids
    assert "KR000660" not in ids, \
        "KR000660 only in stale JSON, must not appear"


# ─── 4. INDEX_CODES: 创业板/科创50 分离与写入 ──────────────────────────────

def test_poll_once_extracts_chiNext_kc50_to_turnover(tmp_db, stale_json, tmp_path):
    """
    When eastmoney returns 创业板 (399006) and 科创50 (000688) index data,
    poll_once must extract chiNext/chiNextPct and kc50/kc50Pct into marketTurnover.
    Index stocks must NOT appear in services list.
    """
    import src.tools.market_data_poller as poller

    def fake_realtime_fallback(symbols):
        # Simulate eastmoney returning watchlist stocks + INDEX_CODES
        result = []
        for s in symbols:
            if s == "399006":
                result.append({"code": s, "name": "创业板", "price": 2050.21,
                               "pct": 0.83, "change": 16.86, "volume": 0,
                               "amount": 0, "amplitude": 0, "turnover": 0,
                               "vol_ratio": 0, "high": 0, "low": 0,
                               "open": 0, "prev_close": 0})
            elif s == "sh000688":
                result.append({"code": s, "name": "科创50", "price": 1020.30,
                               "pct": -0.32, "change": -3.27, "volume": 0,
                               "amount": 0, "amplitude": 0, "turnover": 0,
                               "vol_ratio": 0, "high": 0, "low": 0,
                               "open": 0, "prev_close": 0})
            else:
                result.append({"code": s, "name": f"Name_{s}", "price": 10.0,
                               "pct": 1.0, "change": 0.1, "volume": 1000,
                               "amount": 10000.0, "amplitude": 1.0,
                               "turnover": 1.0, "vol_ratio": 1.0,
                               "high": 10.5, "low": 9.5, "open": 9.8,
                               "prev_close": 9.9})
        return (result, False)

    def fake_turnover():
        return {
            "sh": 338400, "sz": 1135000, "total": 1473900,
            "shIndex": 3384.88, "szIndex": 11351.33,
            "shPct": 0.16, "szPct": 1.57, "verdict": "above_avg",
            # Sina fallback now also returns chiNext/kc50
            "chiNext": 2050.21, "chiNextPct": 0.83,
            "kc50": 1020.30, "kc50Pct": -0.32,
        }

    turnover_args: dict = {}
    write_args: dict[str, list] = {}

    def capture_turnover(turnover, ts, date_str):
        turnover_args["turnover"] = turnover

    def capture_write(services, ts, date_str):
        write_args["services"] = services

    with _config_db_patch(tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json), \
         patch.object(poller, "_write_price_snapshots", side_effect=capture_write), \
         patch.object(poller, "_write_market_turnover", side_effect=capture_turnover), \
         patch("src.tools.market_data_poller.fetch_realtime_with_fallback", side_effect=fake_realtime_fallback), \
         patch("src.tools.market_data_poller.fetch_realtime_yahoo", return_value=[]), \
         patch("src.tools.market_data_poller.fetch_market_turnover", side_effect=fake_turnover), \
         patch.object(poller._futu_enricher, "enrich", return_value=({}, {})):
        result = poller.poll_once()

    assert result is True
    mt = turnover_args.get("turnover", {})

    # Index data written to marketTurnover
    assert mt.get("chiNext") == 2050.21, f"chiNext should be 2050.21, got {mt.get('chiNext')}"
    assert mt.get("chiNextPct") == 0.83, f"chiNextPct should be 0.83, got {mt.get('chiNextPct')}"
    assert mt.get("kc50") == 1020.30, f"kc50 should be 1020.30, got {mt.get('kc50')}"
    assert mt.get("kc50Pct") == -0.32, f"kc50Pct should be -0.32, got {mt.get('kc50Pct')}"

    # Index stocks NOT in services list
    svc_ids = {s["id"] for s in write_args.get("services", [])}
    assert "399006" not in svc_ids, "399006 (创业板) must not appear in services"
    assert "sh000688" not in svc_ids, "sh000688 (科创50) must not appear in services"

    # Watchlist stocks still present
    assert "002080" in svc_ids


# ─── 5. Main loop: market hours gating ─────────────────────────────────────

class TestMarketHoursGating:
    """Tests for poller main loop skipping poll_once during market-closed hours."""

    @staticmethod
    def _mock_lock():
        """Create a fake MonitorLock that always acquires."""
        from unittest.mock import MagicMock
        lock = MagicMock()
        lock.try_acquire.return_value = True
        lock.hostname = "test-host"
        lock.refresh_heartbeat.return_value = True
        return lock

    def test_trading_hours_calls_poll_once(self, tmp_db, stale_json):
        """During trading hours, poll_once should be called in the loop."""
        import src.tools.market_data_poller as poller

        call_count = 0

        def fake_poll_once():
            nonlocal call_count
            call_count += 1

        sleep_count = 0

        def fake_sleep(secs):
            nonlocal sleep_count
            sleep_count += 1
            if sleep_count >= 2:  # first sleep passes, second raises to break loop
                raise InterruptedError

        with patch("src.tools.monitor_lock.MonitorLock", return_value=self._mock_lock()), \
             patch("src.tools.stock_notifier.is_any_market_open", return_value=True), \
             _config_db_patch(tmp_db), \
             patch.object(poller, "CONFIG_PATH", stale_json), \
             patch.object(poller, "load_watchlist_from_db",
                          return_value=({"002080": {"name": "中材科技"}}, {"poll_interval": 1})), \
             patch.object(poller, "poll_once", side_effect=fake_poll_once), \
             patch("src.tools.market_data_poller.time.sleep", side_effect=fake_sleep):

            try:
                poller.main()
            except (InterruptedError, SystemExit, KeyboardInterrupt):
                pass

        assert call_count == 2, f"poll_once should be called twice (startup + loop), got {call_count}"

    def test_non_trading_day_skips_poll_once(self, tmp_db, stale_json):
        """On weekends/holidays, poll_once should NOT be called in the loop."""
        import src.tools.market_data_poller as poller

        call_count = 0

        def fake_poll_once():
            nonlocal call_count
            call_count += 1

        sleep_calls = []

        def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 3:
                raise KeyboardInterrupt

        with patch("src.tools.monitor_lock.MonitorLock", return_value=self._mock_lock()), \
             patch("src.tools.stock_notifier.is_any_market_open", return_value=False), \
             patch("src.tools.trading_calendar.is_trading_day", return_value=False), \
             _config_db_patch(tmp_db), \
             patch.object(poller, "CONFIG_PATH", stale_json), \
             patch.object(poller, "load_watchlist_from_db",
                          return_value=({"002080": {"name": "中材科技"}}, {"poll_interval": 30})), \
             patch.object(poller, "poll_once", side_effect=fake_poll_once), \
             patch("src.tools.market_data_poller.time.sleep", side_effect=fake_sleep):

            try:
                poller.main()
            except (KeyboardInterrupt, SystemExit):
                pass

        # poll_once called once at startup, but NOT in the loop
        assert call_count == 1, f"poll_once should only be called at startup, got {call_count} calls"
        # Sleep interval should be IDLE_INTERVAL (300) in the loop
        loop_sleeps = [s for s in sleep_calls if s == 300]
        assert len(loop_sleeps) >= 1, f"Expected 300s sleep during non-trading day, got {sleep_calls}"

    def test_after_hours_skips_poll_once(self, tmp_db, stale_json):
        """Trading day but outside trading hours (e.g. 20:00), poll_once should be skipped."""
        import src.tools.market_data_poller as poller

        call_count = 0

        def fake_poll_once():
            nonlocal call_count
            call_count += 1

        sleep_calls = []

        def fake_sleep(secs):
            sleep_calls.append(secs)
            if len(sleep_calls) >= 2:
                raise KeyboardInterrupt

        with patch("src.tools.monitor_lock.MonitorLock", return_value=self._mock_lock()), \
             patch("src.tools.stock_notifier.is_any_market_open", return_value=False), \
             patch("src.tools.trading_calendar.is_trading_day", return_value=True), \
             _config_db_patch(tmp_db), \
             patch.object(poller, "CONFIG_PATH", stale_json), \
             patch.object(poller, "load_watchlist_from_db",
                          return_value=({"002080": {"name": "中材科技"}}, {"poll_interval": 30})), \
             patch.object(poller, "poll_once", side_effect=fake_poll_once), \
             patch("src.tools.market_data_poller.time.sleep", side_effect=fake_sleep):

            try:
                poller.main()
            except (KeyboardInterrupt, SystemExit):
                pass

        assert call_count == 1, "poll_once should only run at startup, not during after-hours loop"
        loop_sleeps = [s for s in sleep_calls if s == 300]
        assert len(loop_sleeps) >= 1, "After-hours should use 300s idle interval"

    def test_has_hk_detected_from_watchlist(self, tmp_db, stale_json):
        """has_hk should be True when watchlist contains HK-prefixed symbols."""
        import src.tools.market_data_poller as poller

        has_hk_args = []

        def fake_is_open(hk):
            has_hk_args.append(hk)
            raise KeyboardInterrupt

        with patch("src.tools.monitor_lock.MonitorLock", return_value=self._mock_lock()), \
             patch("src.tools.stock_notifier.is_any_market_open", side_effect=fake_is_open), \
             _config_db_patch(tmp_db), \
             patch.object(poller, "CONFIG_PATH", stale_json), \
             patch.object(poller, "load_watchlist_from_db",
                          return_value=({"HK09988": {"name": "阿里巴巴"}, "002080": {"name": "中材科技"}}, {"poll_interval": 1})), \
             patch.object(poller, "poll_once"), \
             patch("src.tools.market_data_poller.time.sleep", side_effect=lambda s: None):

            try:
                poller.main()
            except (KeyboardInterrupt, SystemExit):
                pass

        assert any(a is True for a in has_hk_args), f"has_hk should be True, got {has_hk_args}"

    def test_no_hk_detected_from_watchlist(self, tmp_db, stale_json):
        """has_hk should be False when watchlist has no HK-prefixed symbols."""
        import src.tools.market_data_poller as poller

        has_hk_args = []

        def fake_is_open(hk):
            has_hk_args.append(hk)
            raise KeyboardInterrupt

        with patch("src.tools.monitor_lock.MonitorLock", return_value=self._mock_lock()), \
             patch("src.tools.stock_notifier.is_any_market_open", side_effect=fake_is_open), \
             _config_db_patch(tmp_db), \
             patch.object(poller, "CONFIG_PATH", stale_json), \
             patch.object(poller, "load_watchlist_from_db",
                          return_value=({"002080": {"name": "中材科技"}}, {"poll_interval": 1})), \
             patch.object(poller, "poll_once"), \
             patch("src.tools.market_data_poller.time.sleep", side_effect=lambda s: None):

            try:
                poller.main()
            except (KeyboardInterrupt, SystemExit):
                pass

        assert any(a is False for a in has_hk_args), f"has_hk should be False, got {has_hk_args}"


def test_poll_once_index_results_empty_on_fetch_failure(tmp_db, stale_json, tmp_path):
    """
    When fetch_realtime_with_fallback returns empty list (fetch failure),
    index_results should be initialized to [] and not crash.
    """
    import src.tools.market_data_poller as poller

    def fake_realtime_fallback(symbols):
        return ([], True)  # Empty + sina fallback

    with _config_db_patch(tmp_db), \
         patch.object(poller, "CONFIG_PATH", stale_json), \
         patch("src.tools.market_data_poller.fetch_realtime_with_fallback", side_effect=fake_realtime_fallback), \
         patch("src.tools.market_data_poller.fetch_realtime_yahoo", return_value=[]), \
         patch("src.tools.market_data_poller.fetch_market_turnover", return_value=None), \
         patch.object(poller._futu_enricher, "enrich", return_value=({}, {})):
        result = poller.poll_once()

    # Should return False (fetch failure) but not crash
    assert result is False
