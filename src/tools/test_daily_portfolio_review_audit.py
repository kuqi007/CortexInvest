import json

import src.sim_trading.db as db
import src.tools.daily_portfolio_review as dpr


def test_daily_portfolio_review_feishu_success_records_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    monkeypatch.setattr(dpr, "_feishu_get_token", lambda: "token")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 0}

    monkeypatch.setattr(dpr.requests, "post", lambda *args, **kwargs: Response())

    assert dpr._feishu_send("每日复盘", "summary")

    conn = db.get_connection()
    row = conn.execute("SELECT payload_json FROM trading_audit_outbox").fetchone()
    conn.close()

    payload = json.loads(row["payload_json"])
    assert payload["after"]["channel"] == "feishu"
    assert payload["after"]["metadata"] == {
        "method": "daily-portfolio-review",
        "kind": "daily_review",
    }


def test_daily_portfolio_review_feishu_failure_does_not_record_audit(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    monkeypatch.setattr(dpr, "_feishu_get_token", lambda: "token")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"code": 999}

    monkeypatch.setattr(dpr.requests, "post", lambda *args, **kwargs: Response())

    assert not dpr._feishu_send("每日复盘", "summary")
    conn = db.get_connection()
    count = conn.execute("SELECT COUNT(*) FROM trading_audit_outbox").fetchone()[0]
    conn.close()

    assert count == 0
