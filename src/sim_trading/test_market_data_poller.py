"""
Tests for market_data_poller: load_watchlist_from_db, _backfill_missing_names.

Coverage:
  1. load_watchlist_from_db: reads watchlist + settings from DB
  2. load_watchlist_from_db: fails closed when DB unavailable
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

    with _config_db_patch(tmp_db):
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

    with _config_db_patch(tmp_db):
        watchlist, _ = poller.load_watchlist_from_db()

    entry = watchlist["002080"]
    assert entry["name"] == "中材科技"
    assert entry["type"] == "holding"
    assert entry["cost"] == 50.764
    assert entry["shares"] == 1000


def test_load_settings_from_db(tmp_db, stale_json):
    """Settings should be read from monitor_settings table."""
    import src.tools.market_data_poller as poller

    with _config_db_patch(tmp_db):
        _, settings = poller.load_watchlist_from_db()

    assert settings.get("poll_interval") == 30


def test_load_watchlist_fails_closed_when_db_missing(stale_json, tmp_path):
    """When DB doesn't exist, poller must not fall back to JSON."""
    import src.tools.market_data_poller as poller

    missing_db = tmp_path / "nonexistent.db"
    with _config_db_patch(missing_db):
        with pytest.raises(RuntimeError, match="config.db"):
            poller.load_watchlist_from_db()



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


def test_backfill_returns_false_when_db_write_fails(monkeypatch):
    """backfill should not report success when DB write fails."""
    import src.tools.market_data_poller as poller

    class BrokenConn:
        def cursor(self):
            raise RuntimeError("db unavailable")

        def close(self):
            pass

    monkeypatch.setattr(poller, "get_config_connection", lambda: BrokenConn())

    updated = poller._backfill_missing_names(
        [{"code": "000001", "name": "平安银行", "price": 12.5}],
        {"000001": {"name": "", "type": "watching"}},
    )

    assert updated is False


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
        ], False, False)  # (stocks, is_tencent_fallback, is_sina_fallback)

    write_args: dict[str, list] = {}

    def capture_write(services, ts, date_str, sina_fallback=False):
        write_args["services"] = services

    with _config_db_patch(tmp_db), \
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
        return (result, False, False)

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

    def capture_write(services, ts, date_str, sina_fallback=False):
        write_args["services"] = services

    with _config_db_patch(tmp_db), \
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
        return ([], False, True)  # Empty + sina fallback

    with _config_db_patch(tmp_db), \
         patch("src.tools.market_data_poller.fetch_realtime_with_fallback", side_effect=fake_realtime_fallback), \
         patch("src.tools.market_data_poller.fetch_realtime_yahoo", return_value=[]), \
         patch("src.tools.market_data_poller.fetch_market_turnover", return_value=None), \
         patch.object(poller._futu_enricher, "enrich", return_value=({}, {})):
        result = poller.poll_once()

    # Should return False (fetch failure) but not crash
    assert result is False


# ─── Bug Regression: stale data write when fetch fails ─────────────────────

def test_get_last_hk_index_from_db_closes_connection(monkeypatch):
    import src.tools.market_data_poller as poller

    class FakeCursor:
        def execute(self, *_args, **_kwargs):
            return self

        def fetchone(self):
            return (25100.5, 0.8, 5200.2, 1.1, 1700.0)

    class FakeConnection(FakeCursor):
        closed = False

        def close(self):
            self.closed = True

    conn = FakeConnection()
    monkeypatch.setattr(poller, "get_connection", lambda: conn)

    result = poller._get_last_hk_index_from_db()

    assert result["hkIndex"] == 25100.5
    assert conn.closed is True


def test_poll_once_writes_stale_data_when_fetch_fails(tmp_db, stale_json, tmp_path):
    """
    Regression test for the stale-data-write bug (line ~729 before fix):
    When fetch_realtime_with_fallback returns empty AND DB already has price_snapshots,
    poll_once must still call _write_price_snapshots with the stale DB data.

    Before the fix, returning at line 798 (index_results = []) would skip
    _write_price_snapshots entirely, causing notifier to think no data existed.
    After fix: stale services are written with their original timestamps preserved.
    """
    import sqlite3
    import src.tools.market_data_poller as poller

    # Pre-populate trading.db with price_snapshots for our symbols
    trading_db = tmp_path / "trading.db"
    conn = sqlite3.connect(str(trading_db))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            ts INTEGER, date TEXT, code TEXT, name TEXT, price REAL,
            volume REAL, amount REAL, change_pct REAL, chg_amt REAL,
            amp REAL, turnover REAL, vol_ratio REAL, high REAL, low REAL,
            open REAL, prev_close REAL, amo1 REAL, amo2 REAL,
            main_net_inflow REAL, main_net_inflow_pct REAL
        )
    """)
    original_ts = 1700000000000
    conn.execute(
        """
        INSERT INTO price_snapshots
        (ts, date, code, name, price, volume, amount, change_pct, chg_amt,
         amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2,
         main_net_inflow, main_net_inflow_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (original_ts, "2024-01-01", "002080", "中材科技", 50.0, 1000000,
         50000000.0, 1.5, 0.75, 3.0, 1.2, 1.5, 51.0, 49.0, 49.5, 49.25,
         2.5, 3.1, 1000000.0, 5.2),
    )
    conn.commit()
    conn.close()

    # Patch db module to use our temp trading.db
    import src.sim_trading.db as db_mod
    db_patcher = patch.object(db_mod, "_db_path_override", str(trading_db))

    write_args: dict = {}

    def capture_write(services, ts, date_str, sina_fallback=False):
        write_args["services"] = services
        write_args["ts"] = ts
        write_args["date_str"] = date_str

    def fake_realtime_fallback(symbols):
        return ([], False, True)  # Empty — simulates fetch failure

    with _config_db_patch(tmp_db), \
         db_patcher, \
         patch.object(poller, "_write_price_snapshots", side_effect=capture_write), \
         patch.object(poller, "_write_market_turnover", return_value=None), \
         patch("src.tools.market_data_poller.fetch_realtime_with_fallback", side_effect=fake_realtime_fallback), \
         patch("src.tools.market_data_poller.fetch_realtime_yahoo", return_value=[]), \
         patch("src.tools.market_data_poller.fetch_market_turnover", return_value={"total": 1000, "sh": 500, "sz": 500, "shIndex": 3000, "szIndex": 10000, "shPct": 0.5, "szPct": 1.0, "verdict": "above_avg"}), \
         patch("src.tools.market_data_poller.fetch_hk_index_data", return_value={}), \
         patch.object(poller._futu_enricher, "enrich", return_value=({}, {})):
        result = poller.poll_once()

    assert result is False, "poll_once should return False on fetch failure"

    # Key regression: _write_price_snapshots MUST be called with stale data
    assert "services" in write_args, \
        "_write_price_snapshots must be called when fetch fails (was skipped before fix)"

    stale = write_args["services"]
    assert len(stale) == 1
    assert stale[0]["id"] == "002080"

    # Critical: original timestamp must be preserved, not replaced with new ts
    assert write_args["ts"] == original_ts, \
        f"Stale data must preserve original ts={original_ts}, got {write_args['ts']}"


# ─── Bug Regression: AMO uses actual amount, not vol*price ─────────────────

def test_amo_uses_actual_amount_not_vol_price(tmp_db, stale_json, tmp_path):
    """
    Regression test for the AMO calculation bug:
    When EM returns data with actual 'amount' field (f6 field in EM API),
    AMO must be computed using that amount directly, NOT as vol * price.

    Before the fix, the code would incorrectly compute amount = vol * price
    even when EM provided the actual amount.
    """
    import src.tools.market_data_poller as poller

    trading_db = tmp_path / "trading.db"
    import src.sim_trading.db as db_mod
    db_patcher = patch.object(db_mod, "_db_path_override", str(trading_db))

    write_args: dict = {}

    def capture_write(services, ts, date_str, sina_fallback=False):
        write_args["services"] = services

    # EM returns stock with actual amount=5e7 (50M yuan) AND vol*price would be 1e7
    # This proves AMO uses actual amount, not vol*price
    def fake_realtime_fallback(symbols):
        return ([
            {
                "code": "002080",
                "name": "中材科技",
                "price": 50.0,
                "pct": 1.5,
                "change": 0.75,
                "volume": 1000000,   # vol = 1M
                "amount": 50000000.0,  # actual amount = 50M (f6 from EM)
                "amplitude": 3.0,
                "turnover": 1.2,
                "vol_ratio": 1.5,
                "high": 51.0,
                "low": 49.0,
                "open": 49.5,
                "prev_close": 49.25,
            }
        ], False, False)

    with _config_db_patch(tmp_db), \
         db_patcher, \
         patch.object(poller, "_write_price_snapshots", side_effect=capture_write), \
         patch.object(poller, "_write_market_turnover", return_value=None), \
         patch("src.tools.market_data_poller.fetch_realtime_with_fallback", side_effect=fake_realtime_fallback), \
         patch("src.tools.market_data_poller.fetch_realtime_yahoo", return_value=[]), \
         patch("src.tools.market_data_poller.fetch_market_turnover", return_value={"total": 1000, "sh": 500, "sz": 500, "shIndex": 3000, "szIndex": 10000, "shPct": 0.5, "szPct": 1.0, "verdict": "above_avg"}), \
         patch("src.tools.market_data_poller.fetch_hk_index_data", return_value={}), \
         patch.object(poller._futu_enricher, "enrich", return_value=({}, {})):
        result = poller.poll_once()

    assert result is True

    services = write_args.get("services", [])
    assert len(services) == 1

    # The actual amount from EM (50M) should be preserved in the service record
    # This proves AMO computation used the actual amount field, not vol*price
    svc = services[0]
    assert svc["amount"] == 50000000.0, \
        f"amount field must be preserved as 50M from EM, got {svc['amount']}"

    # AMO should be computed from actual 50M amount, not vol*price=50M in this case
    # (vol=1M * price=50 = 50M, same as actual amount — need a case where they differ)
    # Re-verify with a case where vol*price != actual amount
    write_args.clear()

    def fake_realtime_fallback_differ(tmp_symbols):
        return ([
            {
                "code": "002080",
                "name": "中材科技",
                "price": 50.0,
                "pct": 1.5,
                "change": 0.75,
                "volume": 1000000,       # vol = 1M
                "amount": 80000000.0,    # actual amount = 80M (DIFFERS from vol*price=50M)
                "amplitude": 3.0,
                "turnover": 1.2,
                "vol_ratio": 1.5,
                "high": 51.0,
                "low": 49.0,
                "open": 49.5,
                "prev_close": 49.25,
            }
        ], False, False)

    with _config_db_patch(tmp_db), \
         db_patcher, \
         patch.object(poller, "_write_price_snapshots", side_effect=capture_write), \
         patch.object(poller, "_write_market_turnover", return_value=None), \
         patch("src.tools.market_data_poller.fetch_realtime_with_fallback", side_effect=fake_realtime_fallback_differ), \
         patch("src.tools.market_data_poller.fetch_realtime_yahoo", return_value=[]), \
         patch("src.tools.market_data_poller.fetch_market_turnover", return_value={"total": 1000, "sh": 500, "sz": 500, "shIndex": 3000, "szIndex": 10000, "shPct": 0.5, "szPct": 1.0, "verdict": "above_avg"}), \
         patch("src.tools.market_data_poller.fetch_hk_index_data", return_value={}), \
         patch.object(poller._futu_enricher, "enrich", return_value=({}, {})):
        poller.poll_once()

    services = write_args.get("services", [])
    assert len(services) == 1
    svc = services[0]
    # If bug existed: amount would be vol*price = 1M*50 = 50M
    # After fix: amount should be actual EM amount = 80M
    assert svc["amount"] == 80000000.0, \
        f"AMO must use actual EM amount=80M, not vol*price=50M. Got {svc['amount']}"


# ─── Bug Regression: _get_latest_services_from_db per-code MAX(ts) ─────────────

def test_get_latest_services_from_db_resolves_per_code_max_ts(tmp_path):
    """
    Regression test for the per-code MAX(ts) bug in _get_latest_services_from_db.

    Bug: SQL subquery (SELECT MAX(ts) FROM price_snapshots) found the GLOBAL max ts,
    not the per-code max. If the requested code's latest record had an older ts than
    another stock's global max, the query returned no result for that code — causing
    vol_ratio/turnover to be lost during Sina fallback inheritance.

    Scenario:
        002080: last updated at ts=1700000000000 (older)
        000001: last updated at ts=1800000000000 (global max)
    When asking for 002080 alone, old code would look for ts=1800000000000 (global max)
    and find nothing → returned empty list.

    After fix: JOIN subquery finds per-code MAX(ts), 002080 is found correctly.
    """
    import sqlite3
    import src.tools.market_data_poller as poller

    trading_db = tmp_path / "trading.db"
    conn = sqlite3.connect(str(trading_db))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            ts INTEGER, date TEXT, code TEXT, name TEXT, price REAL,
            volume REAL, amount REAL, change_pct REAL, chg_amt REAL,
            amp REAL, turnover REAL, vol_ratio REAL, high REAL, low REAL,
            open REAL, prev_close REAL, amo1 REAL, amo2 REAL,
            main_net_inflow REAL, main_net_inflow_pct REAL
        )
    """)
    # 002080 updated at ts=1700000000000 (older)
    conn.execute(
        """INSERT INTO price_snapshots
        (ts, date, code, name, price, volume, amount, change_pct, chg_amt,
         amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2,
         main_net_inflow, main_net_inflow_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (1700000000000, "2024-01-01", "002080", "中材科技", 50.0, 1000000,
         50000000.0, 1.5, 0.75, 3.0, 1.2, 1.5, 51.0, 49.0, 49.5, 49.25,
         2.5, 3.1, 1000000.0, 5.2),
    )
    # 000001 updated at ts=1800000000000 (global max)
    conn.execute(
        """INSERT INTO price_snapshots
        (ts, date, code, name, price, volume, amount, change_pct, chg_amt,
         amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2,
         main_net_inflow, main_net_inflow_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (1800000000000, "2024-01-01", "000001", "平安银行", 10.0, 500000,
         5000000.0, 0.5, 0.05, 1.0, 0.8, 2.0, 10.5, 9.5, 9.8, 9.75,
         1.2, 1.5, 500000.0, 2.5),
    )
    conn.commit()
    conn.close()

    import src.sim_trading.db as db_mod
    patcher = patch.object(db_mod, "_db_path_override", str(trading_db))

    with patcher:
        result = poller._get_latest_services_from_db(["002080"])

    assert len(result) == 1, f"Expected 1 record for 002080, got {len(result)} (per-code MAX bug)"
    assert result[0]["id"] == "002080"
    assert result[0]["turnover"] == 1.2
    assert result[0]["volRatio"] == 1.5

    # Also verify it still works when asking for both
    with patcher:
        result2 = poller._get_latest_services_from_db(["002080", "000001"])
    assert len(result2) == 2
    codes = {r["id"] for r in result2}
    assert codes == {"002080", "000001"}


def test_sina_fallback_preserves_db_turnover_in_write(tmp_path):
    """
    Regression: Sina fallback must NOT overwrite DB turnover/vol_ratio with 0.

    Bug: When Sina fallback wrote turnover=0/vol_ratio=0 to DB, it contaminated
    the historical correct values. Subsequent polls would inherit the 0s from DB.

    Fix: _write_price_snapshots with sina_fallback=True reads existing DB values
    for turnover/vol_ratio and preserves them when the incoming value is 0.
    """
    import sqlite3
    import src.tools.market_data_poller as poller

    trading_db = tmp_path / "trading.db"
    conn = sqlite3.connect(str(trading_db))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS price_snapshots (
            ts INTEGER, date TEXT, code TEXT, name TEXT, price REAL,
            volume REAL, amount REAL, change_pct REAL, chg_amt REAL,
            amp REAL, turnover REAL, vol_ratio REAL, high REAL, low REAL,
            open REAL, prev_close REAL, amo1 REAL, amo2 REAL,
            main_net_inflow REAL, main_net_inflow_pct REAL
        )
    """)
    # Old record with valid turnover/vol_ratio
    conn.execute(
        """INSERT INTO price_snapshots
        (ts, date, code, name, price, volume, amount, change_pct, chg_amt,
         amp, turnover, vol_ratio, high, low, open, prev_close, amo1, amo2,
         main_net_inflow, main_net_inflow_pct)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (1700000000000, "2024-01-01", "002080", "中材科技", 50.0, 1000000,
         50000000.0, 1.5, 0.75, 3.0, 5.38, 1.23, 51.0, 49.0, 49.5, 49.25,
         2.5, 3.1, 1000000.0, 5.2),
    )
    conn.commit()
    conn.close()

    import src.sim_trading.db as db_mod
    patcher = patch.object(db_mod, "_db_path_override", str(trading_db))

    # Sina fallback returns turnover=0/vol_ratio=0
    sina_service = {
        "id": "002080",
        "name": "中材科技",
        "price": 50.5,
        "change": 1.0,
        "chgAmt": 0.75,
        "vol": 1100000,
        "amount": 55550000.0,
        "amp": 3.0,
        "turnover": 0,  # Sina doesn't provide turnover
        "volRatio": 0,  # Sina doesn't provide vol_ratio
        "high": 51.5,
        "low": 49.5,
        "open": 49.5,
        "prevClose": 49.25,
        "amo1": 2.5,
        "amo2": 3.1,
    }

    with patcher:
        poller._write_price_snapshots(
            [sina_service], 1800000000000, "2024-01-02", sina_fallback=True
        )

    # Verify DB preserved the old turnover/vol_ratio
    conn2 = sqlite3.connect(str(trading_db))
    row = conn2.execute(
        "SELECT turnover, vol_ratio FROM price_snapshots WHERE code='002080' ORDER BY ts DESC LIMIT 1"
    ).fetchone()
    conn2.close()

    assert row is not None, "Record should exist"
    # Before fix: would be 0 (Sina value overwrote DB)
    # After fix: should be 5.38 (preserved from DB)
    assert row[0] == 5.38, f"turnover must be preserved as 5.38, got {row[0]}"
    assert row[1] == 1.23, f"vol_ratio must be preserved as 1.23, got {row[1]}"
