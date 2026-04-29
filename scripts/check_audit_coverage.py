#!/usr/bin/env python3
"""Conservative audit coverage scanner for direct DB mutations."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.utils.audit_writer import audit_table_enabled  # noqa: E402

MUTATION_RE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
AUDIT_HELPERS = (
    "record_db_change_best_effort",
    "recordDbChangeBestEffort",
    "recordConfigAudit",
    "insertConfigAuditOutbox",
    "insertTradingAuditOutbox",
    "insert_config_outbox",
    "insert_trading_outbox",
    "build_audit_event_v2",
    "recordTradePlanAudit",
    "recordTradingAudit",
    "_record_trade_plan_audit",
    "_record_config_audit",
)
IGNORED_FILE_PARTS = (
    ".test.",
    "/tests/",
    "/scripts/test_",
    "/src/sim_trading/db.py",
    "/src/sim_trading/seed_live.py",
    "/src/utils/audit_log.py",
    "/src/tools/audit_flush.py",
)
NON_TABLE_TOKENS = {"set", "order", "less", "pending", "config", "highest"}


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    table: str
    statement: str


def _is_ignored_path(path: Path) -> bool:
    normalized = "/" + path.as_posix()
    return path.name.startswith("test_") or any(part in normalized for part in IGNORED_FILE_PARTS)


def _nearby_has_audit(lines: list[str], index: int, window: int = 140) -> bool:
    start = max(0, index - 6)
    end = min(len(lines), index + window + 1)
    snippet = "\n".join(lines[start:end])
    return any(helper in snippet for helper in AUDIT_HELPERS)


def scan_file(path: Path) -> list[Finding]:
    if _is_ignored_path(path):
        return []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for idx, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("#", "//")):
            continue
        chunk = "\n".join(lines[idx : idx + 4])
        for match in MUTATION_RE.finditer(chunk):
            table = match.group(1)
            if table.casefold() in NON_TABLE_TOKENS:
                continue
            actual_line = idx + chunk[: match.start()].count("\n") + 1
            if lines[actual_line - 1].strip().startswith(("#", "//")):
                continue
            marker = (actual_line, table.casefold())
            if marker in seen:
                continue
            seen.add(marker)
            # Outbox mutations are the audit mechanism itself.
            if table.endswith("_audit_outbox"):
                continue
            if not (
                audit_table_enabled("trading.db", table)
                and audit_table_enabled("config.db", table)
            ):
                continue
            if _nearby_has_audit(lines, idx):
                continue
            findings.append(
                Finding(
                    path=path,
                    line=actual_line,
                    table=table,
                    statement=lines[actual_line - 1].strip(),
                )
            )
    return findings


def scan_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        if path.is_dir():
            candidates = sorted(
                p
                for pattern in ("*.py", "*.ts", "*.tsx")
                for p in path.rglob(pattern)
                if not _is_ignored_path(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p)
            )
        else:
            candidates = [path]
        for candidate in candidates:
            findings.extend(scan_file(candidate))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", default=["src", "web/app/api", "web/app/lib"])
    args = parser.parse_args(argv)
    paths = [ROOT / p for p in args.paths]
    findings = scan_paths(paths)
    if findings:
        for finding in findings:
            rel = finding.path.relative_to(ROOT) if finding.path.is_relative_to(ROOT) else finding.path
            print(
                f"{rel}:{finding.line}: unaudited mutation of {finding.table}: {finding.statement}",
                file=sys.stderr,
            )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
