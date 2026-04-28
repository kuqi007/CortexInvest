import json

from src.utils.audit_hash import (
    add_hash_chain,
    canonical_event_bytes,
    compute_event_hash,
    write_jsonl_manifest,
)


def test_canonical_event_bytes_are_stable_for_unicode_and_numbers():
    event = {
        "event_id": "01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        "schema_version": 1,
        "ts": "2026-04-28T03:42:00Z",
        "ts_ms": 1777376520000,
        "source": "api_config",
        "action": "update",
        "entity": "monitor_watchlist",
        "key": "HK09988",
        "before": {"name": "阿里", "large": "9007199254740993"},
        "after": {"cost": 88.8, "name": "阿里"},
        "db": "config.db",
        "prev_hash": "sha256:GENESIS",
    }

    assert canonical_event_bytes(event) == (
        '{"action":"update","after":{"cost":88.8,"name":"阿里"},'
        '"before":{"large":"9007199254740993","name":"阿里"},'
        '"db":"config.db","entity":"monitor_watchlist",'
        '"event_id":"01HWNM4Z7G8E6Q9M3R2T1V0X5K","key":"HK09988",'
        '"prev_hash":"sha256:GENESIS","schema_version":1,'
        '"source":"api_config","ts":"2026-04-28T03:42:00Z",'
        '"ts_ms":1777376520000}'
    ).encode("utf-8")


def test_compute_event_hash_excludes_hash_field():
    event = {
        "event_id": "e1",
        "schema_version": 1,
        "ts": "2026-04-28T03:42:00Z",
        "ts_ms": 1777376520000,
        "source": "api_config",
        "action": "update",
        "entity": "monitor_watchlist",
        "key": "HK09988",
        "before": None,
        "after": {},
        "db": "config.db",
        "prev_hash": "sha256:GENESIS",
    }
    with_hash = {**event, "hash": "sha256:old"}

    assert compute_event_hash(event) == compute_event_hash(with_hash)


def test_add_hash_chain_links_events_from_genesis():
    events = [
        {
            "event_id": "e1",
            "schema_version": 1,
            "ts": "2026-04-28T03:42:00Z",
            "ts_ms": 1777376520000,
            "source": "api_config",
            "action": "update",
            "entity": "monitor_watchlist",
            "key": "HK09988",
            "before": None,
            "after": {"shares": 800},
            "db": "config.db",
        },
        {
            "event_id": "e2",
            "schema_version": 1,
            "ts": "2026-04-28T03:43:00Z",
            "ts_ms": 1777376580000,
            "source": "api_config",
            "action": "update",
            "entity": "monitor_watchlist",
            "key": "HK09988",
            "before": {"shares": 800},
            "after": {"shares": 700},
            "db": "config.db",
        },
    ]

    chained = add_hash_chain(events)

    assert chained[0]["prev_hash"] == "sha256:GENESIS"
    assert chained[0]["hash"].startswith("sha256:")
    assert chained[1]["prev_hash"] == chained[0]["hash"]
    assert chained[1]["hash"].startswith("sha256:")


def test_write_jsonl_manifest_contains_no_payload(tmp_path):
    jsonl_path = tmp_path / "config_events.jsonl"
    rows = [
        {"event_id": "e1", "ts_ms": 1, "hash": "sha256:a", "after": {"shares": 1}},
        {"event_id": "e2", "ts_ms": 2, "hash": "sha256:b", "after": {"shares": 2}},
    ]
    jsonl_path.write_text("\n".join(json.dumps(row) for row in rows) + "\n")

    manifest_path = write_jsonl_manifest(
        jsonl_path,
        rows,
        generated_at_ms=1000,
        generator_version="test:1",
    )

    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema_version"] == 1
    assert manifest["file_name"] == "config_events.jsonl"
    assert manifest["first_event_id"] == "e1"
    assert manifest["last_event_id"] == "e2"
    assert manifest["row_count"] == 2
    assert manifest["file_sha256"].startswith("sha256:")
    assert "after" not in manifest
