from pathlib import Path

from scripts.check_audit_coverage import scan_file


def test_scanner_flags_unaudited_business_mutation(tmp_path):
    path = tmp_path / "route.ts"
    path.write_text(
        """
export function POST(db) {
  db.prepare("UPDATE monitor_watchlist SET name = ? WHERE symbol = ?").run("A", "HK00700");
}
""",
        encoding="utf-8",
    )

    findings = scan_file(path)

    assert len(findings) == 1
    assert findings[0].table == "monitor_watchlist"


def test_scanner_allows_audited_business_mutation(tmp_path):
    path = tmp_path / "route.ts"
    path.write_text(
        """
export function POST(db) {
  db.prepare("UPDATE monitor_watchlist SET name = ? WHERE symbol = ?").run("A", "HK00700");
  recordDbChangeBestEffort(db, { table: "monitor_watchlist" });
}
""",
        encoding="utf-8",
    )

    assert scan_file(path) == []


def test_scanner_skips_excluded_high_frequency_tables(tmp_path):
    path = tmp_path / "poller.py"
    path.write_text(
        """
conn.execute("INSERT OR REPLACE INTO price_snapshots (code, price) VALUES (?, ?)", ("HK00700", 320))
conn.execute("DELETE FROM signals WHERE date < ?", ("2026-01-01",))
""",
        encoding="utf-8",
    )

    assert scan_file(path) == []
