"""Reject staged/committed paths that point at sensitive runtime data."""

from __future__ import annotations

import argparse
from pathlib import PurePosixPath
import sys
import unicodedata


SENSITIVE_ROOTS = (
    PurePosixPath("src/data/audit"),
    PurePosixPath("src/data/archive"),
    PurePosixPath("src/data/backups"),
)


class SensitivePathError(ValueError):
    pass


def _normalize_path(path: str) -> PurePosixPath:
    normalized = unicodedata.normalize("NFC", path.replace("\\", "/"))
    pure = PurePosixPath(normalized)
    parts: list[str] = []
    for part in pure.parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                raise SensitivePathError(f"Path escapes repository root: {path}")
            continue
        parts.append(part)
    return PurePosixPath(*parts)


def _is_under(path: PurePosixPath, root: PurePosixPath) -> bool:
    return path == root or path.is_relative_to(root)


def check_paths(paths: list[str]) -> None:
    rejected: list[str] = []
    for raw_path in paths:
        normalized = _normalize_path(raw_path)
        if any(_is_under(normalized, root) for root in SENSITIVE_ROOTS):
            rejected.append(raw_path)
    if rejected:
        joined = "\n".join(f"- {path}" for path in rejected)
        raise SensitivePathError(f"Sensitive runtime data paths are not allowed:\n{joined}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--paths", nargs="+", required=True)
    args = parser.parse_args(argv)
    try:
        check_paths(args.paths)
    except SensitivePathError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
