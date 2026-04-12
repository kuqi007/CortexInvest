# SQLite 双库分离设计

## 目标

将 `sim_trading.db` 拆为 `config.db` + `trading.db`，解决 OneDrive 同步 Config 数据延迟问题。

## 背景

当前所有数据在一个 `sim_trading.db`（16MB，26 张表）中，通过 OneDrive 同步到两台机器。WAL 模式下写入先到 `.db-wal` 文件，OneDrive 只同步主 `.db`，导致另一台机器看不到 Config 变更（如标签更新）。已用 checkpoint 缓解，但属于 workaround。

## 设计

### 库划分

**config.db** — `src/data/config.db`（OneDrive 同步）

- Journal: DELETE（每次写直接落盘，OneDrive 秒级同步）
- 大小: ~24KB，几乎不变
- 表（4 张）:


| 表                   | 写入方                         | 说明       |
| ------------------- | --------------------------- | -------- |
| monitor_watchlist   | Web API                     | 自选/持仓/标签 |
| monitor_settings    | Web API                     | 全局设置     |
| tag_meta            | Web API (config + sector)   | 标签/板块元数据 |
| position_change_log | Web API, futu_position_sync | 持仓变更记录   |


**trading.db** — `src/data/trading.db`（OneDrive 同步，分钟级延迟可接受）

- Journal: WAL（高性能读写）
- Checkpoint: poller 每轮循环后（~30s）
- 大小: ~16MB，持续增长
- 表（21 张）:


| 表                  | 写入方                                                | 说明              |
| ------------------ | -------------------------------------------------- | --------------- |
| signals            | signal_archiver                                    | 历史信号            |
| price_snapshots    | signal_archiver                                    | 价格快照            |
| session_snapshots  | signal_archiver                                    | 会话快照            |
| live_state         | realtime_engine, futu_position_sync                | 实时持仓状态          |
| trades             | realtime_engine, futu_position_sync, replay_runner | 交易记录            |
| daily_pnl          | futu_position_sync, replay_runner                  | 每日盈亏            |
| futu_orders        | futu_position_sync                                 | Futu 订单         |
| daily_kline        | kline_fetcher                                      | K线缓存            |
| indicator_cache    | indicator_alert_engine                             | 指标缓存（由外挂模块创建）   |
| market_amo_history | market_data_poller                                 | 集合竞价历史          |
| alert_events       | stock_notifier, realtime_engine                    | 告警事件            |
| trade_plan_events  | stock_notifier                                     | 交易计划事件          |
| sector_rotation    | sector_index_engine, Web API sector                | 板块轮动            |
| sector_daily       | sector_index_engine, Web API sector                | 板块日线            |
| stock_daily        | sector_index_engine                                | 个股日线            |
| sector_alerts      | sector_index_engine, Web API sector                | 板块告警            |
| daily_l2_digest    | daily_summary_generator                            | 日报 L2 摘要        |
| leader_election    | monitor_lock                                       | 多机锁（由外挂模块创建）    |
| param_versions     | replay_runner, scoring_backtester                  | 参数版本            |
| optimization_runs  | scoring_backtester                                 | 优化记录            |
| connect_flow_cache | —                                                  | 连接流程缓存（由测试脚本创建） |


*注：原设计提到了 `lost_and_found`，但实际代码库中并无此表，迁移脚本中应确认是否忽略。*

### DB 路径常量

**Python (`src/sim_trading/db.py`)**:

```python
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "src" / "data"

CONFIG_DB_PATH = DATA_DIR / "config.db"
TRADING_DB_PATH = DATA_DIR / "trading.db"

# Legacy path for migration detection
LEGACY_DB_PATH = DATA_DIR / "sim_trading.db"
```

**TypeScript (`web/app/lib/db.ts`)**:

```typescript
import { join } from "path";

const DATA_DIR = join(process.cwd(), "..", "src", "data");

export const CONFIG_DB_PATH = join(DATA_DIR, "config.db");
export const TRADING_DB_PATH = join(DATA_DIR, "trading.db");
```

### 连接函数与初始化

**Python `get_connection()` 与 `init_db()`**:

原单体 `SCHEMA` 和 `init_db` 将被拆分：

- 增加 `CONFIG_SCHEMA` 和 `TRADING_SCHEMA`
- 分别实现 `init_config_db()` 和 `init_trading_db()` 执行对应的初始化建表和字段迁移。

```python
def get_config_connection() -> sqlite3.Connection:
    """Config DB — DELETE mode for OneDrive sync."""
    conn = sqlite3.connect(str(CONFIG_DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn

def get_connection() -> sqlite3.Connection:
    """Trading DB — WAL mode for performance. (existing, just change path)"""
    conn = sqlite3.connect(str(TRADING_DB_PATH), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn
```

**TypeScript**:

```typescript
import Database from "better-sqlite3";

export function openConfigDb(readonly = false) {
  const db = new Database(CONFIG_DB_PATH, { readonly });
  if (!readonly) db.pragma("journal_mode = DELETE");
  db.pragma("busy_timeout = 15000");
  return db;
}

export function openTradingDb(readonly = false) {
  const db = new Database(TRADING_DB_PATH, { readonly });
  if (!readonly) db.pragma("journal_mode = WAL");
  db.pragma("busy_timeout = 15000");
  return db;
}
```

### 代码改动范围

#### Python 端


| 文件                                        | 改动                                                                                                                  |
| ----------------------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `src/sim_trading/db.py`                   | 拆分 SCHEMA/init_db，新增 config/trading 路径常量和连接函数                                                                       |
| `src/utils/config_reader.py`              | 改连 config.db                                                                                                        |
| `src/tools/monitor_lock.py`               | leader_election 仍在 trading.db                                                                                       |
| `src/tools/screenshot_stock_import.py`    | 改连 config.db                                                                                                        |
| `src/tools/monitor_config_db_migrator.py` | 改连 config.db                                                                                                        |
| `src/sim_trading/futu_position_sync.py`   | trades/daily_pnl/futu_orders → trading.db, position_change_log → config.db。**注意：此处为跨库写入，需注意部分失败的处理（日志告警等），无分布式事务。** |
| `src/tools/indicator_alert_engine.py`     | 移除硬编码 `sim_trading.db`，改连 trading.db                                                                                |


其余 Python 文件（poller, notifier, signal_archiver, sector_engine 等）已通过 `db.get_connection()` 连接，自动切到 trading.db。测试脚本中硬编码的路径视情况修改。

#### TypeScript 端


| 文件                                         | 改动                                                              |
| ------------------------------------------ | --------------------------------------------------------------- |
| `web/app/lib/db.ts`                        | 导出两个路径 + 两个 open 函数                                             |
| `web/app/api/config/route.ts`              | 改用 `openConfigDb()`，移除 checkpointForSync（DELETE 模式不需要）          |
| `web/app/api/sector/route.ts`              | tag_meta → config.db, sector_daily/rotation/alerts → trading.db |
| `web/app/api/metrics/route.ts`             | 改连 trading.db                                                   |
| `web/app/api/trade-plans/route.ts`         | 改连 trading.db                                                   |
| `web/app/api/position-change-log/route.ts` | 改连 config.db                                                    |
| `web/app/api/close-events/route.ts`        | 移除硬编码 `SIM_DB_PATH`，改连 trading.db                               |
| `web/app/api/sim/route.ts`                 | 修改 `SIM_DB_PATH` 引用，改连 trading.db                               |


### Checkpoint 策略

trading.db 定时 checkpoint，确保 OneDrive 同步主文件：

- **Python**: `market_data_poller.py` 每轮循环结束后 `PRAGMA wal_checkpoint(TRUNCATE)`
- **Web**: sector API 写入后 checkpoint
- **不覆盖场景**: 信号归档、K线写入等低频操作，等 poller 下轮一起 checkpoint 即可

### 迁移脚本

`scripts/migrate_split_db.py`，基于文件复制而非逐表 INSERT：

1. 检测 `sim_trading.db` 是否存在，已迁移则跳过（幂等）
2. Checkpoint 旧库 WAL，确保数据完整
3. 复制 `sim_trading.db` → `config.db`，然后 `DROP TABLE` 删除 trading 相关表，**最后执行 `VACUUM` 收缩 config.db 文件体积**
4. 复制 `sim_trading.db` → `trading.db`，然后 `DROP TABLE` 删除 config 相关表
5. 设置各自 journal mode（config: DELETE, trading: WAL）
6. 备份旧库为 `sim_trading.db.pre-split.bak`
7. 验证两库行数与旧库一致，输出迁移报告

优势：文件复制秒级完成（16MB），避免逐行 INSERT 大表（daily_kline 5万行）

### 启动脚本改动

`start_ai_investor_full.sh`:

- 在启动服务前运行迁移脚本（幂等，已迁移则跳过）

### 回退方案

1. 旧 `sim_trading.db` 保留为 `.pre-split.bak`
2. 代码中有 `LEGACY_DB_PATH` 兼容：如果新库不存在但旧库存在，自动触发迁移
3. 手动回退：`cp sim_trading.db.pre-split.bak sim_trading.db` + 切回旧代码

### 测试策略

- 现有 138 个测试中，由于拆分成双库，凡直接 `get_connection()` 或测试直接操作 SQLite 的场景，可能需要同时 override `config.db` 和 `trading.db`，或改用两份 `:memory:` 数据库重构 fixture。
- 新增迁移脚本测试：验证拆分后行数一致
- 手动验证：config.db 标签更新后另一台机器立即可见

### 不做的事

- 不改表结构、不改 API 接口
- 不引入新依赖
- 不做实时双向同步（OneDrive 单向足够）

