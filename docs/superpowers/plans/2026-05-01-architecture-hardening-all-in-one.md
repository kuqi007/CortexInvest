# Architecture Hardening All-In-One Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full architecture hardening sequence in small, reviewable PRs: secure `/api/indicators`, block root runtime backups, add baseline CI, add architecture guards, document Web mutations, make sector GET read-only, and begin DDL centralization.

**Architecture:** Keep the existing Python daemon + Next.js dashboard + SQLite split architecture. Do not rewrite the system. Add narrow safety rails around the highest-risk boundaries: subprocess execution, runtime data commits, test/build feedback, Web write permissions, route mutation documentation, GET purity, and schema authority.

**Tech Stack:** Python 3, uv, pytest, Next.js 15, TypeScript, Vitest, GitHub Actions, SQLite.

---

## Source Spec

The rationale and risk model are documented in:

- `docs/superpowers/specs/2026-05-01-architecture-hardening-plan.md`

This all-in-one plan replaces the need to jump between the split plan files during implementation. The split files can remain as reference copies.

## Execution Order

Implement as separate PRs unless the user explicitly asks to batch:

1. PR 1: Fix `/api/indicators` subprocess boundary.
2. PR 2: Protect root `backups/`.
3. PR 3: Add baseline CI and expand pytest discovery.
4. PR 4: Add architecture contract checks.
5. PR 5: Document Web mutation matrix.
6. PR 6: Make `GET /api/sector` read-only.
7. PR 7: Centralize DDL migration authority.

## Global Rules

- Do not modify runtime DBs or JSON files directly.
- Do not commit files under `backups/`.
- Do not combine behavior changes with unrelated CI/doc changes unless explicitly requested.
- Run each PR's focused tests before moving to the next PR.
- Commit only when the user explicitly asks.

---

# PR 1: Fix `/api/indicators` Subprocess Boundary

## Goal

Replace unsafe `execSync + python -c + user input interpolation` with:

- strict stock code validation,
- async `spawn`,
- JSON over stdin,
- Python CLI module.

## Files

- Create: `web/app/lib/stock-code.ts`
- Create: `web/app/lib/stock-code.test.ts`
- Create: `src/tools/indicators_cli.py`
- Create: `src/tools/test_indicators_cli.py`
- Modify: `web/app/api/indicators/route.ts`
- Create: `web/app/api/indicators/route.test.ts`

## Task 1.1: Add Stock Code Validation

- [ ] Create `web/app/lib/stock-code.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { parseStockCode } from "./stock-code";

describe("parseStockCode", () => {
  it("accepts A-share six digit symbols", () => {
    expect(parseStockCode("603929")).toEqual({ ok: true, symbol: "603929" });
    expect(parseStockCode("000001")).toEqual({ ok: true, symbol: "000001" });
  });

  it("accepts HK symbols in project format", () => {
    expect(parseStockCode("HK09988")).toEqual({ ok: true, symbol: "HK09988" });
    expect(parseStockCode("hk00700")).toEqual({ ok: true, symbol: "HK00700" });
  });

  it("rejects missing, KR, and malformed symbols", () => {
    expect(parseStockCode(null)).toEqual({ ok: false, error: "symbol required" });
    expect(parseStockCode("")).toEqual({ ok: false, error: "symbol required" });
    expect(parseStockCode("KR005930")).toEqual({ ok: false, error: "KR not supported" });
    expect(parseStockCode("603929'")).toEqual({ ok: false, error: "invalid symbol" });
    expect(parseStockCode("HK9988")).toEqual({ ok: false, error: "invalid symbol" });
    expect(parseStockCode("abc")).toEqual({ ok: false, error: "invalid symbol" });
  });
});
```

- [ ] Run and verify failure:

```bash
cd web && npm run test:unit -- app/lib/stock-code.test.ts
```

Expected: import failure because `stock-code.ts` does not exist.

- [ ] Create `web/app/lib/stock-code.ts`:

```typescript
export type StockCodeParseResult =
  | { ok: true; symbol: string }
  | { ok: false; error: string };

const A_SHARE_SYMBOL_RE = /^\d{6}$/;
const HK_SYMBOL_RE = /^HK\d{5}$/;

export function parseStockCode(raw: string | null): StockCodeParseResult {
  const symbol = raw?.trim();
  if (!symbol) {
    return { ok: false, error: "symbol required" };
  }

  const normalized = symbol.toUpperCase();
  if (normalized.startsWith("KR")) {
    return { ok: false, error: "KR not supported" };
  }

  if (A_SHARE_SYMBOL_RE.test(normalized) || HK_SYMBOL_RE.test(normalized)) {
    return { ok: true, symbol: normalized };
  }

  return { ok: false, error: "invalid symbol" };
}
```

- [ ] Run:

```bash
cd web && npm run test:unit -- app/lib/stock-code.test.ts
```

Expected: PASS.

## Task 1.2: Add Python Indicators CLI

- [ ] Create `src/tools/test_indicators_cli.py`:

```python
import json

import pandas as pd
import pytest

from src.tools import indicators_cli


def test_parse_payload_accepts_valid_a_share():
    payload = indicators_cli.parse_payload('{"symbol":"603929","live_price":128.5}')
    assert payload == {"symbol": "603929", "live_price": 128.5}


def test_parse_payload_normalizes_hk_symbol():
    payload = indicators_cli.parse_payload('{"symbol":"hk09988","live_price":null}')
    assert payload == {"symbol": "HK09988", "live_price": None}


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "{}",
        '{"symbol":"KR005930"}',
        '{"symbol":"603929\'"}',
        '{"symbol":"HK9988"}',
        '{"symbol":"603929","live_price":-1}',
        '{"symbol":"603929","live_price":1000001}',
    ],
)
def test_parse_payload_rejects_invalid_payload(raw):
    with pytest.raises(ValueError):
        indicators_cli.parse_payload(raw)


def test_run_returns_indicator_json(monkeypatch):
    def fake_fetch(symbol, days):
        assert symbol == "603929"
        assert days == 60
        return pd.DataFrame(
            {
                "close": [10.0] * 30,
                "high": [10.5] * 30,
                "low": [9.5] * 30,
                "volume": [1000.0] * 30,
            }
        )

    def fake_compute(kline, live_price=None):
        assert live_price == 128.5
        return {"close": 128.5, "rsi": 55.0}

    monkeypatch.setattr(indicators_cli, "fetch_kline_akshare", fake_fetch)
    monkeypatch.setattr(indicators_cli, "compute_indicators", fake_compute)

    assert indicators_cli.run({"symbol": "603929", "live_price": 128.5}) == {
        "close": 128.5,
        "rsi": 55.0,
    }


def test_run_returns_error_when_kline_missing(monkeypatch):
    monkeypatch.setattr(indicators_cli, "fetch_kline_akshare", lambda symbol, days: None)
    assert indicators_cli.run({"symbol": "603929", "live_price": None}) == {"error": "kline fetch failed"}


def test_main_prints_json(monkeypatch, capsys):
    monkeypatch.setattr(indicators_cli, "run", lambda payload: {"close": 10.0, "rsi": 50.0})

    exit_code = indicators_cli.main('{"symbol":"603929","live_price":10}')

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == {"close": 10.0, "rsi": 50.0}
```

- [ ] Run and verify failure:

```bash
uv run pytest src/tools/test_indicators_cli.py -q
```

Expected: import failure because `src/tools/indicators_cli.py` does not exist.

- [ ] Create `src/tools/indicators_cli.py`:

```python
"""CLI bridge for the Next.js indicators API."""

from __future__ import annotations

import json
import re
import sys
from typing import Any

from src.tools.indicator_alert_engine import compute_indicators, fetch_kline_akshare

A_SHARE_SYMBOL_RE = re.compile(r"^\d{6}$")
HK_SYMBOL_RE = re.compile(r"^HK\d{5}$")
MAX_LIVE_PRICE = 1_000_000


def parse_payload(raw: str) -> dict[str, float | str | None]:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid json") from exc

    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")

    symbol_raw = payload.get("symbol")
    if not isinstance(symbol_raw, str) or not symbol_raw.strip():
        raise ValueError("symbol required")

    symbol = symbol_raw.strip().upper()
    if symbol.startswith("KR"):
        raise ValueError("KR not supported")
    if not (A_SHARE_SYMBOL_RE.match(symbol) or HK_SYMBOL_RE.match(symbol)):
        raise ValueError("invalid symbol")

    live_price_raw = payload.get("live_price")
    if live_price_raw is None:
        live_price = None
    elif isinstance(live_price_raw, (int, float)) and not isinstance(live_price_raw, bool):
        live_price = float(live_price_raw)
        if live_price <= 0 or live_price > MAX_LIVE_PRICE:
            raise ValueError("invalid live_price")
    else:
        raise ValueError("invalid live_price")

    return {"symbol": symbol, "live_price": live_price}


def run(payload: dict[str, Any]) -> dict[str, Any]:
    symbol = str(payload["symbol"])
    live_price = payload.get("live_price")
    kline = fetch_kline_akshare(symbol, days=60)
    if kline is None:
        return {"error": "kline fetch failed"}

    indicators = compute_indicators(
        kline,
        live_price=live_price if isinstance(live_price, (int, float)) else None,
    )
    if indicators is None:
        return {"error": "insufficient data"}

    return indicators


def main(raw: str | None = None) -> int:
    try:
        payload = parse_payload(sys.stdin.read() if raw is None else raw)
        print(json.dumps(run(payload), ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Run:

```bash
uv run pytest src/tools/test_indicators_cli.py -q
printf '{"symbol":"603929","live_price":128.5}' | uv run python -m src.tools.indicators_cli
```

Expected: tests pass; CLI prints one JSON object and no traceback.

## Task 1.3: Rewrite Route to Use Spawn

- [ ] Create `web/app/api/indicators/route.test.ts` with mocked `child_process.spawn` and tests for:
  - missing symbol returns 400,
  - malformed symbol returns 400 and does not spawn,
  - valid request calls `uv run python -m src.tools.indicators_cli`,
  - payload is written via stdin JSON,
  - CLI JSON error returns 500,
  - non-zero child exit returns 502,
  - timeout kills child and returns 504.

- [ ] Run and verify failure:

```bash
cd web && npm run test:unit -- app/api/indicators/route.test.ts
```

- [ ] Replace `web/app/api/indicators/route.ts` so it:
  - imports `spawn` from `child_process`,
  - imports `parseStockCode` from `../../lib/stock-code`,
  - removes `execSync`,
  - removes `python -c`,
  - calls `spawn("uv", ["run", "python", "-m", "src.tools.indicators_cli"], { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"] })`,
  - sends `child.stdin.write(JSON.stringify(payload))`,
  - enforces 20s timeout,
  - returns 400/500/502/504 as described above.

- [ ] Verify:

```bash
cd web && npm run test:unit -- app/lib/stock-code.test.ts app/api/indicators/route.test.ts
uv run pytest src/tools/test_indicators_cli.py -q
rg "execSync|python -c|poetry run python" web/app/api/indicators/route.ts
cd web && npm run build
```

Expected: tests/build pass; `rg` has no matches.

---

# PR 2: Protect Root Runtime Backups

## Goal

Reject repository-root `backups/` paths in the existing sensitive path scanner.

## Files

- Modify: `scripts/check_sensitive_paths.py`
- Modify: `scripts/test_check_sensitive_paths.py`

## Tasks

- [ ] Add root backups assertions to `test_check_paths_rejects_audit_archive_and_backup_paths()`:

```python
with pytest.raises(SensitivePathError):
    check_paths(["backups/data_20260430_102555/config.db"])
with pytest.raises(SensitivePathError):
    check_paths(["BACKUPS/data_20260430_102555/config.db"])
```

- [ ] Add stdin0 test:

```python
def test_main_rejects_root_backups_path_from_stdin(monkeypatch):
    class FakeStdin:
        buffer = type(
            "Buffer",
            (),
            {"read": staticmethod(lambda: b"backups/data_20260430_102555/config.db\0")},
        )()

    monkeypatch.setattr("sys.stdin", FakeStdin())

    assert main(["--stdin0"]) == 1
```

- [ ] Run and verify failure:

```bash
uv run pytest scripts/test_check_sensitive_paths.py -q
```

- [ ] Update `SENSITIVE_ROOTS`:

```python
SENSITIVE_ROOTS = (
    PurePosixPath("backups"),
    PurePosixPath("src/data/audit"),
    PurePosixPath("src/data/archive"),
    PurePosixPath("src/data/backups"),
)
```

- [ ] Verify:

```bash
uv run pytest scripts/test_check_sensitive_paths.py -q
git ls-files -z | uv run python scripts/check_sensitive_paths.py --stdin0
```

Expected: tests pass; tracked files pass if no sensitive runtime paths are already tracked.

---

# PR 3: Add Basic CI Coverage

## Goal

Run Python tests, Web unit tests, and Next build in CI; make root `uv run pytest` collect more than `src/sim_trading`.

## Files

- Modify: `pyproject.toml`
- Create: `.github/workflows/ci.yml`

## Tasks

- [ ] Run current collection:

```bash
uv run pytest --collect-only -q
```

Expected before change: default collection is limited by `testpaths = ["src/sim_trading"]`.

- [ ] Update `pyproject.toml`:

```toml
[tool.pytest.ini_options]
testpaths = [
    "src/sim_trading",
    "src/tools",
    "src/utils",
    "src/analysis",
    "scripts",
]
markers = [
    "smoke: critical risk-control tests (run via -m smoke)",
]
```

If `scripts` causes collection issues, use:

```toml
testpaths = [
    "src/sim_trading",
    "src/tools",
    "src/utils",
    "src/analysis",
]
```

and make CI explicitly run `scripts/test_*.py`.

- [ ] Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  pull_request:
  push:

jobs:
  python-tests:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Install Python dependencies
        run: uv sync --extra dev
      - name: Run Python tests
        run: uv run pytest

  web-unit-tests:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: web/package-lock.json
      - name: Install Web dependencies
        run: npm ci
      - name: Run Web unit tests
        run: npm run test:unit

  web-build:
    runs-on: ubuntu-latest
    defaults:
      run:
        working-directory: web
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 22
          cache: npm
          cache-dependency-path: web/package-lock.json
      - name: Install Web dependencies
        run: npm ci
      - name: Build Web
        run: npm run build
```

- [ ] Verify:

```bash
ls web/package-lock.json
uv run pytest
cd web && npm run test:unit
cd web && npm run build
```

Expected: all pass, or historical failures are recorded and fixed before enabling strict CI.

---

# PR 4: Add Architecture Contract Checks

## Goal

Prevent Web API production code from writing `price_snapshots` or `alert_events`.

## Files

- Create: `scripts/check_architecture_contracts.py`
- Create: `scripts/test_check_architecture_contracts.py`
- Modify: `.github/workflows/ci.yml` if PR 3 exists, otherwise `.github/workflows/audit-coverage.yml`

## Tasks

- [ ] Create `scripts/test_check_architecture_contracts.py` with tests that:
  - flag `INSERT INTO price_snapshots`,
  - flag `DELETE FROM alert_events`,
  - allow `SELECT FROM price_snapshots`,
  - allow mutations in `*.test.ts`,
  - allow mutations of other tables.

- [ ] Create `scripts/check_architecture_contracts.py`:

```python
#!/usr/bin/env python3
"""Static checks for high-value architecture contracts."""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_WEB_MUTATION_TABLES = {"price_snapshots", "alert_events"}
MUTATION_RE = re.compile(
    r"\b(?:INSERT\s+(?:OR\s+\w+\s+)?INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
IGNORED_FILE_PARTS = (".test.", ".spec.", "/__tests__/", "/fixtures/", "/test-fixtures/")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int
    table: str
    statement: str


def _is_ignored_path(path: Path) -> bool:
    normalized = "/" + path.as_posix()
    return any(part in normalized for part in IGNORED_FILE_PARTS)


def scan_file(path: Path) -> list[Finding]:
    if _is_ignored_path(path):
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    findings: list[Finding] = []
    seen: set[tuple[int, str]] = set()
    for idx, line in enumerate(lines):
        if line.strip().startswith(("//", "#")):
            continue
        chunk = "\n".join(lines[idx : idx + 4])
        for match in MUTATION_RE.finditer(chunk):
            table = match.group(1)
            if table.casefold() not in FORBIDDEN_WEB_MUTATION_TABLES:
                continue
            actual_line = idx + chunk[: match.start()].count("\n") + 1
            marker = (actual_line, table.casefold())
            if marker in seen:
                continue
            seen.add(marker)
            findings.append(Finding(path, actual_line, table, lines[actual_line - 1].strip()))
    return findings


def scan_paths(paths: list[Path]) -> list[Finding]:
    findings: list[Finding] = []
    for path in paths:
        candidates = (
            sorted(candidate for pattern in ("*.ts", "*.tsx") for candidate in path.rglob(pattern))
            if path.is_dir()
            else [path]
        )
        for candidate in candidates:
            findings.extend(scan_file(candidate))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="*", default=["web/app/api"])
    args = parser.parse_args(argv)
    findings = scan_paths([ROOT / path for path in args.paths])
    if findings:
        for finding in findings:
            rel = finding.path.relative_to(ROOT) if finding.path.is_relative_to(ROOT) else finding.path
            print(f"{rel}:{finding.line}: web api must not mutate {finding.table}: {finding.statement}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] Wire into CI:

If `.github/workflows/ci.yml` exists:

```yaml
  architecture-contracts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Check architecture contracts
        run: uv run python scripts/check_architecture_contracts.py
```

If not, add the same command as a step in `.github/workflows/audit-coverage.yml`.

- [ ] Verify:

```bash
uv run pytest scripts/test_check_architecture_contracts.py -q
uv run python scripts/check_architecture_contracts.py
```

Expected: both pass.

---

# PR 5: Document Web Mutations

## Goal

Make Web write boundaries explicit and fix stale docs.

## Files

- Create: `docs/WEB_MUTATIONS.md`
- Modify: `web/AGENTS.md`
- Modify: `docs/ARCHITECTURE.md`
- Modify: `docs/CONFIG_API.md`

## Tasks

- [ ] Create `docs/WEB_MUTATIONS.md` with:
  - rules,
  - mutation routes,
  - known `GET /api/sector` side effect,
  - read-only examples,
  - checklist for adding new mutation routes.

Required mutation rows:

```markdown
| Method | Route | DB | Tables | Audit | Owner / Notes |
|---|---|---|---|---|---|
| `POST` | `/api/config` | `config.db` | `monitor_watchlist`, `monitor_settings`, `alert_rules`, `position_change_log`, `config_audit_outbox` | Yes | Watchlist, real holdings, settings, L3 alert thresholds, pin/order/tag operations. |
| `POST` | `/api/sector` | `config.db` | `tag_meta`, `monitor_watchlist` | Yes | Sector/tag metadata management and tag assignment cleanup. |
| `POST` | `/api/sector` | `trading.db` | `portfolio_config`, `sector_daily`, `sector_alerts`, `trading_audit_outbox` | Partial / table-dependent | Sector dashboard config and cleanup of derived sector rows during tag deletion. |
| `POST` | `/api/trade-plans` | `trading.db` | `trade_plans`, `trading_audit_outbox` | Yes | User-maintained conditional trade plans. |
| `POST` | `/api/earnings` | `trading.db` | `portfolio_config`, `trading_audit_outbox` | Yes | Earnings check trigger. |
```

- [ ] Replace `web/AGENTS.md` with an updated DB-first summary:
  - no `market_data.json` runtime source,
  - no “only config writes” claim,
  - link to `../docs/WEB_MUTATIONS.md`.

- [ ] Update `docs/ARCHITECTURE.md` with a `Web API Mutation Matrix` section linking to `WEB_MUTATIONS.md`.

- [ ] Update `docs/CONFIG_API.md` top section:

```markdown
所有自选/真实持仓/监控设置配置变更必须通过 `POST /api/config`，禁止直接修改 JSON 或 SQLite。

其它领域写入口（如板块标签、交易计划、earnings trigger）见 [WEB_MUTATIONS.md](WEB_MUTATIONS.md)。这些入口同样必须写 SQLite + audit outbox；不得恢复 runtime JSON 写源。
```

- [ ] Verify:

```bash
rg "market_data.json|only write endpoint|exports JSON" web/AGENTS.md docs/ARCHITECTURE.md docs/CONFIG_API.md
rg "WEB_MUTATIONS.md" docs web/AGENTS.md
```

Expected: stale claims gone; links present.

---

# PR 6: Make `GET /api/sector` Read-Only

## Goal

Remove hidden `GET /api/sector` writes by moving live sector refresh to explicit POST.

## Files

- Modify: `web/app/api/sector/route.ts`
- Create or modify: `web/app/api/sector/route.test.ts`
- Modify: `docs/SECTOR.md`
- Modify: `docs/WEB_MUTATIONS.md` if PR 5 exists

## Tasks

- [ ] Add test proving GET opens DBs readonly and does not call writable `openTradingDb()`:

```typescript
expect(openConfigDbMock).toHaveBeenCalledWith(true);
expect(openTradingDbMock).toHaveBeenCalledWith(true);
expect(openTradingDbMock).not.toHaveBeenCalledWith();
expect(openTradingDbMock).not.toHaveBeenCalledWith(false);
```

- [ ] Change `refreshLiveRotation(category)` to return:

```typescript
Promise<{ refreshed: boolean; reason?: string; rows?: number }>
```

Return status objects for outside trading hours, throttled, unknown category, failed fetch, parse failure, empty boards, success, and caught failures.

- [ ] Remove from `GET`:

```typescript
await refreshLiveRotation(category);
```

- [ ] Add POST action:

```typescript
case "refresh-live-rotation": {
  const category = typeof body.category === "string" ? body.category : "industry";
  const result = await refreshLiveRotation(category);
  return NextResponse.json({ ok: true, ...result });
}
```

- [ ] Update `docs/SECTOR.md` API section:

```markdown
- `GET /api/sector` → indices + alerts + rotation（只读 SQLite）
- `GET /api/sector?category=industry&sort=change_pct&top_n=10` → 轮动矩阵（只读 SQLite）
- `POST /api/sector` → create/update/delete/watch/star/config
- `POST /api/sector` with `{"action":"refresh-live-rotation","category":"industry"}` → 显式刷新实时新浪板块排名（过渡方案，长期建议迁到 Python 后台任务）
```

- [ ] Verify:

```bash
cd web && npm run test:unit -- app/api/sector/route.test.ts
rg "await refreshLiveRotation" web/app/api/sector/route.ts
cd web && npm run build
```

Expected: tests/build pass; `await refreshLiveRotation` appears only in POST path or not at all.

---

# PR 7: Centralize DDL Migration Authority

## Goal

Stop Web API request handlers from adding business-table columns at request time.

## Files

- Modify: `web/app/api/trade-plans/route.ts`
- Modify: `web/app/api/sim/route.ts`
- Possibly modify: `web/app/api/config/route.ts`
- Modify: `scripts/check_architecture_contracts.py`
- Modify: `scripts/test_check_architecture_contracts.py`
- Modify: `docs/ARCHITECTURE.md`

## Tasks

- [ ] Extend architecture scanner tests with:
  - flag `ALTER TABLE trade_plans ...` in production route,
  - allow `ALTER TABLE` in `*.test.ts`.

- [ ] Add `ALTER_RE` to `scripts/check_architecture_contracts.py`:

```python
ALTER_RE = re.compile(
    r"\bALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
```

In `scan_file()`, append findings for `ALTER TABLE`.

- [ ] Remove `ensureScopeColumn()` from `web/app/api/trade-plans/route.ts` and remove calls to it.

- [ ] Remove lazy `ALTER TABLE trade_plans ADD COLUMN scope TEXT NOT NULL DEFAULT 'real'` from `web/app/api/sim/route.ts`.

- [ ] Compare config route ALTERs with Python schema:

```bash
rg "ALTER TABLE" web/app/api/config/route.ts
rg "dip_buy|alias|tags|watch_price|watch_price_date|pin_order|type_from|type_to|flushed_at|flush_id|flush_started_at_ms" src/sim_trading/db.py
```

If safe, remove config route ALTERs too. If not safe for local legacy DBs, leave them temporarily with:

```typescript
// ARCH-COMPAT: legacy config.db field backfill. Do not add new route-level ALTER TABLE.
```

and make scanner allow only `ARCH-COMPAT` lines.

- [ ] Add to `docs/ARCHITECTURE.md`:

```markdown
## Schema Authority

`src/sim_trading/db.py` is the current long-term schema authority for `config.db` and `trading.db`.

- New runtime tables and columns must be added there first.
- Web API request handlers must not introduce new `ALTER TABLE` migrations.
- Temporary compatibility backfills must be marked with `ARCH-COMPAT` and removed after the migration window.
- A future migration runner may replace this convention, but there must still be one schema authority.
```

- [ ] Verify:

```bash
uv run pytest scripts/test_check_architecture_contracts.py -q
uv run python scripts/check_architecture_contracts.py
cd web && npm run test:unit -- app/api/trade-plans/route.test.ts app/api/sim/route.test.ts
cd web && npm run build
```

Expected: all pass; no unexplained route-level `ALTER TABLE` remains.

---

# Global Verification After PR 1-4

Run:

```bash
uv run pytest
uv run python scripts/check_audit_coverage.py
git ls-files -z | uv run python scripts/check_sensitive_paths.py --stdin0
uv run python scripts/check_architecture_contracts.py
cd web && npm run test:unit
cd web && npm run build
```

Skip `check_architecture_contracts.py` until PR 4 has landed.

# Stop Conditions

Pause and ask before proceeding if:

- `uv run pytest` reveals many unrelated historical failures.
- `cd web && npm run build` fails because of missing environment/runtime data.
- Plan 6 affects visible sector dashboard behavior and no explicit refresh UX is agreed.
- Plan 7 finds real local DB compatibility concerns requiring legacy `ALTER TABLE` support.

# Completion Definition

This hardening phase is complete when:

- `/api/indicators` no longer uses `execSync` or `python -c`.
- Root `backups/` paths are blocked.
- CI runs Python tests, Web unit tests, and Web build.
- Web production API cannot write `price_snapshots` or `alert_events` without failing a guard.
- Web mutation routes are documented.
- `GET /api/sector` is read-only.
- New Web route-level `ALTER TABLE` statements are blocked or explicitly marked as temporary compatibility backfills.
