from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Iterable
from pathlib import Path

from src.tools.screenshot_import import DEFAULT_MAX_IMAGE_BYTES


class PathSafetyError(ValueError):
    """Raised when a path fails screenshot-import safety checks."""


def _resolve_roots(allowed_roots: Iterable[Path]) -> list[Path]:
    return [Path(r).expanduser().resolve(strict=False) for r in allowed_roots]


def _is_under(path: Path, roots: list[Path]) -> bool:
    p = path.resolve(strict=False)
    for root in roots:
        r = root.resolve(strict=False)
        try:
            p.relative_to(r)
            return True
        except ValueError:
            continue
    return False


def _symlink_escape_in_chain(candidate: Path, roots: list[Path]) -> None:
    cand = Path(candidate).expanduser()
    if not cand.is_absolute():
        cand = cand.resolve(strict=False)

    chain: list[Path] = []
    cur: Path = cand
    while True:
        chain.append(cur)
        if cur == cur.parent:
            break
        cur = cur.parent
    chain.reverse()

    for prefix in chain:
        if prefix.exists() and prefix.is_symlink():
            resolved_link = prefix.resolve(strict=False)
            if not _is_under(resolved_link, roots):
                raise PathSafetyError(
                    f"symbolic link {prefix} resolves outside allowed roots"
                )


def validate_existing_image_path(
    path: str | Path,
    allowed_roots: Iterable[Path],
    max_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
) -> Path:
    roots = _resolve_roots(allowed_roots)
    resolved = Path(path).expanduser().resolve(strict=True)

    if not _is_under(resolved, roots):
        raise PathSafetyError("image path is outside allowed roots")

    st = resolved.stat()
    if not stat.S_ISREG(st.st_mode):
        raise PathSafetyError("image path is not a regular file")

    size = st.st_size
    if size > max_bytes:
        raise PathSafetyError(f"file is larger than {max_bytes} bytes")

    return resolved


def validate_output_path(path: str | Path, allowed_roots: Iterable[Path]) -> Path:
    roots = _resolve_roots(allowed_roots)
    candidate = Path(path).expanduser()

    parent = candidate.parent
    parent_resolved = parent.resolve(strict=False)
    if not _is_under(parent_resolved, roots):
        raise PathSafetyError("output parent directory is outside allowed roots")

    if candidate.exists() and candidate.is_symlink():
        raise PathSafetyError("output path must not be an existing symbolic link")

    _symlink_escape_in_chain(candidate, roots)

    return candidate


def atomic_write_json(path: str | Path, payload: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=".tmp_screenshot_import_",
        suffix=".json",
        dir=path.parent,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                payload,
                f,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            f.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
