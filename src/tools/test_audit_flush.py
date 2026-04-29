import json

import src.sim_trading.db as db
from src.tools.audit_flush import flush_outbox_once
from src.utils.audit_hash import add_hash_chain
from src.utils.audit_log import build_audit_event, insert_config_outbox


def test_flush_outbox_once_writes_jsonl_manifest_and_marks_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    try:
        event = build_audit_event(
            event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
            ts_ms=1777376520000,
            source="api_config",
            action="update",
            entity="monitor_watchlist",
            key="HK09988",
            db_name="config.db",
            before=None,
            after={"shares": 800},
        )
        insert_config_outbox(conn, event)
        conn.commit()
    finally:
        conn.close()

    result = flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376521000)

    assert result.flushed_count == 1
    jsonl_path = tmp_path / "audit" / "config_events.jsonl"
    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
    assert rows[0]["event_id"] == "01HWNM4Z7G8E6Q9M3R2T1V0X5K"
    assert rows[0]["prev_hash"] == "sha256:GENESIS"
    assert rows[0]["hash"].startswith("sha256:")

    manifest = json.loads((tmp_path / "audit" / "config_events.jsonl.manifest.json").read_text())
    assert manifest["row_count"] == 1
    assert manifest["file_sha256"].startswith("sha256:")
    assert manifest["last_event_id"] == rows[0]["event_id"]

    conn = db.get_config_connection()
    try:
        outbox = conn.execute(
            "SELECT flushed_at_ms, flush_id FROM config_audit_outbox"
        ).fetchone()
        assert outbox["flushed_at_ms"] == 1777376521000
        assert outbox["flush_id"] == result.flush_id
    finally:
        conn.close()


def test_flush_outbox_once_is_idempotent_when_no_pending_rows(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()

    first = flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376521000)
    second = flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376522000)

    assert first.flushed_count == 0
    assert second.flushed_count == 0


def test_flush_outbox_once_recovers_when_pending_event_already_in_jsonl(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    event = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before=None,
        after={"shares": 800},
    )
    conn = db.get_config_connection()
    try:
        insert_config_outbox(conn, event)
        conn.commit()
    finally:
        conn.close()

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    jsonl_path = audit_dir / "config_events.jsonl"
    already_flushed = add_hash_chain([event])[0]
    jsonl_path.write_text(json.dumps(already_flushed, ensure_ascii=False) + "\n")

    result = flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376521000)

    assert result.flushed_count == 1
    lines = jsonl_path.read_text().splitlines()
    assert len(lines) == 1
    conn = db.get_config_connection()
    try:
        outbox = conn.execute(
            "SELECT flushed_at_ms FROM config_audit_outbox WHERE event_id = ?",
            (event["event_id"],),
        ).fetchone()
        assert outbox["flushed_at_ms"] == 1777376521000
    finally:
        conn.close()


def test_flush_outbox_once_appends_to_existing_hash_chain(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    first = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before=None,
        after={"shares": 800},
    )
    second = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5L",
        ts_ms=1777376580000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before={"shares": 800},
        after={"shares": 700},
    )
    conn = db.get_config_connection()
    try:
        insert_config_outbox(conn, first)
        conn.commit()
    finally:
        conn.close()

    flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376521000)
    conn = db.get_config_connection()
    try:
        insert_config_outbox(conn, second)
        conn.commit()
    finally:
        conn.close()

    flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376581000)

    jsonl_path = tmp_path / "audit" / "config_events.jsonl"
    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
    assert rows[1]["prev_hash"] == rows[0]["hash"]


def test_flush_outbox_once_rotates_legacy_unchained_jsonl(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    event = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before=None,
        after={"shares": 800},
    )
    conn = db.get_config_connection()
    try:
        insert_config_outbox(conn, event)
        conn.commit()
    finally:
        conn.close()

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    jsonl_path = audit_dir / "config_events.jsonl"
    legacy_event = {
        "event_id": "legacy-1",
        "ts_ms": 1777376400000,
        "source": "notification",
        "after": {"message": "legacy plaintext"},
    }
    jsonl_path.write_text(json.dumps(legacy_event, ensure_ascii=False) + "\n")

    result = flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376521000)

    assert result.flushed_count == 1
    rows = [json.loads(line) for line in jsonl_path.read_text().splitlines()]
    assert [row["event_id"] for row in rows] == [event["event_id"]]
    assert rows[0]["prev_hash"] == "sha256:GENESIS"

    legacy_paths = list(audit_dir.glob("config_events.jsonl.legacy.*"))
    assert len(legacy_paths) == 1
    assert json.loads(legacy_paths[0].read_text()) == legacy_event


def test_flush_outbox_once_refuses_event_id_match_with_different_payload(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    event = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before=None,
        after={"shares": 800},
    )
    conn = db.get_config_connection()
    try:
        insert_config_outbox(conn, event)
        conn.commit()
    finally:
        conn.close()

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    jsonl_path = audit_dir / "config_events.jsonl"
    wrong_event = {**event, "after": {"shares": 1}}
    jsonl_path.write_text(
        json.dumps(add_hash_chain([wrong_event])[0], ensure_ascii=False) + "\n"
    )

    try:
        flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376521000)
        raise AssertionError("flush should reject mismatched duplicate event_id")
    except RuntimeError as exc:
        assert "payload mismatch" in str(exc)


def test_flush_outbox_once_refuses_tampered_existing_hash_chain(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    event = build_audit_event(
        event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
        ts_ms=1777376520000,
        source="api_config",
        action="update",
        entity="monitor_watchlist",
        key="HK09988",
        db_name="config.db",
        before=None,
        after={"shares": 800},
    )
    conn = db.get_config_connection()
    try:
        insert_config_outbox(conn, event)
        conn.commit()
    finally:
        conn.close()

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    chained = add_hash_chain([{**event, "after": {"shares": 1}}])[0]
    chained["hash"] = "sha256:bad"
    (audit_dir / "config_events.jsonl").write_text(
        json.dumps(chained, ensure_ascii=False) + "\n"
    )

    try:
        flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376521000)
        raise AssertionError("flush should reject tampered existing audit chain")
    except RuntimeError as exc:
        assert "hash mismatch" in str(exc)


def test_flush_outbox_once_refuses_corrupt_jsonl_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    try:
        insert_config_outbox(
            conn,
            build_audit_event(
                event_id="01HWNM4Z7G8E6Q9M3R2T1V0X5K",
                ts_ms=1777376520000,
                source="api_config",
                action="update",
                entity="monitor_watchlist",
                key="HK09988",
                db_name="config.db",
                before=None,
                after={"shares": 800},
            ),
        )
        conn.commit()
    finally:
        conn.close()

    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "config_events.jsonl").write_text('{"event_id": "partial"')

    try:
        flush_outbox_once("config", data_dir=tmp_path, now_ms=1777376521000)
        raise AssertionError("flush should reject corrupt JSONL")
    except RuntimeError as exc:
        assert "corrupt JSONL" in str(exc)
