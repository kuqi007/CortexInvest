import pytest

import src.sim_trading.db as db
from src.utils import config_reader


@pytest.fixture()
def config_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "_config_db_path_override", str(tmp_path / "config.db"))
    monkeypatch.setattr(db, "_db_path_override", str(tmp_path / "trading.db"))
    db.init_config_db()
    conn = db.get_config_connection()
    conn.execute(
        """
        INSERT INTO monitor_watchlist (
            symbol, name, list_type, cost, shares, lot, hidden, star,
            dip_buy, tags, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "HK09988",
            "阿里巴巴",
            "holding",
            88.8,
            100,
            100,
            0,
            1,
            0,
            '["AI"]',
            1777376520000,
            1777376520000,
        ),
    )
    conn.execute(
        "INSERT INTO monitor_settings (key, value, updated_at) VALUES (?, ?, ?)",
        ("poll_interval", 30, 1777376520000),
    )
    conn.commit()
    conn.close()
    return tmp_path / "config.db"


def test_read_monitor_config_reads_db_when_json_is_missing(config_db, monkeypatch, tmp_path):
    monkeypatch.setattr(config_reader, "JSON_PATH", tmp_path / "missing.json")

    cfg = config_reader.read_monitor_config()

    assert cfg["watchlist"]["HK09988"]["name"] == "阿里巴巴"
    assert cfg["watchlist"]["HK09988"]["type"] == "holding"
    assert cfg["watchlist"]["HK09988"]["star"] is True
    assert cfg["watchlist"]["HK09988"]["tags"] == ["AI"]
    assert cfg["settings"]["poll_interval"] == 30


def test_read_monitor_config_raises_when_db_unavailable_and_json_exists(
    tmp_path, monkeypatch
):
    missing_db = tmp_path / "missing-config.db"
    stale_json = tmp_path / "monitor_config.json"
    stale_json.write_text('{"watchlist": {"KR000660": {"name": "SK"}}}')
    monkeypatch.setattr(db, "_config_db_path_override", str(missing_db))
    monkeypatch.setattr(config_reader, "JSON_PATH", stale_json)

    with pytest.raises(RuntimeError, match="config.db"):
        config_reader.read_monitor_config()
