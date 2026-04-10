# K线数据存储方案：不丢失 + 跨设备可用

## 数据规模分析

| 表 | 行数 | 大小 | 特点 |
|-----|------|------|------|
| daily_kline | 50K | ~10MB | 日K线，只读，长期保存 |
| price_snapshots | 24K | ~2MB | 30秒快照，可定期清理 |
| sector_daily | 3.4K | ~0.5MB | 板块日线，只读 |

**总计：约 12MB**（非常小，易于管理）

---

## 推荐方案

### 方案 1：分层存储（推荐 ⭐⭐⭐⭐⭐）

```
┌─────────────────────────────────────────────────────────────┐
│                        生产机 (Mac Studio)                   │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ 实时数据      │    │ 本地SQLite    │    │ 导出历史     │  │
│  │ (price_snap) │───▶│ (全量数据)    │───▶│ (Parquet)   │  │
│  └──────────────┘    └──────────────┘    └──────┬───────┘  │
│                                                  │          │
└──────────────────────────────────────────────────┼──────────┘
                                                   │
                              ┌────────────────────▼──────────┐
                              │     对象存储（S3/R2）         │
                              │  - klines/daily/YYYY-MM.parquet│
                              │  - klines/snapshots/...       │
                              │  - 版本控制、不丢失            │
                              └────────────────────┬──────────┘
                                                   │
┌──────────────────────────────────────────────────┼──────────┐
│                     本机 (MacBook Pro)            │          │
│  ┌──────────────┐    ┌──────────────┐           │          │
│  │ 实时数据      │◀───│ 本地缓存      │◀──────────┘          │
│  │ (alerts)     │    │ (最近30天)    │                      │
│  └──────────────┘    └──────────────┘                      │
│                                                              │
│  历史查询：直接读取 S3/R2 (按需下载)                           │
└──────────────────────────────────────────────────────────────┘
```

#### 实施步骤

**1. 实时数据（SQLite）**
```python
# 继续用现有 SQLite 存储最近 30 天数据
# 生产机和本机各自维护自己的 SQLite（不同数据）
```

**2. 历史数据导出（生产机 Cron）**
```bash
#!/bin/bash
# 每天凌晨导出前一天的K线数据

DATE=$(date -d "yesterday" +%Y-%m-%d)
YEAR_MONTH=$(date -d "yesterday" +%Y-%m)

# 导出为 Parquet（压缩率高，查询快）
python3 << EOF
import sqlite3
import pandas as pd

conn = sqlite3.connect("$HOME/OneDrive - Autodesk/ai-investor-data/sim_trading.db")

# 导出 daily_kline
df = pd.read_sql_query(
    "SELECT * FROM daily_kline WHERE date = '$DATE'", 
    conn
)
df.to_parquet(
    f"$HOME/ai-investor-exports/daily_kline/{YEAR_MONTH}/{DATE}.parquet",
    compression='zstd'
)

# 导出 price_snapshots（可选，数据量大）
df = pd.read_sql_query(
    "SELECT * FROM price_snapshots WHERE date = '$DATE'",
    conn
)
df.to_parquet(
    f"$HOME/ai-investor-exports/price_snapshots/{YEAR_MONTH}/{DATE}.parquet",
    compression='zstd'
)

conn.close()
EOF

# 上传到 S3/R2
# rclone copy $HOME/ai-investor-exports/ s3:ai-investor-klines/
```

**3. 对象存储选择**

| 服务 | 免费额度 | 成本 | 推荐度 |
|------|---------|------|--------|
| **Cloudflare R2** | 10GB/月 | $0.015/GB | ⭐⭐⭐⭐⭐ |
| **AWS S3** | 5GB/12月 | $0.023/GB | ⭐⭐⭐⭐ |
| **Backblaze B2** | 10GB/天 | $0.006/GB | ⭐⭐⭐⭐ |
| **腾讯云 COS** | 50GB/月 | ¥0.15/GB | ⭐⭐⭐ |

**R2 配置示例**（推荐，免费10GB够用）：
```bash
# 安装 rclone
brew install rclone

# 配置 R2
rclone config
# > n) New remote
# > name: r2
# > type: s3
# > provider: Cloudflare
# > access_key_id: xxx
# > secret_access_key: xxx
# > endpoint: xxx.r2.cloudflarestorage.com

# 上传
rclone sync ~/ai-investor-exports/ r2:ai-investor-klines/
```

**4. 本机读取历史数据**
```python
import pandas as pd

# 直接读取云端 Parquet（按需）
def get_kline_history(code: str, start_date: str, end_date: str):
    # 构建 S3 URL
    urls = []
    for month in get_months_between(start_date, end_date):
        urls.append(f"s3://ai-investor-klines/daily_kline/{month}/*.parquet")
    
    # 使用 pandas 读取（支持过滤下推）
    df = pd.read_parquet(
        urls,
        filters=[('code', '=', code)],
        columns=['date', 'open', 'high', 'low', 'close', 'volume']
    )
    return df
```

#### 优势
- ✅ **不丢失**：对象存储 99.999999999% 持久性
- ✅ **跨设备**：任何机器都能通过 API 访问
- ✅ **成本低**：10GB 免费，K线数据压缩后极小
- ✅ **性能好**：Parquet 列式存储，查询快
- ✅ **版本控制**：对象存储天然支持版本

---

### 方案 2：Turso 重新启用（只读副本）

```
生产机 ──► 本地SQLite ──► 每日批量导入 ──► Turso（只读）
                                              ▲
本机 Web ─────────────────────────────────────┘
```

#### 与之前 Turso 方案的区别
- **之前**：实时双写，网络不稳定
- **现在**：每日批量导入，失败可重试

#### 实施
```bash
# 每天导出并导入 Turso（离线任务，不阻塞主流程）
sqlite3 local.db ".dump daily_kline" | turso db shell ai-investor
```

#### 优势
- ✅ 边缘数据库，全球访问快
- ✅ 免费 500MB，K线数据够用
- ⚠️ 需要网络连接

---

### 方案 3：Git + Git LFS（简单但有效）

```bash
# 初始化仓库
git init ~/ai-investor-kline-data
cd ~/ai-investor-kline-data

# 启用 LFS（存储大文件）
git lfs track "*.db"
git lfs track "*.parquet"

# 每天提交
DATE=$(date +%Y-%m-%d)
cp ~/OneDrive*/sim_trading.db ./sim_trading_$DATE.db
git add .
git commit -m "K线数据更新 $DATE"
git push origin main
```

#### 优势
- ✅ 版本控制，可追溯
- ✅ 免费（GitHub/GitLab）
- ✅ 跨设备 `git pull` 即可
- ⚠️ 仓库会越来越大（需要定期归档）

---

## 方案对比

| 维度 | 方案1: S3/R2 | 方案2: Turso | 方案3: Git LFS |
|------|-------------|--------------|----------------|
| **不丢失** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **跨设备** | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐ |
| **成本** | ⭐⭐⭐⭐⭐（免费10GB） | ⭐⭐⭐⭐⭐（免费500MB） | ⭐⭐⭐⭐⭐（免费） |
| **查询速度** | ⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ |
| **运维复杂度** | ⭐⭐⭐ | ⭐⭐ | ⭐⭐ |
| **离线可用** | ⭐⭐（需缓存） | ⭐⭐ | ⭐⭐⭐⭐⭐ |

---

## 推荐选择

### 短期（本周实施）
**方案 1（S3/R2 + Parquet）**
- 数据量小（12MB），成本低
- Parquet 格式最适合分析
- 与现有架构解耦

### 中期（本月）
- 实施自动导出脚本
- 配置 rclone 定时同步
- 本机 Web 支持历史查询

### 长期（下季度）
- 评估是否需要时序数据库（InfluxDB）
- 如果数据量增长到 GB 级别

---

## 立即实施

需要我帮你：
1. 配置 R2/S3 账户？
2. 编写导出脚本？
3. 修改 Web 支持历史查询？
