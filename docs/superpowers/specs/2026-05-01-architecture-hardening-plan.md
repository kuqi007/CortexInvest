# 架构稳定性加固方案

## 目标

在不做大重构的前提下，把当前系统中最容易导致后续“越改 bug 越多”的风险收敛到可控边界：

- 修复明确的 Web/API 安全与稳定风险。
- 防止运行态备份、行情表、告警表被误提交或误写。
- 补齐基础 CI，让 Python、Web、架构约束能在 PR 阶段暴露问题。
- 明确 Web/API 写入口、双 SQLite 边界和 schema 迁移责任。

本方案优先做小步、可回滚、可独立验收的 PR，不引入 Postgres、不合并双库、不做一次性巨型重构。

## 背景

当前系统已经形成较清晰的运行架构：

```text
market_data_poller ──→ trading.db:price_snapshots / market_turnover
stock_notifier ──────→ trading.db:alert_events
l2_strategy_daemon ──→ trading.db:signals / live_state / trades
web dashboard ───────→ 读取 config.db + trading.db，并通过 API 写低频配置
```

核心规则已经写入 `CLAUDE.md`、`AGENTS.md`、`docs/ARCHITECTURE.md`：

- Poller 是行情唯一生产者。
- `DeltaAlertEngine` 是告警计算主入口。
- `SimulationEngine.calc_cost()` 是手续费计算来源。
- `config.db` / `trading.db` 是运行态权威源，JSON 只用于迁移、归档和 audit。
- 配置变更通过 API 写 DB + audit outbox。

但是多 agent 只读复核后确认，部分规则还没有充分落到代码边界、CI 和文档中，导致后续新增功能时容易复制旧模式或绕过约束。

## 已确认问题

### 1. `/api/indicators` 存在明确安全和稳定风险

`web/app/api/indicators/route.ts` 当前使用 `execSync` 拼接 `poetry run python -c "..."`，并把请求参数 `symbol` 插入 Python 源码字符串。

风险：

- `execSync` 阻塞 Node event loop。
- 字符串命令经 shell 执行。
- `symbol` 未经严格白名单校验即进入 Python 源码字符串，存在代码注入或运行失败风险。
- 使用 `poetry` 与项目常用 `uv` 命令不一致，部署和本地环境更脆。

### 2. 根目录 `backups/` 未被敏感路径检查覆盖

`scripts/check_sensitive_paths.py` 只覆盖：

- `src/data/audit`
- `src/data/archive`
- `src/data/backups`

当前工作区存在大量根目录 `backups/...` 未跟踪文件。若误执行 `git add backups/`，现有 CI 不会拒绝。

### 3. CI 不跑基础测试和 Web build

`.github/workflows/` 当前主要有：

- `audit-coverage.yml`
- `sensitive-paths.yml`

缺少：

- Python tests
- Web unit tests
- Next build
- 架构不变量检查

同时 `pyproject.toml` 的 `testpaths` 只包含 `src/sim_trading`，导致根目录 `uv run pytest` 默认不会收集 `src/tools`、`src/utils`、`src/analysis`、`scripts` 下的测试。

### 4. Web/API 写入口文档与现实不一致

`web/AGENTS.md` 仍描述 `market_data.json`，并写着 “All routes are read-only except config”。实际重要写入口包括：

- `POST /api/config`
- `POST /api/sector`
- `POST /api/trade-plans`
- `POST /api/earnings`

另外，`GET /api/sector` 当前会在某些路径刷新并写入 `sector_rotation`，属于读接口带写副作用。

### 5. DDL / ALTER 分散在请求路径中

长期 schema 主要在 `src/sim_trading/db.py`，但 Web route 中也存在 `CREATE TABLE IF NOT EXISTS`、`ALTER TABLE` 等惰性迁移逻辑。

风险：

- 新字段可能只改一端。
- 请求路径承担 schema 修复责任。
- fresh init 与真实运行路径可能不一致。

### 6. 架构铁律缺少静态护栏

现有 `check_audit_coverage.py` 能检查一部分 DB mutation 是否带 audit helper，但不能保证：

- Web 生产代码不会写 `price_snapshots`。
- Web 生产代码不会写 `alert_events`。
- 新 runtime 路径不会重新 fallback 到旧 JSON。
- Web 不会复制手续费计算公式。

## 设计原则

- **先修明确风险，再做结构治理**：先处理 `/api/indicators`、`backups/`、基础 CI。
- **小 PR 独立验收**：每个 PR 应能单独合入和回滚。
- **不追求全局单写入口**：系统天然存在 poller、notifier、web config、L2 等不同生产者；治理重点是允许列表和边界清晰。
- **GET 默认纯读**：读接口不应默默写库；若必须刷新，使用显式 POST 或后台 daemon。
- **Schema 单一权威**：新 DDL 不应继续散落在业务 route 中。
- **CI 检查低误报优先**：第一版只检查清晰、高价值、低误报的规则。

## 非目标

- 不合并 `config.db` 与 `trading.db`。
- 不迁移到 Postgres。
- 不一次性拆分 `stock_notifier.py` 等大模块。
- 不强行把所有 mutation 合并到 `/api/config`。
- 不把所有高频表纳入 audit。
- 不在第一阶段引入全站 zod/valibot schema。

## PR 拆分

### PR 1：修复 `/api/indicators` 子进程与输入风险

**优先级：P0**

**目标：** 去掉 `execSync + python -c + 用户输入拼接`，改为异步子进程与结构化输入。

**涉及文件：**

- 修改：`web/app/api/indicators/route.ts`
- 新建：`web/app/lib/stock-code.ts`
- 新建：`src/tools/indicators_cli.py`
- 测试：`web/app/api/indicators/route.test.ts` 或同目录现有测试文件

**设计：**

1. `web/app/lib/stock-code.ts` 提供股票代码校验函数。
  - A 股：`000001` 这类 6 位数字。
  - 港股：保留当前系统常用格式，例如 `HK09988`。
  - 韩国 `KR` 继续拒绝。
  - 非法值返回 400。
2. `src/tools/indicators_cli.py` 从 stdin 读取 JSON。
  - 输入：`{"symbol":"603929","live_price":128.5}`
  - 调用 `src.tools.indicator_alert_engine.fetch_kline_akshare` 和 `compute_indicators`。
  - stdout 只输出 JSON。
  - stderr 用于错误诊断。
3. `route.ts` 使用 `spawn` 参数数组。
  - 不使用 shell 字符串。
  - 不使用 `python -c`。
  - 通过 stdin 写 JSON。
  - 保留超时，超时后 kill 子进程。
  - 子进程失败时返回 502 或 500，错误信息截断，避免泄露完整环境。

**验收标准：**

- `rg "execSync|python -c" web/app/api/indicators/route.ts` 无命中。
- 非法 `symbol` 返回 400。
- `symbol` 不再拼入 Python 源码字符串。
- 单测覆盖合法参数、非法参数、子进程失败、超时。

**测试命令：**

```bash
cd web && npm run test:unit -- indicators
cd web && npm run build
```

可选手工验证：

```bash
curl "http://localhost:3120/api/indicators?symbol=603929&live_price=128.5"
```

### PR 2：阻止根目录 `backups/` 被提交

**优先级：P0**

**目标：** 防止运行态备份误提交到 Git。

**涉及文件：**

- 修改：`scripts/check_sensitive_paths.py`
- 修改：`scripts/test_check_sensitive_paths.py`

**设计：**

在 `SENSITIVE_ROOTS` 中加入：

```python
PurePosixPath("backups")
```

测试增加：

- `backups/foo.json` 应被拒绝。
- 非敏感路径继续通过。

**验收标准：**

```bash
uv run pytest scripts/test_check_sensitive_paths.py -q
git ls-files -z | uv run python scripts/check_sensitive_paths.py --stdin0
```

### PR 3：补齐基础 CI 和 pytest 收集范围

**优先级：P0/P1**

**目标：** PR 阶段自动发现 Python、Web、build 回归。

**涉及文件：**

- 新建：`.github/workflows/ci.yml`
- 修改：`pyproject.toml`

**设计：**

新增 CI jobs：

- `pytest`
  - `actions/checkout`
  - `astral-sh/setup-uv`
  - `uv sync --extra dev`
  - `uv run pytest`
- `web-unit`
  - `actions/checkout`
  - `actions/setup-node`
  - `cd web && npm ci`
  - `npm run test:unit`
- `web-build`
  - `cd web && npm ci`
  - `npm run build`

`pyproject.toml` 的 `testpaths` 扩展到：

```toml
testpaths = [
    "src/sim_trading",
    "src/tools",
    "src/utils",
    "src/analysis",
    "scripts",
]
```

如果 `scripts` 中有非 pytest 兼容文件，CI 可先显式运行：

```bash
uv run pytest src/sim_trading src/tools src/utils src/analysis scripts/test_*.py
```

**验收标准：**

```bash
uv run pytest
cd web && npm run test:unit
cd web && npm run build
```

### PR 4：新增架构不变量静态检查

**优先级：P1**

**目标：** 防止 Web/API 生产代码破坏核心数据边界。

**涉及文件：**

- 新建：`scripts/check_architecture_contracts.py`
- 新建：`scripts/test_check_architecture_contracts.py`
- 修改：`.github/workflows/ci.yml` 或现有 guard workflow

**第一版规则：**

扫描 `web/app/api/**/*.ts`，排除：

- `*.test.ts`
- `*.spec.ts`
- fixtures
- 测试 helper

禁止生产代码对以下表执行写操作：

- `price_snapshots`
- `alert_events`

匹配 SQL 操作：

- `INSERT INTO`
- `INSERT OR REPLACE INTO`
- `REPLACE INTO`
- `UPDATE`
- `DELETE FROM`

允许：

- `SELECT ... FROM price_snapshots`
- `SELECT ... FROM alert_events`
- 测试文件插入 fixture 数据

**后续可选规则：**

- 检查 runtime 重新读取 `monitor_config.json` / `market_data.json`。
- 检查 Web 复制手续费公式。

这些规则误报风险更高，第一版不强制做。

**验收标准：**

```bash
uv run pytest scripts/test_check_architecture_contracts.py -q
uv run python scripts/check_architecture_contracts.py
```

### PR 5：写入口矩阵与文档修正

**优先级：P1**

**目标：** 让未来维护者知道哪些 API 可以写库、写哪些表、是否 audit。

**涉及文件：**

- 新建或修改：`docs/WEB_MUTATIONS.md`
- 修改：`docs/ARCHITECTURE.md`
- 修改：`docs/CONFIG_API.md`
- 修改：`web/AGENTS.md`
- 视情况修改：`CLAUDE.md`、`AGENTS.md`

**文档应列出：**

- HTTP 方法
- 路径
- 读写 DB
- 表名
- 是否 audit
- 生产者说明
- 是否存在读接口副作用

最低必须覆盖：

- `POST /api/config`
- `POST /api/sector`
- `POST /api/trade-plans`
- `POST /api/earnings`
- `GET /api/sector` 当前的 `sector_rotation` 写副作用

**验收标准：**

- `web/AGENTS.md` 不再提 `market_data.json` 作为页面运行源。
- `web/AGENTS.md` 不再声称只有 `/api/config` 是写入口。
- `docs/ARCHITECTURE.md` 能解释 Web 写入口与 Poller/Notifier 写入口的关系。

### PR 6：让 `GET /api/sector` 纯读

**优先级：P1/P2**

**目标：** 移除读接口写库副作用。

**方案 A：最小改动**

- 从 `GET /api/sector` 中移除自动 `refreshLiveRotation` 写库。
- 新增显式刷新入口：
  - `POST /api/sector/refresh`，或
  - `POST /api/sector` action `refresh_live_rotation`
- 前端需要刷新时显式调用 POST。

**方案 B：更符合架构**

- 把新浪板块刷新和 `sector_rotation` 写入迁到 Python `sector_index_engine.py` 或独立后台任务。
- Web 只读 DB。

**建议：** 先做方案 A，后续再评估方案 B。

**涉及文件：**

- 修改：`web/app/api/sector/route.ts`
- 可能修改：`web/app/sector/page.tsx`
- 修改：`docs/SECTOR.md` 或 `docs/MONITORING.md`

**验收标准：**

- 常规 `GET /api/sector` 不执行 `INSERT`、`UPDATE`、`DELETE`。
- 刷新动作必须是显式 POST 或后台 daemon。

### PR 7：收敛 DDL / ALTER 到单一来源

**优先级：P2**

**目标：** 请求路径不再负责 schema 迁移。

**涉及文件：**

- 修改：`src/sim_trading/db.py`
- 修改：`web/app/api/config/route.ts`
- 修改：`web/app/api/trade-plans/route.ts`
- 修改：`web/app/api/sim/route.ts`
- 视情况修改：`web/app/lib/audit.ts`
- 视情况修改：`src/tools/stock_notifier.py`

**设计：**

第一阶段不引入复杂 migration 工具，只规定：

- 长期 schema 以 `src/sim_trading/db.py` 为权威。
- 新 `ALTER TABLE` 不再写入 `web/app/api` 请求路径。
- 现有重复 `ALTER` 逐步删除或集中到初始化脚本。

后续若 schema 继续变复杂，再引入：

- `migrations/*.sql`
- `schema_migrations` 表
- 启动/部署时运行 migration runner

**验收标准：**

- `web/app/api` 中不再在 GET/POST handler 里执行业务表 `ALTER TABLE`。
- fresh init 后无需打开页面即可得到完整 schema。

## 推荐实施顺序

```text
PR 1 indicators 安全修复
PR 2 backups 敏感路径
PR 3 基础 CI
PR 4 架构静态护栏
PR 5 写入口矩阵与文档
PR 6 GET /api/sector 纯读
PR 7 DDL 收敛
```

若只能先做三个：

1. PR 1
2. PR 2
3. PR 3

## 验收总清单

完成第一阶段后应满足：

- `/api/indicators` 不再使用 `execSync` 或 `python -c`。
- 根目录 `backups/` 被 sensitive path 检查拒绝。
- PR 会跑 Python tests、Web unit tests、Next build。
- `uv run pytest` 不再只覆盖 `src/sim_trading`。
- Web 生产 API 不能写 `price_snapshots` 和 `alert_events`。
- 文档准确列出 Web 写入口。

## 风险与回滚

- PR 1 可能受本机 Python 命令和 PATH 影响。回滚方式是还原 `route.ts` 和删除 CLI 文件，但不建议回到 `execSync + python -c`；更好的回滚是临时禁用 `/api/indicators`。
- PR 3 可能暴露历史测试失败。若失败过多，可以先把 CI 改为显式稳定路径，再逐步扩大 `testpaths`。
- PR 4 可能有误报。第一版只检查两张表，且排除测试文件，误报应较低。
- PR 6 可能影响板块页面实时性。需要让前端显式刷新或由后台任务补齐刷新。
- PR 7 动面较大，应在 CI 稳定后再做。

## 后续决策点

- 是否将 `sector_rotation` 的唯一推荐生产者改为 Python daemon。
- 是否引入 `zod` 或 `valibot` 统一 Web POST schema。
- 是否把 `EXCLUDED_AUDIT_TABLES` 从 Python/TypeScript 双维护改为共享 JSON 或 parity test。
- 是否将 `DeltaAlertEngine` 与 dip-buy alert 写入路径进一步统一。
- 是否将回测费用计算统一调用 `SimulationEngine.calc_cost()` 或共享费用函数。

