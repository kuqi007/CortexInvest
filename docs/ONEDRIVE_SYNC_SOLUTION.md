# OneDrive 同步冲突解决方案

## 问题
OneDrive 同步多台机器的 SQLite 数据库时产生冲突文件：
- `sim_trading-ADSKKN7X1GJJYG.db` (本机)
- `sim_trading-ADSKX21K67LYQL.db` (生产机)
- `sim_trading.db` (默认文件)

## 解决方案

### 方案：本地数据库 + 定时同步

使用本地目录 `~/.ai-investor-data-local/` 存储数据库，完全避开 OneDrive 同步。

```
┌─────────────────┐         ┌─────────────────┐
│   生产机        │         │    本机         │
│ (ADSKX21K67LYQL)│         │ (ADSKKN7X1GJJYG)│
├─────────────────┤         ├─────────────────┤
│ OneDrive 同步   │◀───────▶│ OneDrive 同步   │
│ (冲突文件)      │         │ (冲突文件)      │
└─────────────────┘         └─────────────────┘
         │                            │
         │ 复制                       │ 复制
         ▼                            ▼
┌─────────────────┐         ┌─────────────────┐
│ 本地副本        │         │ 本地副本        │
│ ~/.ai-investor  │         │ ~/.ai-investor  │
└─────────────────┘         └─────────────────┘
         │                            │
         └────────────┬───────────────┘
                      │
                      ▼
            ┌─────────────────┐
            │  Web 服务读取    │
            │  (本地 SQLite)   │
            └─────────────────┘
```

## 实施步骤

### 1. 初始化本地数据库（已自动完成）

本地数据库已创建于：`~/.ai-investor-data-local/sim_trading.db`

数据已同步自生产机数据库（82 条 alerts）。

### 2. 定时同步（Cron）

添加以下定时任务，自动从 OneDrive 同步最新数据：

```bash
# 编辑 crontab
crontab -e

# 添加以下行（每2分钟同步一次）
*/2 * * * * cp /Users/zhul1/OneDrive\ -\ Autodesk/ai-investor-data/sim_trading-ADSKX21K67LYQL.db /Users/zhul1/.ai-investor-data-local/sim_trading.db 2>/dev/null
```

### 3. 手动同步命令

```bash
# 立即同步最新数据
cp /Users/zhul1/OneDrive\ -\ Autodesk/ai-investor-data/sim_trading-ADSKX21K67LYQL.db ~/.ai-investor-data-local/sim_trading.db
```

## 配置

### Web 数据库路径

已配置为读取本地数据库：
- 文件：`web/app/lib/db.ts`
- 路径：`~/.ai-investor-data-local/sim_trading.db`

### Python 数据库路径

Python 服务继续写入 OneDrive 目录（供其他机器同步），或配置为双写：
- 默认：`src/data/sim_trading.db` (OneDrive 同步目录)
- 本地：`~/.ai-investor-data-local/sim_trading.db`

## 优势

1. ✅ **无冲突** - 本地文件不受 OneDrive 同步影响
2. ✅ **高性能** - 本地文件读取速度极快
3. ✅ **可靠性** - 即使 OneDrive 离线也能正常工作
4. ✅ **简单** - 无需复杂的数据库同步逻辑

## 注意事项

- 本地数据库是 OneDrive 数据库的**只读副本**
- 写入操作（如交易记录）仍需通过 OneDrive 同步
- 建议定期备份本地数据库
