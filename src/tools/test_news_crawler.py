import json

import src.sim_trading.db as db
import src.tools.news_crawler as nc


def _news() -> list[dict]:
    return [
        {
            "title": "公司获得大额订单",
            "content": "公司披露获得重要客户订单，预计改善收入。",
            "publish_time": "2026-04-28 09:00:00",
            "source": "公告",
        }
    ]


def test_news_sentiment_reads_cached_score_from_db_without_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    db.init_trading_db()
    cache_key = nc._sentiment_cache_key(_news(), 5, True)
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO sentiment_cache (cache_key, payload_json, updated_at_ms) VALUES (?, ?, ?)",
        (cache_key, json.dumps({"score": 0.75}), 1777376520000),
    )
    conn.commit()
    conn.close()

    assert nc.get_news_sentiment(_news(), use_structured_output=True) == 0.75


def test_news_sentiment_writes_score_to_db_not_json(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    cache_file = tmp_path / "sentiment_cache.json"
    monkeypatch.setattr(nc, "SENTIMENT_CACHE_FILE", cache_file, raising=False)
    db.init_trading_db()

    class FakeClient:
        def get_completion(self, *args, **kwargs):
            return '{"score": 0.42, "confidence": 0.8, "key_factors": ["订单"]}'

    monkeypatch.setattr(nc.LLMClientFactory, "create_client", lambda: FakeClient())

    assert nc.get_news_sentiment(_news(), use_structured_output=True) == 0.42
    assert not cache_file.exists()
    conn = db.get_connection()
    row = conn.execute(
        "SELECT payload_json FROM sentiment_cache WHERE cache_key = ?",
        (nc._sentiment_cache_key(_news(), 5, True),),
    ).fetchone()
    conn.close()
    assert row is not None
    assert json.loads(row["payload_json"])["score"] == 0.42
