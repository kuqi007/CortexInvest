# AI 股票监控系统

Python 3.13 + uv, Next.js 15, SQLite。行情轮询/告警/模拟交易/Web Dashboard。

## 规则

- **本地数据优先**: 查行情/持仓/告警 → 读 `config.db` + `trading.db`，不爬外部
- **Poller 唯一生产者**: Web/API 只读，不写行情数据
- **告警单一数据源**: `stock_notifier.py` DeltaAlertEngine 是唯一计算引擎
- **手续费单一计算源**: `SimulationEngine.calc_cost()` 是唯一来源，Web 直接读 DB pnl
- **Config DB-first**: API 写 DB + audit outbox，Python 用 `config_reader.read_monitor_config()` 读 DB；旧 JSON 仅迁移/归档
- **Audit（变更记录）**: 对受覆盖表做 DB 变更时，经 `config_audit_outbox` / `trading_audit_outbox` 由 `audit_flush` 落盘到 `src/data/audit/*.jsonl`（含 manifest、哈希链）；高频表在 `EXCLUDED_AUDIT_TABLES` 中排除。实现见 `src/utils/audit_writer.py`、`web/app/lib/audit.ts`；CI 用 `scripts/check_audit_coverage.py` 扫描非排除表的写路径
- **Config 必须通过 API 更新**: 禁止直接改 JSON/SQLite，详见 [docs/CONFIG_API.md](docs/CONFIG_API.md)

## 风控硬线（不得放松）

`max_single_stock_pct=25%, max_total_invested_pct=80%, position_pct=25%, max_new_positions_per_day=1, min_notional=30000, min_hold_minutes=30`

## 命令

```
./start_ai_investor_full.sh                                    # 启动
uv run pytest src/sim_trading/test_sim_trading.py -m smoke -q  # 冒烟 14 tests <1s
uv run pytest src/sim_trading/test_sim_trading.py -v           # 全量 138 tests
cd web && npm run dev                                          # Web :3120
```

## 告警 L1-L4

详见 [docs/notification-system.md](docs/notification-system.md)

L1(star,4%,3%,弹窗+声) → L2(holding,6%,5%,弹窗) → L3(watching,threshold,仅web) → L4(hidden,不通知)

## 关键文件

`src/tools/market_data_poller.py` `src/tools/stock_notifier.py` `src/tools/l2_strategy_daemon.py` `src/sim_trading/broker.py` `src/sim_trading/position_manager.py` `start_ai_investor_full.sh`

## 参考数据目录 `stocks/`

`stocks/` 目录包含 AI 投资的参考数据，**必须提交到 Git**。

- `stocks/{CODE}_{NAME}/` — 每只股票的独立目录，保存 mx-data 缓存、研报、分析结论
- **stocks 下的基本面分析报告是 AI 决策的重要依据**，分析或研究股票时，将结果存入对应 `stocks/` 子目录

## 运行态数据目录 `src/data/`

- 代码中路径为 `src/data/`（本机可为指向 OneDrive 等位置的符号链接）。**权威状态只在 SQLite**：`config.db`、`trading.db`。
- **`audit/`**：`config_events.jsonl`、`trading_events.jsonl` 及 `.manifest.json` — mutation 审计日志，不是配置源。
- **`archive/`**：按日归档的历史快照（如旧流程导出的日报/L2 信号），供查阅或回填，不参与实时读写主链路。
- **根目录遗留 `*.json`（如 `monitor_config.json`、`market_data.json`、`trade_plans.json` 等）**：已由 DB 表替代，**不得作为 runtime 配置源**；若仍存在，多为迁移前备份或多机副本（`-*主机名*.json` / `-*主机名*.db`），整理前需确认是否仍被引用。

## 子文档索引

| 文档 | 内容 |
|------|------|
| [docs/CONFIG_API.md](docs/CONFIG_API.md) | 配置 API 用法（增删改自选/持仓） |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | 系统架构、数据流 |
| [docs/SIM_TRADING.md](docs/SIM_TRADING.md) | 模拟交易 v3、评分系统 |
| [docs/MONITORING.md](docs/MONITORING.md) | Poller、Notifier、L2 Daemon |
| [docs/SECTOR.md](docs/SECTOR.md) | 板块指数、主线检测 |
| [.claude/rules/sim-trading-safety.md](.claude/rules/sim-trading-safety.md) | 风控硬线、测试覆盖要求 |
| [docs/notification-system.md](docs/notification-system.md) | 告警分级、时段、dispatch |
| [docs/futu-api.md](docs/futu-api.md) | Futu API 参数陷阱、额度（调 Futu 接口前必读） |
| [docs/superpowers/specs/2026-04-28-json-to-db-audit-log-design.md](docs/superpowers/specs/2026-04-28-json-to-db-audit-log-design.md) | JSON→DB 与 audit 设计说明（表映射、排除表） |
