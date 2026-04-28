import pytest

from src.utils.path_safety import materialize_repo_file


def test_materialize_repo_file_rejects_parent_traversal(tmp_path):
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    with pytest.raises(ValueError, match="parent traversal"):
        materialize_repo_file(repo_root, "../outside.json")


def test_materialize_repo_file_rejects_symlink_component(tmp_path):
    repo_root = tmp_path / "repo"
    safe_dir = repo_root / "src" / "data"
    safe_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "config_events.jsonl").write_text("{}\n")
    (safe_dir / "audit").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="symlink"):
        materialize_repo_file(
            repo_root,
            "src/data/audit/config_events.jsonl",
            allowed_roots=("src/data/audit",),
        )


def test_materialize_repo_file_reads_bytes_for_allowed_path(tmp_path):
    repo_root = tmp_path / "repo"
    audit_dir = repo_root / "src" / "data" / "audit"
    audit_dir.mkdir(parents=True)
    target = audit_dir / "config_events.jsonl"
    target.write_text('{"ok": true}\n')

    materialized = materialize_repo_file(
        repo_root,
        "src/data/audit/config_events.jsonl",
        allowed_roots=("src/data/audit",),
        allowed_suffixes=(".jsonl",),
    )

    assert materialized.relative_path == "src/data/audit/config_events.jsonl"
    assert materialized.data == b'{"ok": true}\n'
    assert materialized.sha256.startswith("sha256:")
