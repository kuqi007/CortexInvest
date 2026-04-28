"""Signal rule loading from config.db."""

from __future__ import annotations

import json
import logging

from src.sim_trading.db import get_config_connection, init_config_db

logger = logging.getLogger("signal_rules")


def load_signal_rules() -> dict:
    """Load enabled signal rules from config.db."""
    conn = None
    try:
        init_config_db()
        conn = get_config_connection()
        rows = conn.execute(
            """
            SELECT rule_id, rule_json
            FROM signal_rules
            WHERE enabled = 1
            ORDER BY rule_id
            """
        ).fetchall()
    except Exception as e:
        logger.warning(f"Cannot read signal_rules from config.db: {e}")
        return {}
    finally:
        if conn is not None:
            conn.close()

    rules: dict = {}
    for row in rows:
        try:
            rules[row["rule_id"]] = json.loads(row["rule_json"] or "{}")
        except json.JSONDecodeError:
            logger.warning(f"Invalid signal_rules row: {row['rule_id']}")
    return rules
