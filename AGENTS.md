# AGENTS.md — AI Stock Monitor System

## Project Overview

A-stock/HK monitoring system: real-time polling, rule-based alerts, simulated trading, web dashboard.
Stack: **Python 3.13 + uv**, **Next.js 15 + React 19**, **SQLite**, **Tailwind CSS 4**.

## Build & Run Commands

### Python (backend)

```bash
# Install dependencies
uv sync

# Run full test suite (113 tests)
uv run pytest src/sim_trading/test_sim_trading.py -v

# Run a single test class
uv run pytest src/sim_trading/test_sim_trading.py::TestTrailingStop -v

# Run a single test by name
uv run pytest src/sim_trading/test_sim_trading.py::TestTrailingStop::test_trail_ratchets_up -v

# Run tests matching a keyword
uv run pytest src/sim_trading/test_sim_trading.py -k "trailing" -v

# Quick check (quiet)
uv run pytest src/sim_trading/test_sim_trading.py -q

# Other Python tests
uv run pytest src/sim_trading/test_futu_enricher.py -v
uv run pytest src/sim_trading/test_market_data_poller.py -v

# Run individual tools
uv run python -m src.tools.washout_calculator --code 002438 --days 90
uv run python -m src.sim_trading.replay_runner
uv run python -m src.sim_trading.scoring_backtester
uv run python -m src.tools.sector_index_engine
```

No linter is configured for Python. No `ruff`, `flake8`, or `black` config exists. Keep existing style.

### Web (frontend — `web/` directory)

```bash
# Install dependencies
cd web && npm install

# Dev server on :3120
npm run dev

# Production build
npm run build

# Unit tests (Vitest)
npm run test:unit
npx vitest run path/to/specific.test.ts

# E2E tests (Playwright — requires running server)
npx playwright test
npx playwright test e2e/dashboard.spec.ts

# All tests
npm run test:all
```

No ESLint config exists. TypeScript strict mode is enabled.

### Full system start

```bash
./start_ai_investor_full.sh          # start|stop|status|restart
```

Launches: Poller → Notifier → L2 Daemon → Web (all background).

## Project Structure

```
src/
├── tools/           # Core daemons & utilities
│   ├── market_data_poller.py    # Produces src/data/market_data.json
│   ├── stock_notifier.py        # Alert engine (sole computation source)
│   ├── l2_strategy_daemon.py    # L2 trading daemon
│   ├── washout_calculator.py    # Washout bottom calculator
│   └── sector_index_engine.py   # Sector rotation (independent cron)
├── sim_trading/     # Simulated trading engine
│   ├── broker.py               # AbstractBroker + Virtual/FutuBroker
│   ├── position_manager.py     # Position management
│   ├── simulation_engine.py    # Cost model, P&L
│   ├── signal_mapper.py        # Signal → decision mapping
│   ├── realtime_engine.py      # V3 real-time engine
│   ├── test_sim_trading.py     # Main test file (113 tests)
│   └── ...
├── data/            # Runtime data (JSON snapshots + SQLite)
│   ├── market_data.json        # Real-time quotes (poller output)
│   ├── monitor_config.json     # Watchlist + settings
│   └── sim_trading.db          # SQLite for trades, alerts, plans
└── utils/           # Shared utilities (logging, LLM clients)

web/
├── app/
│   ├── page.tsx                 # Main dashboard
│   ├── layout.tsx               # Root layout
│   ├── types.ts                 # Shared TypeScript types (single source of truth)
│   ├── theme.ts                 # Design tokens & colors
│   ├── hooks/                   # Custom React hooks (useAlerts, useCommand, useTradePlans)
│   ├── lib/                     # Shared logic (db.ts, trading-hours.ts, tag-utils.ts)
│   ├── providers/               # React context providers (MetricsProvider)
│   ├── components/              # Reusable UI components
│   ├── api/                     # Next.js API routes (read-only from data sources)
│   └── {alerts,manage,sim,sector,watching,daily}/  # Page routes
├── e2e/                          # Playwright E2E specs
└── vitest.config.ts              # Unit test config
```

## Code Style — Python

### Imports
- stdlib → third-party → local (separated by blank lines)
- Use relative imports within `sim_trading/`: `from .position_manager import Position`
- Use absolute `src.` imports from tools: `from src.utils.logging_config import setup_logger`
- Never use `import *`

### Formatting
- Indent: 4 spaces
- Max line ~120 chars (not enforced)
- Single quotes and double quotes both used — be consistent within a file
- Trailing commas in multi-line collections

### Naming
- `snake_case` for functions, variables, modules
- `PascalCase` for classes (`SimulationEngine`, `PositionManager`)
- `UPPER_SNAKE` for constants (`MARKET_DATA_PATH`, `EM_UT`)
- Private helpers prefixed with `_` (`_archive_and_reset`, `_make_signal`)

### Type hints
- Use type hints on function signatures, especially in `broker.py` ABC methods
- Use `dict | None` (union syntax) not `Optional[dict]` for return types
- Internal helper functions may omit hints

### Docstrings
- Module-level docstring with usage example
- Class and public method docstrings
- Chinese comments are acceptable and common

### Error handling
- Use `try/except` with specific exceptions; log with `logger.exception()` or `logger.error()`
- `setup_logger(name)` from `src.utils.logging_config` — creates both console + file handler
- Never swallow exceptions silently — always log at minimum

### Testing patterns
- Tests in same directory as code (`test_sim_trading.py` alongside modules)
- `pytest` fixtures for shared setup (rules, mapper, engine, pos_mgr)
- Test classes group related tests: `TestTrailingStop`, `TestMinHoldGuard`
- Helper factories: `_make_signal()`, `_make_position()`
- `unittest.mock.MagicMock` and `patch` for external dependencies

## Code Style — TypeScript / React

### Imports
- `"use client"` directive at top of client components
- React imports first, then Next.js, then local
- Use `import type { ... }` for type-only imports
- Path alias: `@/*` maps to project root (but relative imports preferred within `app/`)

### Formatting
- Tailwind CSS classes (no CSS modules)
- Inline styles via `theme.ts` constants (`D.red`, `D.green`, `EM_UT`)
- Arrow functions for React components are NOT used — use `function` or `export default function`
- No semicolons (inconsistent in codebase)

### Types
- All shared types in `web/app/types.ts`
- Interfaces over type aliases for object shapes
- API routes use `NextResponse.json()` for responses

### API routes
- Located in `web/app/api/<resource>/route.ts`
- `GET` / `POST` exported as named functions: `export async function GET(request)`
- Read data from JSON files (`market_data.json`, `monitor_config.json`) or SQLite (`better-sqlite3`)
- Never compute alerts in API routes — read from DB only

### Testing
- Unit tests: Vitest (`*.test.ts` alongside source files)
- E2E tests: Playwright (`web/e2e/*.spec.ts`)
- Test DB: create temporary SQLite in `os.tmpdir()`

## Architecture Rules (MUST follow)

1. **Poller is the producer** — only `market_data_poller.py` writes `market_data.json`
2. **Alerts computed once** — `stock_notifier.py` DeltaAlertEngine is the sole engine; Web only reads
3. **Cost calculation single source** — `SimulationEngine.calc_cost()` is authoritative
4. **Local data first** — read `src/data/market_data.json` for stock prices before calling external APIs
5. **Config DB-first dual-write** — `/api/config` writes SQLite + exports JSON snapshot
6. **Config read from DB** — Python tools must use `src.utils.config_reader.read_monitor_config()` to read watchlist/settings; JSON is backup only
7. **Config updates via API only** — NEVER modify SQLite directly. Use `POST /api/config` with `action: add|update|remove|batch` to update holdings/watchlist. This ensures validation, change logging, and JSON snapshot sync.

## Risk Control (DO NOT relax)

When modifying sim_trading code, these parameters are hard limits:
- `max_single_stock_pct`: 25%, `max_total_invested_pct`: 80%, `position_pct`: 25%
- `max_new_positions_per_day`: 1, `min_notional`: 30,000 HKD, `min_hold_minutes`: 30
- Every position must have a stop-loss
- Run `uv run pytest src/sim_trading/test_sim_trading.py -q` before committing sim_trading changes

## Data Files

| File | Writer | Readers |
|------|--------|---------|
| `src/data/market_data.json` | Poller | Notifier, Web API, tools |
| `src/data/monitor_config.json` | Web `/api/config` (export) | Python tools (fallback) |
| `src/data/sim_trading.db` | Web `/api/config`, Trading engine | Web API, Python tools (primary) |

## Config API Usage

All config changes must go through the API:

```bash
# Add/update a holding
curl -X POST http://localhost:3120/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "action": "add",
    "code": "002436",
    "data": {
      "name": "兴森科技",
      "type": "holding",
      "cost": 23.687,
      "shares": 8700,
      "star": true
    }
  }'

# Batch update multiple stocks
curl -X POST http://localhost:3120/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "action": "batch",
    "updates": [
      {"code": "002436", "data": {"type": "holding", "cost": 23.69, "shares": 8700}},
      {"code": "000338", "data": {"type": "holding", "cost": 26.45, "shares": 2000}}
    ]
  }'

# Remove a stock
curl -X POST http://localhost:3120/api/config \
  -H "Content-Type: application/json" \
  -d '{
    "action": "remove",
    "codes": ["000078"]
  }'
```

Python example:
```python
import requests

# Update holding via API
requests.post("http://localhost:3120/api/config", json={
    "action": "add",
    "code": "002436",
    "data": {
        "name": "兴森科技",
        "type": "holding",
        "cost": 23.687,
        "shares": 8700,
        "star": True
    }
})
```

## Key Dependencies

- **Python**: requests, pandas, numpy, futu-api, akshare, rich, fastapi, langchain
- **Web**: next, react, better-sqlite3, tailwindcss 4, playwright, vitest
