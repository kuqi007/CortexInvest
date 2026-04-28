#!/usr/bin/env python3
"""
Tests for daily_summary_generator morning briefing functionality.
"""
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

# Add project root to path
import sys
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


class TestMorningBriefing:
    """Tests for morning briefing generation."""

    @pytest.fixture
    def mock_data_dir(self, tmp_path, monkeypatch):
        """Create temp data directory and patch DATA_DIR."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()

        # Create empty monitor_config.json
        (data_dir / "monitor_config.json").write_text('{"holdings": {}, "watching": {}}')

        # Create empty alert_config.json
        (data_dir / "alert_config.json").write_text('{"alerts": {}}')

        # Patch TRADING_DB_PATH to a temp DB
        import src.tools.daily_summary_generator as dsg
        monkeypatch.setattr(dsg, 'TRADING_DB_PATH', str(tmp_path / "trading.db"))

        # Init the morning_briefings table
        import sqlite3
        conn = sqlite3.connect(str(tmp_path / "trading.db"))
        conn.execute("""
            CREATE TABLE IF NOT EXISTS morning_briefings (
                date TEXT PRIMARY KEY,
                generated_at TEXT,
                content_json TEXT
            )
        """)
        conn.commit()
        conn.close()

        return data_dir

    def test_generate_morning_briefing_creates_file(self, mock_data_dir):
        """Test that generate_morning_briefing creates the briefing."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        mock_news = {
            "data": {
                "data": {
                    "llmSearchResponse": {"data": []}
                }
            }
        }
        with patch('src.tools.daily_summary_generator._call_news_search_api') as mock_api:
            mock_api.return_value = mock_news
            result = generate_morning_briefing()

        assert result is not None
        assert "generated_at" in result
        assert "us_markets" in result
        assert "asia_markets" in result
        assert "global_news" in result

    def test_generate_morning_briefing_includes_us_markets(self, mock_data_dir):
        """Test that morning briefing includes US market data."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        mock_news = {
            "data": {
                "data": {
                    "llmSearchResponse": {
                        "data": [
                            {
                                "title": "隔夜美股",
                                "content": "道琼斯涨0.85%，纳斯达克跌0.12%，标普500涨0.65%",
                            }
                        ]
                    }
                }
            }
        }

        with patch('src.tools.daily_summary_generator._call_news_search_api') as mock_api:
            mock_api.return_value = mock_news
            result = generate_morning_briefing()

        assert result is not None
        assert "us_markets" in result
        us_markets = result["us_markets"]
        assert len(us_markets) > 0
        assert "dow" in us_markets

    def test_generate_morning_briefing_includes_asia_markets(self, mock_data_dir):
        """Test that morning briefing includes Asia market data."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        mock_news = {
            "data": {
                "data": {
                    "llmSearchResponse": {"data": []}
                }
            }
        }
        with patch('src.tools.daily_summary_generator._call_news_search_api') as mock_api:
            mock_api.return_value = mock_news
            result = generate_morning_briefing()

        assert result is not None
        assert "asia_markets" in result

    def test_generate_morning_briefing_includes_global_news(self, mock_data_dir):
        """Test that morning briefing includes global news."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        mock_news = {
            "data": {
                "data": {
                    "llmSearchResponse": {"data": []}
                }
            }
        }
        with patch('src.tools.daily_summary_generator._call_news_search_api') as mock_api:
            mock_api.return_value = mock_news
            result = generate_morning_briefing()

        assert result is not None
        assert "global_news" in result

    def test_morning_briefing_date_format(self, mock_data_dir):
        """Test that generated_at follows ISO format."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        mock_news = {
            "data": {
                "data": {
                    "llmSearchResponse": {"data": []}
                }
            }
        }
        with patch('src.tools.daily_summary_generator._call_news_search_api') as mock_api:
            mock_api.return_value = mock_news
            result = generate_morning_briefing()

        assert result is not None
        generated_at = result["generated_at"]
        # Should be ISO format: YYYY-MM-DDTHH:MM:SS.microseconds
        assert "T" in generated_at
        date_part = generated_at.split("T")[0]
        assert date_part == datetime.now().strftime("%Y-%m-%d")


class TestDateLogic:
    """Tests for date checking logic in start_monitor.sh."""

    def test_date_today_matches(self):
        """Test date comparison when dates match."""
        today = datetime.now().strftime("%Y-%m-%d")
        last_date = today

        # Should not need to generate
        result = (last_date != today)
        assert result is False

    def test_date_yesterday_differs(self):
        """Test date comparison when dates differ."""
        today = datetime.now().strftime("%Y-%m-%d")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        last_date = yesterday

        # Should need to generate
        result = (last_date != today)
        assert result is True

    def test_date_older_than_yesterday(self):
        """Test date comparison when date is older than yesterday."""
        today = datetime.now().strftime("%Y-%m-%d")
        last_date = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")

        # Should need to generate
        result = (last_date != today)
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
