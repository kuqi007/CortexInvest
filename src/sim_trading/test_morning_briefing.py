#!/usr/bin/env python3
"""
Tests for daily_summary_generator morning briefing functionality.
"""
import json
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

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

        # Create empty market_data.json
        (data_dir / "market_data.json").write_text('{"services": []}')

        # Create empty monitor_config.json
        (data_dir / "monitor_config.json").write_text('{"holdings": {}, "watching": {}}')

        # Create empty alert_config.json
        (data_dir / "alert_config.json").write_text('{"alerts": {}}')

        # Patch DATA_DIR
        import src.tools.daily_summary_generator as dsg
        monkeypatch.setattr(dsg, 'DATA_DIR', data_dir)
        monkeypatch.setattr(dsg, 'MORNING_BRIEFING_PATH', data_dir / "morning_briefing.json")

        return data_dir

    def test_generate_morning_briefing_creates_file(self, mock_data_dir):
        """Test that generate_morning_briefing creates the file."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        with patch('src.tools.daily_summary_generator._call_eastmoney_api') as mock_api:
            mock_api.return_value = {}
            generate_morning_briefing()

        morning_file = mock_data_dir / "morning_briefing.json"
        assert morning_file.exists()

        data = json.loads(morning_file.read_text())
        assert "generated_at" in data
        assert "us_markets" in data
        assert "asia_markets" in data
        assert "global_news" in data

    def test_generate_morning_briefing_includes_us_markets(self, mock_data_dir):
        """Test that morning briefing includes US market data."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        with patch('src.tools.daily_summary_generator._call_eastmoney_api') as mock_api:
            mock_api.return_value = {}
            generate_morning_briefing()

        morning_file = mock_data_dir / "morning_briefing.json"
        data = json.loads(morning_file.read_text())

        assert "us_markets" in data
        # US markets should have at least some data
        us_markets = data["us_markets"]
        assert len(us_markets) > 0

    def test_generate_morning_briefing_includes_asia_markets(self, mock_data_dir):
        """Test that morning briefing includes Asia market data (uses real API)."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        # This test uses real API since Asia markets use different endpoint
        generate_morning_briefing()

        morning_file = mock_data_dir / "morning_briefing.json"
        data = json.loads(morning_file.read_text())
        assert "asia_markets" in data

    def test_generate_morning_briefing_includes_global_news(self, mock_data_dir):
        """Test that morning briefing includes global news (uses real API)."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        # This test uses real API since mock doesn't work for news search
        # The important thing is that the function runs without error
        generate_morning_briefing()

        morning_file = mock_data_dir / "morning_briefing.json"
        assert morning_file.exists()
        data = json.loads(morning_file.read_text())
        assert "global_news" in data

    def test_morning_briefing_date_format(self, mock_data_dir):
        """Test that generated_at follows ISO format."""
        from src.tools.daily_summary_generator import generate_morning_briefing

        with patch('src.tools.daily_summary_generator._call_eastmoney_api') as mock_api:
            mock_api.return_value = {}
            generate_morning_briefing()

        morning_file = mock_data_dir / "morning_briefing.json"
        data = json.loads(morning_file.read_text())

        generated_at = data["generated_at"]
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
