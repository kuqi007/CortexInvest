# 大盘摘要区实现计划

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Holdings/Watching 页面顶部新增独立大盘摘要区，显示 A 股（SH+SZ+创业板+科创50+成交额+AMO）和港股（恒生+恒生科技+成交额）

**Architecture:**
- **数据层**: Poller 在 eastmoney 批量请求中追加创业板/科创50，结果写入 `marketTurnover`；Futu enricher 追加 HK.800000/HK.HSTECH，写入 `marketTurnover`（不写 services 列表）
- **前端层**: 新建 `MarketSummaryBar.tsx` 组件，A/HK 两套渲染逻辑
- **集成层**: `page.tsx` 和 `watching/page.tsx` 移除旧内嵌大盘行，替换为新组件

**Tech Stack:** Python (poller/enricher), TypeScript/Next.js (frontend)

---

## Chunk 1: 后端数据层 — Poller + Futu Enricher

**Spec:** `docs/superpowers/specs/2026-03-20-market-summary-bar-design.md` 第 4.1 节、第 8 步 1-2

### Task 1: Poller — 追加创业板/科创50指数

**Files:**
- Modify: `src/tools/market_data_poller.py`

**关键上下文（请先阅读）：**
- `poll_once()` 在 line ~475 调用 `fetch_realtime_with_fallback(em_symbols)`
- `em_symbols = [s for s in symbols if not is_kr_symbol(s)]` 目前只包含 watchlist 中的 A 股 + 港股代码
- `fetch_realtime_eastmoney` 内部 `_em_secid()` 自动处理 `399006` → `1.399006`、`000688` → `1.000688` 的映射
- Eastmoney 返回后，`build_services(stocks, watchlist)` 将结果组装为前端 `Service` 格式
- 当前 `payload["marketTurnover"] = turnover`，其中 `turnover` 来自 `fetch_market_turnover()`

**实现步骤：**

- [ ] **Step 1: 在 `poll_once` 中追加指数代码**

找到 `stocks, is_sina_fallback = fetch_realtime_with_fallback(em_symbols)` (约 line 475)，在其上方添加：

```python
# 追加 A 股指数：创业板、科创50
INDEX_CODES = ["399006", "000688"]
em_with_index = em_symbols + INDEX_CODES
stocks, is_sina_fallback = fetch_realtime_with_fallback(em_with_index)
```

- [ ] **Step 2: 分离指数结果与个股结果**

在 `build_services` 调用前，从 `stocks` 中分离出指数行情：

```python
# 指数代码集合（不在 watchlist 中）
index_codes_set = set(INDEX_CODES)
index_results = [s for s in stocks if s.get("code", "") in index_codes_set]
stock_results = [s for s in stocks if s.get("code", "") not in index_codes_set]
# 后续只对 stock_results 调用 build_services
services = build_services(stock_results, watchlist)
```

- [ ] **Step 3: 将指数数据写入 marketTurnover**

在 `payload["marketTurnover"] = turnover` 之前（约 line 588），从 `index_results` 提取指数数据：

```python
# 提取指数数据写入 marketTurnover
for idx in index_results:
    code = idx.get("code", "")
    price = idx.get("price", 0) or 0
    pct = idx.get("pct", 0) or 0
    if code == "399006":
        turnover["chiNext"] = round(price, 2) if price else 0
        turnover["chiNextPct"] = round(pct, 2) if pct else 0
    elif code == "000688":
        turnover["kc50"] = round(price, 2) if price else 0
        turnover["kc50Pct"] = round(pct, 2) if pct else 0
```

- [ ] **Step 4: 同步处理新浪降级路径**

在 `if is_sina_fallback:` 分支中（约 line 532），`index_results` 也走新浪接口，数据提取逻辑与 Step 3 相同（代码不变）。确保指数数据写入 `turnover` 字典即可。

- [ ] **Step 5: 测试验证**

```bash
poetry run python -c "
from src.tools.market_data_poller import poll_once, INDEX_CODES
print('INDEX_CODES:', INDEX_CODES)
result = poll_once()
import json
with open('src/data/market_data.json') as f:
    d = json.load(f)
mt = d.get('marketTurnover', {})
print('chiNext:', mt.get('chiNext'), 'pct:', mt.get('chiNextPct'))
print('kc50:', mt.get('kc50'), 'pct:', mt.get('kc50Pct'))
" 2>&1
```

**预期输出：** `chiNext` 和 `kc50` 有数值（交易日）或 0（盘前/休市）

- [ ] **Step 6: Commit**

```bash
git add src/tools/market_data_poller.py
git commit -m "feat: add ChiNext/KC50 index to marketTurnover

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 2: Futu Enricher — 追加恒生/恒生科技指数

**Files:**
- Modify: `src/tools/futu_enricher.py`

**关键上下文（请先阅读）：**
- `FutuL2Enricher.enrich(services)` 在 `poll_once` 中被调用（line ~551）
- `get_market_snapshot(hk)` 目前只传入 watchlist 中的港股代码
- 返回字段 `last_price`（点位）、`change_ratio`（涨跌幅%）、`turnover`（成交额）
- `to_futu_code("HK800000")` → `"HK.800000"` ✅ 正确，无需改 `futu_codes.py`
- 指数不写入 `result[code]`，而是提取数据后写入 `marketTurnover` 全局字段

**实现步骤：**

- [ ] **Step 1: 添加指数代码常量 + 修改 enrich 方法**

找到 `FutuL2Enricher.enrich` 方法的签名和 `get_market_snapshot` 调用（约 line ~130），在方法开头添加指数代码：

```python
class FutuL2Enricher:
    # 类属性，指数代码不参与 to_futu_code 转换（已有特殊分支）
    INDEX_CODES = ["HK800000", "HKHSTECH"]

    def enrich(self, services: list[dict]) -> dict[str, dict]:
        """Enrich services with Futu L2 data. Also extracts index data to marketTurnover."""
        from src.utils.futu_codes import to_futu_code
        # ... existing code ...

        # 指数代码列表
        index_futu_codes = [to_futu_code(c) for c in self.INDEX_CODES]
        # 港股股票代码
        hk = [s for s in all_hk if s not in self.INDEX_CODES]

        # 合并：指数 + 股票，一起请求
        all_futu_codes = index_futu_codes + hk
        ret, data = self._ctx.get_market_snapshot(all_futu_codes)
```

- [ ] **Step 2: 在处理循环中分离指数行和股票行**

在 `for _, row in data.iterrows():` 循环内（约 line ~183），添加：

```python
from_futu = from_futu_code(row["code"])

# 跳过指数（写入 marketTurnover，不写 result）
if from_futu in self.INDEX_CODES:
    last_price = row.get("last_price", 0) or 0
    change_ratio = row.get("change_ratio", 0) or 0
    turnover_val = row.get("turnover", 0) or 0
    if from_futu == "HK800000":
        self._hk_index_data = {
            "hkIndex": round(float(last_price), 2) if last_price else 0,
            "hkIndexPct": round(float(change_ratio), 2) if change_ratio else 0,
            "hkTurnover": round(float(turnover_val), 2) if turnover_val else 0,
        }
    elif from_futu == "HKHSTECH":
        self._hk_index_data = {
            "hkTech": round(float(last_price), 2) if last_price else 0,
            "hkTechPct": round(float(change_ratio), 2) if change_ratio else 0,
        }
    continue  # 不写入 result[code]

# 股票处理（现有逻辑不变）
entry = {}
# ... 现有代码 ...
```

- [ ] **Step 3: 添加 `_hk_index_data` 属性和返回机制**

在 `__init__` 方法中添加：

```python
self._hk_index_data: dict = {}  # 存储港股指数数据
```

在 `enrich` 方法末尾返回指数数据。修改 `enrich` 返回值为元组 `(dict[str, dict], dict]`：

```python
def enrich(self, services: list[dict]) -> tuple[dict[str, dict], dict]:
    """Returns (l2_extra, hk_index_data)"""
    # ... 现有处理逻辑 ...
    # 在 return 前:
    return result, self._hk_index_data
```

- [ ] **Step 4: 更新 poll_once 中的调用**

在 `market_data_poller.py` 中找到 `l2_data = _futu_enricher.enrich(services)` (约 line 551)，修改为：

```python
l2_data, hk_index = _futu_enricher.enrich(services)
if l2_data:
    for svc in services:
        extra = l2_data.get(svc["id"])
        if extra:
            svc.update(extra)
    logger.info(f"L2 增强: {len(l2_data)}/{len(services)} 只")

# 将港股指数数据写入 marketTurnover
if hk_index:
    for k, v in hk_index.items():
        turnover[k] = v
    logger.info(f"港股指数: hkIndex={hk_index.get('hkIndex')} hkTech={hk_index.get('hkTech')}")
```

- [ ] **Step 5: 确认 `hkTurnover` 单位**

`get_market_snapshot` 返回的 `turnover` 字段是港股成交额（HKD），直接写入 `marketTurnover.hkTurnover`，前端按"亿"或"万"单位显示。

- [ ] **Step 6: 测试验证**

```bash
poetry run python -c "
from src.tools.futu_enricher import FutuL2Enricher
from src.utils.futu_codes import to_futu_code
en = FutuL2Enricher()
print('INDEX_CODES:', en.INDEX_CODES)
print('to_futu_code test:')
for c in en.INDEX_CODES:
    print(f'  {c} -> {to_futu_code(c)}')
" 2>&1
```

**预期输出：** `HK800000 -> HK.800000`，`HKHSTECH -> HK.HSTECH`

- [ ] **Step 7: Commit**

```bash
git add src/tools/futu_enricher.py src/tools/market_data_poller.py
git commit -m "feat: add HK index (HSI/HSTECH) to futu_enricher and marketTurnover

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 2: 前端类型定义

**Spec:** `docs/superpowers/specs/2026-03-20-market-summary-bar-design.md` 第 4.3 节

### Task 3: TypeScript 类型

**Files:**
- Modify: `web/app/types.ts`

- [ ] **Step 1: 更新 MarketTurnover 类型**

在 `web/app/types.ts` 找到 `MarketTurnover` 接口（约 line 88），替换为：

```typescript
export interface MarketTurnover {
  sh: number;
  sz: number;
  total: number;
  shIndex: number;
  szIndex: number;
  shPct: number;
  szPct: number;
  verdict: string;
  // A-share new index fields
  chiNext?: number;      // 创业板点位
  chiNextPct?: number;  // 创业板涨跌幅
  kc50?: number;        // 科创50点位
  kc50Pct?: number;     // 科创50涨跌幅
  // HK index fields
  hkIndex?: number;     // 恒生指数点位
  hkIndexPct?: number; // 恒生指数涨跌幅
  hkTech?: number;      // 恒生科技点位
  hkTechPct?: number;  // 恒生科技涨跌幅
  hkTurnover?: number;  // 港股成交额（HK.800000 turnover）
  // AMO
  amo1: number;
  amo2: number;
}
```

- [ ] **Step 2: 验证 API route 不需要改动**

`web/app/api/metrics/route.ts` 中 `marketTurnover` 是透传的，`Types.ts` 更新后 Next.js 会自动验证类型兼容性。无需修改 API route 代码。

- [ ] **Step 3: TypeScript 编译检查**

```bash
cd web && npx tsc --noEmit 2>&1 | head -20
```

**预期输出：** 无错误

- [ ] **Step 4: Commit**

```bash
git add web/app/types.ts
git commit -m "feat: add 9 new fields to MarketTurnover TypeScript type

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 3: 前端组件 — MarketSummaryBar

**Spec:** `docs/superpowers/specs/2026-03-20-market-summary-bar-design.md` 第 5.1 节

### Task 4: MarketSummaryBar 组件

**Files:**
- Create: `web/app/components/MarketSummaryBar.tsx`

- [ ] **Step 1: 创建组件**

创建 `web/app/components/MarketSummaryBar.tsx`：

```typescript
'use client';

import { MarketTurnover } from '../types';

interface MarketSummaryBarProps {
  marketTurnover: MarketTurnover;
  market: 'A' | 'HK';
}

function chgColor(pct: number | undefined): string {
  if (pct === undefined || pct === null) return '#6272a4'; // gray for undefined
  if (pct > 0) return '#50fa7b';
  if (pct < 0) return '#ff5555';
  return '#6272a4';
}

function fmtIdx(val: number | undefined, decimals = 2): string {
  if (!val) return '—';
  return val.toFixed(decimals);
}

function fmtPct(pct: number | undefined): string {
  if (pct === undefined || pct === null) return '—';
  const sign = pct >= 0 ? '+' : '';
  return `${sign}${pct.toFixed(2)}%`;
}

function fmtTurnover(total: number | undefined): string {
  if (!total) return '—';
  if (total >= 1_0000_0000) return `${(total / 1_0000_0000).toFixed(2)}万亿`;
  if (total >= 1_0000) return `${(total / 1_0000).toFixed(0)}亿`;
  return `${total.toFixed(0)}元`;
}

// A-share tab
function ASummary({ mt }: { mt: MarketTurnover }) {
  const { chiNext, chiNextPct, kc50, kc50Pct, shIndex, shPct, szIndex, szPct, total, amo1, amo2 } = mt;
  const amoUp = (amo1 ?? 0) > (amo2 ?? 0);
  const amoColor = amoUp ? '#50fa7b' : '#ff5555';
  const amoArrow = amoUp ? '↑' : '↓';

  return (
    <div style={{
      display: 'flex', gap: '0', fontSize: '13px', fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      background: '#282a36', border: '1px solid #44475a', borderRadius: '4px',
      padding: '6px 12px', overflowX: 'auto', whiteSpace: 'nowrap',
    }}>
      {/* SH */}
      <span>
        <span style={{ color: '#6272a4' }}>SH </span>
        <span style={{ color: chgColor(shPct) }}>{fmtIdx(shIndex, 2)} {fmtPct(shPct)}</span>
      </span>
      <Divider />
      {/* SZ */}
      <span>
        <span style={{ color: '#6272a4' }}>SZ </span>
        <span style={{ color: chgColor(szPct) }}>{fmtIdx(szIndex, 2)} {fmtPct(szPct)}</span>
      </span>
      <Divider />
      {/* ChiNext */}
      <span>
        <span style={{ color: '#6272a4' }}>创业板 </span>
        <span style={{ color: chgColor(chiNextPct) }}>{fmtIdx(chiNext, 2)} {fmtPct(chiNextPct)}</span>
      </span>
      <Divider />
      {/* KC50 */}
      <span>
        <span style={{ color: '#6272a4' }}>科创50 </span>
        <span style={{ color: chgColor(kc50Pct) }}>{fmtIdx(kc50, 2)} {fmtPct(kc50Pct)}</span>
      </span>
      <Divider />
      {/* Turnover */}
      <span>
        <span style={{ color: '#6272a4' }}>成交 </span>
        <span>{fmtTurnover(total)}</span>
      </span>
      <Divider />
      {/* AMO */}
      <span>
        <span style={{ color: '#6272a4' }}>AMO </span>
        <span style={{ color: amoColor }}>
          {`AMO1=${amo1?.toFixed(2) ?? '—'} AMO2=${amo2?.toFixed(2) ?? '—'} ${amoArrow}`}
        </span>
      </span>
    </div>
  );
}

// HK tab
function HKSummary({ mt }: { mt: MarketTurnover }) {
  const { hkIndex, hkIndexPct, hkTech, hkTechPct, hkTurnover } = mt;

  return (
    <div style={{
      display: 'flex', gap: '0', fontSize: '13px', fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
      background: '#282a36', border: '1px solid #44475a', borderRadius: '4px',
      padding: '6px 12px', overflowX: 'auto', whiteSpace: 'nowrap',
    }}>
      {/* HSI */}
      <span>
        <span style={{ color: '#6272a4' }}>恒生 </span>
        <span style={{ color: chgColor(hkIndexPct) }}>{fmtIdx(hkIndex, 2)} {fmtPct(hkIndexPct)}</span>
      </span>
      <Divider />
      {/* HSTECH */}
      <span>
        <span style={{ color: '#6272a4' }}>恒生科技 </span>
        <span style={{ color: chgColor(hkTechPct) }}>{fmtIdx(hkTech, 2)} {fmtPct(hkTechPct)}</span>
      </span>
      <Divider />
      {/* Turnover (reference) */}
      <span>
        <span style={{ color: '#6272a4' }}>成交 </span>
        <span>{fmtTurnover(hkTurnover)} <span style={{ color: '#6272a4', fontSize: '11px' }}>(参考)</span></span>
      </span>
    </div>
  );
}

// Vertical divider
function Divider() {
  return <span style={{ color: '#44475a', margin: '0 8px', userSelect: 'none' }}>|</span>;
}

export default function MarketSummaryBar({ marketTurnover, market }: MarketSummaryBarProps) {
  if (!marketTurnover) return null;
  if (market === 'HK') return <HKSummary mt={marketTurnover} />;
  return <ASummary mt={marketTurnover} />;
}
```

- [ ] **Step 2: TypeScript 编译检查**

```bash
cd web && npx tsc --noEmit 2>&1 | head -20
```

**预期输出：** 无错误

- [ ] **Step 3: Commit**

```bash
git add web/app/components/MarketSummaryBar.tsx
git commit -m "feat: add MarketSummaryBar component

A-share: SH/SZ/ChiNext/KC50 + turnover + AMO1/AMO2
HK: HSI/HSTECH + turnover (reference)

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## Chunk 4: 页面集成 — Holdings + Watching + E2E

**Spec:** `docs/superpowers/specs/2026-03-20-market-summary-bar-design.md` 第 5.2-5.3 节

### Task 5: Holdings 页面集成

**Files:**
- Modify: `web/app/page.tsx`

**关键上下文（请先阅读）：**
- 在 `web/app/page.tsx` 中搜索 `vol:` 和 `marketTurnover` 的行（约现有 summary bar 中的 A 股成交额行）
- 搜索 `FX:` 或 `hkdCnyRate` 的行（港股 FX 行）
- `MarketSwitch` 在约 line 367，`MarketSummaryBar` 应插入其下方
- 搜索 `useMetrics` 获取 `marketTurnover` 的地方，确认传递方式

- [ ] **Step 1: 添加 import**

在 `page.tsx` 顶部 import 区添加：

```typescript
import MarketSummaryBar from './components/MarketSummaryBar';
```

- [ ] **Step 2: 从 useMetrics 解构 marketTurnover**

找到 `const { services, ts, loading, fetchError, ... } = useMetrics()` 行，添加：

```typescript
const { services, ts, loading, fetchError, marketTurnover, ... } = useMetrics();
```

- [ ] **Step 3: 删除旧的 A 股 vol 行**

找到包含 `vol:` 和 `marketTurnover` 的 `<div>` 行，删除整行。内容类似：
```tsx
<span>vol: {marketTurnover.total ? ... : '—'}...</span>
```

- [ ] **Step 4: 删除 FX 汇率行**

找到包含 `FX:` 或 `hkdCnyRate` 的 `<div>` 行，删除整行。

- [ ] **Step 5: 在 MarketSummaryBar 后插入新组件**

在 MarketSwitch 区域下方（约在 "watch header" div 之前）添加：

```tsx
{/* 大盘摘要区 */}
<MarketSummaryBar marketTurnover={marketTurnover ?? {} as any} market={marketTab === 'A' ? 'A' : 'HK'} />
```

- [ ] **Step 6: TypeScript 编译检查**

```bash
cd web && npx tsc --noEmit 2>&1 | grep -E "page\\.tsx|types\\.ts" | head -10
```

**预期输出：** 无 page.tsx 相关错误

- [ ] **Step 7: Commit**

```bash
git add web/app/page.tsx
git commit -m "feat: integrate MarketSummaryBar into Holdings page

Remove old A-share vol row and FX rate row from summary bar.
Replace with MarketSummaryBar component above stock table.

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 6: Watching 页面集成

**Files:**
- Modify: `web/app/watching/page.tsx`

- [ ] **Step 1: 添加 import**

```typescript
import MarketSummaryBar from '../components/MarketSummaryBar';
```

- [ ] **Step 2: 从 useMetrics 解构 marketTurnover**

与 Holdings 页面相同，添加 `marketTurnover` 解构。

- [ ] **Step 3: 删除旧的大盘行（如有）并插入新组件**

与 Holdings 页面相同，在 MarketSwitch 下方插入 `<MarketSummaryBar />`。

- [ ] **Step 4: Commit**

```bash
git add web/app/watching/page.tsx
git commit -m "feat: integrate MarketSummaryBar into Watching page

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

### Task 7: E2E 截图测试

**Files:**
- Create: `web/screenshots/test_market_summary.mjs`

- [ ] **Step 1: 编写 Playwright 测试**

```javascript
import { test, expect } from '@playwright/test';

test('MarketSummaryBar renders correctly on A-share tab', async ({ page }) => {
  await page.goto('http://localhost:3120/');
  await page.waitForSelector('text=Nodes:', { timeout: 10000 });

  // 大盘摘要区应该存在
  const bar = page.locator('text=SH ');
  await expect(bar).toBeVisible();

  // 应该显示创业板、科创50、成交额、AMO
  await expect(page.locator('text=创业板')).toBeVisible();
  await expect(page.locator('text=科创50')).toBeVisible();
  await expect(page.locator('text=成交')).toBeVisible();
  await expect(page.locator('text=AMO')).toBeVisible();

  // 截图为后续审查用
  await page.screenshot({ path: 'screenshots/test_market_summary_a.png' });
});

test('MarketSummaryBar renders correctly on HK tab', async ({ page }) => {
  await page.goto('http://localhost:3120/?tab=HK');
  await page.waitForSelector('text=Nodes:', { timeout: 10000 });

  // 应该显示恒生、恒生科技
  await expect(page.locator('text=恒生 ')).toBeVisible();
  await expect(page.locator('text=恒生科技')).toBeVisible();

  // 不应显示 AMO
  await expect(page.locator('text=AMO').first()).not.toBeVisible();

  // 截图为后续审查用
  await page.screenshot({ path: 'screenshots/test_market_summary_hk.png' });
});
```

- [ ] **Step 2: 运行测试**

```bash
cd web && npx playwright test screenshots/test_market_summary.mjs --reporter=list 2>&1
```

**预期输出：** 两个测试 PASS

- [ ] **Step 3: 查看截图**

用 Read 工具查看 `web/screenshots/test_market_summary_a.png` 和 `test_market_summary_hk.png`，确认大盘摘要区渲染正确。

- [ ] **Step 4: Commit**

```bash
git add web/screenshots/test_market_summary.mjs
git commit -m "test: add MarketSummaryBar E2E tests for A-share and HK tabs

Co-Authored-By: Claude Opus 4.6 <noreply@anthropic.com>"
```

---

## 完成后验证

```bash
# 1. 重启 poller（Pick up 代码变更）
pkill -f "market_data_poller" && sleep 2 && nohup poetry run python src/tools/market_data_poller.py >> logs/l2_daemon_out.log 2>&1 &

# 2. 重启 notifier
pkill -f "stock_notifier" && sleep 2 && nohup poetry run python src/tools/stock_notifier.py >> logs/l2_daemon_out.log 2>&1 &

# 3. 检查 market_data.json 有新字段
sleep 15 && poetry run python -c "
import json
with open('src/data/market_data.json') as f:
    d = json.load(f)
mt = d.get('marketTurnover', {})
print('marketTurnover:', json.dumps({k: v for k, v in mt.items() if v}, indent=2, ensure_ascii=False))
"

# 4. 启动 Web 并截图
# cd web && npm run dev &
# npx playwright test screenshots/test_market_summary.mjs --reporter=list
```
