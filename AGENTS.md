# AGENTS.md — Guidelines for AI Agents

## Project Overview

- **Python backend**: `src/` — trading agents, sim engine, market data poller, notifier
- **TypeScript frontend**: `web/` — Next.js 15 + React 19 web dashboard
- **Database**: SQLite (`src/data/sim_trading.db`) with WAL mode
- **Data files** (`src/data/`): JSON configs, market data JSON — atomic writes (tmp → rename)

## Build / Lint / Test Commands

### Python (Poetry)
```bash
poetry install                                      # install dependencies
poetry run black --check src/                      # lint check (no changes)
poetry run black src/                              # auto-format
poetry run isort --check src/                      # import check
poetry run isort src/                              # fix imports

poetry run pytest src/sim_trading/test_sim_trading.py -v           # run all tests
poetry run pytest src/sim_trading/test_sim_trading.py -v -k "T3"  # single test by name
poetry run pytest src/sim_trading/test_sim_trading.py -v -x       # fail-fast
poetry run pytest src/sim_trading/test_position_change_log.py -v   # position change tests
poetry run pytest src/tools/test_market_data_poller.py -v          # poller unit tests

poetry run python src/main.py --ticker 000000 --show-reasoning     # run agent analysis
poetry run python src/tools/l2_strategy_daemon.py                  # run L2 daemon
poetry run python -m src.sim_trading.replay_runner                # sim replay
poetry run python -m src.sim_trading.scoring_backtester            # v3 Optuna backtest
poetry run python src/sim_trading/kline_fetcher.py --codes HK00700,HK09988  # fetch Kline
```

### Web / TypeScript (Next.js)
```bash
cd web && npm run dev              # dev server on :3120
cd web && npx tsc --noEmit        # TypeScript type check (no build required)
cd web && npm run test:unit        # vitest unit tests
cd web && npx vitest run           # vitest (alternate)
cd web && npm run test             # Playwright E2E tests
cd web && npm run test:ui          # Playwright with UI
cd web && npm run test:headed      # Playwright headed
cd web && npm run test:all         # vitest + playwright
```

### Critical Notes
- **Never run `next build` while dev server is running** — build overwrites `.next/` causing `Cannot find module` errors.
- **Webpack dev cache**: If stale chunk errors, `rm -rf web/.next` and restart dev.
- **Vitest** config: `web/vitest.config.ts`; **Playwright** config: `web/playwright.config.ts`.
- **Playwright E2E**: Every new feature or UI change must include an E2E test in `web/screenshots/test_<feature>.mjs`. Tests without E2E coverage are considered incomplete.
- **Python linting**: `black` (line length default 88) + `isort`. No ruff/flake8 config.

---

## Code Style Guidelines

### Python

**Imports**: Use absolute imports from `src.*` for project code. Group: stdlib → third-party → local. One blank line between groups.
```python
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch
from .broker import AbstractBroker
from src.utils.logging_config import setup_logger
```

**Formatting**: `black` (default 88 chars) + `isort` for import sorting. Docstrings in Chinese are common throughout the codebase.

**Types**: Use `dataclass` for structured data (e.g., `TradeDecision`, `Position`). Avoid `Any`. Prefer explicit type annotations on function signatures. Use `dict`/`list`/`tuple` generics (`dict[str, int]`) not `Dict`/`List`/`Tuple` from typing.

**Naming**:
- Classes: `PascalCase` (e.g., `RealtimeSimEngine`)
- Functions/variables: `snake_case` (e.g., `sync_live_state`, `_t3_cooldown`)
- Private/internal: leading `_`
- Constants: `UPPER_SNAKE_CASE`
- Type aliases: `SomeType = dict[str, Any]`

**Error Handling**: Use logging (`logger.error`, `logger.warning`) for operational errors. Catch specific exceptions. Never swallow errors silently. Use `try/except/finally` with meaningful messages.

**Data Classes**:
```python
from dataclasses import dataclass, field
@dataclass
class TradeDecision:
    action: str
    code: str
    confidence: float = 0.0
    position_pct: float = 0.0
    trigger_signal_ids: list[int] = field(default_factory=list)
```

**SQLite**: Use WAL mode, `busy_timeout=5000`, `isolation_level=None`. Use `readFileSync`/`writeFileSync` with atomic rename (tmp → rename) for JSON config writes. All DB operations must use try/finally with explicit `close()`.

**Constants**: All numeric magic numbers must be named constants (e.g., `T3_COOLDOWN_MS = 5 * 60 * 1000`). Tuple keys are preferred for compound state (e.g., `_t3_cooldown: dict[tuple[str, str], int]` keyed by `(code, strategy)`).

### TypeScript / Next.js

**Imports**: Next.js 15 + React 19. Use `next/link` for client-side navigation (not `<a>`). Relative imports within same module use `./`. Use aliased paths from `web/app/lib` or `web/app/hooks`.

**Types**: Use `interface` for public APIs (e.g., `WatchEntry`, `MonitorConfig`). Use `type` for unions/intersections. **Never use `any` — use `unknown` or precise types**. JSON.parse returns `unknown`, cast explicitly.

```typescript
import type { WatchEntry, MonitorConfig } from "../../types";
type DbWatchRow = { symbol: string; name: string; list_type: "holding" | "watching"; };
```

**Naming**: `camelCase` for variables/functions, `PascalCase` for components and types. Boolean variables use prefixes: `is*`, `has*`, `can*`, `should*`.

**Error Handling**: API routes return `NextResponse.json({ error: "..." }, { status: 400 })`. Wrap DB/filesystem operations in try/catch. Never expose internal error details to client.

```typescript
try {
  const db = new Database(SIM_DB_PATH, { readonly: true });
  // ...
} catch (e) {
  return NextResponse.json({ error: String(e) }, { status: 500 });
}
```

**React**: Use `use client` directive only when needed. Prefer `const` + inline returns for simple components. Use `React.Context` for shared state (`MetricsProvider`). Avoid spreading unknown props onto DOM elements.

---

## Architecture Reminders

- **Poller = Producer, Web = Consumer**. Poller writes `market_data.json`; Web reads it via `/api/metrics`. Never fetch market data in Next.js API routes.
- **DB-first config**: `/api/config` writes SQLite `monitor_watchlist` then exports JSON snapshot. Python side (Poller/Notifier) reads JSON. Web reads both.
- **Single source of truth**: `alert_events` → SQLite `alert_events` table (written by Python Notifier); Web reads only, never computes alerts.
- **Data files** (`monitor_config.json`, `alert_config.json`, `sim_trading.db`): use atomic file writes (tmp → rename).
- **Secrets**: Never log or commit API keys. Use `.env` + `python-dotenv`.
- **All market data and FX fetching must happen in Python poller**, not in Next.js API routes.
- **Alert config and monitor config are separate**: `above`/`below` thresholds live in `alert_config.json`, not inside watchlist entries. Delete operations must clean both.
- **Fee calculation is Python-only**: `SimulationEngine.calc_cost()` is the single source of truth. Web `/api/sim` reads DB `pnl` directly, never recalculates.
- **Stock code prefixes**: `HK` = 港股, no prefix = A股, `KR` = 韩国（不支持实时行情）.
- **HK P&L FX conversion**: Apply `fxRate` (from poller, fallback 0.92) to `mktVal`, `totalPnlRaw`, `dayPnl` for HK stocks. Percentage fields (`pnl%`, `change%`) are NOT converted.
- **P&L guard**: `totalPnlRaw` calculation requires `s.cost > 0` (not just `!= null`). Cost=0 holdings display `-`.
- **Day P&L cap**: For stocks bought today, when `rawDayPnl` exceeds `totalPnl` in the same direction, cap at `totalPnl` to exclude overnight gaps.

### Data File Responsibilities

| Storage | Written by | Content |
|---------|-----------|---------|
| `market_data.json` | Poller (Python) | Real-time quotes + market turnover + FX |
| `sim_trading.db → monitor_watchlist` | UI (/api/config) | Holdings config (primary, DB-first) |
| `monitor_config.json` | UI (/api/config) | JSON snapshot of holdings config |
| `alert_config.json` | UI (/api/config) | Alert thresholds (above/below) |
| `sim_trading.db → alert_events` | Notifier (Python) | Alert event stream |
| `trade_plans.json` | UI + TradePlanEngine | Conditional orders |
| `l2_strategy_signals.json` | L2 daemon | L2 signals + session context (archived daily) |

### Shared Types (web/app/types.ts)

```typescript
export interface WatchEntry {
  name: string; alias?: string; type?: string;
  cost?: number | null; shares?: number | null; lot?: number | null;
  hidden?: boolean; star?: boolean; dip_buy?: boolean;
  tags?: string[]; watch_price?: number; watch_price_date?: string;
}
export interface Service { /* full quote + position data */ }
export type AlertEvent = { ts: number; symbol: string; level: string; message: string; display: string; };
```
