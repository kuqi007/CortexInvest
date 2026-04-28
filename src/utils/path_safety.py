"""Safe repo-root-relative file materialization helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import unicodedata


@dataclass(frozen=True)
class MaterializedFile:
    relative_path: str
    real_path: Path
    data: bytes
    sha256: str


def _normalized_posix(path: Path) -> str:
    return unicodedata.normalize("NFC", path.as_posix())


def _reject_symlink_components(repo_root: Path, target: Path) -> None:
    current = repo_root
    for part in target.relative_to(repo_root).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"Refusing symlink component: {part}")


def materialize_repo_file(
    repo_root: Path,
    relative_path: str,
    *,
    allowed_roots: tuple[str, ...] = ("src/data/audit", "src/data/archive"),
    allowed_suffixes: tuple[str, ...] = (".jsonl", ".json"),
) -> MaterializedFile:
    if Path(relative_path).is_absolute():
        raise ValueError("absolute paths are not allowed")
    requested = Path(relative_path)
    if ".." in requested.parts:
        raise ValueError("parent traversal is not allowed")
    if requested.suffix not in allowed_suffixes:
        raise ValueError(f"unsupported file suffix: {requested.suffix}")

    repo_root = repo_root.resolve()
    target = repo_root / requested
    _reject_symlink_components(repo_root, target)
    real_path = target.resolve(strict=True)

    root_text = _normalized_posix(repo_root)
    real_text = _normalized_posix(real_path)
    if not (real_text == root_text or real_text.startswith(f"{root_text}/")):
        raise ValueError("resolved path escaped repo root")

    allowed = False
    requested_text = _normalized_posix(requested)
    for allowed_root in allowed_roots:
        allowed_root = allowed_root.rstrip("/")
        if requested_text == allowed_root or requested_text.startswith(f"{allowed_root}/"):
            allowed = True
            break
    if not allowed:
        raise ValueError("path is outside allowed roots")

    data = real_path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    return MaterializedFile(
        relative_path=requested.as_posix(),
        real_path=real_path,
        data=data,
        sha256=f"sha256:{digest}",
    )
