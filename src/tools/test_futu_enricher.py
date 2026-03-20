"""
Tests for futu_enricher: INDEX_CODES, tuple return, HK index data extraction.

Coverage:
  1. enrich returns tuple (l2_extra, hk_index_data)
  2. INDEX_CODES constant present
  3. HK.800000 and HK.HSTECH data extracted to hk_index_data
  4. Index stocks excluded from l2_extra dict
  5. Graceful degradation when Futu unavailable
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _make_snapshot_df(rows):
    """Build a Futu-style snapshot DataFrame from dict rows."""
    import pandas as pd
    return pd.DataFrame(rows)


class MockFutuContext:
    """Mock Futu OpenQuoteContext for testing.

    get_market_snapshot receives codes in Futu format (e.g. "HK.800000",
    "HK.HSTECH", "HK.00700") and returns (RET_OK, DataFrame).
    """

    def __init__(self, snapshot_data_by_code):
        # snapshot_data_by_code: dict of {futu_code: row_dict}
        self.snapshot_data = snapshot_data_by_code
        self.closed = False

    def get_market_snapshot(self, codes):
        rows = []
        for code in codes:
            row = self.snapshot_data.get(code)
            if row:
                rows.append(row)
        return (0, _make_snapshot_df(rows))  # RET_OK = 0

    def get_stock_quote(self, codes):
        """Return stock quote for index codes — used by HK index fetching path."""
        rows = []
        for code in codes:
            row = self.snapshot_data.get(code)
            if row:
                rows.append(row)
        return (0, _make_snapshot_df(rows))

    def close(self):
        self.closed = True


def test_enrich_returns_tuple():
    """enrich() must return (l2_extra, hk_index_data) tuple."""
    from src.tools.futu_enricher import FutuL2Enricher

    enricher = FutuL2Enricher.__new__(FutuL2Enricher)
    enricher._ctx = None
    enricher._last_fail_time = float("inf")  # Force cooldown
    enricher._host = "127.0.0.1"
    enricher._port = 11111
    enricher._hk_index_data = {}

    # Without OpenD, enrich should return ({}, {})
    result = enricher.enrich([])
    assert isinstance(result, tuple), f"enrich must return tuple, got {type(result)}"
    assert len(result) == 2, f"enrich must return 2-element tuple, got {len(result)}"
    l2_extra, hk_index = result
    assert isinstance(l2_extra, dict)
    assert isinstance(hk_index, dict)


def test_index_codes_constant():
    """INDEX_CODES must contain HK800000 and HKHSTECH."""
    from src.tools.futu_enricher import FutuL2Enricher

    assert hasattr(FutuL2Enricher, "INDEX_CODES")
    codes = FutuL2Enricher.INDEX_CODES
    assert "HK800000" in codes, "HK800000 (恒生) must be in INDEX_CODES"
    assert "HKHSTECH" in codes, "HKHSTECH (恒生科技) must be in INDEX_CODES"


def test_enrich_extracts_hk_index_data():
    """When HK index data is in snapshot, it goes to hk_index_data, not l2_extra."""
    from src.tools.futu_enricher import FutuL2Enricher

    # Keys are Futu-format codes as received by get_market_snapshot
    mock_ctx = MockFutuContext(snapshot_data_by_code={
        "HK.800000": {"code": "HK.800000", "last_price": 24196.95, "change_ratio": -0.13,
                       "turnover": 1234567890.0, "prev_close_price": 24228.50,
                       "open_price": 24200.0, "high_price": 24300.0, "low_price": 24100.0,
                       "volume": 1000000, "amplitude": 0.5,
                       "bid_ask_ratio": 0, "avg_price": 0, "volume_ratio": 0, "turnover_rate": 0},
        "HK.HSTECH": {"code": "HK.HSTECH", "last_price": 5564.37, "change_ratio": -0.35,
                       "turnover": 0, "prev_close_price": 5584.0,
                       "open_price": 5570.0, "high_price": 5590.0, "low_price": 5550.0,
                       "volume": 0, "amplitude": 0,
                       "bid_ask_ratio": 0, "avg_price": 0, "volume_ratio": 0, "turnover_rate": 0},
        "HK.00700":  {"code": "HK.00700", "last_price": 400.0, "change_ratio": 1.5,
                       "turnover": 500000000.0, "prev_close_price": 394.0,
                       "open_price": 395.0, "high_price": 402.0, "low_price": 393.0,
                       "volume": 1250000, "amplitude": 2.0,
                       "bid_ask_ratio": 0, "avg_price": 0, "volume_ratio": 0, "turnover_rate": 0},
    })

    enricher = FutuL2Enricher.__new__(FutuL2Enricher)
    enricher._ctx = mock_ctx
    enricher._last_fail_time = 0
    enricher._host = "127.0.0.1"
    enricher._port = 11111
    enricher._hk_index_data = {}

    # Services with HK stock (id uses project format HK00700)
    services = [{"id": "HK00700", "code": "HK00700", "price": 400.0}]

    l2_extra, hk_index = enricher.enrich(services)

    # HK index data in hk_index, not in l2_extra
    assert hk_index.get("hkIndex") == 24196.95, f"hkIndex should be 24196.95, got {hk_index.get('hkIndex')}"
    assert hk_index.get("hkIndexPct") == -0.13, f"hkIndexPct should be -0.13, got {hk_index.get('hkIndexPct')}"
    assert hk_index.get("hkTech") == 5564.37, f"hkTech should be 5564.37, got {hk_index.get('hkTech')}"
    assert hk_index.get("hkTechPct") == -0.35, f"hkTechPct should be -0.35, got {hk_index.get('hkTechPct')}"
    assert hk_index.get("hkTurnover") == 1234567890.0, f"hkTurnover should be present, got {hk_index.get('hkTurnover')}"

    # HK00700 L2 data in l2_extra, but NOT the index stocks
    assert "HK00700" in l2_extra
    assert "HK800000" not in l2_extra, "HK800000 must not be in l2_extra"
    assert "HKHSTECH" not in l2_extra, "HKHSTECH must not be in l2_extra"


def test_enrich_excludes_index_from_l2_services():
    """Index codes must not appear in l2_extra dict."""
    from src.tools.futu_enricher import FutuL2Enricher

    # Set _ctx to a truthy mock so _ensure_connected() returns True,
    # allowing enrich() to reach the patched _do_enrich.
    mock_ctx = MagicMock()

    fake_hk_index = {
        "hkIndex": 24000.0, "hkIndexPct": 0.5, "hkTurnover": 1000000000.0,
        "hkTech": 5500.0, "hkTechPct": -0.5,
    }

    def fake_do_enrich(self, services):
        # self is the enricher instance; return (l2_extra, hk_index_data)
        return {}, fake_hk_index

    with patch.object(FutuL2Enricher, "_do_enrich", fake_do_enrich):
        enricher = FutuL2Enricher.__new__(FutuL2Enricher)
        enricher._ctx = mock_ctx
        enricher._last_fail_time = 0
        enricher._host = "127.0.0.1"
        enricher._port = 11111
        enricher._hk_index_data = {}
        l2_extra, hk_index = enricher.enrich([])

    # Index codes must not leak into l2_extra
    assert "HK800000" not in l2_extra
    assert "HKHSTECH" not in l2_extra
    # Index data goes to hk_index
    assert hk_index.get("hkIndex") == 24000.0
    assert hk_index.get("hkTech") == 5500.0


def test_enrich_graceful_degradation_no_opend():
    """When OpenD unavailable, enrich returns ({}, {}) without crashing."""
    from src.tools.futu_enricher import FutuL2Enricher

    enricher = FutuL2Enricher.__new__(FutuL2Enricher)
    enricher._ctx = None
    enricher._last_fail_time = float("inf")  # Force cooldown
    enricher._host = "127.0.0.1"
    enricher._port = 11111
    enricher._hk_index_data = {}

    l2_extra, hk_index = enricher.enrich([{"id": "HK00700"}])

    assert l2_extra == {}
    assert hk_index == {}
