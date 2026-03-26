# AGENTS.md — Guidelines for AI Agents

## Project Overview

这是一个基于多智能体(Multi-Agent)架构的AI股票投资分析系统。项目采用Python + TypeScript全栈架构：

- **Python后端**: `src/` — 多Agent协同分析引擎、模拟交易系统、实时行情轮询、告警通知
- **TypeScript前端**: `web/` — Next.js 15 + React 19 实时监控Dashboard
- **数据库**: SQLite (`src/data/sim_trading.db`) 配合WAL模式，支持高并发读写
- **数据文件**: `src/data/` — JSON配置文件、市场数据缓存，采用原子写入(tmp → rename)

### 核心架构

系统通过LangGraph构建多Agent工作流，不同角色的研究员(多头/空头)和分析师协同工作：

```
market_data_agent → [technical, fundamentals, sentiment, valuation] (并行)
                  → researcher_bull + researcher_bear
                  → debate_room_agent (LLM仲裁多空观点)
                  → risk_management_agent → macro_analyst_agent
                  → portfolio_management_agent → END

macro_news_agent (并行运行) → portfolio_management_agent
```

### 技术栈

**后端**: Python 3.9+, Poetry, LangChain/LangGraph, FastAPI, SQLite, AkShare, yfinance
**前端**: Next.js 15, React 19, TypeScript 5.7, Tailwind CSS 4, better-sqlite3
**测试**: pytest (Python), Vitest + Playwright (TypeScript)

---

## Build / Lint / Test Commands

### Python (Poetry)

```bash
# 依赖管理
poetry install                                      # 安装依赖
poetry lock --no-update                            # 更新lock文件

# 代码格式
poetry run black src/                              # 格式化代码
poetry run black --check src/                      # 检查格式
poetry run isort src/                              # 整理import
poetry run isort --check src/                      # 检查import顺序
poetry run flake8 src/                             # 代码检查

# 测试
poetry run pytest src/sim_trading/test_sim_trading.py -v           # 运行所有测试(113+ tests)
poetry run pytest src/sim_trading/test_sim_trading.py -v -k "T3"   # 按名称过滤测试
poetry run pytest src/sim_trading/test_sim_trading.py -v -x        # fail-fast模式
poetry run pytest src/sim_trading/test_position_change_log.py -v   # 仓位变动日志测试
poetry run pytest src/tools/test_market_data_poller.py -v          # 行情轮询测试

# 运行主程序
poetry run python src/main.py --ticker 000000                      # CLI分析
poetry run python src/main.py --ticker 000000 --show-reasoning     # 显示推理过程
poetry run python src/main.py --ticker 000000 --summary            # 显示汇总报告
poetry run python src/backtester.py --ticker 301157 --start-date 2024-12-11 --end-date 2025-01-07 --num-of-news 20  # 回测
poetry run python run_with_backend.py                              # 启动FastAPI服务(:8000)
poetry run python run_with_backend.py --ticker 002848              # 启动服务并立即分析

# 模拟交易
poetry run python src/tools/l2_strategy_daemon.py                  # L2策略守护进程
poetry run python -m src.sim_trading.replay_runner                 # 模拟交易回放
poetry run python -m src.sim_trading.scoring_backtester            # Optuna参数优化回测
poetry run python src/sim_trading/kline_fetcher.py --codes HK00700,HK09988  # 获取K线数据

# 工具脚本
poetry run python src/tools/market_data_poller.py                  # 行情数据轮询
poetry run python src/tools/stock_monitor.py                       # macOS股票监控
poetry run python src/tools/sector_index_engine.py                 # 板块指数引擎
poetry run python src/tools/washout_calculator.py --code 000001    # 洗盘底部计算
poetry run python src/tools/news_crawler.py --query 贵州茅台       # 新闻爬虫
poetry run python src/tools/daily_summary_generator.py             # 生成每日摘要
```

### Web / TypeScript (Next.js)

```bash
cd web && npm install              # 安装依赖
cd web && npm run dev              # 开发服务器 :3120
cd web && npm run build            # 生产构建
cd web && npm run start            # 生产服务器 :3120
cd web && npx tsc --noEmit        # TypeScript类型检查

# 测试
cd web && npm run test:unit        # Vitest单元测试
cd web && npx vitest run           # Vitest (alternate)
cd web && npm run test             # Playwright E2E测试
cd web && npm run test:ui          # Playwright带UI
cd web && npm run test:headed      # Playwright headed模式
cd web && npm run test:all         # Vitest + Playwright

# 截图测试脚本
cd web && node screenshots/test_dashboard_e2e.mjs        # Dashboard E2E测试
cd web && node screenshots/test_manage_stocks_e2e.mjs    # 管理页面E2E测试
cd web && node screenshots/test_sector_e2e.mjs           # 板块页面E2E测试
```

### Critical Notes

- **永远不要同时运行 `next build` 和 dev server** — build会覆盖`.next/`目录导致`Cannot find module`错误
- **Webpack dev缓存**: 如果出现stale chunk错误，执行 `rm -rf web/.next` 后重启dev server
- **Vitest配置**: `web/vitest.config.ts` 排除 `e2e/` 目录
- **Playwright配置**: `web/playwright.config.ts`，baseURL为 `http://localhost:3120`
- **Playwright E2E**: 每个新功能必须包含E2E测试在 `web/screenshots/test_<feature>.mjs`
- **Python linting**: 使用 `black` (默认88字符行宽) + `isort`，无ruff配置

---

## Code Style Guidelines

### Python

**Imports**: 使用绝对导入 `src.*`，分组顺序：stdlib → third-party → local，每组之间空一行
```python
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import requests

from src.sim_trading.broker import AbstractBroker
from src.utils.logging_config import setup_logger
```

**Formatting**: `black` (默认88字符) + `isort` 排序import。代码注释主要使用中文。

**Types**: 使用 `dataclass` 定义结构化数据，避免使用 `Any`。函数签名使用显式类型注解，使用 `dict`/`list`/`tuple` 泛型而非 `typing.Dict`/`List`/`Tuple`。

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

**Naming**:
- Classes: `PascalCase` (e.g., `RealtimeSimEngine`, `TradeSignalMapper`)
- Functions/variables: `snake_case` (e.g., `sync_live_state`, `_t3_cooldown`)
- Private/internal: 前导下划线 `_`
- Constants: `UPPER_SNAKE_CASE`
- Type aliases: `SomeType = dict[str, Any]`

**Error Handling**: 使用logging记录操作错误，捕获具体异常，绝不静默吞掉错误。

```python
try:
    result = some_operation()
except SpecificException as e:
    logger.error(f"Operation failed: {e}")
    raise
```

**SQLite**: 使用WAL模式，`busy_timeout=5000`，`isolation_level=None`。所有DB操作必须使用try/finally显式`close()`。

**文件写入**: JSON配置文件使用原子写入模式 (tmp → rename)。

```python
tmp_path = path.with_suffix(".tmp")
tmp_path.write_text(json.dumps(data), encoding="utf-8")
tmp_path.replace(path)
```

**Constants**: 所有魔法数字必须定义为命名常量。

```python
T3_COOLDOWN_MS = 5 * 60 * 1000  # 5分钟冷却
MAX_SIGNALS_PER_STOCK = 3
DEFAULT_LOT_SIZE = 100
```

### TypeScript / Next.js

**Imports**: Next.js 15 + React 19。使用 `next/link` 进行客户端导航(不用`<a>`)。相对导入用于同模块内，使用alias路径如 `web/app/lib` 或 `web/app/hooks`。

**Types**: interface用于公共API，type用于联合/交叉类型。**绝不使用`any`** — 使用`unknown`或精确类型。

```typescript
import type { WatchEntry, MonitorConfig } from "../../types";

type DbWatchRow = { 
  symbol: string; 
  name: string; 
  list_type: "holding" | "watching"; 
};

interface AlertEvent {
  ts: number;
  symbol: string;
  level: string;
  message: string;
  display: string;
}
```

**Naming**: 
- `camelCase` 用于变量/函数
- `PascalCase` 用于组件和类型
- 布尔变量前缀: `is*`, `has*`, `can*`, `should*`

**Error Handling**: API路由返回 `NextResponse.json({ error: "..." }, { status: 400 })`。DB/文件系统操作使用try/catch包装。

```typescript
try {
  const db = new Database(SIM_DB_PATH, { readonly: true });
  // ...
} catch (e) {
  return NextResponse.json({ error: String(e) }, { status: 500 });
}
```

**React**: 
- 仅在必要时使用 `"use client"` 指令
- 简单组件使用 `const` + inline returns
- 共享状态使用 `React.Context` (`MetricsProvider`)
- 避免将未知props spread到DOM元素

---

## Project Structure

### Python模块

```
src/
├── agents/                    # Agent定义和工作流
│   ├── fundamentals.py        # 基本面分析Agent
│   ├── technicals.py          # 技术分析Agent
│   ├── sentiment.py           # 情绪分析Agent
│   ├── valuation.py           # 估值分析Agent
│   ├── researcher_bull.py     # 多头研究员
│   ├── researcher_bear.py     # 空头研究员
│   ├── debate_room.py         # 辩论室Agent (LLM仲裁)
│   ├── macro_analyst.py       # 宏观分析师
│   ├── macro_news_agent.py    # 宏观新闻Agent
│   ├── risk_manager.py        # 风险管理Agent
│   ├── portfolio_manager.py   # 投资组合管理Agent
│   ├── market_data.py         # 市场数据Agent
│   └── state.py               # AgentState定义
├── sim_trading/               # 模拟交易系统
│   ├── simulation_engine.py   # 交易成本和滑点计算
│   ├── position_manager.py    # 仓位管理
│   ├── signal_mapper.py       # 信号映射到交易决策
│   ├── trade_analyzer.py      # 交易分析器 (Sharpe/MaxDD)
│   ├── broker.py              # 券商接口抽象
│   ├── futu_trade_adapter.py  # 富途交易适配器
│   ├── realtime_engine.py     # 实时交易引擎
│   ├── db.py                  # 数据库schema和连接
│   ├── kline_fetcher.py       # K线数据获取
│   └── test_sim_trading.py    # 模拟交易测试(113+ tests)
├── tools/                     # 工具和功能模块
│   ├── market_data_poller.py  # 行情数据轮询(东方财富API)
│   ├── l2_strategy_daemon.py  # L2策略守护进程
│   ├── l2_strategy_engine.py  # L2策略引擎
│   ├── stock_monitor.py       # 股票监控
│   ├── stock_notifier.py      # 通知器
│   ├── sector_index_engine.py # 板块指数引擎
│   ├── news_crawler.py        # 新闻爬虫
│   ├── washout_calculator.py  # 洗盘底部计算器
│   └── trading_calendar.py    # 交易日历
├── utils/                     # 通用工具
│   ├── llm_clients.py         # LLM客户端工厂
│   ├── logging_config.py      # 日志配置
│   └── api_utils.py           # API工具
└── main.py                    # Agent工作流定义和CLI入口

backend/                       # FastAPI后端
├── main.py                    # FastAPI应用实例
├── routers/                   # API路由
│   ├── analysis.py            # 分析相关路由
│   ├── agents.py              # Agent状态路由
│   ├── workflow.py            # 工作流状态路由
│   ├── logs.py                # 日志路由
│   └── runs.py                # 运行历史路由
├── services/                  # 业务逻辑
│   └── analysis.py            # 分析服务
├── storage/                   # 日志存储
│   ├── base.py                # 存储接口
│   └── memory.py              # 内存存储实现
├── models/                    # Pydantic模型
├── schemas.py                 # 内部数据结构
└── state.py                   # 内存状态管理
```

### TypeScript/Next.js模块

```
web/
├── app/                       # Next.js App Router
│   ├── page.tsx               # 持仓首页 (Holdings)
│   ├── watching/              # 自选页面
│   ├── alerts/                # 告警页面
│   ├── sim/                   # 模拟交易页面
│   ├── sector/                # 板块指数页面
│   ├── manage/                # 管理页面
│   ├── api/                   # API路由
│   │   ├── metrics/route.ts   # 核心数据API
│   │   ├── config/route.ts    # 配置管理API
│   │   ├── sim/route.ts       # 模拟交易API
│   │   └── sector/route.ts    # 板块API
│   ├── components/            # 共享组件
│   │   ├── AppTabs.tsx        # 顶部导航
│   │   ├── AppTitleBar.tsx    # 标题栏
│   │   ├── MarketSwitch.tsx   # A/港股切换
│   │   └── StockDrawer.tsx    # 股票详情抽屉
│   ├── providers/             # Context Providers
│   │   ├── MetricsProvider.tsx # 数据共享Provider
│   │   └── ClientProviders.tsx # 客户端Provider包装
│   ├── hooks/                 # 自定义Hooks
│   │   ├── useAlerts.ts       # 告警Hook
│   │   └── useTradePlans.ts   # 交易计划Hook
│   ├── lib/                   # 工具库
│   │   ├── db.ts              # 数据库工具
│   │   └── trading-hours.ts   # 交易时间判断
│   └── types.ts               # 共享类型定义
├── e2e/                       # Playwright E2E测试
│   └── dashboard.spec.ts      # Dashboard测试
├── screenshots/               # 截图测试脚本
│   ├── test_dashboard_e2e.mjs
│   └── test_*.mjs             # 各功能测试
├── next.config.ts             # Next.js配置
├── playwright.config.ts       # Playwright配置
└── vitest.config.ts           # Vitest配置
```

---

## Architecture Reminders

### Data Flow Principles

- **Poller = Producer, Web = Consumer**: Poller写入`market_data.json`，Web通过`/api/metrics`读取。Next.js API路由绝不直接获取市场数据。
- **DB-first Config**: `/api/config`写入SQLite `monitor_watchlist`，然后导出JSON快照。Python侧读取JSON，Web侧读取两者。
- **Single Source of Truth**: `alert_events` → SQLite表(由Python Notifier写入)，Web只读不做告警计算。
- **Fee Calculation Python-only**: `SimulationEngine.calc_cost()`是唯一真实来源，Web `/api/sim`直接读取DB的pnl字段不重算。

### Stock Code Prefixes

- `HK` = 港股 (e.g., `HK00700`, `HK09988`)
- 无前缀 = A股 (e.g., `000001`, `600000`)
- `KR` = 韩国股票 (不支持实时行情)

### HK P&L汇率转换

港股盈亏显示需应用`fxRate`(来自poller，默认0.92)到`mktVal`、`totalPnlRaw`、`dayPnl`。百分比字段(`pnl%`, `change%`)**不转换**。

### P&L计算Guard

`totalPnlRaw`计算要求 `s.cost > 0` (不只是`!= null`)。Cost=0的持仓显示`-`。

### Day P&L Cap

对于当日买入的股票，当`rawDayPnl`与`totalPnl`同方向且超过`totalPnl`时，封顶为`totalPnl`(排除隔夜缺口)。

### Data File Responsibilities

| Storage | Written by | Content |
|---------|-----------|---------|
| `market_data.json` | Poller (Python) | 实时行情 + 市场成交额 + 汇率 |
| `sim_trading.db → monitor_watchlist` | UI (/api/config) | 持仓配置(主存储，DB-first) |
| `monitor_config.json` | UI (/api/config) | 持仓配置JSON快照 |
| `alert_config.json` | UI (/api/config) | 告警阈值(above/below) |
| `sim_trading.db → alert_events` | Notifier (Python) | 告警事件流 |
| `trade_plans.json` | UI + TradePlanEngine | 条件单 |
| `l2_strategy_signals.json` | L2 daemon | L2信号 + 会话上下文 |
| `sector_config.json` | UI (/api/sector) | 板块告警规则 |
| `sim_trading.db → sector_*` | sector_index_engine | 板块排名 + 自定义指数 |

### Shared Types (web/app/types.ts)

```typescript
export interface WatchEntry {
  name: string;
  alias?: string;
  type?: string;
  cost?: number | null;
  shares?: number | null;
  lot?: number | null;
  hidden?: boolean;
  star?: boolean;
  dip_buy?: boolean;
  tags?: string[];
  watch_price?: number;
  watch_price_date?: string;
}

export interface Service {
  id: string;
  name: string;
  alias?: string;
  type: string;
  price: number;
  change: number;
  chgAmt: number;
  vol: number;
  amount: number;
  amp: number;
  turnover: number;
  volRatio: number;
  high: number;
  low: number;
  open: number;
  prevClose: number;
  cost: number | null;
  shares: number | null;
  pnl: number | null;
  above: number | null;
  below: number | null;
  hidden?: boolean;
  star?: boolean;
  dip_buy?: boolean;
  tags?: string[];
  mainNetInflow?: number;      // L2主力净流入
  mainNetInflowPct?: number;   // 主力净流入占比
  bidAskRatio?: number;
  avgPrice?: number;
  indicators?: {               // 技术指标缓存
    close: number;
    rsi: number;
    dif: number;
    dea: number;
    macd_hist: number;
    ma5: number;
    ma10: number;
    ma20: number;
    macd_golden_cross: boolean;
    macd_death_cross: boolean;
    ma5_turn_up: boolean;
  };
}

export interface AlertEvent {
  ts: number;
  time: string;
  symbol: string;
  kind: string;
  level?: number;
  message: string;
  display: string;
  change_pct: number;
}

export interface MarketTurnover {
  sh: number; sz: number; total: number;
  shIndex: number; szIndex: number;
  shPct: number; szPct: number;
  verdict: string;
  chiNext?: number; chiNextPct?: number;
  kc50?: number; kc50Pct?: number;
  hkIndex?: number; hkIndexPct?: number;
  hkTech?: number; hkTechPct?: number;
  hkTurnover?: number;
  amo1: number; amo2: number;
}
```

---

## Testing Strategy

### Python测试

- **位置**: `src/sim_trading/test_sim_trading.py` (113+ 测试)
- **框架**: pytest
- **运行**: `poetry run pytest src/sim_trading/test_sim_trading.py -v`
- **关键测试类别**:
  - TradeSignalMapper: 信号到决策映射
  - PositionManager: 仓位管理、开平仓、止损止盈
  - SimulationEngine: 交易成本计算、滑点
  - TradeAnalyzer: Sharpe、最大回撤、归因分析
  - T3策略冷却、紧急止损、持仓同步

### TypeScript测试

- **单元测试**: Vitest (`web/vitest.config.ts`)
  - 排除 `e2e/` 和 `node_modules/`
  - 运行: `cd web && npm run test:unit`

- **E2E测试**: Playwright (`web/playwright.config.ts`)
  - baseURL: `http://localhost:3120`
  - 浏览器: Chromium
  - 自动截图失败用例
  - 运行: `cd web && npm run test`

- **截图测试**: Node.js脚本 (`web/screenshots/*.mjs`)
  - 每个新功能必须有对应截图测试
  - 示例: `test_dashboard_e2e.mjs`, `test_sector_e2e.mjs`

---

## Security Considerations

- **Secrets**: 绝不在代码中硬编码API密钥，使用`.env` + `python-dotenv`
- **Never log API keys**: 日志中绝不输出密钥
- **API Route安全**: 内部错误详情绝不暴露给客户端
- **文件权限**: 数据文件使用合理权限，避免全局可写
- **CORS**: FastAPI配置允许所有来源(`["*"]`)，生产环境需调整

---

## Development Workflow

### 添加新Agent

1. 在 `src/agents/` 创建 `{agent_name}.py`
2. 实现 `agent_name_agent(state: AgentState) -> AgentState` 函数
3. 使用 `@agent_endpoint` 装饰器注册
4. 在 `src/main.py` 的StateGraph中添加节点和边
5. 更新 `src/agents/state.py` 中的状态类型(如需要)

### 添加新API端点

1. 在 `backend/routers/` 创建或修改路由文件
2. 使用标准响应格式 `ApiResponse[T]`
3. 在 `backend/main.py` 中注册router
4. 如需前端调用，更新对应的hooks

### 添加新页面

1. 在 `web/app/` 创建目录和 `page.tsx`
2. 使用 `AppTitleBar` 和 `AppTabs` 组件保持统一风格
3. 如需共享数据，使用 `useMetrics()` hook
4. 添加E2E测试到 `web/screenshots/`

---

## Environment Variables

创建 `.env` 文件 (复制自 `.env.example`):

```bash
# Gemini API 配置
GEMINI_API_KEY=your_gemini_api_key
GEMINI_MODEL=gemini-1.5-flash

# OpenAI Compatible API 配置 (可选，优先级更高)
OPENAI_COMPATIBLE_API_KEY=your_key
OPENAI_COMPATIBLE_BASE_URL=https://api.example.com/v1
OPENAI_COMPATIBLE_MODEL=your_model

# 配置源选择 (可选)
# CONFIG_SOURCE=json  # 强制使用JSON而非SQLite
```

---

## Common Issues

1. **Stale chunk errors in dev**: `rm -rf web/.next && npm run dev`
2. **SQLite busy**: WAL模式已启用，如仍有问题检查并发写入
3. **Module not found in dev**: 确保未在dev server运行时执行build
4. **API 404**: 检查backend是否运行，端口是否正确
5. **Empty metrics**: 检查poller是否运行，`market_data.json`是否存在
