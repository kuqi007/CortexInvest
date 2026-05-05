<div align="center">

# 🧠 CortexInvest

### AI-Powered Market Intelligence System with Multi-Agent Architecture

*基于大语言模型(LLM)的 A股/港股智能投研系统*

<img src="https://img.shields.io/badge/Python-3.9+-blue.svg?style=for-the-badge&logo=python&logoColor=white">
<img src="https://img.shields.io/badge/Next.js-15-black?style=for-the-badge&logo=next.js&logoColor=white">
<img src="https://img.shields.io/badge/LLM-Gemini%20%7C%20OpenAI%20%7C%20Kimi-orange?style=for-the-badge">
<img src="https://img.shields.io/badge/AI%20Agent-Multi--Agent-purple?style=for-the-badge">
<img src="https://img.shields.io/badge/Token%20Plan-Ready-green?style=for-the-badge">

**核心亮点**: 多 Agent 协作 · LLM 驱动的市场分析 · 智能日报生成 · 模型路由抽象层

</div>

---

## 🤖 AI 架构概览

CortexInvest 是一个**以 LLM 为核心**的智能投研系统，采用 **Multi-Agent 架构**：

```
┌─────────────────────────────────────────────────────────────┐
│                    Multi-Agent 协作层                        │
├─────────────┬─────────────┬──────────────┬──────────────────┤
│  DataAgent  │ AlertAgent  │  TradeAgent  │   SummaryAgent   │
│  (数据获取)  │  (告警分析)  │  (交易决策)   │  (日报生成)      │
├─────────────┼─────────────┼──────────────┼──────────────────┤
│             │             │              │                  │
│  ┌──────────┴─────────────┴──────────────┴──────────────┐  │
│  │              LLM Client Abstraction Layer              │  │
│  │         (Gemini / OpenAI / Kimi / MiMo Ready)         │  │
│  └────────────────────────────────────────────────────────┘  │
│                              │                                │
│              ┌───────────────┼───────────────┐                │
│              ▼               ▼               ▼                │
│         ┌─────────┐    ┌─────────┐    ┌─────────┐           │
│         │ Gemini  │    │ OpenAI  │    │  MiMo   │           │
│         │ 1.5/2.0 │    │ GPT-4   │    │ V2.5    │           │
│         └─────────┘    └─────────┘    └─────────┘           │
└─────────────────────────────────────────────────────────────┘
```

### LLM 应用场景

| 模块 | LLM 应用 | Token 消耗/次 | 频率 |
|------|---------|--------------|------|
| **Daily Summary** | 盘后综合分析生成 | ~50K | 每日 |
| **Morning Briefing** | 早间市场简报 | ~30K | 每日 |
| **Stock Analysis** | 个股深度分析 | ~20K | 按需 |
| **Signal Interpret** | 交易信号解读 | ~10K | 实时 |
| **Risk Assessment** | 风险评估报告 | ~15K | 每日 |

**日均 Token 消耗**: ~100K-200K tokens

---

## 🚀 快速开始

### 1. 安装依赖

```bash
# Python 依赖
uv sync --all-extras

# Web 依赖
cd web && npm install
```

### 2. 配置 LLM API Keys

```bash
cp .env.example .env
# 编辑 .env 填入 LLM API keys:
# - GEMINI_API_KEY (Google Gemini)
# - OPENAI_COMPATIBLE_API_KEY (OpenAI/Kimi)
# - MIMO_API_KEY (Xiaomi MiMo - 预留接口)
```

### 3. 一键启动监控服务

```bash
./start_ai_investor.sh start
```

启动后会运行以下服务：
- **Poller** (port: 无) - 行情数据轮询
- **Notifier** (port: 无) - 分级告警通知
- **L2 Daemon** (port: 无) - L2 策略交易引擎
- **Web** (port: 3120) - 监控面板 http://localhost:3120

---

## 📊 系统架构

```
┌─────────────────────────────────────────────────────────────┐
│                    start_ai_investor.sh                         │
├─────────────┬─────────────┬──────────────┬──────────────────┤
│   Poller    │  Notifier   │  L2 Daemon   │      Web         │
│  (Python)   │  (Python)   │   (Python)   │   (Next.js)      │
├─────────────┼─────────────┼──────────────┼──────────────────┤
│ 行情轮询    │ 分级告警    │ 模拟交易     │ 监控面板         │
│ 数据写入    │ 通知推送    │ 策略执行     │ 可视化展示       │
│ price_snap  │ alert_events│ sim/L2       │ /api/*           │
└─────────────┴─────────────┴──────────────┴──────────────────┘
         │              │               │              │
         └──────────────┴───────────────┴──────────────┘
                              │
                    ┌─────────┴─────────┐
                    │ config.db +       │
                    │ trading.db        │
                    │ (+ audit/*.jsonl) │
                    └───────────────────┘
```

---

## 🛠️ 核心功能

### 1. 实时行情监控 (Poller)

- A股/港股实时行情获取
- 自动降级：东方财富 → 新浪
- 汇率自动获取 (HKD/CNY)
- 量比/换手率继承（降级保护）

### 2. 分级告警系统 (Notifier)

| 级别 | 触发条件 | 通知方式 | 冷却时间 |
|------|---------|---------|---------|
| L1 ★ | star 标记股票涨跌幅 ≥ 4% | 弹窗+声音 | 5分钟 |
| L2 | 持仓股票涨跌幅 ≥ 6% | 弹窗静默 | 15分钟 |
| L3 | 自选股价突破 above/below | 仅 Web | 30分钟 |

- Delta 驱动：价格变化触发，非状态驱动
- Stealth 模式：通知伪装为 CI/系统告警

### 3. 模拟交易系统 (L2 Daemon)

**v3 评分驱动策略**：
- 入场：日线评分 ≥ 60 + ADX ≥ 25 + RR ≥ 2.0
- 止损：max(price - 2×ATR, price×0.90)
- 止盈：price + 3×ATR
- Trailing Stop：盈利后动态抬升止损
- 风控：单股 ≤ 25%，总仓位 ≤ 80%

**支持 broker 模式**：
- VirtualBroker：纯模拟交易
- FutuBroker：连接富途 OpenD（模拟盘）

### 4. Web 监控面板 (Next.js)

| 页面 | URL | 功能 |
|------|-----|------|
| 持仓 | `/` | P&L、成本、日盈亏、A/HK切换 |
| 自选 | `/watching` | 行情监控、涨跌排行 |
| 告警 | `/alerts` | 告警历史、日报展示 |
| 模拟盘 | `/sim` | 持仓、交易计划、净值曲线 |
| 板块 | `/sector` | 自定义指数、主线检测 |
| 管理 | `/manage` | 交易计划、持仓编辑 |

---

## 🤖 LLM 集成架构

### 多模型支持

```python
# src/utils/llm_clients.py
from src.utils.llm_clients import LLMClientFactory

# 支持多种 LLM 后端
client = LLMClientFactory.create("gemini")      # Google Gemini
client = LLMClientFactory.create("openai")      # OpenAI GPT-4
client = LLMClientFactory.create("kimi")        # Moonshot Kimi
client = LLMClientFactory.create("mimo")        # Xiaomi MiMo (预留)
```

### AI 功能模块

| 模块 | 文件 | 功能描述 |
|------|------|---------|
| **LLM Client** | `src/utils/llm_clients.py` | 多模型抽象层，支持 Gemini/OpenAI/Kimi/MiMo |
| **Daily Summary** | `src/tools/daily_summary_generator.py` | LLM 驱动的盘后综合分析 |
| **Morning Brief** | `src/tools/daily_summary_generator.py` | LLM 生成早间市场简报 |
| **Signal Analysis** | `src/tools/l2_strategy_engine.py` | AI 信号解读与风险评估 |

---

## 📁 项目结构

```
.
├── start_ai_investor.sh           # 一键启停脚本
├── src/
│   ├── tools/                 # 监控工具
│   │   ├── market_data_poller.py      # 行情轮询
│   │   ├── stock_notifier.py          # 告警通知
│   │   ├── l2_strategy_daemon.py      # L2 策略守护
│   │   ├── l2_strategy_engine.py      # L2 策略引擎
│   │   ├── sector_index_engine.py     # 板块指数
│   │   └── daily_summary_generator.py # LLM 日报生成
│   ├── sim_trading/           # 模拟交易系统
│   │   ├── realtime_engine.py         # 实时引擎 (v3)
│   │   ├── broker.py                  # Broker 抽象
│   │   ├── position_manager.py        # 持仓管理
│   │   ├── signal_mapper.py           # 信号映射
│   │   └── futu_trade_adapter.py      # 富途适配
│   ├── data/                  # 运行态数据（常为 symlink；权威在 SQLite）
│   │   ├── config.db                  # 持仓/自选/设置/告警规则等
│   │   ├── trading.db               # 行情、告警、交易计划、模拟、板块、日报…
│   │   ├── audit/*.jsonl            # mutation 审计（outbox flush 落盘）
│   │   └── archive/                 # 历史归档快照（可选）
│   └── utils/                 # 工具函数
│       └── llm_clients.py             # LLM 多模型客户端抽象层
├── web/                       # Next.js 监控面板
│   ├── app/
│   │   ├── page.tsx           # Holdings 页面
│   │   ├── watching/page.tsx  # Watching 页面
│   │   ├── alerts/page.tsx    # Alerts 页面
│   │   ├── sim/page.tsx       # Sim 页面
│   │   ├── sector/page.tsx    # Sector 页面
│   │   ├── manage/page.tsx    # Manage 页面
│   │   └── api/               # API routes
│   └── package.json
├── docs/                      # 技术文档
│   ├── AI_ARCHITECTURE.md     # AI 架构文档
│   ├── TOKEN_CONSUMPTION.md   # Token 消耗统计
│   └── ...
├── logs/                      # 日志目录
└── pyproject.toml
```

---

## 🔧 常用命令

```bash
# 启动/停止/重启
./start_ai_investor.sh start
./start_ai_investor.sh stop
./start_ai_investor.sh restart
./start_ai_investor.sh status

# 查看日志
tail -f logs/poller-$(date +%Y-%m-%d).log
tail -f logs/notifier-$(date +%Y-%m-%d).log
tail -f logs/l2_daemon-$(date +%Y-%m-%d).log

# 模拟交易回测
uv run python -m src.sim_trading.scoring_backtester
uv run python -m src.sim_trading.replay_runner

# 板块指数计算
uv run python -m src.tools.sector_index_engine

# 生成早间简报/日报 (LLM 驱动)
uv run python -c "from src.tools.daily_summary_generator import generate_morning_briefing; generate_morning_briefing()"
uv run python -c "from src.tools.daily_summary_generator import generate_daily_summary; generate_daily_summary()"
```

---

## ⚙️ 配置与数据

- **所有配置变更**通过 Web `POST /api/config` 写入 `config.db`（禁止手改 JSON/SQLite）。说明见 [docs/CONFIG_API.md](docs/CONFIG_API.md)、项目根目录 [CLAUDE.md](CLAUDE.md)。
- **根目录遗留 `*.json`**（如旧文档中的 `monitor_config.json`、`trade_plans.json`）已由 DB 表替代；若数据目录中仍有同名文件，多为备份或迁移前快照，**不作为运行态来源**。

---

## 🔔 告警级别说明

- **L1 (★星标)**：最重要的持仓，弹窗+声音通知
- **L2 (普通持仓)**：一般持仓，静默弹窗
- **L3 (自选)**：仅 Web 日志记录，不弹窗
- **L4 (隐藏)**：不计入通知，但保留数据

---

## 🧪 测试

```bash
# 模拟交易单元测试
uv run pytest src/sim_trading/test_sim_trading.py -v

# E2E 测试 (需要 Playwright)
node web/screenshots/test_full_checkup.mjs
```

---

## 📚 相关文档

- [AI_ARCHITECTURE.md](./docs/AI_ARCHITECTURE.md) - AI 架构与 Multi-Agent 设计
- [TOKEN_CONSUMPTION.md](./docs/TOKEN_CONSUMPTION.md) - LLM Token 消耗统计
- [CLAUDE.md](./CLAUDE.md) - AI 开发入口文档（架构索引 + 命令速查）
- [docs/](./docs/) - 分领域详细文档（ARCHITECTURE / SIM_TRADING / MONITORING / SECTOR / API / DATABASE）

---

## 🎯 MiMo 接入计划

本项目已预留 **Xiaomi MiMo** 接入接口：

- ✅ 多模型抽象层 (`llm_clients.py`)
- ✅ OpenAI 兼容 API 格式支持
- 🔄 MiMo V2.5 模型适配（待接入）
- 🔄 中文金融文本优化（待测试）

**预期收益**：
- 降低 60% 以上的 LLM API 成本
- 提升中文金融文本理解准确率
- 支持更长上下文窗口（128K+）

---

## 📜 License

GNU GPL v3 with Non-Commercial Clause

**严禁商业用途**，仅供个人学习研究。
</div>
