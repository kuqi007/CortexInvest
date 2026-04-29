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

UNICODE_SEPARATOR_TRANSLATION = str.maketrans(
    {
        "\\": "/",
        "\u2044": "/",  # fraction slash
        "\u2215": "/",  # division slash
        "\u2216": "/",  # set minus, commonly confused with backslash
        "\u29f8": "/",  # big solidus
        "\ufe68": "/",  # small reverse solidus
        "\uff0f": "/",  # fullwidth solidus
        "\uff3c": "/",  # fullwidth reverse solidus
    }
)


class SensitivePathError(ValueError):
    pass


def _normalize_path(path: str) -> PurePosixPath:
    normalized = unicodedata.normalize("NFKC", path).translate(
        UNICODE_SEPARATOR_TRANSLATION
    )
    if normalized.strip() != normalized:
        raise SensitivePathError(f"Paths with leading/trailing whitespace are not allowed: {path}")
    for char in normalized:
        if unicodedata.category(char).startswith("C"):
            raise SensitivePathError(f"Paths with control/format characters are not allowed: {path}")
    if normalized.startswith("/"):
        raise SensitivePathError(f"Absolute paths are not allowed: {path}")
    if len(normalized) >= 2 and normalized[1] == ":":
        raise SensitivePathError(f"Drive-qualified paths are not allowed: {path}")
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
        parts.append(part.casefold())
    if parts and parts[0] == "src":
        for part in parts[:3]:
            if not part.isascii():
                raise SensitivePathError(
                    f"Non-ASCII path segments under src are not allowed: {path}"
                )
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
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--paths", nargs="+")
    group.add_argument(
        "--stdin0",
        action="store_true",
        help="Read NUL-delimited paths from stdin, e.g. git diff --cached --name-only -z",
    )
    args = parser.parse_args(argv)
    paths = (
        [p for p in sys.stdin.buffer.read().decode("utf-8").split("\0") if p]
        if args.stdin0
        else args.paths
    )
    try:
        check_paths(paths)
    except SensitivePathError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
