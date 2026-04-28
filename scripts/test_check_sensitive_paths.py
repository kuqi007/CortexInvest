import pytest

from scripts.check_sensitive_paths import SensitivePathError, check_paths


def test_check_paths_rejects_audit_archive_and_backup_paths():
    with pytest.raises(SensitivePathError):
        check_paths(["src/data/audit/config_events.jsonl"])
    with pytest.raises(SensitivePathError):
        check_paths(["src/data/archive/2026-04-28/price_snapshots.json"])
    with pytest.raises(SensitivePathError):
        check_paths(["src/data/backups/restore/2026-04-28/db.sqlite"])


def test_check_paths_allows_source_files_and_fixtures():
    check_paths(
        [
            "src/tools/audit_flush.py",
            "tests/fixtures/src/data/audit/sample.jsonl",
            "docs/superpowers/specs/2026-04-28-json-to-db-audit-log-design.md",
        ]
    )


def test_check_paths_rejects_parent_traversal_into_sensitive_dir():
    with pytest.raises(SensitivePathError):
        check_paths(["src/data/tmp/../audit/config_events.jsonl"])
