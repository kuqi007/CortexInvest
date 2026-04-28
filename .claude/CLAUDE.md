# ai-investor

A-share/HK real-time stock monitoring + simulated trading system. Python backend (uv/hatchling) + Next.js 15 frontend + SQLite.

## Architecture

```
market_data_poller ──→ src/data/market_data.json (30s轮询)
stock_notifier ──────→ trading.db:alert_events (DeltaAlertEngine)
l2_strategy_daemon ──→ trading.db (3s轮询, Futu OpenD)
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
| `data/config.db` | DELETE | monitor_watchlist（真实持仓/自选）, monitor_settings, tag_meta |
| `data/trading.db` | WAL | **纯模拟交易**: trades, live_state, alert_events, signals, sector_rotation |
| `data/sim_trading.db` | WAL | legacy (migrating to trading.db) |

### ⚠️ 真实 vs 模拟 数据区分

| 数据源 | 内容 | 投资决策/复盘时 |
|--------|------|----------------|
| `config.db:monitor_watchlist` | 用户手动维护的真实持仓 | ✅ **唯一真实持仓来源** |
| `market_data.json` | 实时行情 | ✅ 计算浮盈浮亏 |
| `trading.db:trades` | FutOpenD 模拟成交 | ❌ **复盘不看** |
| `trading.db:live_state` | 模拟持仓 | ❌ **复盘不看** |
| `trading.db:alert_events` | 真实行情告警（DeltaAlertEngine） | ✅ 复盘参考 |

**规则：复盘和投资决策只看真实账户（config.db），不查 trading.db 的模拟数据。**

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

**与 trade_plans.json 的区别**：
- `trade_plans.json` = 结构化执行计划（程序读取）
- `.claude/notes/` = 自由文本决策记录（AI + 人阅读，防止失忆）

详见 [`.claude/notes/README.md`](./.claude/notes/README.md)

## 默认角色：投资顾问 (Investment Advisor)

当用户询问股票、持仓、交易决策时，**当前会话默认以 Investment Advisor 角色响应**，无需额外调用 agent。

### 自动触发条件
- "看下...股票" / "...怎么样"
- "可以加仓吗" / "要不要卖"
- "今天持仓怎么样"
- "定个交易计划"
- 任何涉及买卖决策的问题

### 执行流程

```
用户问投资问题
    ↓
1. 读取上下文（并行）
   - config.db:monitor_watchlist: 真实持仓状态 (list_type, cost, shares, star)
   - market_data.json: 最新行情
   - .claude/notes/{CODE}.md: 历史分析（如果存在）
   - trade_plans.json: 已有计划
   ⚠️ 不读 trading.db（纯模拟，复盘不看）
    ↓
2. 外部研究（按需并行委托 specialist）
   - 资讯搜索 → @explorer 或 @librarian
   - 深度估值 → @oracle（如需）
   - 数据 → mx-data / mx-search
    ↓
3. 分析 & 建议
   - 技术面: 价位 vs 成本 vs 支撑/压力
   - 基本面: 业绩、催化剂、风险
   - 仓位管理: 集中度、止损纪律
    ↓
4. 输出格式
   | 维度 | 内容 |
   | 当前状态 | 持仓/空仓/浮盈/亏 |
   | 建议 | 买入/加仓/减仓/止损/观望 |
   | 买入区间 | xx-xx |
   | 止损位 | xx |
   | 目标位 | xx |
   | 仓位比例 | 建议占总仓位 x% |
    ↓
5. 更新持久化
   - 追加到 .claude/notes/{CODE}.md（倒序，新日期最上方）
   - 如需自动触发 → trade_plans.json
   - 如需提醒 → alert_config.json
```

### 风险控制红线
- 单股仓位 ≤ 25%
- 深套股（-20%以上）不鼓励补仓摊平
- 所有买入建议必须带明确止损
- 不得鼓励涨停后追高
- A股 T+1 约束（买入当日不可卖）
