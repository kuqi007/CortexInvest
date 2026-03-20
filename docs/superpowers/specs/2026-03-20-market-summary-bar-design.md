# 大盘摘要区重新设计 Spec

## 1. 概述

将现有 Holdings 页面的大盘信息从"个股统计行"中分离出来，形成独立的大盘摘要区，使投资者能够一眼看清市场整体状态（成交量、趋势、各大指数涨跌），无需与持仓数据混杂。

## 2. 页面布局

```
┌──────────────────────────────────────────────────────────────────┐
│  AppTitleBar (macOS 红绿灯)                                      │
├──────────────────────────────────────────────────────────────────┤
│  AppTabs (holdings/watching/alerts/sim/sector/manage)            │
├──────────────────────────────────────────────────────────────────┤
│  MarketSwitch (A share | HK)                                     │
├──────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  大盘摘要区（独立横卡，竖线分隔各项，突出显示）               │  │
│  └────────────────────────────────────────────────────────────┘  │
├──────────────────────────────────────────────────────────────────┤
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  个股区（持仓/自选表格，与大盘区视觉分离）                   │  │
│  └────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

大盘区位于 MarketSwitch 下方，个股表格上方，占页面顶部固定高度。个股表格正常滚动，不受大盘区影响。

## 3. 大盘区内容

### 3.1 A 股 Tab

```
SH: 3384.88 +0.16% | SZ: 11351.33 +1.57% | 创业板: 2050.21 +0.83% | 科创50: 1020.30 -0.32% | 成交额: 1.16万亿  AMO1=1.2x AMO2=0.9x ↑
```

| 项目 | 数据来源 | 说明 |
|------|---------|------|
| SH（上证） | 现有 `marketTurnover.shIndex / shPct` | 不变 |
| SZ（深证） | 现有 `marketTurnover.szIndex / szPct` | 不变 |
| 创业板 | **新增**，代码 `399006`（eastmoney 接口 `1.399006`） | eastmoney 实时行情 |
| 科创50 | **新增**，代码 `000688`（eastmoney 接口 `1.000688`） | eastmoney 实时行情 |
| 成交额 | 现有 `marketTurnover.total`，单位万亿（沪深合计） | 不变 |
| AMO1 | 现有 `marketTurnover.amo1` | 6日均量比，基于沪深合计成交额 |
| AMO2 | 现有 `marketTurnover.amo2` | 12日均量比，基于沪深合计成交额 |
| 趋势箭头 | AMO1 > AMO2 → ↑，否则 → ↓ | |

> **创业板/科创50 不参与 AMO 计算**。AMO1/AMO2 始终基于沪深合计成交额（两市总成交额），指数仅供点位和涨跌幅参考。

**指数颜色**：涨 = 绿色（`#50fa7b`），跌 = 红色（`#ff5555`），平 = 灰色（`#6272a4`）。

**AMO 颜色**：
- AMO1 > AMO2（放量，绿色 `←#50fa7b`）
- AMO1 ≤ AMO2（缩量，红色 `←#ff5555`）
- 数值格式：`AMO1=1.2x AMO2=0.9x ↑`

### 3.2 港股 Tab

```
恒生: 24000.33 +0.52% | 恒生科技: 5800.10 -0.31% | 成交额: 200亿 (参考)
```

| 项目 | 数据来源 | 说明 |
|------|---------|------|
| 恒生指数 | **新增**，Futu `HK.800000` via `futu_enricher.py` | `last_price` + `change_ratio` |
| 恒生科技 | **新增**，Futu `HK.HSTECH` via `futu_enricher.py` | `last_price` + `change_ratio` |
| 成交额 | `HK.800000` 的 `turnover` 字段 | 恒生指数自身成交额，作为参考值 |

**注**：
- 港股不显示 AMO（无全市场成交额数据源，口径不可比）
- 港股不保留 FX 汇率行（FX 数据仍从 `useMetrics()` 获取，供 P&L 换算用，只是不在摘要区单独显示）

**FX 说明**：删除的是摘要区的 `FX: HKD/CNY` 行，`hkdCnyRate` 数据仍在 `market_data.json` 中，不影响港股盈亏换算。

## 4. 数据层改动

### 4.1 Poller（`src/tools/market_data_poller.py`）

**新增指数代码**（A 股，eastmoney）：
- `399006`（创业板）、`000688`（科创50）

**实现方式**：在 `fetch_realtime_with_fallback()` 的批量请求列表中追加这 2 个指数代码。Eastmoney 返回后提取 `price`（指数点位）、`change_pct`（涨跌幅），写入 `market_data.json` 的 `marketTurnover` 中新增字段。

**字段命名**：
```typescript
// src/data/market_data.json marketTurnover 新增字段
{
  "chiNext": 2050.21,      // 创业板点位
  "chiNextPct": 0.83,     // 创业板涨跌幅
  "kc50": 1020.30,        // 科创50点位
  "kc50Pct": -0.32,       // 科创50涨跌幅
  "hkTech": 5800.10,      // 恒生科技点位
  "hkTechPct": -0.31,     // 恒生科技涨跌幅
}
```

**成交额**：eastmoney 返回的 `amount` 字段即为成交额（单位：元），直接使用。

### 4.2 Web API（`web/app/api/metrics/route.ts`）

`marketTurnover` 透传到前端，不改动 merge 逻辑。

### 4.3 TypeScript 类型（`web/app/types.ts`）

```typescript
export interface MarketTurnover {
  sh: number;       // 不变
  shIndex: number;
  shPct: number;
  sz: number;       // 不变
  szIndex: number;
  szPct: number;
  total: number;    // 不变
  // 新增
  chiNext?: number;    // 创业板点位
  chiNextPct?: number; // 创业板涨跌幅
  kc50?: number;       // 科创50点位
  kc50Pct?: number;    // 科创50涨跌幅
  hkTech?: number;     // 恒生科技点位
  hkTechPct?: number;  // 恒生科技涨跌幅
  hkTurnover?: number; // 港股成交额（HK.800000 turnover）
  //
  amo1: number;     // 不变
  amo2: number;     // 不变
}
```

## 5. 前端组件改动

### 5.1 大盘摘要区组件（`web/app/components/MarketSummaryBar.tsx`）

**新增独立组件**，替代 `page.tsx` 内嵌的 summary bar 大盘行（lines 441–456）。

**Props**：
```typescript
interface MarketSummaryBarProps {
  marketTurnover: MarketTurnover;
  market: 'A' | 'HK';
}
```

**渲染逻辑**：
- A 股 tab：SH + SZ + 创业板 + 科创50 + 成交额 + AMO1/AMO2
- 港股 tab：恒生 + 恒生科技 + 成交额（无 AMO）
- 指数涨跌幅带颜色：涨绿、跌红、平灰
- AMO 趋势箭头 + 颜色（A 股 tab 专用）

**降级处理**：若指数字段缺失（如 Poller 未重启），显示 `—`；成交额为 0 或 null 时也显示 `—`。

**盘前处理**：09:00–09:15（A股竞价）/ 09:00–09:30（港股竞价）期间指数可能为 0，显示 `—` 而非 0.00%。

**样式**：Dracula 主题，独立 `<div>` 横卡，`border: 1px solid #44475a`，背景略深（`#282a36`），字体等宽风格。

### 5.2 Holdings 页面（`web/app/page.tsx`）

定位方式：以代码逻辑定位，非硬编码行号
- 删除 A 股 vol 行：搜索包含 `vol:` 和 `marketTurnover` 的行（约在现有代码中搜索定位）
- 删除 FX 汇率行：搜索包含 `FX:` 或 `hkdCnyRate` 的行
- 在 `MarketSummaryBar` 下方插入新的大盘摘要区组件
- 保留个股统计行（Nodes/holdings/up/down/throughput/avg_delta/alerts）

**调整后的 summary bar 结构**：
```
┌──────────────────────────────────────────────────────────────┐
│  大盘摘要区（MarketSummaryBar 组件）                          │
├──────────────────────────────────────────────────────────────┤
│  Nodes:38  holdings:13  up:6  down:5  P&L:-1.47% 今天-16K │
└──────────────────────────────────────────────────────────────┘
```

### 5.3 Watching 页面（`web/app/watching/page.tsx`）

同样插入 `MarketSummaryBar` 组件，保持一致性。

## 6. AMO 个股列（可选，默认隐藏）

在持仓/自选表格中为每行添加 AMO1 列，供用户查看个股量能状态。

**实现**：在 `HoldRow`/`WatchRow` 中新增 AMO1 字段显示，通过用户设置控制显示/隐藏（`monitor_config.json` 增加 `showAmoColumn: boolean`，默认 false）。

此项为可选功能，可在第一期不实现。

## 7. 数据流

```
Poller (market_data_poller.py)
  ├── eastmoney 实时行情 → 现有A股持仓 + 新增创业板/科创50
  ├── futu_enricher → HK.800000(恒生) + HK.HSTECH(恒生科技) 行情
  ├── 计算 AMO1/AMO2（已有逻辑，不变）
  └── 写入 market_data.json → marketTurnover
            ↓
Web /api/metrics
  └── marketTurnover（含新增指数字段）
            ↓
MarketSummaryBar 组件（A 股 tab / HK tab）
  └── 渲染指数 + 成交额 + AMO 趋势
```

## 8. 实现步骤

1. **Poller**：在 eastmoney 批量请求中追加创业板(`399006`)、科创50(`000688`) 的实时行情，提取 `price` 和 `change_pct` 写入 `marketTurnover` 新增字段（恒生科技和恒生指数由 Futu enricher 负责，见步骤2）
2. **Futu enricher**：在 `futu_enricher.py` 的 `get_market_snapshot` 调用中追加指数代码 `HK.800000`（恒生）和 `HK.HSTECH`（恒生科技），提取 `last_price`、`change_ratio`、`turnover`，写入 `marketTurnover` 新增字段
3. **API route**：`marketTurnover` 透传，不变
4. **TypeScript**：更新 `MarketTurnover` 类型，新增 6 个可选字段
5. **MarketSummaryBar 组件**：新建 `web/app/components/MarketSummaryBar.tsx`，实现 A/HK 两套渲染逻辑
6. **page.tsx**：删除内嵌大盘行，插入新组件
7. **watching/page.tsx**：同样插入 `MarketSummaryBar`
8. **E2E 测试**：Playwright 截图验证 A 股/HK 两 tab 的大盘摘要区渲染正确

## 9. 非功能性

- 涨跌颜色：继承现有 Dracula 主题惯例（涨 = `#50fa7b` 绿，跌 = `#ff5555` 红），保持与页面其他部分一致
- 不影响现有持仓/自选表格逻辑
- 科创50/创业板开市后才有数据，盘前显示 `—`
- 港股不显示 AMO（无全市场成交额数据源）