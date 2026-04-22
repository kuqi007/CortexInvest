# ai-investor

A-share/HK real-time stock monitoring + simulated trading system. Python backend (uv/hatchling) + Next.js 15 frontend + SQLite.

## Architecture

```
market_data_poller ──→ src/data/market_data.json (30s轮询)
stock_notifier ──────→ sim_trading.db:alert_events (DeltaAlertEngine)
l2_strategy_daemon ──→ sim_trading.db (3s轮询, Futu OpenD)
web (port 3120) ─────→ read-only JSON + DB
```

**Data authority rules** (never violate):
- Web/API only **read** market data — Poller is the sole producer
- `DeltaAlertEngine` is the **only** alert computation source
- `SimulationEngine.calc_cost()` is the **only** fee calculator
- Config updates **must** go through `POST /api/config` — never edit JSON/SQLite directly
- **DB (config.db) 是唯一权威源**，JSON (monitor_config.json) 只是备份快照
- 数据流: `POST /api/config` → 写 DB → 导出 JSON；Python 读 DB (fallback JSON)；Web 前端读 JSON
- 加/改持仓走 API: `curl -X POST localhost:3120/api/config -H 'Content-Type: application/json' -d '{"action":"add",...}'`

## Database Split

| File | Mode | Purpose |
|------|------|---------|
| `data/config.db` | DELETE | monitor_watchlist, signals, alert_events, sector_index |
| `data/trading.db` | WAL | positions, trades, orders, daily_pnl, live_state |
| `data/sim_trading.db` | WAL | legacy (migrating to trading.db) |

Use `init_db()` from each module's db util. Config DB-first: Python reads via `read_monitor_config()`, never raw JSON.

## Conventions (All Python)

- `PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent` + `sys.path.insert(0, str(PROJECT_ROOT))` for standalone scripts
- `setup_logger(name)` from `src/utils/logging_config.py` — every module
- Daemon singleton: `fcntl.flock(open(lockfile), fcntl.LOCK_EX | fcntl.LOCK_NB)` — poller, notifier, l2_daemon
- CLI entry: `if __name__ == '__main__': main()` — standard across all daemon scripts
- Kline fetch: **always** pass both `start` and `end` to `request_history_kline` (Futu SDK trap)

## Safety (Simulated Trading)

Hard limits — **never relax** in code changes:
- `max_single_stock_pct=25%`, `max_total_invested_pct=80%`, `position_pct=25%`
- `max_new_positions_per_day=1`, `min_notional=30000`, `min_hold_minutes=30`
- Every position must have stop-loss; `check_exits()` runs every tick
- Kline stale check + SL sanity check must remain
- Before changing sim_trading code: confirm no bypass of stop-loss, no position increase, no frequency increase

## Test Commands

```bash
uv run pytest src/sim_trading/test_sim_trading.py -q   # quick (~113 tests)
uv run pytest src/sim_trading/test_sim_trading.py -v   # verbose
cd web && npm run dev                                    # frontend :3120
```

## Directory Guides

- `src/tools/` — Polling, alerting, L2 execution, sector rotation
- `src/sim_trading/` — Trading engine, brokers, position management
- `web/` — Next.js dashboard, API routes, UI components

详见 [CLAUDE.md](./CLAUDE.md) for full project documentation.
