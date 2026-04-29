import pytest

from scripts.check_sensitive_paths import SensitivePathError, check_paths, main


def test_check_paths_rejects_audit_archive_and_backup_paths():
    with pytest.raises(SensitivePathError):
        check_paths(["src/data/audit/config_events.jsonl"])
    with pytest.raises(SensitivePathError):
        check_paths(["SRC/Data/Audit/config_events.jsonl"])
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


def test_check_paths_rejects_absolute_or_drive_qualified_paths():
    for path in [
        "/src/data/audit/config_events.jsonl",
        "//src/data/audit/config_events.jsonl",
        "C:/src/data/audit/config_events.jsonl",
        r"C:\src\data\audit\config_events.jsonl",
        " /src/data/audit/config_events.jsonl",
        "\tC:/src/data/audit/config_events.jsonl",
    ]:
        with pytest.raises(SensitivePathError):
            check_paths([path])


def test_check_paths_rejects_unicode_separator_or_format_bypasses():
    with pytest.raises(SensitivePathError):
        check_paths(["src／data／audit／config_events.jsonl"])
    with pytest.raises(SensitivePathError):
        check_paths(["\u200bC:/src/data/audit/config_events.jsonl"])
    with pytest.raises(SensitivePathError):
        check_paths(["src/data/\u0430udit/config_events.jsonl"])


def test_check_paths_allows_non_ascii_outside_src_prefix():
    check_paths(["stocks/HK03696_英矽智能/report.md"])


def test_main_reads_nul_delimited_paths_from_stdin(monkeypatch):
    class FakeStdin:
        buffer = type(
            "Buffer",
            (),
            {"read": staticmethod(lambda: b"src/tools/audit_flush.py\0README.md\0")},
        )()

    monkeypatch.setattr("sys.stdin", FakeStdin())

    assert main(["--stdin0"]) == 0


def test_main_rejects_sensitive_path_from_stdin(monkeypatch):
    class FakeStdin:
        buffer = type(
            "Buffer",
            (),
            {"read": staticmethod(lambda: b"src/data/audit/config_events.jsonl\0")},
        )()

    monkeypatch.setattr("sys.stdin", FakeStdin())

    assert main(["--stdin0"]) == 1
