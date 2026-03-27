# 文档架构重构设计

## 目标

将项目文档从"万金油手册"模式改为**分层文档 + 快速入口**模式。CLAUDE.md 作为唯一主入口，精简到 150 行以内，详细内容按领域拆分到独立文档。

## 变更概览

| 操作 | 文件/目录 | 说明 |
|------|----------|------|
| 重写 | `CLAUDE.md` | 精简到 ~120 行，作为入口索引 |
| 创建 | `docs/ARCHITECTURE.md` | 系统架构详细文档 |
| 创建 | `docs/SIM_TRADING.md` | 模拟交易系统完整文档 |
| 创建 | `docs/MONITORING.md` | 盯盘系统文档 |
| 创建 | `docs/SECTOR.md` | 板块指数文档 |
| 创建 | `docs/API.md` | Web API 路由清单 |
| 创建 | `docs/DATABASE.md` | SQLite 表结构文档 |
| 删除 | `AGENTS.md` | 内容已迁移到各领域文档 |
| 删除 | `SESSION_CONTEXT.md` | 会话临时文件，不应存在于代码库 |
| 移动 | `docs/plans/` 早期文件 | 归档到 `docs/archive/` |
| 移动 | `src/tools/test_*.py` | 移入 `src/sim_trading/` |

## 新文档结构

```
ai-investor/
├── CLAUDE.md                         # 唯一入口 (~120 行)
│
├── docs/
│   ├── ARCHITECTURE.md               # 架构 (~200 行)
│   ├── SIM_TRADING.md                # 模拟交易 (~350 行)
│   ├── MONITORING.md                 # 盯盘系统 (~200 行)
│   ├── SECTOR.md                     # 板块指数 (~150 行)
│   ├── API.md                        # API 路由 (~80 行)
│   ├── DATABASE.md                   # 数据库 (~100 行)
│   ├── skills/                       # skills 目录
│   └── archive/                      # 历史 plan 归档
│       └── plans/                    # 2026-03 前的 plan
│
├── src/
│   ├── sim_trading/
│   │   ├── test_*.py                # 测试文件（从 tools/ 移入）
│   │   └── ...
│   └── tools/
│       └── test_*.py                # 删除
│
├── AGENTS.md                         # 删除
└── SESSION_CONTEXT.md                 # 删除
```

## CLAUDE.md 目标内容（约 120 行）

```markdown
# AI 股票监控系统

## 概述
- Python 盯盘系统：行情轮询、规则告警、模拟交易、Web Dashboard
- 技术栈：Python 3.13 + uv, Next.js 15 + React 19, SQLite
- 数据流：Poller → market_data.json → Notifier → 告警/交易

## 架构
[ASCII 架构图]

## 命令速查
[10 条最常用命令]

## 文档索引
| 文档 | 内容 |
|------|------|
| docs/ARCHITECTURE.md | 系统架构、数据流、设计原则 |
| docs/SIM_TRADING.md | 模拟交易 v3、评分系统、风控参数 |
| docs/MONITORING.md | Poller、Notifier、L2 Daemon |
| docs/SECTOR.md | 板块指数、主线检测 |
| docs/API.md | Web API 路由清单 |
| docs/DATABASE.md | SQLite 表结构 |

## 关键原则（AI 必读）
1. Poller 是生产者，UI 是消费者
2. 告警计算单一数据源
3. 手续费单一计算源
...

## 风控硬性参数
[模拟交易风控参数表]

## L1-L4 告警规则
[告警分级表]
```

## 各领域文档内容

### docs/ARCHITECTURE.md (~200 行)
- 系统架构图
- 数据流原则（Poller/Notifier/UI 职责分离）
- Config DB-first 双写模式
- Stock Code 前缀规则
- HK P&L FX 转换规则
- Poller 降级保护

### docs/SIM_TRADING.md (~350 行)
- v3 策略核心（entry/exit/trailing/cooldown）
- 评分维度表
- 信号规则
- Daemon 架构
- AbstractBroker 架构
- Futu 模拟盘交易
- SQLite 表结构
- 回测/回放命令

### docs/MONITORING.md (~200 行)
- Poller 数据源（EM + Sina fallback）
- Notifier DeltaAlertEngine
- L2 Strategy Engine
- DailyIndicatorTracker 评分
- 分级通知 L1-L4
- Stealth 模式
- 降噪规则

### docs/SECTOR.md (~150 行)
- Tag-Based Index 架构
- 指数计算流程
- 主线检测规则
- API 路由
- SQLite 表

### docs/API.md (~80 行)
- Web API 路由清单（metrics/config/sim/sector/trade-plans）
- 请求/响应格式

### docs/DATABASE.md (~100 行)
- 所有 SQLite 表结构
- 表职责说明
- 索引说明

## 归档规则

以下文件归档到 `docs/archive/plans/`：
- `docs/plans/2025-*` 全部
- `docs/plans/2026-02-*` 全部
- `docs/plans/2026-03-01` 到 `2026-03-19`
- `docs/sim_trading_spec.md`（内容已迁移到 SIM_TRADING.md）
- `docs/l2_strategy_spec.md`（内容已迁移到 MONITORING.md）

保留：
- `docs/plans/portfolio_recovery_strategy.md`（无日期前缀，保留原位）
- `docs/plans/2026-03-20-*` 及之后
- `docs/superpowers/plans/` 全部（当前工作流）

## 测试文件迁移

从 `src/tools/` 移入 `src/sim_trading/` 的文件：
- `test_market_data_poller.py` → `src/sim_trading/test_market_data_poller.py`
- `test_morning_briefing.py` → `src/sim_trading/test_morning_briefing.py`
- `test_news_crawler.py` → `src/sim_trading/test_news_crawler.py`
- `test_gemini.py` → `src/sim_trading/test_gemini.py`
- `test_backtest.py` → 删除（重复功能）
- `test_data_pipeline.py` → 删除（过时不适用）
- `test_futu_enricher.py` → `src/sim_trading/test_futu_enricher.py`
- `test.py` → 删除（过时不适用）

注意：`src/sim_trading/test_sim_trading.py` 保持原位置不动。

## 实施步骤

1. 创建 `docs/archive/` 目录
2. 移动归档文件到 `docs/archive/`
3. 创建 `docs/ARCHITECTURE.md`
4. 创建 `docs/SIM_TRADING.md`
5. 创建 `docs/MONITORING.md`
6. 创建 `docs/SECTOR.md`
7. 创建 `docs/API.md`
8. 创建 `docs/DATABASE.md`
9. 重写 `CLAUDE.md`（精简为入口）
10. 移动测试文件到 `src/sim_trading/`
11. 删除 `AGENTS.md`
12. 删除 `SESSION_CONTEXT.md`
13. 更新 `.gitignore`（如有需要）

## 验证

- `uv run pytest src/sim_trading/test_sim_trading.py -q` 全绿
- `cd web && npx tsc --noEmit` 无错误
- CLAUDE.md 行数 < 150
