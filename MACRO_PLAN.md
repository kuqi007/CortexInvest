# Macro Monitor 实现方案

## 架构
将宏观数据监控嵌入 `market_data_poller.py`，与个股行情共用轮询循环，独立函数 `_poll_macro_once()`，失败不影响主流程。

## 数据表
```sql
CREATE TABLE IF NOT EXISTS macro_indicators (
    ts INTEGER PRIMARY KEY,
    date TEXT NOT NULL,
    northbound_net REAL,          -- 北向资金净流入(亿元)
    northbound_total REAL,       -- 北向成交额(亿元)
    gold_price REAL,             -- COMEX黄金(美元/盎司)
    gold_change_pct REAL,        -- 黄金日涨跌%
    copper_price REAL,           -- LME铜(美元/吨)或COMEX铜
    copper_change_pct REAL,      -- 铜日涨跌%
    vix REAL,                    -- VIX指数
    ty10y REAL,                  -- 10年期美债收益率(%)
    usd_cnh REAL,                -- 离岸人民币
    usd_cnh_change_pct REAL,     -- 汇率日涨跌%
    UNIQUE(ts)
);
```

## Agent 分工

### Agent 1: backend-poller
**目标**: 修改 `src/tools/market_data_poller.py` 和 `src/sim_trading/db.py`

1. **db.py** 加 `init_macro_tables()`：
   ```python
   def init_macro_tables():
       conn = get_connection()
       conn.execute("""
           CREATE TABLE IF NOT EXISTS macro_indicators (
               ts INTEGER PRIMARY KEY,
               date TEXT NOT NULL,
               northbound_net REAL,
               northbound_total REAL,
               gold_price REAL,
               gold_change_pct REAL,
               copper_price REAL,
               copper_change_pct REAL,
               vix REAL,
               ty10y REAL,
               usd_cnh REAL,
               usd_cnh_change_pct REAL
           )
       """)
       conn.commit()
       conn.close()
   ```

2. **market_data_poller.py** 加 `_poll_macro_once()`：
   - 5 分钟冷却（`_MACRO_LAST_RUN` 全局）
   - 北向资金：`ak.stock_hsgt_hist_em(symbol="北向资金", period="实时")`
   - 黄金/铜：yfinance `"GC=F"`, `"HG=F"`
   - 汇率：已有逻辑复用或 yfinance `"USDCNH=X"`
   - VIX/美债：`"^VIX"`, `"^TNX"`
   - 所有异常 catch 住，logger.warning 但不抛
   - 写入 `macro_indicators`

3. **poll_once() 末尾** 调用 `_poll_macro_once()`

### Agent 2: backend-api  
**目标**: 新增 `/api/macro` endpoint

在 `web/app/api/macro/route.ts`（或类似位置）创建：
```typescript
// GET /api/macro
// 返回最近 24h 的宏观数据
export async function GET() {
  // 读 trading.db macro_indicators 最新一条
  // + 读最近 N 条用于趋势图
  // 返回 { data: MacroData, history: MacroData[] }
}
```

### Agent 3: frontend-macro
**目标**: 新页面 `web/app/macro/page.tsx`

- Dracula 暗色主题 + JetBrains Mono
- 5 个卡片网格：北向资金 / 黄金 / 铜 / VIX / 汇率
- 每个卡片显示：当前值 + 涨跌% + 颜色指示
- 底部趋势图占位（历史数据回顾功能开发中）
- 从 `/api/macro` 获取数据
- AppTitleBar title="macro — 宏观指标"

## 告警阈值（前端判断，不发 alert_events）
- 北向净流出 > 50亿 → 红色高亮
- 黄金波动 > 2% → 橙色
- 铜波动 > 3% → 红色（紫金黄金关联）
- VIX > 25 → 红色
- 10Y美债 > 4.5% → 橙色

## 关键约束
- 所有写操作通过 poller，Web/API 只读
- yfinance 调用需 try/catch，失败不影响个股行情
- akshare 北向资金接口不稳定，需 fallback
- 休市时 5 分钟轮询，macro 自然降频
- 无 emoji
- CNY/HKD 替代货币符号
