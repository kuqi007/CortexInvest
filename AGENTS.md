# ai-investor

A-share/HK real-time stock monitoring + simulated trading system. Python backend (uv/hatchling) + Next.js 15 frontend + SQLite.

## Architecture

```
market_data_poller ──→ trading.db:price_snapshots / market_turnover (30s轮询)
stock_notifier ──────→ trading.db:alert_events (DeltaAlertEngine)
l2_strategy_daemon ──→ trading.db (3s轮询, Futu OpenD)
web (port 3120) ─────→ read-only SQLite APIs
```

**Data authority rules** (never violate):
- Web/API only **read** market data from DB — Poller is the sole producer
- `DeltaAlertEngine` is the **only** alert computation source
- `SimulationEngine.calc_cost()` is the **only** fee calculator
- Config updates **must** go through `POST /api/config` — never edit JSON/SQLite directly
- **DB (config.db/trading.db) 是唯一运行态权威源**；JSON/JSONL 仅用于迁移、归档和 audit log
- 数据流: `POST /api/config` → 写 DB + audit outbox；Python/Web 都读 DB，禁止 runtime JSON fallback
- 加/改持仓走 API: `curl -X POST localhost:3120/api/config -H 'Content-Type: application/json' -d '{"action":"add",...}'`

## Database Split

| File | Mode | Purpose |
|------|------|---------|
| `data/config.db` | DELETE | monitor_watchlist（真实持仓/自选）, monitor_settings, tag_meta |
| `data/trading.db` | WAL | **纯模拟交易**: trades, live_state, alert_events, signals, sector_rotation |
| `data/sim_trading.db` | WAL | legacy (migrating to trading.db) |

### ⚠️ 真实 vs 模拟 数据区分

| 数据源 | 内容 | 投资决策/复盘时 |
|--------|------|----------------|
| `config.db:monitor_watchlist` | 用户手动维护的真实持仓 | ✅ **唯一真实持仓来源** |
| `trading.db:price_snapshots` | 实时行情 | ✅ 计算浮盈浮亏 |
| `trading.db:alert_events` | 真实行情告警（DeltaAlertEngine） | ✅ 复盘参考 |
| `trading.db:trades` | FutOpenD 模拟成交 | ❌ **复盘不看** |
| `trading.db:live_state` | 模拟持仓 | ❌ **复盘不看** |

**规则：复盘和投资决策只看真实账户（config.db），不查 trading.db 的模拟成交/持仓。**

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
- `stocks/` — **AI 投资参考数据**（股票分析、研报、mx-data 缓存），必须提交 Git，是投资决策的重要参考

详见 [CLAUDE.md](./CLAUDE.md) for full project documentation.

## AI Stock Notes (防止失忆)

每只股票可有一份 `.claude/notes/{CODE}.md` 备忘录，保存复杂交易计划、AI 分析结论、关键观察指标。

**规则**：
- 用户提到某只股票时，AI **必须先读取**对应的 notes 文件（如果存在）
- 分析结束后，AI **主动将新结论追加写入** `## AI 历史分析记录`**（新日期放在最上方，倒序排列）**
- 如果 notes 不存在，AI **创建它**并写入初始分析
- 用户可随时说"帮我在 {CODE} notes 里加上..."或"读一下 {CODE} 的 notes"

**与 trade_plans 的区别**：
- `trading.db:trade_plans` = 结构化执行计划（程序读取）
- `.claude/notes/` = 自由文本决策记录（AI + 人阅读，防止失忆）

详见 [`.claude/notes/README.md`](./.claude/notes/README.md)
