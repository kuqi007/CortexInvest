from __future__ import annotations

import json
from pathlib import Path

from src.utils.audit_system import build_audit_event_v2, make_actor


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def test_build_audit_event_v2_matches_golden_fixture() -> None:
    fixture = json.loads(
        (PROJECT_ROOT / "tests/fixtures/audit_event_v2.json").read_text(
            encoding="utf-8"
        )
    )

    event = build_audit_event_v2(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        correlation_id="01HWNM4Z7G8E6Q9M3R2T1V0X5Y",
        source="api_config",
        actor=make_actor(actor_type="user", actor_id="local-user", host="localhost"),
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before={"shares": 1000},
        after={"shares": 800},
        metadata={"request_id": "req-1"},
    )

    assert event == fixture
