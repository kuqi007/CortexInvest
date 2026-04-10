# K线数据简单方案

## 最简单的方案：只用 OneDrive，但分开存储

```
OneDrive/ai-investor-data/
├── sim_trading.db              # 实时数据（alerts、持仓）- 本地优先
├── klines/
│   ├── daily_kline.db          # 日K线数据 - 只读
│   ├── price_snapshots.db      # 价格快照 - 只读
│   └── sector_daily.db         # 板块数据 - 只读
└── sync_status.json            # 同步状态标记
```

## 核心思路

**K线数据 vs 实时数据 分开处理**：

| 数据类型 | 特点 | 处理方式 |
|---------|------|---------|
| **Alerts、持仓** | 实时、经常变更 | 本地SQLite为主（避免OneDrive冲突） |
| **K线历史** | 只读、不常变更 | 直接放OneDrive（多台机器共享读取） |

## 实施步骤

### 1. 分离数据库（5分钟）

```bash
# 在生产机执行：导出K线数据到单独文件
cd ~/OneDrive\ -\ Autodesk/ai-investor-data
mkdir -p klines

# 导出日K线
sqlite3 sim_trading-ADSKX21K67LYQL.db <<EOF
ATTACH DATABASE 'klines/daily_kline.db' AS klines;
CREATE TABLE klines.daily_kline AS SELECT * FROM daily_kline;
CREATE INDEX klines.idx_kline_date ON daily_kline(date);
CREATE INDEX klines.idx_kline_code ON daily_kline(code);
DETACH DATABASE klines;
EOF

# 导出板块数据
sqlite3 sim_trading-ADSKX21K67LYQL.db <<EOF
ATTACH DATABASE 'klines/sector_daily.db' AS klines;
CREATE TABLE klines.sector_daily AS SELECT * FROM sector_daily;
DETACH DATABASE klines;
EOF

echo "✓ K线数据已分离"
```

### 2. Web端同时连接两个数据库

```typescript
// web/app/lib/db.ts
import { join } from "path";

// 实时数据（本地，避免OneDrive冲突）
export const REALTIME_DB_PATH = join(
  process.env.HOME || "~",
  ".ai-investor-data-local",
  "sim_trading.db"
);

// K线历史数据（OneDrive共享）
export const KLINE_DB_PATH = join(
  process.cwd(),
  "..",
  "src",
  "data",
  "klines",
  "daily_kline.db"
);
```

### 3. 查询时自动选择数据库

```typescript
// 查询实时数据（持仓、alerts）
const realtimeDb = new Database(REALTIME_DB_PATH);
const alerts = realtimeDb.prepare("SELECT * FROM alert_events WHERE date = ?").all(today);

// 查询历史K线（只读，不冲突）
const klineDb = new Database(KLINE_DB_PATH, { readonly: true });
const klines = klineDb.prepare("SELECT * FROM daily_kline WHERE code = ?").all(code);
```

## 为什么这样不会冲突？

| 场景 | 原方案（单库） | 新方案（分库） |
|------|--------------|---------------|
| 生产机写入Alert | ✗ 触发OneDrive同步 | ✓ 写本地库，无同步 |
| 本机读取K线 | ✗ 可能读到冲突文件 | ✓ K线库只读，安全 |
| 生产机写入K线 | ✗ 增加冲突风险 | ✓ 夜间批量，一次性 |

## 数据更新机制

```
每日收盘后（15:30）：
1. 生产机将当日K线追加到 klines/daily_kline.db
2. OneDrive自动同步（K线库只读，不会冲突）
3. 本机第二天自动获取最新K线数据
```

## 优势

- ✅ **简单**：不需要新服务、不需要云存储
- ✅ **免费**：继续用OneDrive
- ✅ **不丢失**：OneDrive自动版本控制
- ✅ **跨设备**：K线库只读，多台机器共享
- ✅ **无冲突**：实时数据本地处理

## 文件大小预估

```
sim_trading.db（实时）        ~3MB  → 本地存储
klines/daily_kline.db         ~10MB → OneDrive共享
klines/sector_daily.db        ~0.5MB → OneDrive共享
─────────────────────────────────────────────
总计                          ~13MB
```

## 下一步

需要我帮你：
1. 导出分离K线数据库？
2. 修改Web代码支持双数据库连接？
3. 配置生产机自动每日更新K线？
