import json
from pathlib import Path

import pytest

from src.tools.screenshot_import.path_safety import (
    PathSafetyError,
    atomic_write_json,
    validate_debug_output_dir,
    validate_existing_image_path,
    validate_output_path,
)


def test_validate_existing_image_path_allows_regular_file_under_allowed_root(tmp_path):
    image = tmp_path / "shot.png"
    image.write_bytes(b"\x89PNG\r\n\x1a\n")
    resolved = validate_existing_image_path(image, allowed_roots=[tmp_path], max_bytes=20)
    assert resolved == image.resolve()


def test_validate_existing_image_path_rejects_large_file(tmp_path):
    image = tmp_path / "large.png"
    image.write_bytes(b"x" * 21)
    with pytest.raises(PathSafetyError, match="larger than"):
        validate_existing_image_path(image, allowed_roots=[tmp_path], max_bytes=20)


def test_validate_output_path_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSafetyError):
        validate_output_path(link / "plan.json", allowed_roots=[allowed])


def test_atomic_write_json_writes_complete_json(tmp_path):
    path = tmp_path / "plan.json"
    atomic_write_json(path, {"b": ["x"], "a": 1})
    text = path.read_text(encoding="utf-8")
    assert json.loads(text) == {"a": 1, "b": ["x"]}
    assert text.endswith("\n")
    assert text.index('"a"') < text.index('"b"')


def test_validate_debug_output_dir_rejects_symlink_escape(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    link = allowed / "escape"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(PathSafetyError):
        validate_debug_output_dir(link / "dbg", allowed_roots=[allowed])
