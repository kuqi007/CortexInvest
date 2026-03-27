# AI 股票监控系统

## 概述

Python 盯盘系统：行情轮询、规则告警、模拟交易、Web Dashboard。
技术栈：Python 3.13 + uv, Next.js 15 + React 19, SQLite。

## 架构

```
Poller → market_data.json → Notifier → 告警/SQLite
                                ↘ L2 Daemon → 模拟交易
                                ↘ sector_index_engine → 板块指数
Web: market_data.json + DB → /api/metrics → Dashboard
```

## 命令速查

```bash
./start_monitor.sh                         # 启动盯盘（Poller + Notifier + Web）
uv run python -m src.sim_trading.replay_runner     # 模拟交易回放
uv run pytest src/sim_trading/test_sim_trading.py -v  # 测试 (113 tests)
uv run python -m src.sim_trading.scoring_backtester  # Optuna 回测
uv run python -m src.tools.sector_index_engine       # 板块指数
cd web && npm run dev                           # Web 开发 (:3120)
```

## 文档索引

| 文档 | 内容 |
|------|------|
| docs/ARCHITECTURE.md | 系统架构、数据流、设计原则 |
| docs/SIM_TRADING.md | 模拟交易 v3、评分系统、风控 |
| docs/MONITORING.md | Poller、Notifier、L2 Daemon |
| docs/SECTOR.md | 板块指数、主线检测 |
| docs/API.md | Web API 路由 |
| docs/DATABASE.md | SQLite 表结构 |

## 核心原则（AI 必读）

1. **Poller 是生产者，UI 是消费者** — 所有市场数据在 poller 获取，Next.js API 只读
2. **告警计算单一数据源** — `stock_notifier.py` DeltaAlertEngine 是唯一计算引擎，Web 只读
3. **手续费单一计算源** — `SimulationEngine.calc_cost()` 是唯一来源，Web 直接读 DB pnl
4. **Config DB-first 双写** — `/api/config` 写 DB + 导 JSON 快照，Python 读 JSON
5. **Poller 降级不丢数据** — 东方财富失败时 fallback 新浪，量比/汇率继承上轮值
6. **板块轮动独立于 Poller** — `sector_index_engine.py` 是独立 cron，不嵌入 poller 循环

## 风控硬性参数（不得放松）

| 参数 | 值 | 含义 |
|------|---|------|
| `max_single_stock_pct` | 25% | 单股最大仓位 |
| `max_total_invested_pct` | 80% | 总投资上限 |
| `position_pct` | 25% | 每笔开仓比例 |
| `max_new_positions_per_day` | 1 | 每日最多新开仓 |
| `min_notional` | 30,000 HKD | 最小交易金额 |
| `min_hold_minutes` | 30 | T3 最短持有 |

## L1-L4 告警规则

| Level | 匹配 | trigger | delta | 方式 |
|-------|------|---------|-------|------|
| L1 ★ | star=true | 4% | 3% | 弹窗+声音 |
| L2 | holding | 6% | 5% | 弹窗静默 |
| L3 | watching | 仅threshold | 仅threshold | 仅web日志 |
| L4 | hidden | - | - | 不通知 |

## 关键文件

- `src/tools/market_data_poller.py` — 行情轮询
- `src/tools/stock_notifier.py` — 告警引擎
- `src/tools/l2_strategy_daemon.py` — L2 策略 Daemon
- `src/sim_trading/realtime_engine.py` — v3 实时引擎
- `src/sim_trading/broker.py` — AbstractBroker
- `src/sim_trading/position_manager.py` — 仓位管理
- `start_monitor.sh` — 启停脚本
