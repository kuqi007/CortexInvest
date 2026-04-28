# 运行态 JSON 迁移到 DB + Audit Log 设计

## 目标

将 `src/data` 下会影响系统运行的 JSON 文件从运行时数据源迁移到 SQLite。迁移后，系统运行时只读写 `config.db` 和 `trading.db`；JSON 文件只作为审计日志和历史快照，用于人工排查和显式恢复，避免 JSON 与 DB 双源漂移。

## 范围

包含 `src/data` 下运行态 JSON：

- `monitor_config.json`
- `market_data.json`
- `trade_plans.json`
- `tick_monitor_state.json`
- `alert_config.json`
- `l2_strategy_signals.json`
- `l2_strategy_config.json`
- `signal_rules.json`
- `sector_config.json`
- `trading_calendar_cache.json`
- `sentiment_cache.json`
- `daily_summary.json`

不包含：

- `stocks/**/valuation_result.json`、`metadata.json` 等研究资料和归档产物。
- DB 表内的 JSON 字符串字段，例如 `trade_plans.orders_json`、`indicator_cache.data_json`、`session_snapshots.session_json`。这些字段属于 SQLite 内部结构化存储，不是 JSON 文件运行源。
- 测试 fixture、CLI 临时输出、人工导出的研究报告。

## 设计原则

- **DB-only 运行**：daemon、API、Web 前端运行时只能从 DB 读取权威数据。
- **不自动 fallback**：DB 不可用或为空时明确失败，不再隐式读取旧 JSON。
- **显式恢复**：只能通过恢复 CLI 从 audit/snapshot 回灌 DB，默认 dry-run，确认后 `--apply`。
- **事件与快照分层**：低频配置变更写 JSONL 事件日志；高频行情和信号从 DB 生成日快照。
- **Audit 先入 DB outbox**：业务数据与 audit 事件先在同一 SQLite 事务中提交，再由 flush 工具写出 JSONL，避免 DB 状态和 audit 文件静默分叉。
- **集中 DDL**：所有运行态表的长期 schema 定义集中到 `src/sim_trading/db.py`，避免 route/tool 懒建表导致 fresh init 不完整。
- **Poller 仍是行情唯一生产者**：行情写入 `trading.db`，Web/API 只读。

## 数据模型

### `config.db`

`config.db` 保存用户手动维护或低频配置：


| 数据           | 表                                                         | 说明                                |
| ------------ | --------------------------------------------------------- | --------------------------------- |
| 自选/持仓        | `monitor_watchlist`                                       | 已存在，继续作为真实持仓唯一来源                  |
| 全局设置         | `monitor_settings`                                        | 已存在                               |
| 告警规则         | `alert_rules`                                             | 已存在                               |
| 标签元数据        | `tag_meta`                                                | 已存在                               |
| Panic 参数     | `monitor_settings`                                        | 使用 `panic_`* key 存储，避免为少量全局参数新增表  |
| Audit outbox | `config_audit_outbox`                                     | 与 config 写事务同库提交，后续 flush 到 JSONL |
| Restore 状态   | `config_restore_sessions`、`config_restore_applied_events` | 记录恢复会话和已应用 event，支持幂等和崩溃检测        |


### `trading.db`

`trading.db` 保存运行中产生的数据和缓存：


| 数据                      | 表                                                           | 说明                                                               |
| ----------------------- | ----------------------------------------------------------- | ---------------------------------------------------------------- |
| 实时行情                    | `price_snapshots`、`market_turnover`                         | 已存在，替代 `market_data.json`                                        |
| 交易计划                    | `trade_plans`、`trade_plan_events`                           | 纳入集中 schema，替代 `trade_plans.json`                                |
| Tick Monitor 状态         | `tick_monitor_state`                                        | 新增，用于替代 `tick_monitor_state.json` 冷却状态                           |
| Tick Monitor 事件         | `tick_monitor_events`                                       | 已在 `tick_monitor.py` 定义，纳入 schema 初始化                            |
| L2 信号                   | `signals`、`session_snapshots`                               | 已存在，替代 `l2_strategy_signals.json` 的运行读取                          |
| 日报                      | `daily_summaries`、`morning_briefings`                       | 已存在，替代 `daily_summary.json`                                      |
| 交易日历缓存                  | `trading_calendar_cache`                                    | 新增或补齐，用于替代 `trading_calendar_cache.json`                         |
| 新闻情绪缓存                  | `sentiment_cache`                                           | 新增或补齐，用于替代 `sentiment_cache.json`                                |
| 板块派生数据                  | `sector_*` 表                                                | `sector_config.json` 不再作为运行源；人维护的 tag 元数据仍在 `config.db:tag_meta` |
| L2 策略配置                 | `l2_strategy_config`、`signal_rules`                         | 替代 `l2_strategy_config.json`、`signal_rules.json`                 |
| Poller leader/heartbeat | `poller_leader_lease`                                       | 替代 `market_data.json` mtime 远程活跃检测，避免与现有 `leader_election` 表冲突   |
| Audit outbox            | `trading_audit_outbox`                                      | 与 trading 写事务同库提交，后续 flush 到 JSONL                               |
| Restore 状态              | `trading_restore_sessions`、`trading_restore_applied_events` | 记录恢复会话和已应用 event，支持幂等和崩溃检测                                       |


### 集中 Schema

所有上表必须由 `init_db()` 初始化。现有分散 DDL 需要收口：

- `trade_plans`、`trade_plan_events`
- `daily_summaries`、`morning_briefings`
- `tick_monitor_events`、`tick_monitor_state`
- `l2_strategy_config`、`signal_rules`
- `trading_calendar_cache`、`sentiment_cache`
- `poller_leader_lease`
- `config_audit_outbox`、`trading_audit_outbox`
- `config_restore_sessions`、`config_restore_applied_events`
- `trading_restore_sessions`、`trading_restore_applied_events`

Web route 和 daemon 可以做幂等字段检查，但不再作为唯一建表入口。

DDL 唯一权威放在 `src/sim_trading/db.py`。独立 migration 脚本只做数据搬迁和 `ALTER TABLE`，不拥有长期 schema 定义。

### 迁移矩阵


| JSON 文件 / Key                            | 当前用途                | 目标 DB                                            |
| ---------------------------------------- | ------------------- | ------------------------------------------------ |
| `monitor_config.json` watchlist/settings | 自选、持仓、全局设置          | `config.db:monitor_watchlist`、`monitor_settings` |
| `market_data.json` services/turnover     | 实时行情和大盘成交额          | `trading.db:price_snapshots`、`market_turnover`   |
| `trade_plans.json` plans                 | 条件单、tick monitor 计划 | `trading.db:trade_plans`                         |
| `tick_monitor_state.json`                | 冷却状态                | `trading.db:tick_monitor_state`                  |
| `alert_config.json` panic keys           | panic 通知参数          | `config.db:monitor_settings` 的 `panic_*` key     |
| `alert_config.json` alert rules          | 价格阈值                | `config.db:alert_rules`                          |
| `l2_strategy_config.json`                | L2 daemon 配置        | `trading.db:l2_strategy_config`                  |
| `signal_rules.json`                      | 模拟/L2 信号规则          | `trading.db:signal_rules`                        |
| `l2_strategy_signals.json`               | L2 当前信号快照           | `trading.db:signals`、`session_snapshots`         |
| `sector_config.json` tag defaults        | 板块/标签配置             | `config.db:tag_meta`；派生行情在 `trading.db:sector_*` |
| `trading_calendar_cache.json`            | 交易日缓存               | `trading.db:trading_calendar_cache`              |
| `sentiment_cache.json`                   | 新闻情绪缓存              | `trading.db:sentiment_cache`                     |
| `daily_summary.json`                     | 日报展示/归档             | `trading.db:daily_summaries`、`morning_briefings` |


### Consumer Inventory

实施前必须用 `rg` 生成并维护读者清单，直到 Batch 2 全部清零：


| Consumer 类型         | 检查内容                                                        | 目标                                |
| ------------------- | ----------------------------------------------------------- | --------------------------------- |
| Python daemon/tool  | `open/read_text/json.load` 读取 `src/data/*.json`             | Batch 2 后无运行态读取                   |
| Next.js API/server  | `fs.readFileSync`、dynamic import、`require("fs")` 读取运行态 JSON | Batch 2 后无运行态读取                   |
| Shell/start scripts | 等待或检查 `market_data.json`、`monitor_config.json`              | Batch 1 后移除                       |
| Docs/rules          | 宣称 JSON 是运行源或热备                                             | Batch 1/2 同步改成 DB + audit/archive |
| Tests/fixtures      | 临时 JSON fixture                                             | 允许保留，但不得作为运行 fallback 证明          |


## Audit JSON

### 路径

- 事件日志：`src/data/audit/*.jsonl`
- 日快照：`src/data/archive/YYYY-MM-DD/*.json`

### Event Envelope Contract

所有写入 outbox 的事件必须先构造统一 envelope，再序列化到 `payload_json`：


| 字段               | 类型          | 规则                                                                  |
| ---------------- | ----------- | ------------------------------------------------------------------- |
| `event_id`       | string      | UUID/ULID，全局唯一；同一 DB 内重复 event_id 必须指向同一 event_hash                 |
| `schema_version` | integer     | 当前固定为 `1`                                                           |
| `ts`             | string      | RFC 3339 UTC，例如 `2026-04-28T11:42:00Z`                              |
| `ts_ms`          | integer     | Unix milliseconds，排序权威字段                                            |
| `correlation_id` | string      | 一次用户/API/daemon 操作的关联 ID；跨 DB 操作必须提供                                |
| `source`         | string      | 枚举，如 `api_config`、`trade_plan_api`、`tick_monitor`、`audit_restore`   |
| `action`         | string      | 枚举，如 `create`、`update`、`delete`、`reset`、`restore`、`partial_failure` |
| `entity`         | string      | 表/领域实体，如 `monitor_watchlist`、`trade_plans`、`tick_monitor_state`     |
| `key`            | string      | 主业务 key；多 key 操作用稳定组合 key 或批次 key                                   |
| `before`         | object/null | 最小恢复所需旧值；敏感字段只在 audit/archive 中保留                                   |
| `after`          | object/null | 最小恢复所需新值                                                            |
| `db`             | string      | `config.db` 或 `trading.db`                                          |


`payload_json` 是唯一权威事件内容，存储不含 `hash` 的完整 envelope。outbox 的 `schema_version`、`correlation_id`、`ts`、`ts_ms`、`source`、`action`、`entity`、`key`、`db` 只是查询/索引用冗余列，写入时必须由同一个 envelope 派生；flush 发现列值与 `payload_json` 不一致时 hard fail，不写 JSONL。flush 时追加 `prev_hash` 和 `hash` 后写入物理 JSONL。所有 action/entity/source 枚举由 Python 共享模块维护，TypeScript 只能复用同一枚举定义或生成代码产物。

### 事件日志

低频、需要追溯的 DB 写入先在同事务写入 audit outbox，再由 flush 工具追加 JSONL：


| 物理文件                   | 来源                                                                       |
| ---------------------- | ------------------------------------------------------------------------ |
| `config_events.jsonl`  | `/api/config`、截图导入、Futu 持仓同步                                             |
| `trading_events.jsonl` | `/api/trade-plans`、`TradePlanEngine`、`tick_monitor`、显式恢复工具的 trading 恢复事件 |


逻辑事件类型通过 `source`、`action`、`entity` 区分，不再为 trade plan、tick state、restore 拆多个物理 JSONL。hash 链范围固定为单个物理文件。

事件字段：

```json
{
  "event_id": "018f6e31-8d90-7c1e-a3a1-7f8a34f6c0ef",
  "schema_version": 1,
  "ts": "2026-04-28T11:42:00Z",
  "ts_ms": 1777376520000,
  "correlation_id": "01HWNM4Z7G8E6Q9M3R2T1V0X5K",
  "source": "api_config",
  "action": "update",
  "entity": "monitor_watchlist",
  "key": "HK09988",
  "before": {"shares": 1000, "cost": 88.8},
  "after": {"shares": 800, "cost": 90.1},
  "db": "config.db",
  "prev_hash": "sha256:...",
  "hash": "sha256:..."
}
```

`event_id` 用于恢复幂等；`schema_version` 用于未来格式升级；`correlation_id` 用于把一次 API/daemon 操作关联到多条表变更。`prev_hash`/`hash` 用于轻量防篡改检测，恢复工具发现 hash 链断裂时默认拒绝 `--apply`。

### Audit Outbox

业务写入时不直接依赖 JSONL 文件成功写入。每个写入口在同一个 SQLite 事务内写业务表和 outbox 表：

```sql
CREATE TABLE IF NOT EXISTS config_audit_outbox (
  event_id TEXT PRIMARY KEY,
  schema_version INTEGER NOT NULL DEFAULT 1,
  correlation_id TEXT,
  ts TEXT NOT NULL,
  ts_ms INTEGER NOT NULL,
  source TEXT NOT NULL,
  action TEXT NOT NULL,
  entity TEXT NOT NULL,
  key TEXT NOT NULL,
  db TEXT NOT NULL DEFAULT 'config.db' CHECK (db = 'config.db'),
  payload_json TEXT NOT NULL,
  flushed_at TEXT,
  flushed_at_ms INTEGER,
  flush_id TEXT,
  flush_started_at_ms INTEGER,
  CHECK (length(trim(payload_json)) > 0)
);

-- trading_audit_outbox uses the same columns, except:
--   db TEXT NOT NULL DEFAULT 'trading.db' CHECK (db = 'trading.db')

CREATE INDEX IF NOT EXISTS idx_config_audit_outbox_pending
  ON config_audit_outbox(ts_ms, event_id)
  WHERE flushed_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_config_audit_outbox_correlation
  ON config_audit_outbox(correlation_id, ts_ms)
  WHERE correlation_id IS NOT NULL;
```

`trading_audit_outbox` 使用同样结构，但 `db` 列必须 `DEFAULT 'trading.db' CHECK (db = 'trading.db')`。flush 工具按 `ts_ms,event_id` 顺序写 JSONL，写成功后更新 `flushed_at`。如果 flush 失败，DB 里仍保留待刷事件；健康检查可根据未刷事件数量和滞后时间报警。

Flush 进程先获取 DB 旁路文件锁（例如 `config.db.flush.lock` / `trading.db.flush.lock`），再使用 `BEGIN IMMEDIATE` 读取并标记待刷事件，单个 DB 同一时间只允许一个 flush writer。拿到锁后必须重新查询 `flushed_at IS NULL` 的行，避免两个进程基于旧快照重复 append。每个 outbox 写入独立 JSONL 文件，不跨 DB 混写：`config_audit_outbox` → `config_events.jsonl`，`trading_audit_outbox` → `trading_events.jsonl`，恢复事件按目标 DB 写入对应 outbox。API 写入使用短事务，flush 不跨 DB 同时持锁；如确需触两库，锁顺序固定为 `config.db` → `trading.db`。

Flush lock 文件路径必须和 restore backup path 使用同级安全规则：repo-root-relative、拒绝 `..`、拒绝 symlink component，并用 exclusive create/open 获取锁，避免锁文件路径被重定向。

Flush 每批有上限，默认最多 1,000 rows 或 2 秒事务时间，避免在 `config.db` DELETE journal 下长时间阻塞 API 写入。

Flush 运行契约：默认每 30 秒或进程空闲时执行一次；未 flush 事件超过 5 分钟或超过 10,000 条时告警。实现可以由守护脚本、启动脚本定时调用或 daemon idle hook 触发，但同一 DB 必须共享同一个 flush lock。

Flush 崩溃安全协议：

1. 获取 flush lock 后读取 pending rows，并在同一 `BEGIN IMMEDIATE` 事务中为本批次写入 `flush_id` / `flush_started_at_ms`（实现可用临时状态列或单独 `audit_flush_batches` 表）。
2. 将本批事件写入 sidecar 文件，例如 `config_events.jsonl.<flush_id>.pending`，内容包含计算好的 `prev_hash/hash`，并 fsync 文件。
3. 原子 append 到目标 JSONL 或通过安全合并工具追加；追加后 fsync 目标文件和目录。
4. 重新读取目标 JSONL 尾部，确认本批最后一个 `event_id/hash` 已存在且链连续。
5. 在 SQLite 中把本批 rows 标记 `flushed_at_ms/flushed_at` 和 `flush_id`，提交事务。

如果进程在第 2-4 步崩溃，下一次 flush 必须先检查 sidecar 和目标 JSONL 尾部：已完整追加则只补标记 DB；未追加或链不连续则丢弃 sidecar 并重新生成本批。禁止简单地再次 append 同一批 pending rows。恢复工具遇到同一物理 JSONL 中重复 `event_id` 时 hard fail，除非重复行字节完全相同且属于已记录的崩溃恢复场景。

连接与时间字段约定：所有 DB 连接统一通过项目连接 helper 打开，并启用 `PRAGMA foreign_keys=ON` 和一致的 `busy_timeout`。`config.db` 继续使用 DELETE journal 以配合 OneDrive，同步路径上的写事务必须短；`trading.db` 维持 WAL/现有模式。所有 INTEGER 墙钟时间字段统一使用 Unix milliseconds，字段名以 `_ms` 结尾；Outbox 排序只依赖 `ts_ms,event_id`，不依赖 `ts` 字符串排序。`ts` 必须由 `ts_ms` 通过同一个 helper 派生为 UTC RFC 3339；写入时若 `payload_json.ts` 和 `payload_json.ts_ms` 不匹配则 hard fail。

Hash 链在 flush 时计算，范围是单个 JSONL 文件。第一条事件的 `prev_hash` 必须精确等于常量字符串 `sha256:GENESIS`。`hash` 的输入为 Python `json.dumps(event_without_hash, sort_keys=True, separators=(",", ":"), ensure_ascii=False)` 输出的 UTF-8 bytes；输入保留 `prev_hash`，去掉 `hash`，末尾不含换行。解析时拒绝重复 key；恢复工具用同一规则验证，并用 golden-vector fixture 固化序列化结果。字符串值不做语义归一化，hash 绑定原始 Unicode scalar sequence；golden vector 必须覆盖 NFC/NFD、非整数 float、大整数和中文字段。hash 链用于发现常见截断和编辑，不抵御可重写整条链的强攻击者。

Hash 计算和验证由 Python 共享模块负责；TypeScript 只生成 envelope 数据并写 outbox，不计算 hash。TypeScript 写入前必须通过共享 schema/golden fixture 测试，确保 `payload_json` 被 Python `loads -> dumps` 后语义稳定；禁止在 TS 中写超出 JavaScript safe integer 的数值字段，超大整数必须以字符串存储。若未来必须在 TS 侧验证 hash，必须先加入跨语言 golden-vector CI。JSONL 轮转时，每个新物理文件从 `sha256:GENESIS` 开始；跨文件完整性依赖文件 SHA-256 manifest，而不是把 hash 链跨文件延伸。

JSONL manifest 与每个物理 JSONL 文件同目录生成，例如 `config_events.jsonl.manifest.json` 或轮转后的 `<filename>.manifest.json`。manifest 至少包含：`schema_version`、`file_name`、`file_sha256`、`first_event_id`、`last_event_id`、`first_ts_ms`、`last_ts_ms`、`row_count`、`generated_at_ms`、`generator_version`。flush/retention 只有在 manifest 写入临时文件、fsync、atomic replace 并校验 `file_sha256` 后，才允许把对应 outbox rows 视为可清理。manifest 不含 payload、cost/shares 或 token。

### 敏感数据规则

Audit 和 archive 与 DB 备份同级敏感，默认不得提交到 Git。需要更新 `.gitignore` 覆盖：

- `src/data/audit/*.jsonl`
- `src/data/archive/**/*.json`
- `src/data/backups/**/*`

事件 payload 默认只记录恢复所需的最小字段，不记录完整 request body、环境变量、API key、token 或未脱敏文本。真实持仓、成本、股数、策略阈值可记录，但只能出现在 audit/archive 目录，不能写入普通日志。

Audit、archive、backups 只允许服务端代码写入，不提供前端 API 读取接口。创建目录时使用当前用户可读写的保守权限；JSONL 和 DB 备份尽量使用 `0600` 文件权限，目录使用 `0700`。dry-run 默认只展示行数、表名、key 和 hash，不打印 cost/shares/threshold 等敏感值；需要查看明细必须显式传 `--show-sensitive`。这些文件与 DB 备份同敏感等级；默认假设受本机用户权限和磁盘加密保护，若拷贝到外部介质或云盘，必须使用加密归档。

`.gitignore` 不是安全边界。Batch 0 必须增加 pre-commit 和 CI 路径 deny 检查，扫描 staged paths 和 committed tree，拒绝 canonical path 位于 `src/data/audit/`、`src/data/archive/`、`src/data/backups/` 下的任何文件，即使通过 `git add -f` 强制加入。检查逻辑必须做路径规范化，拒绝 symlink/homoglyph/path traversal 绕过。restore/flush 的日志必须使用固定格式和脱敏字段，不能把 SQL bind 值、request body、完整 payload 或敏感 dry-run diff 写到普通日志。

### 日快照

高频数据不逐条追加 JSONL，避免文件膨胀。日切或收盘归档时从 DB 导出：

- `price_snapshots_YYYY-MM-DD.json`
- `market_turnover_YYYY-MM-DD.json`
- `l2_signals_YYYY-MM-DD.json`
- `session_snapshots_YYYY-MM-DD.json`
- `daily_summary_YYYY-MM-DD.json`

归档文件是历史证据，不参与运行时读取。

### Archive Restore Semantics

`archive/YYYY-MM-DD/*.json` 是快照恢复，不按事件顺序 replay：


| Archive 文件                          | 恢复语义                                                       |
| ----------------------------------- | ---------------------------------------------------------- |
| `price_snapshots_YYYY-MM-DD.json`   | 以 `(ts, code)` UPSERT 到 `price_snapshots`；同 key 冲突时以文件内容覆盖 |
| `market_turnover_YYYY-MM-DD.json`   | 以 `ts` UPSERT 到 `market_turnover`                          |
| `l2_signals_YYYY-MM-DD.json`        | 以 `(ts, strategy, code)` UPSERT 到 `signals`                |
| `session_snapshots_YYYY-MM-DD.json` | 以 `(ts, code)` UPSERT 到 `session_snapshots`                |
| `daily_summary_YYYY-MM-DD.json`     | 以 `date` UPSERT 到 `daily_summaries` / `morning_briefings`  |


快照恢复的 idempotency key 是 `source_sha256 + row_key`，不是 JSONL `event_id`。默认只允许恢复单日快照；跨日恢复必须逐日 dry-run/apply。

## 读写流程

### 正常运行

1. Web/API/daemon 读取 DB。
2. 写操作和 audit outbox event 在同一个 DB 事务内完成。
3. Flush 工具将已提交的 outbox event 追加到 audit JSONL。
4. 日切或收盘任务从 DB 导出快照到 `archive/YYYY-MM-DD/`。
5. 启动时不读取旧 JSON，不检查旧 JSON mtime。

### 恢复

新增 CLI：

```bash
uv run python -m src.tools.audit_restore --kind config --from src/data/audit/config_events.jsonl
uv run python -m src.tools.audit_restore --kind config --from src/data/audit/config_events.jsonl --apply
```

恢复规则：

- 默认 dry-run，只展示将写入哪些表、影响哪些记录。
- `--from` 只允许解析到 `src/data/audit/` 或 `src/data/archive/` 目录内；拒绝 symlink、路径逃逸、错误扩展名和 `--kind` 不匹配的文件。
- 按行严格校验 JSONL：最大行长度、必填字段、`schema_version`、`event_id`、`action/entity/db` 枚举、hash 链。
- 恢复幂等：已应用的 `event_id` 记录在 DB，重复 replay 自动跳过。
- `--apply` 前自动备份目标 DB，并输出源文件 SHA-256、目标 DB 路径、当前 git commit。
- dry-run 生成 restore token，绑定 source SHA-256、目标 DB path、目标行 fingerprint 和时间；apply 必须使用该 token。若 source SHA-256 或目标行 fingerprint 改变，必须重新 dry-run。
- apply 尽量在单个 SQLite 事务中完成；开始和结束都写 restore marker，崩溃后可检测未完成恢复。
- 恢复后通过目标 DB 的 outbox 追加 restore audit event，最终进入 `config_events.jsonl` 或 `trading_events.jsonl`。
- 恢复工具是旧 JSON/audit/snapshot 回灌 DB 的唯一入口。

路径校验算法：

1. `--from` 优先按 repo-root-relative 路径解析；路径中出现 `..` 直接拒绝。绝对路径仅在 canonical 后严格位于 repo root 内时允许。
2. 逐级检查 repo root 到目标文件的每个 path component，任一 component 是 symlink 则拒绝；最终文件本身也必须通过 `lstat` 确认不是 symlink。
3. 使用安全打开语义读取文件：macOS 上用 `open` 后 `fcntl(F_GETPATH)` 复核真实路径，Linux 上优先使用 `O_NOFOLLOW`；dry-run 和 apply 必须调用同一个 “materialize restore bytes” 函数，从安全打开的 fd 读取并计算 SHA-256，apply 从同一字节副本或复制到 restore backup 目录的只读副本读取，不重新按路径打开。
4. 对目标文件取 canonical realpath，并按 POSIX `realpath` + Unicode NFC 规范化后检查其严格位于 `src/data/audit/` 或 `src/data/archive/` 下。
5. 文件扩展名必须匹配 `--kind`：事件恢复使用 `.jsonl`，快照恢复使用 `.json`。

Backup 路径使用同级安全规则：`src/data/backups/restore/YYYY-MM-DD/<restore_id>/` 必须从 repo root 逐级创建，拒绝 `..` 和任何 symlink component；最终 DB 备份文件必须用 exclusive create 打开，避免覆盖或跟随 symlink。

Restore token 规则：token 随 dry-run 生成，默认 30 分钟有效，单次 apply 后失效；token 只保存 source SHA-256、target DB、fingerprint hash、restore kind、生成时间和随机 nonce，不保存敏感 payload。CLI UX 固定为 dry-run 输出一次性 token，apply 优先通过 stdin/prompt 传入；`--token` 仅允许本地开发或自动化测试使用，并必须提示 shell history 风险。token 不写入日志、不写入 evidence、不落盘到 repo。生产 apply 还必须要求用户显式确认目标 DB path 和 source SHA-256。

Token 必须使用本机随机 secret 或进程内密钥做 HMAC，不能只是 base64/JSON 拼接；如果实现选择只保存在内存中，也必须保证 token 具有不可预测 nonce，避免被猜测或伪造。

JSONL 校验限制：

- 单行最大 1MB。
- 单个恢复文件默认最大 256MB，单次恢复默认最多 200,000 行；超过需要 `--force-large-restore --reason "<reason>"`，并把执行用户、hostname、path、byte size、line count 和 reason 写入 restore session。
- 嵌套深度最大 16。
- 单个字符串最大 256KB。
- 单个 object 最大 256 个 key，单个 array 最大 10,000 项。
- 未知顶层字段默认拒绝，除非 `schema_version` 的解析器明确允许。

Restore 状态表：

```sql
CREATE TABLE IF NOT EXISTS config_restore_sessions (
  restore_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  source_path TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  target_db TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('started', 'completed', 'failed')),
  phase TEXT NOT NULL DEFAULT 'created'
    CHECK (phase IN ('created', 'backup_verified', 'replay_started', 'verified', 'completed', 'aborted')),
  started_at_ms INTEGER NOT NULL,
  completed_at_ms INTEGER,
  backup_path TEXT NOT NULL,
  git_commit TEXT
);

CREATE INDEX IF NOT EXISTS idx_config_restore_sessions_incomplete
  ON config_restore_sessions(status, started_at_ms)
  WHERE status != 'completed';

CREATE TABLE IF NOT EXISTS config_restore_applied_events (
  event_id TEXT PRIMARY KEY,
  restore_id TEXT NOT NULL,
  source_sha256 TEXT NOT NULL,
  event_hash TEXT NOT NULL,
  applied_at_ms INTEGER NOT NULL,
  FOREIGN KEY (restore_id) REFERENCES config_restore_sessions(restore_id)
);
```

`trading_restore_sessions` 和 `trading_restore_applied_events` 使用同样结构。所有连接启用 `PRAGMA foreign_keys=ON`，测试必须断言该 pragma 为 1。幂等范围是目标 DB 内全局 `event_id`；如果已应用 `event_id` 的 `event_hash` 相同则跳过，不同则 hard fail。恢复顺序：进入维护窗口并停止相关 daemons → 检测 `.pid`、leader lease、API/dev server 写锁和 flush lock 等活跃写者，生产路径发现活跃写者时 fail closed → 使用 dry-run token 绑定的 source bytes → 获取 flush lock 和 SQLite exclusive lock → 在锁内重新计算目标行 fingerprint 并与 token 比对 → 备份 DB 到 `src/data/backups/restore/YYYY-MM-DD/<restore_id>/` → 校验备份可读、size > 0、SHA-256 和 `PRAGMA integrity_check` → 插入 `started` session，phase=`backup_verified` → replay 并记录 applied events，phase=`replay_started` → 对受影响 key 做行数/hash 校验，phase=`verified` → 标记 `completed`，phase=`completed` → 写 restore audit outbox → 释放锁。replay 不把源 JSONL 的每条历史事件重新插入 `*_audit_outbox`；只对本次 restore 操作追加一条 restore audit event，避免 outbox PK 冲突和重复历史。若 apply 阶段 fingerprint 与 dry-run token 不一致，必须重新 dry-run。

崩溃恢复策略：发现超过 10 分钟仍为 `started` 的 session 时，restore CLI 默认拒绝新的 apply，并提示 operator 选择 `--abort-session <id>` 或 `--resume-session <id>`。`--resume-session` 必须先校验 backup SHA-256、source SHA-256、已应用 event 与实际目标行 hash；若 replay 进度和 DB 状态不一致，只允许 operator 从 backup 手动恢复后重新 dry-run。`--abort-session` 只标记 `failed`，不自动回滚 DB，并必须打印“abort 不等于回滚”的下一步提示。

状态机约束：`status='started'` 可配 `created`、`backup_verified`、`replay_started`、`verified`；`status='completed'` 只能配 `completed`；`status='failed'` 只能配 `aborted`。`*_restore_sessions` 和 `*_restore_applied_events` 是全局 replay 幂等账本，默认永久保留最小元数据；cleanup 只能清理 `completed` 或 `failed` 且已超过保留窗口的 backup 文件，不能删除 session/applied-events 行，也不能删除 `started` session 的 backup。

### Bootstrap

新机器或空 DB 不从旧 JSON 自动启动。支持的初始化路径只有：

1. 正常开发/生产已有 DB：运行 `init_db()` 补齐 schema 后直接启动。
2. 空 DB 需要恢复数据：先运行 `audit_restore --kind ... --from ...` dry-run，确认后 `--apply`。
3. 完全新环境无历史数据：运行显式 seed/import 命令创建最小 watchlist/settings，不读取旧运行态 JSON。

Batch 4 完成前，路径 2 不属于可用 bootstrap/DR 能力；此阶段只能使用完整 DB 备份或一次性 seed/import 命令。任何运维 runbook 不得在 Batch 4 evidence 完成前要求 operator 使用 `audit_restore --apply`。

### One-Time Migration Script Contract

一次性导入/迁移脚本是从旧 runtime JSON 进入 DB-only 世界的过渡工具，不是长期 fallback：

- 脚本必须显式声明 `source_json`、`target_db`、`target_tables`、`correlation_id` 和 `schema_version`。
- 执行前必须 dry-run：校验源文件路径、schema、目标 DB fingerprint、预期 upsert/delete 数量，并输出脱敏摘要。
- `source_json` 必须使用与 restore `--from` 同一套 canonical path / symlink / fd-bound materialize validator；如果迁移需要读取 `src/data/` 根旧文件，也必须显式列入允许清单，禁止任意路径输入。
- apply 必须和业务写入同事务写 outbox；失败时不得只写日志。
- 跨 `config.db` 和 `trading.db` 的迁移必须沿用跨 DB 一致性规则：先 config 后 trading、同一 `correlation_id`、第二库失败时写 `partial_failure`，不得声称单事务原子性。
- 每个脚本只能运行一次或幂等运行；幂等 key 必须写入 DB ledger 或 outbox，不能靠“文件已删除”判断。
- 脚本完成后必须删除代码中的自动 JSON fallback，并在 evidence 中记录 reader inventory 清零。
- 生产运行脚本前必须先做 DB 文件备份；备份路径不得写入 committed evidence。

## 跨 DB 一致性

SQLite 不能跨 `config.db` 和 `trading.db` 做单个原子事务。跨库操作必须遵守：

- 优先拆分为单库操作；确实跨库时使用同一个 `correlation_id`。
- 操作顺序默认为先写 `config.db`，再写 `trading.db`，因为真实持仓和用户配置优先。
- 第二库失败时，不回滚第一库；必须写失败日志，并在 audit outbox 中记录 `partial_failure` 事件。
- 恢复工具按 DB 分别恢复；跨库恢复需要用户分别 dry-run/apply 两个 kind，并比对同一 `correlation_id`。

`partial_failure` 记录规则：

- 如果第一库成功、第二库失败，第一库 outbox 记录 `partial_failure`，payload 包含目标第二库、失败阶段和人工处理建议。
- 如果第一库失败，业务操作整体失败，不写第二库。
- 如果两个库各自已有部分事件，两个 outbox 都写同一 `correlation_id` 的状态事件，operator 用该 ID 检索两边记录。

Operator 处理 checklist：暂停相关 daemon → 查询两库同一 `correlation_id` 的 outbox/audit 状态 → 选择重试第二库操作或用 restore 工具回灌 → 验证关键表行数/目标 key → 恢复 daemon。

`partial_failure` envelope 固定为 `action="partial_failure"`，`entity` 为原业务实体，payload 至少包含：

```json
{
  "failed_db": "trading.db",
  "failed_stage": "write_trade_plan_event",
  "succeeded_db": "config.db",
  "recommended_action": "pause_daemons_and_reconcile_by_correlation_id"
}
```

同一 `correlation_id` 下的正常事件仍保留，`partial_failure` 是补充状态事件，不覆盖或删除已成功事件。跨库恢复默认顺序同写入顺序：先 `config`，后 `trading`，除非 dry-run 明确提示反向依赖。

如果第一库业务事务已经提交，`partial_failure` 必须作为第一库的第二个短事务持久化；如果第一库业务事务尚未提交，则业务行和 `partial_failure` 必须同事务提交。禁止只写 stderr 或普通日志。

### API Contract

运行时接口按写入范围分类：


| 类型     | 行为                                                                                                                                   |
| ------ | ------------------------------------------------------------------------------------------------------------------------------------ |
| 单库写入   | 成功返回 2xx；DB 失败返回 5xx/4xx，不写另一库                                                                                                       |
| 有意跨库写入 | 第一库成功、第二库失败时返回 `409 Conflict`，响应必须包含 `success:false`、`partial:true`、`correlation_id`、`succeeded_db`、`failed_db`、`recommended_action` |
| 幂等重试   | 客户端用同一 `correlation_id` 重试时，服务端必须先查询已提交状态，避免重复写第一库                                                                                   |


用户可见 Web UI 不应把内部 payload 展示给前端，只显示“部分成功，需要人工 reconcile”及 `correlation_id`。

`correlation_id` 必须是不可预测 UUID/ULID，不使用时间戳/路径派生。跨库 API 在返回 2xx 前必须确认两库写入都成功；第一库成功、第二库失败只能返回 `409 Conflict`，不得先返回 2xx。409 响应和 APM/server logs 只能包含脱敏字段，不包含 `before/after`、SQL bind 值或完整 payload。

首批需要按此合同分类的操作：


| Route / 操作                                         | DB 范围                                | Partial 行为                   |
| -------------------------------------------------- | ------------------------------------ | ---------------------------- |
| `POST /api/config` add/update/remove/batch         | `config.db`                          | 单库，无 partial                 |
| `POST /api/trade-plans` create/update/delete/reset | `trading.db`，读取 `config.db` 仅用于展示时不写 | 写路径单库，无 partial              |
| 持仓同步 / 导入同时写 config 和 trading event                | `config.db` + `trading.db`           | 跨库，失败返回/记录 partial           |
| future 跨库维护脚本                                      | 视脚本声明                                | 必须显式声明 DB 范围和 correlation_id |


幂等重试查询优先查 `*_audit_outbox` 的 `correlation_id` 索引；自动重试窗口与 flushed outbox retention 相同，默认最长 365 天。outbox 清理后，重试窗口已过，只能通过业务主键/业务行状态、JSONL manifest 和人工 reconcile 判断，不得继续自动重放原请求。

## Rollout Controls

迁移必须通过显式 rollout gate 推进，不允许代码合并后自动进入下一阶段：


| Gate                          | 默认值     | 作用                                                             |
| ----------------------------- | ------- | -------------------------------------------------------------- |
| `JSON_DB_AUDIT_ENABLED`       | `false` | 开启 outbox 写入和 flush，不改变运行读路径                                   |
| `JSON_DB_ONLY_CONFIG`         | `false` | config/watchlist 读路径 DB-only，禁止 JSON fallback                  |
| `JSON_DB_ONLY_RUNTIME`        | `false` | trade plan、tick state、L2、calendar/news、summary 等运行态读路径 DB-only |
| `ALLOW_JSON_MIXED_MODE`       | unset   | 非 rollout gate；仅本地开发允许，生产环境设置后必须 fail fast                     |
| `AUDIT_RESTORE_APPLY_ENABLED` | `false` | 允许 `audit_restore --apply`，默认只 dry-run                         |


Gate 推进顺序固定为：`JSON_DB_AUDIT_ENABLED` → `JSON_DB_ONLY_CONFIG` → `JSON_DB_ONLY_RUNTIME` → `AUDIT_RESTORE_APPLY_ENABLED`。任一 gate 回退必须保留 DB 数据，不允许恢复自动 JSON fallback；回退只关闭新写路径或恢复读旧代码版本。

### Gate Readiness Matrix

每个 gate 只能在对应 evidence 完成后开启，启动脚本和 CI 必须校验 gate 与 batch 状态匹配：


| Gate                          | 最早开启时机               | 开启前必须证明                                                                                             | 必须拒绝开启的状态                                           |
| ----------------------------- | -------------------- | --------------------------------------------------------------------------------------------------- | --------------------------------------------------- |
| `JSON_DB_AUDIT_ENABLED`       | Batch 0 evidence 完成后 | outbox DDL、flush lock、path deny、schema/golden-vector 测试通过                                           | outbox 不存在或 flush 无锁                                |
| `JSON_DB_ONLY_CONFIG`         | Batch 1 evidence 完成后 | `monitor_config.json` / `market_data.json` runtime readers 清零，config/poller/dashboard 删除旧 JSON 后仍工作 | `JSON_DB_AUDIT_ENABLED=false`                       |
| `JSON_DB_ONLY_RUNTIME`        | Batch 2 evidence 完成后 | runtime JSON reader inventory 清零，trade plans/tick/L2/calendar/news/summary 均从 DB 读写                 | `JSON_DB_ONLY_CONFIG=false` 或仍有 runtime JSON reader |
| `AUDIT_RESTORE_APPLY_ENABLED` | Batch 4 evidence 完成后 | restore dry-run/apply、backup、hash/schema/path、active-writer fail-closed 测试通过                        | Batch 4 未完成或生产 writer 未停                            |


CI 可以用 evidence 文件中的 batch id 作为人工检查输入，但不能只检查文件存在；必须同时执行 schema/readers/tests 的机器校验。生产启动时若发现 gate 超前于 evidence 或 reader inventory，必须 fail fast。

### Evidence Package

每个 Batch 完成时必须产出一份 evidence markdown，存到 `docs/superpowers/evidence/YYYY-MM-DD-json-db-batch-N.md`：

- 当前 git commit / branch。
- 开启的 rollout gates。
- `rg` consumer inventory 输出摘要。
- DB schema 检查结果：关键表/索引存在。
- 测试命令和结果。
- 失败/跳过项及原因。
- 手工验证截图或脱敏日志摘要（如适用）。

Evidence 文件不得包含 audit payload、真实成本/股数明细、token、API key、完整 DB 备份路径、restore token、restore session payload 或其他敏感信息。允许记录脱敏后的 restore_id / correlation_id 末 6 位用于关联。

## 迁移批次

### 0. Audit/DDL 基础设施

先建立最小 outbox、flush、restore 状态表、`.gitignore` 和集中 DDL，再移除 JSON fallback。这样后续批次迁移写路径时不会出现“DB-only 但没有 audit”的中间状态。

Batch 0-3 的灾备只支持原始 DB 文件备份恢复；JSONL replay 和 archive restore 在 Batch 4 完成前不作为可用 DR 能力。任何需要 event replay 的上线范围必须等 Batch 4 完成。

Batch 4 完成后的 DR 权威顺序：优先恢复最近的 DB 文件备份；如果需要补齐备份后的逻辑变更，先检查 DB outbox 是否有未 flush 事件并执行 flush，再从 JSONL/Archive 做显式 restore。JSONL 是事件历史，不替代完整 DB 备份。

涉及：

- `src/sim_trading/db.py`
- `src/utils/audit_log.py`
- `web/app/lib/audit.ts`
- `src/tools/audit_flush.py`
- `.gitignore`

改动：

- 集中创建 audit outbox、restore 状态表、运行态目标表。
- 提供 Python/TypeScript 写 outbox 的统一 envelope。
- Flush outbox 到 JSONL，并支持 hash 链。
- 添加 pre-commit/CI 路径 deny，禁止提交 audit/archive/backups。

`web/app/lib/audit.ts` 是 server-only helper，只能被 API routes/server code import；不得导出到 client component 或浏览器 bundle。

TypeScript 允许通过 `better-sqlite3` 直接写 outbox，但必须复用 `web/app/lib/db` 的连接 helper、busy timeout、server-only helper 和同一事务边界；不得绕过 Python/TS 共享 envelope 校验。跨 DB 写入必须由单个 orchestrator 函数管理 correlation_id 和 partial failure，不允许散落在 route handler 中手写双库流程。

Batch 0 验收：

- Fresh `init_db()` 能创建全部新表和索引。
- Python/TypeScript 均能写 outbox envelope，且 schema/golden vector 测试通过。
- Flush lock 双进程测试证明不会重复 append。
- pre-commit/CI 路径 deny 在强制 add 场景下失败。

### 1. 清除自动 fallback

涉及：

- `src/utils/config_reader.py`
- `src/tools/market_data_poller.py`
- `src/tools/stock_monitor.py`
- `src/tools/monitor_lock.py`
- `start_ai_investor_full.sh`
- `CLAUDE.md`、`AGENTS.md`、`docs/CONFIG_API.md`、`docs/MONITORING.md`

改动：

- `read_monitor_config()` 只读 `config.db`。
- poller/watchlist 不再从 `monitor_config.json` fallback。
- `monitor_lock` 不再用 `market_data.json` mtime 判断远程 poller，改用 DB heartbeat/leader 表。
- 启动脚本不再等待 `market_data.json` 更新。
- 文档同步更新 `docs/ARCHITECTURE.md`、`docs/notification-system.md` 和 Git 提交规则，移除 JSON 运行源描述。

新增 `poller_leader_lease` 表，避免与现有 `leader_election(lock_name, hostname, pid, heartbeat)` 发生 `CREATE TABLE IF NOT EXISTS` 静默冲突：

```sql
CREATE TABLE IF NOT EXISTS poller_leader_lease (
  name TEXT PRIMARY KEY,
  holder_id TEXT NOT NULL,
  hostname TEXT NOT NULL,
  pid INTEGER,
  generation INTEGER NOT NULL,
  lease_until_ms INTEGER NOT NULL,
  heartbeat_ts_ms INTEGER NOT NULL,
  CHECK (lease_until_ms >= heartbeat_ts_ms)
);
```

poller 是 `market_data_poller` lease 的唯一续约方。续约和接管都必须用 `BEGIN IMMEDIATE` 序列化；TTL 必须大于 poller 周期且小于人工感知故障窗口，例如续约间隔 30 秒、TTL 90 秒。新 holder 接管时递增 `generation`，日志记录接管原因。判断是否持有 lease 只看 `generation` 和 `lease_until_ms`，不能依赖 PID。

Batch 1 验收：

- 移除 `monitor_config.json`、`market_data.json` 前，Web/API consumer inventory 对这两个文件必须已清零；删除后 config reader、poller、notifier 和 dashboard 基础读路径仍工作。
- `monitor_lock` 不再读取 `market_data.json`，只使用 `poller_leader_lease`。
- 文档中不再声明 `market_data.json` 或 `monitor_config.json` 是运行态输入。

现有 `leader_election` 表继续归原有 monitor lock/legacy 逻辑所有；新 `poller_leader_lease` 只服务 market data poller 的远程活跃检测。Batch 1 完成后，不再用 `leader_election` 判断 market data freshness。

### 2. 迁移残留 JSON 运行源

涉及：

- `src/tools/tick_monitor.py`
- `src/tools/daily_summary_generator.py`
- `src/tools/stock_notifier.py`
- `src/tools/sector_index_engine.py`
- `src/tools/trading_calendar.py`
- `src/tools/news_crawler.py`
- `web/app/api/trade-plans/route.ts`

改动：

- `tick_monitor` 从 `trade_plans` 读取 `scope='tick_monitor'` 的计划，从 `tick_monitor_state` 读写冷却状态。
- `daily_summary_generator` 从 DB 读取 trade plans、L2 信号和日报，不再读 `trade_plans.json`。
- `stock_notifier` 的 panic 配置进入 DB，不再读 `alert_config.json`。
- `l2_strategy_config.json`、`signal_rules.json` 迁入 DB；L2 daemon 不再读文件作为运行配置。
- `sector_config.json` 中仍有用的默认配置迁入 DB 或代码默认值；运行态只读 DB。
- calendar/news cache 改为 DB cache 表。
- `/api/trade-plans` 移除 `migrateScopeFromJson()` 的运行时 JSON 读取。
- `screenshot_stock_import`、`monitor_config_db_migrator export` 等非 API 写 JSON 路径改为写 DB + outbox；显式 export 只能写 audit/archive 快照，不能更新运行态根 JSON。

Batch 2 验收：

- `rg` 清单中不再有运行态代码读取范围内 JSON 文件；测试 fixture 和 docs 示例除外。
- 删除或改名范围内旧 JSON 文件后，tick monitor、L2 daemon、daily summary、calendar/news cache 不因文件缺失失败。
- `/api/trade-plans` 和相关 daemon 对 `scope='tick_monitor'` 的计划读取一致。

### 3. 建立 archive/export 层

新增归档工具：

- `src/tools/db_snapshot_exporter.py`

职责：

- 按日期从 DB 导出行情、信号、日报等快照。
- 写入临时文件后原子 replace。
- 保留现有 90 天归档清理策略。

Exporter 代码可以在 PR 5 合并，但定时任务/生产自动导出默认关闭；必须等 Batch 2 evidence 完成并确认 `JSON_DB_ONLY_RUNTIME=true` 后才允许开启。手工 dry-run 可以提前执行，但输出必须标记为非权威，不得进入正式 archive retention/DR 流程。

旧的根目录单文件 snapshot（例如 `monitor_config.json`）不再作为运行态热备。替代方式是：DB 文件本身作为主备份，audit JSONL 作为可 replay 的事件历史，`db_snapshot_exporter.py` 只向 `archive/YYYY-MM-DD/` 写人工查看/恢复用快照。

Batch 3 验收：

- 每类日快照可从 DB 导出，并通过 dry-run 验证能按定义的 UPSERT key 恢复。
- exporter 使用临时文件 + atomic replace；备份工具忽略临时文件。
- archive retention 和压缩备份策略可重复运行且幂等。

### Mixed Mode 约束

Batch 0 必须先部署。Batch 1 完成后，config/poller/leader 已 DB-only，但 tick monitor、L2、calendar/news cache 等可能仍短期读 JSON；这段 mixed mode 只能用于本地验证，不作为长期生产状态。Batch 2 完成前禁止删除仍被迁移清单标记为“未完成”的 JSON 文件；Batch 2 完成后回归测试必须证明旧 JSON 缺失不影响运行。

Mixed Mode 是对“DB-only 运行”目标态的临时例外，只允许在开发/本地验证环境出现；生产部署必须以 Batch 2 完成作为 DB-only 出口条件。

启动脚本/部署检查应支持 `ALLOW_JSON_MIXED_MODE=1` 作为本地开发开关；生产环境未完成 Batch 2 时必须 fail fast。

所有 Python daemon 入口和 Next.js server 启动检查都必须尊重该开关：生产环境检测到仍有运行态 JSON reader 清单未清零时直接退出。

### 4. 恢复工具和测试

新增：

- `src/tools/audit_restore.py`
- Python 单元测试覆盖 dry-run、backup、apply。
- Web route tests 覆盖 config/trade plan 写入后产生 audit event。
- 回归测试覆盖旧 JSON 缺失或过期不会影响运行。

Batch 4 验收：

- Restore CLI 默认 dry-run；生产 apply 在活跃 writer 存在时 fail closed。
- JSONL replay 和 archive snapshot restore 均覆盖 happy path、重复 replay、hash/schema 错误、路径逃逸和 symlink。
- `--abort-session`、`--resume-session` 的提示和状态迁移有测试覆盖。

## 验证策略

- 删除或改名旧 `monitor_config.json`、`trade_plans.json`、`market_data.json` 后，poller、notifier、tick monitor、dashboard 仍能从 DB 正常工作。
- DB 不可用时明确报错，不隐式读取 JSON。
- 每次 config/trade plan/tick state 写入都在同事务产生 outbox event，flush 后有 JSONL event。
- 日归档能从 DB 重建当天关键快照。
- Fresh DB init 能创建全部运行态表，不依赖 route/tool 懒建表。
- 恢复工具拒绝路径逃逸、symlink、错误 schema、超大 JSONL 行、hash 链断裂和重复 destructive apply。
- 恢复 apply 需要维护窗口或 exclusive lock；目标 DB 与 dry-run fingerprint 不一致时拒绝 apply。
- 并发写入 audit outbox 与 flush 不产生重复或乱序 JSONL。
- Flush 双进程竞争不会重复 append；外键 pragma、busy_timeout、flush lock 均有测试覆盖。
- pre-commit/CI 拒绝提交 `src/data/audit/`、`src/data/archive/`、`src/data/backups/` 下文件。
- Archive restore 对每类快照的 UPSERT/冲突规则有测试覆盖。
- `uv run pytest src/sim_trading/test_market_data_poller.py -q`
- `uv run pytest src/sim_trading/test_sim_trading.py -m smoke -q`
- `cd web && npm test -- trade-plans metrics`

### Test Matrix

实现时至少覆盖以下测试层级：


| 层级             | 必测内容                                                                                             | 示例                                  |
| -------------- | ------------------------------------------------------------------------------------------------ | ----------------------------------- |
| Unit           | envelope schema、hash golden vector、path validator、gate parser、timestamp helper、DB upsert key     | Python pytest + TS unit tests       |
| DB integration | fresh `init_db()`、pragma、DDL indexes、outbox same-transaction rollback、restore ledger idempotency | temp SQLite DB                      |
| Concurrency    | 双 flush 进程、API 写入与 flush 并发、poller lease takeover、restore active-writer fail-closed              | multiprocessing / subprocess        |
| Migration      | 旧 JSON dry-run/import、重复导入、schema 错误、reader inventory 清零                                         | fixture JSON + temp repo            |
| Web/API        | `/api/config`、`/api/trade-plans`、409 partial response、server-only DB helper import boundary      | Next test runner                    |
| E2E smoke      | 删除旧 runtime JSON 后启动 poller/notifier/dashboard，验证 DB-only 读路径                                    | local start script + smoke commands |
| Security       | path traversal、symlink、oversized JSONL、token redaction、CI path deny forced-add                   | pytest + shell hook tests           |


每个 PR 只需跑与本 PR 范围相关的矩阵子集，但 Batch evidence 必须汇总对应 batch 的完整结果。测试 fixture 可保留 JSON，但文件名和路径必须明确在 `tests/fixtures` 或临时目录下，不能放在运行态根路径伪装成 fallback。

## Implementation Task Boundaries

后续 implementation plan 必须按以下边界拆任务，不能把所有迁移压进一个大 PR：


| PR / Task                      | 范围                                                                     | 必须先完成     |
| ------------------------------ | ---------------------------------------------------------------------- | --------- |
| 1. Schema + connection pragmas | `db.py` 集中 DDL、连接 helper、foreign_keys/busy_timeout、rollout gate 读取     | 无         |
| 2. Audit envelope/outbox/flush | Python/TS envelope、outbox writes、flush lock、hash chain、path deny       | PR 1      |
| 3. Config DB-only              | `config_reader`、poller watchlist、monitor lock、docs 同步                  | PR 1-2    |
| 4. Runtime JSON readers        | tick monitor、trade plans、L2 config/signals、calendar/news、daily summary | PR 1-3    |
| 5. Archive exporter            | DB snapshot export、archive retention、snapshot dry-run restore schema   | PR 1-2    |
| 6. Restore CLI                 | `audit_restore` dry-run/apply、tokens、backup, replay, session state     | PR 1-2, 5 |
| 7. Cleanup docs/tests          | consumer inventory 清零、old JSON docs 清理、evidence 汇总                     | PR 1-6    |


每个任务必须保持系统可启动；跨任务临时 mixed mode 只允许本地 gate 显式开启。PR 之间不得引入“读 DB 但无审计写入”的新写入口。

PR 与 Batch 的关系：


| Batch   | 对应 PR / Task | Gate                                   |
| ------- | ------------ | -------------------------------------- |
| Batch 0 | PR 1-2       | `JSON_DB_AUDIT_ENABLED`                |
| Batch 1 | PR 3         | `JSON_DB_ONLY_CONFIG`                  |
| Batch 2 | PR 4         | `JSON_DB_ONLY_RUNTIME`                 |
| Batch 3 | PR 5         | 无新 runtime gate；为 restore/archive 能力准备 |
| Batch 4 | PR 6         | `AUDIT_RESTORE_APPLY_ENABLED`          |
| Cleanup | PR 7         | 无新 gate；清理文档、旧示例和 evidence             |


PR 可以在 gate 关闭的前提下提前合并，但 Batch evidence 必须按 Batch 0 → 1 → 2 → 3 → 4 顺序完成。Batch 3 没有新 gate；runbook 只验证 archive/export 能力和 evidence，不执行 gate flip。Batch 4 的 restore apply gate 不得因为 PR 6 已合并而开启，必须等 Batch 4 evidence 完成。

## Release Runbook

每个 batch 上线必须按固定顺序执行：

1. 部署代码但保持新 gate 关闭。
2. 运行 schema check、reader inventory、相关测试矩阵。
3. 生成 batch evidence。
4. 发布/共享 evidence 前运行脱敏检查，确认没有 audit payload、完整 backup path、token、真实成本/股数、SQL bind 或 restore payload。
5. 在维护窗口或低流量时开启对应 gate；Batch 3 无 gate，此步骤记为 N/A。
6. 观察 outbox pending rows、flush lag、SQLite busy/locked 错误、API 409 partial 计数、poller lease generation、restore ledger row count / DB size。
7. 若指标异常，按 Rollback 关闭 gate 或恢复代码；不得直接恢复 JSON fallback。

Runbook 必须声明 owner、开始/结束时间、当前 batch、gate 变更、回退条件和验证命令。任何跨 DB 操作或 restore apply 必须额外记录 `correlation_id` / 脱敏 `restore_id`。指标阈值沿用前文 SLO：flush lag 超过 5 分钟、pending rows 超过 10,000、SQLite busy/locked 持续出现、409 partial 非零且无法 reconcile、poller generation 异常跳变，均进入暂停或回退判断。restore ledger row count / DB size 没有固定失败阈值，但必须记录趋势；异常增长需要暂停 restore/import 类操作并排查。

## Rollback

Rollback 分为代码回退和数据回退：

- **代码回退**：关闭当前 rollout gate，回退到上一批代码；保留已写入 DB 表和 outbox，不删除新表。
- **数据回退**：只通过 DB 文件备份或 `audit_restore` 执行；禁止手工编辑 JSON/SQLite。
- **flush 回退**：如果 JSONL flush 发现 hash 链异常，先停 flush，保留 outbox pending rows，修复或恢复 JSONL 后再继续；不得跳过 hash 检查强行标记 `flushed_at`。
- **mixed mode 回退**：仅本地使用 `ALLOW_JSON_MIXED_MODE=1` 重新验证旧读路径；生产不允许用 mixed mode 作为长期回退。

Rollback evidence 必须记录触发原因、关闭的 gates、恢复的 DB 备份或 restore session、验证命令结果。

## 不做的事

- 不迁移 `stocks/` 下研究资料 JSON。
- 不把 DB 内部 JSON 字符串字段拆成多表，除非后续查询需求明确需要。
- 不保留自动 JSON fallback。
- 不引入新的外部数据库或消息队列。
- 不改变 Web Dashboard 的用户可见接口。
- 不保证 JSONL 是 OS 级不可篡改介质；hash 链只用于检测常见截断/编辑，真正不可篡改备份另行设计。

## 风险与处理


| 风险                    | 处理                                                    |
| --------------------- | ----------------------------------------------------- |
| 一次迁移范围大               | 按批次实施，每批独立验证                                          |
| Audit 写失败导致误以为已审计     | 业务事务同库写 outbox，flush 失败可重试并报警                         |
| 高频快照文件过大              | 只做日快照，不逐 tick 写 JSONL                                 |
| 旧脚本仍读 JSON            | 迁移批次 1 和 2 用搜索回归测试兜底                                  |
| 恢复覆盖真实持仓              | 恢复默认 dry-run，`--apply` 前备份 DB，并记录 restore event       |
| Audit/Archive 泄露持仓和策略 | `.gitignore` 禁止提交，payload 最小化，普通日志不输出敏感字段             |
| 恢复文件被篡改或误选            | 路径白名单、schema 校验、hash 链、源文件 SHA-256 和 apply 前确认        |
| 多 poller split-brain  | `poller_leader_lease` lease + generation，TTL 过期后才允许接管 |
| JSONL/outbox 无限增长     | outbox flushed rows 和 JSONL 按 retention 清理，清理前保留压缩备份  |


## Retention


| 数据                                                     | 保留策略                                         |
| ------------------------------------------------------ | -------------------------------------------- |
| DB 热数据：`price_snapshots`、`signals`、`session_snapshots` | 180 天，沿用现有清理方向                               |
| DB 告警：`alert_events`                                   | 30 天，沿用现有清理方向                                |
| Audit JSONL                                            | 默认 365 天；超过后先导出压缩备份再清理                       |
| 日快照 archive                                            | 默认 90 天；需要长期保留时移动到人工归档目录                     |
| Restore 自动 DB 备份                                       | 默认保留最近 10 份或 5GB，先到先清                        |
| Restore session / applied-events ledger                | 默认永久保留最小元数据，用于全局 `event_id` 幂等               |
| Audit outbox flushed rows                              | `flushed_at` 超过 365 天后清理；未 flush rows 永不自动清理 |


Restore 备份路径固定为 `src/data/backups/restore/YYYY-MM-DD/<restore_id>/`，该目录整体不提交 Git。

Retention 清理顺序：确认 JSONL 已 fsync 且压缩备份/manifest 已写入后，才允许删除对应 flushed outbox rows；restore backup 只有在关联 session 为 `completed` 或 `failed` 且超过保留窗口后才允许清理。restore session/applied-events ledger 不随 backup 清理删除，否则会破坏重复 replay 的全局幂等性。

运维监控需要跟踪 restore session/applied-events ledger 的 row count 和 DB size；ledger 默认不清理，但如果未来必须做隐私/合规删除，必须先设计替代幂等账本，不能直接删除现有行。

SQLite 维护：删除大量 flushed outbox rows 后，按月运行受控 `VACUUM` 或 `incremental_vacuum`（取决于 DB pragma）回收文件空间；未 flush rows 不参与 vacuum 清理条件。