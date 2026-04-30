# 架构稳定性加固一体化实施计划

> **给执行 agent：** 实施本计划时必须使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans`，按任务逐步执行。步骤使用 checkbox（`- [ ]`）跟踪。

**目标：** 用小而可审查的 PR 完成整套架构加固：修复 `/api/indicators`，阻止根目录运行态备份误提交，补基础 CI，增加架构护栏，记录 Web 写入口，让 sector GET 只读，并启动 DDL 收敛。

**架构：** 保持现有 Python daemon + Next.js dashboard + SQLite 双库架构，不重写系统。只在风险最高的边界上加窄护栏：子进程执行、运行态数据提交、测试/构建反馈、Web 写权限、route mutation 文档、GET 纯读语义和 schema 权威来源。

**技术栈：** Python 3、uv、pytest、Next.js 15、TypeScript、Vitest、GitHub Actions、SQLite。

---

## 设计依据

背景、问题确认和风险判断见：

- `docs/superpowers/specs/2026-05-01-architecture-hardening-plan.md`

实施时只需要看这份一体化计划，不需要在多份拆分计划之间来回跳转。

## 执行顺序

除非用户明确要求合并，否则按独立 PR 实施：

1. PR 1：修复 `/api/indicators` 子进程边界。
2. PR 2：保护根目录 `backups/`。
3. PR 3：增加基础 CI，并扩大 pytest 收集范围。
4. PR 4：增加架构契约检查。
5. PR 5：记录 Web mutation matrix。
6. PR 6：让 `GET /api/sector` 只读。
7. PR 7：收敛 DDL 迁移权威来源。

## 全局规则

- 不直接修改运行态 DB 或 JSON 文件。
- 不提交 `backups/` 下的文件。
- 除非明确要求，不把行为变更和无关 CI/文档变更混在一个 PR。
- 每个 PR 完成后先跑对应的聚焦测试，再进入下一个 PR。
- 只有用户明确要求时才提交 commit。

---

# PR 1：修复 `/api/indicators` 子进程边界

## 目标

把不安全的 `execSync + python -c + 用户输入拼接` 替换为：

- 严格股票代码校验；
- 异步 `spawn`；
- 通过 stdin 传 JSON；
- 独立 Python CLI module。

## 文件

- 新建：`web/app/lib/stock-code.ts`
- 新建：`web/app/lib/stock-code.test.ts`
- 新建：`src/tools/indicators_cli.py`
- 新建：`src/tools/test_indicators_cli.py`
- 修改：`web/app/api/indicators/route.ts`
- 新建：`web/app/api/indicators/route.test.ts`

## 任务 1.1：增加股票代码校验

- 新建 `web/app/lib/stock-code.test.ts`：

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

- 运行测试并确认先失败：

```bash
cd web && npm run test:unit -- app/lib/stock-code.test.ts
```

预期：因为 `stock-code.ts` 尚不存在，测试因 import 失败。

- 新建 `web/app/lib/stock-code.ts`：

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

- Run:

```bash
cd web && npm run test:unit -- app/lib/stock-code.test.ts
```

预期：PASS。

## 任务 1.2：增加 Python 指标 CLI

- 新建 `src/tools/test_indicators_cli.py`：

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

- 运行测试并确认先失败：

```bash
uv run pytest src/tools/test_indicators_cli.py -q
```

预期：因为 `src/tools/indicators_cli.py` 尚不存在，测试因 import 失败。

- 新建 `src/tools/indicators_cli.py`：

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

- 运行：

```bash
uv run pytest src/tools/test_indicators_cli.py -q
printf '{"symbol":"603929","live_price":128.5}' | uv run python -m src.tools.indicators_cli
```

预期：测试通过；CLI 在 stdout 打印一个 JSON object，不打印 traceback。

## 任务 1.3：把 route 改为使用 spawn

- 新建 `web/app/api/indicators/route.test.ts`，mock `child_process.spawn`，覆盖：
  - 缺少 symbol 返回 400；
  - 非法 symbol 返回 400 且不启动子进程；
  - 合法请求调用 `uv run python -m src.tools.indicators_cli`；
  - payload 通过 stdin JSON 写入；
  - CLI 返回 JSON error 时返回 500；
  - 子进程非 0 退出时返回 502；
  - 超时时 kill 子进程并返回 504。
- 运行测试并确认先失败：

```bash
cd web && npm run test:unit -- app/api/indicators/route.test.ts
```

- 替换 `web/app/api/indicators/route.ts`，要求：
  - 从 `child_process` import `spawn`；
  - 从 `../../lib/stock-code` import `parseStockCode`；
  - 移除 `execSync`；
  - 移除 `python -c`；
  - 调用 `spawn("uv", ["run", "python", "-m", "src.tools.indicators_cli"], { cwd: ROOT, stdio: ["pipe", "pipe", "pipe"] })`；
  - 使用 `child.stdin.write(JSON.stringify(payload))` 传参；
  - 保留 20 秒超时；
  - 按上述要求返回 400/500/502/504。
- 验证：

```bash
cd web && npm run test:unit -- app/lib/stock-code.test.ts app/api/indicators/route.test.ts
uv run pytest src/tools/test_indicators_cli.py -q
rg "execSync|python -c|poetry run python" web/app/api/indicators/route.ts
cd web && npm run build
```

预期：测试和 build 通过；`rg` 无匹配。

---

# PR 2：保护根目录运行态备份

## 目标

让现有 sensitive path scanner 拒绝仓库根目录 `backups/` 路径。

## 文件

- 修改：`scripts/check_sensitive_paths.py`
- 修改：`scripts/test_check_sensitive_paths.py`

## 任务

- 在 `test_check_paths_rejects_audit_archive_and_backup_paths()` 中增加根目录 backups 断言：

```python
with pytest.raises(SensitivePathError):
    check_paths(["backups/data_20260430_102555/config.db"])
with pytest.raises(SensitivePathError):
    check_paths(["BACKUPS/data_20260430_102555/config.db"])
```

- 增加 stdin0 测试：

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

- 运行测试并确认先失败：

```bash
uv run pytest scripts/test_check_sensitive_paths.py -q
```

- 更新 `SENSITIVE_ROOTS`：

```python
SENSITIVE_ROOTS = (
    PurePosixPath("backups"),
    PurePosixPath("src/data/audit"),
    PurePosixPath("src/data/archive"),
    PurePosixPath("src/data/backups"),
)
```

- 验证：

```bash
uv run pytest scripts/test_check_sensitive_paths.py -q
git ls-files -z | uv run python scripts/check_sensitive_paths.py --stdin0
```

预期：测试通过；如果当前没有已跟踪的敏感运行态路径，`git ls-files` 检查通过。

---

# PR 3：增加基础 CI 覆盖

## 目标

在 CI 中运行 Python 测试、Web 单元测试和 Next build；让仓库根目录的 `uv run pytest` 不再只收集 `src/sim_trading`。

## 文件

- 修改：`pyproject.toml`
- 新建：`.github/workflows/ci.yml`

## 任务

- 查看当前测试收集范围：

```bash
uv run pytest --collect-only -q
```

变更前预期：默认收集范围受 `testpaths = ["src/sim_trading"]` 限制。

- 更新 `pyproject.toml`：

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

如果 `scripts` 导致收集问题，则改用：

```toml
testpaths = [
    "src/sim_trading",
    "src/tools",
    "src/utils",
    "src/analysis",
]
```

并让 CI 显式运行 `scripts/test_*.py`。

- 新建 `.github/workflows/ci.yml`：

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

- 验证：

```bash
ls web/package-lock.json
uv run pytest
cd web && npm run test:unit
cd web && npm run build
```

预期：全部通过；如果暴露历史失败，先记录并修复，再启用严格 CI。

---

# PR 4：增加架构契约检查

## 目标

防止 Web API 生产代码写入 `price_snapshots` 或 `alert_events`。

## 文件

- 新建：`scripts/check_architecture_contracts.py`
- 新建：`scripts/test_check_architecture_contracts.py`
- 修改：如果 PR 3 已存在则改 `.github/workflows/ci.yml`，否则改 `.github/workflows/audit-coverage.yml`

## 任务

- 新建 `scripts/test_check_architecture_contracts.py`，覆盖：
  - 标记 `INSERT INTO price_snapshots`；
  - 标记 `DELETE FROM alert_events`；
  - 允许 `SELECT FROM price_snapshots`；
  - 允许 `*.test.ts` 中的测试数据写入；
  - 允许其它表的合法写入。
- 新建 `scripts/check_architecture_contracts.py`：

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

- 接入 CI：

如果 `.github/workflows/ci.yml` 已存在：

```yaml
  architecture-contracts:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v5
      - name: Check architecture contracts
        run: uv run python scripts/check_architecture_contracts.py
```

如果不存在，则把同样的命令作为 step 加到 `.github/workflows/audit-coverage.yml`。

- 验证：

```bash
uv run pytest scripts/test_check_architecture_contracts.py -q
uv run python scripts/check_architecture_contracts.py
```

预期：两个命令都通过。

---

# PR 5：记录 Web 写入口

## 目标

明确 Web 写边界，并修正文档中的过时描述。

## 文件

- 新建：`docs/WEB_MUTATIONS.md`
- 修改：`web/AGENTS.md`
- 修改：`docs/ARCHITECTURE.md`
- 修改：`docs/CONFIG_API.md`

## 任务

- 新建 `docs/WEB_MUTATIONS.md`，包含：
  - 规则；
  - mutation routes；
  - 已知 `GET /api/sector` 副作用；
  - 只读 route 示例；
  - 新增 mutation route 的检查清单。

必须包含以下 mutation rows：

```markdown
| Method | Route | DB | Tables | Audit | Owner / Notes |
|---|---|---|---|---|---|
| `POST` | `/api/config` | `config.db` | `monitor_watchlist`, `monitor_settings`, `alert_rules`, `position_change_log`, `config_audit_outbox` | Yes | Watchlist, real holdings, settings, L3 alert thresholds, pin/order/tag operations. |
| `POST` | `/api/sector` | `config.db` | `tag_meta`, `monitor_watchlist` | Yes | Sector/tag metadata management and tag assignment cleanup. |
| `POST` | `/api/sector` | `trading.db` | `portfolio_config`, `sector_daily`, `sector_alerts`, `trading_audit_outbox` | Partial / table-dependent | Sector dashboard config and cleanup of derived sector rows during tag deletion. |
| `POST` | `/api/trade-plans` | `trading.db` | `trade_plans`, `trading_audit_outbox` | Yes | User-maintained conditional trade plans. |
| `POST` | `/api/earnings` | `trading.db` | `portfolio_config`, `trading_audit_outbox` | Yes | Earnings check trigger. |
```

- 用 DB-first 摘要替换 `web/AGENTS.md`：
  - 不再把 `market_data.json` 写成运行态数据源；
  - 不再声称“只有 config 会写”；
  - 链接到 `../docs/WEB_MUTATIONS.md`。
- 在 `docs/ARCHITECTURE.md` 增加 `Web API Mutation Matrix` 小节并链接到 `WEB_MUTATIONS.md`。
- 更新 `docs/CONFIG_API.md` 开头：

```markdown
所有自选/真实持仓/监控设置配置变更必须通过 `POST /api/config`，禁止直接修改 JSON 或 SQLite。

其它领域写入口（如板块标签、交易计划、earnings trigger）见 [WEB_MUTATIONS.md](WEB_MUTATIONS.md)。这些入口同样必须写 SQLite + audit outbox；不得恢复 runtime JSON 写源。
```

- 验证：

```bash
rg "market_data.json|only write endpoint|exports JSON" web/AGENTS.md docs/ARCHITECTURE.md docs/CONFIG_API.md
rg "WEB_MUTATIONS.md" docs web/AGENTS.md
```

预期：过时描述消失；链接存在。

---

# PR 6：让 `GET /api/sector` 只读

## 目标

移除 `GET /api/sector` 的隐藏写库副作用，把实时板块刷新移动到显式 POST。

## 文件

- 修改：`web/app/api/sector/route.ts`
- 新建或修改：`web/app/api/sector/route.test.ts`
- 修改：`docs/SECTOR.md`
- 如果 PR 5 已存在，修改：`docs/WEB_MUTATIONS.md`

## 任务

- 增加测试，证明 GET 只用 readonly DB，不调用可写 `openTradingDb()`：

```typescript
expect(openConfigDbMock).toHaveBeenCalledWith(true);
expect(openTradingDbMock).toHaveBeenCalledWith(true);
expect(openTradingDbMock).not.toHaveBeenCalledWith();
expect(openTradingDbMock).not.toHaveBeenCalledWith(false);
```

- 修改 `refreshLiveRotation(category)`，让它返回：

```typescript
Promise<{ refreshed: boolean; reason?: string; rows?: number }>
```

针对非交易时间、throttled、未知 category、fetch 失败、解析失败、空 boards、成功和捕获异常，都返回 status object。

- 从 `GET` 中移除：

```typescript
await refreshLiveRotation(category);
```

- 增加 POST action：

```typescript
case "refresh-live-rotation": {
  const category = typeof body.category === "string" ? body.category : "industry";
  const result = await refreshLiveRotation(category);
  return NextResponse.json({ ok: true, ...result });
}
```

- 更新 `docs/SECTOR.md` API 小节：

```markdown
- `GET /api/sector` → indices + alerts + rotation（只读 SQLite）
- `GET /api/sector?category=industry&sort=change_pct&top_n=10` → 轮动矩阵（只读 SQLite）
- `POST /api/sector` → create/update/delete/watch/star/config
- `POST /api/sector` with `{"action":"refresh-live-rotation","category":"industry"}` → 显式刷新实时新浪板块排名（过渡方案，长期建议迁到 Python 后台任务）
```

- 验证：

```bash
cd web && npm run test:unit -- app/api/sector/route.test.ts
rg "await refreshLiveRotation" web/app/api/sector/route.ts
cd web && npm run build
```

预期：测试和 build 通过；`await refreshLiveRotation` 只出现在 POST 路径中，或完全不出现。

---

# PR 7：收敛 DDL 迁移权威来源

## 目标

停止让 Web API 请求 handler 在请求时给业务表补列。

## 文件

- 修改：`web/app/api/trade-plans/route.ts`
- 修改：`web/app/api/sim/route.ts`
- 可能修改：`web/app/api/config/route.ts`
- 修改：`scripts/check_architecture_contracts.py`
- 修改：`scripts/test_check_architecture_contracts.py`
- 修改：`docs/ARCHITECTURE.md`

## 任务

- 扩展 architecture scanner 测试：
  - 标记生产 route 中的 `ALTER TABLE trade_plans ...`；
  - 允许 `*.test.ts` 中的 `ALTER TABLE`。
- 在 `scripts/check_architecture_contracts.py` 中增加 `ALTER_RE`：

```python
ALTER_RE = re.compile(
    r"\bALTER\s+TABLE\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)
```

在 `scan_file()` 中对 `ALTER TABLE` 追加 finding。

- 从 `web/app/api/trade-plans/route.ts` 移除 `ensureScopeColumn()` 及其调用。
- 从 `web/app/api/sim/route.ts` 移除惰性 `ALTER TABLE trade_plans ADD COLUMN scope TEXT NOT NULL DEFAULT 'real'`。
- 对比 config route 中的 ALTER 和 Python schema：

```bash
rg "ALTER TABLE" web/app/api/config/route.ts
rg "dip_buy|alias|tags|watch_price|watch_price_date|pin_order|type_from|type_to|flushed_at|flush_id|flush_started_at_ms" src/sim_trading/db.py
```

如果安全，也移除 config route 中的 ALTER。如果考虑本地 legacy DB 兼容性暂时不能移除，则保留并增加：

```typescript
// ARCH-COMPAT: legacy config.db field backfill. Do not add new route-level ALTER TABLE.
```

并让 scanner 只允许带 `ARCH-COMPAT` 标记的行。

- 添加到 `docs/ARCHITECTURE.md`：

```markdown
## Schema Authority

`src/sim_trading/db.py` is the current long-term schema authority for `config.db` and `trading.db`.

- New runtime tables and columns must be added there first.
- Web API request handlers must not introduce new `ALTER TABLE` migrations.
- Temporary compatibility backfills must be marked with `ARCH-COMPAT` and removed after the migration window.
- A future migration runner may replace this convention, but there must still be one schema authority.
```

- 验证：

```bash
uv run pytest scripts/test_check_architecture_contracts.py -q
uv run python scripts/check_architecture_contracts.py
cd web && npm run test:unit -- app/api/trade-plans/route.test.ts app/api/sim/route.test.ts
cd web && npm run build
```

预期：全部通过；不再有未解释的 route-level `ALTER TABLE`。

---

# PR 1-4 后的全局验证

运行：

```bash
uv run pytest
uv run python scripts/check_audit_coverage.py
git ls-files -z | uv run python scripts/check_sensitive_paths.py --stdin0
uv run python scripts/check_architecture_contracts.py
cd web && npm run test:unit
cd web && npm run build
```

在 PR 4 落地前，跳过 `check_architecture_contracts.py`。

# 暂停条件

遇到以下情况先暂停并询问：

- `uv run pytest` 暴露大量无关历史失败。
- `cd web && npm run build` 因缺少环境或运行态数据失败。
- PR 6 影响 sector dashboard 可见行为，且还没有明确的显式刷新 UX。
- PR 7 发现真实本地 DB 兼容性问题，需要保留 legacy `ALTER TABLE`。

# 完成定义

满足以下条件时，本轮架构加固完成：

- `/api/indicators` 不再使用 `execSync` 或 `python -c`。
- 根目录 `backups/` 路径被拦截。
- CI 会运行 Python tests、Web unit tests 和 Web build。
- Web 生产 API 写 `price_snapshots` 或 `alert_events` 会被 guard 拦截。
- Web mutation routes 已记录成文档。
- `GET /api/sector` 是只读的。
- 新增 Web route-level `ALTER TABLE` 会被拦截，或明确标记为临时兼容 backfill。

