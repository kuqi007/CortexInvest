# 股票关联分析系统 — 实现方案

## 问题定义

当前 `investment-advisor` agent 分析个股时**孤立地看单只股票**：
- 分析 **紫金矿业(02899.HK)** 时看不到黄金/铜价格走势
- 分析 **澜起科技(HK06809)** 时看不到 A 股澜起科技(688008) 的溢价/折价
- 分析 **佳鑫国际(HK03858)** 时钨价数据需要手动查

**目标**：把"关联资产"绑定在系统里，AI 分析时**自动一并分析**。

---

## 方案架构

```
┌─────────────────────────────────────────────────────────────┐
│                    关联配置层 (Config)                        │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐     │
│  │ 关联定义表    │  │ 数据源映射    │  │ 影响系数     │     │
│  │ relationships│  │ data_sources  │  │ weights      │     │
│  └──────────────┘  └──────────────┘  └──────────────┘     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    数据聚合层 (Aggregator)                    │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐    │
│  │ /api/macro│  │ Futu API │  │ akshare  │  │ yfinance │    │
│  │ 宏观指标  │  │ 个股行情  │  │ A股数据  │  │ 国际期货  │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    分析注入层 (Injection)                     │
│  ┌──────────────────────────────────────────────────────┐   │
│  │ investment-advisor agent                              │   │
│  │ Step 1: Read primary stock data                     │   │
│  │ Step 2: Read related assets → auto-merge context    │   │
│  │ Step 3: Output analysis with correlation insights   │   │
│  └──────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

---

## 第一层：关联配置存储

### 方案 A：SQLite 表（推荐 — 可扩展、可查询）

**新增表** `stock_relationships`：

```sql
CREATE TABLE stock_relationships (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,           -- 主股票代码，如 "02899.HK"
    related_type TEXT NOT NULL,     -- 关联类型：commodity | peer_stock | currency | index | sector
    related_code TEXT NOT NULL,     -- 关联资产代码，如 "GC=F" | "688008.SH" | "USDCNH"
    related_name TEXT,              -- 关联资产名称，如 "COMEX黄金" | "澜起科技A股"
    data_source TEXT NOT NULL,      -- 数据源：macro_api | futu | akshare | yfinance | manual
    field_path TEXT,                -- 数据路径，如 "gold_price" | "gold_change_pct"
    influence TEXT DEFAULT 'positive', -- 影响方向：positive | negative | neutral | complex
    weight REAL DEFAULT 1.0,        -- 影响权重 0-1（用于多关联时排序）
    threshold_pct REAL,             -- 触发阈值，如 2.0（变动超过2%时高亮提醒）
    is_active INTEGER DEFAULT 1,    -- 是否启用
    created_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    updated_at INTEGER NOT NULL DEFAULT (strftime('%s', 'now')),
    UNIQUE(symbol, related_code)
);

CREATE INDEX idx_rel_symbol ON stock_relationships(symbol);
CREATE INDEX idx_rel_type ON stock_relationships(related_type);
CREATE INDEX idx_rel_active ON stock_relationships(is_active);
```

**预置关联数据示例**：

```sql
-- 紫金矿业 ↔ 黄金/铜
INSERT INTO stock_relationships(symbol, related_type, related_code, related_name, data_source, field_path, influence, weight, threshold_pct)
VALUES
("02899.HK", "commodity", "gold_price", "COMEX黄金", "macro_api", "gold_price,gold_change_pct", "positive", 0.8, 2.0),
("02899.HK", "commodity", "copper_price", "LME铜", "macro_api", "copper_price,copper_change_pct", "positive", 0.7, 3.0);

-- 澜起科技H股 ↔ A股对标
INSERT INTO stock_relationships(symbol, related_type, related_code, related_name, data_source, field_path, influence, weight, threshold_pct)
VALUES
("06809.HK", "peer_stock", "688008.SH", "澜起科技A股", "akshare", "price,change_pct", "positive", 0.9, 2.0);

-- 佳鑫国际 ↔ 钨价
INSERT INTO stock_relationships(symbol, related_type, related_code, related_name, data_source, field_path, influence, weight, threshold_pct)
VALUES
("03858.HK", "commodity", "tungsten_price", "65%黑钨精矿", "macro_api", "tungsten_price,tungsten_change_pct", "positive", 0.95, 2.0);

-- 小米集团 ↔ 恒生科技指数
INSERT INTO stock_relationships(symbol, related_type, related_code, related_name, data_source, field_path, influence, weight, threshold_pct)
VALUES
("01810.HK", "index", "HSTECH", "恒生科技指数", "akshare", "price,change_pct", "positive", 0.6, 1.5);

-- 中材科技 ↔ 基建/新能源板块
INSERT INTO stock_relationships(symbol, related_type, related_code, related_name, data_source, field_path, influence, weight, threshold_pct)
VALUES
("03296.HK", "commodity", "copper_price", "LME铜", "macro_api", "copper_price,copper_change_pct", "positive", 0.5, 3.0);
```

### 方案 B：JSON 配置文件（快速实现、易手动编辑）

**文件** `.claude/relationships.json`：

```json
{
  "02899.HK": {
    "name": "紫金矿业",
    "relationships": [
      {
        "type": "commodity",
        "code": "gold_price",
        "name": "COMEX黄金",
        "source": "macro_api",
        "fields": ["gold_price", "gold_change_pct"],
        "influence": "positive",
        "weight": 0.8,
        "threshold_pct": 2.0,
        "reason": "紫金矿业黄金业务占比 60%+，金价每涨 10% → 净利润弹性约 15%"
      },
      {
        "type": "commodity",
        "code": "copper_price",
        "name": "LME铜",
        "source": "macro_api",
        "fields": ["copper_price", "copper_change_pct"],
        "influence": "positive",
        "weight": 0.7,
        "threshold_pct": 3.0,
        "reason": "铜业务占比 30%，铜价与基建/新能源需求挂钩"
      }
    ]
  },
  "06809.HK": {
    "name": "澜起科技",
    "relationships": [
      {
        "type": "peer_stock",
        "code": "688008.SH",
        "name": "澜起科技A股",
        "source": "akshare",
        "fields": ["price", "change_pct"],
        "influence": "positive",
        "weight": 0.9,
        "threshold_pct": 2.0,
        "reason": "A/H 联动，A股溢价率 34%，A股动向领先 H 股"
      },
      {
        "type": "peer_stock",
        "code": "603986.SH",
        "name": "兆易创新",
        "source": "akshare",
        "fields": ["price", "change_pct"],
        "influence": "positive",
        "weight": 0.6,
        "threshold_pct": 2.0,
        "reason": "半导体存储对标，AH 溢价可比"
      }
    ]
  },
  "03858.HK": {
    "name": "佳鑫国际资源",
    "relationships": [
      {
        "type": "commodity",
        "code": "tungsten_price",
        "name": "65%黑钨精矿",
        "source": "macro_api",
        "fields": ["tungsten_price", "tungsten_change_pct"],
        "influence": "positive",
        "weight": 0.95,
        "threshold_pct": 2.0,
        "reason": "纯钨矿标的，股价与钨价弹性约 1.2-1.5x"
      }
    ]
  }
}
```

**推荐**：先用 **方案 B JSON** 快速验证，稳定后迁移到 **方案 A SQLite**。

---

## 第二层：数据聚合引擎

### 新增模块 `src/tools/relationship_engine.py`

```python
"""
股票关联分析引擎
- 读取 stock_relationships 配置
- 聚合关联资产实时数据
- 生成关联分析上下文供 AI 使用
"""

import json, sqlite3, urllib.request
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

class RelatedType(Enum):
    COMMODITY = "commodity"
    PEER_STOCK = "peer_stock"
    CURRENCY = "currency"
    INDEX = "index"
    SECTOR = "sector"

@dataclass
class Relationship:
    symbol: str
    related_type: RelatedType
    related_code: str
    related_name: str
    data_source: str
    field_path: str
    influence: str  # positive | negative | neutral
    weight: float
    threshold_pct: Optional[float]
    reason: str = ""

@dataclass
class RelatedData:
    relationship: Relationship
    current_value: Optional[float]
    change_pct: Optional[float]
    is_triggered: bool  # 是否超过 threshold
    raw_data: dict


class RelationshipEngine:
    """核心引擎：读取配置 → 拉数据 → 输出关联上下文"""

    def __init__(self, db_path: str, relationships_json: Optional[str] = None):
        self.db_path = db_path
        self.relationships_json = relationships_json
        self._macro_cache: Optional[dict] = None
        self._macro_ts: int = 0

    def get_relationships(self, symbol: str) -> List[Relationship]:
        """获取某股票的所有关联定义"""
        # 优先从 JSON 读取
        if self.relationships_json:
            with open(self.relationships_json) as f:
                data = json.load(f)
            if symbol in data:
                return [
                    Relationship(symbol=symbol, **r)
                    for r in data[symbol].get("relationships", [])
                ]
        # 回退到 SQLite
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM stock_relationships WHERE symbol=? AND is_active=1",
            (symbol,)
        ).fetchall()
        conn.close()
        return [Relationship(**dict(r)) for r in rows]

    def fetch_related_data(self, rel: Relationship) -> RelatedData:
        """根据数据源拉取关联资产的实时数据"""
        raw = {}
        current = None
        change_pct = None

        if rel.data_source == "macro_api":
            raw = self._fetch_macro_api()
            # 解析 field_path，如 "gold_price,gold_change_pct"
            fields = rel.field_path.split(",")
            if len(fields) >= 1:
                current = raw.get("data", {}).get(fields[0])
            if len(fields) >= 2:
                change_pct = raw.get("data", {}).get(fields[1])

        elif rel.data_source == "akshare":
            raw = self._fetch_akshare(rel.related_code)
            current = raw.get("price")
            change_pct = raw.get("change_pct")

        elif rel.data_source == "futu":
            raw = self._fetch_futu(rel.related_code)
            current = raw.get("price")
            change_pct = raw.get("change_pct")

        elif rel.data_source == "yfinance":
            raw = self._fetch_yfinance(rel.related_code)
            current = raw.get("price")
            change_pct = raw.get("change_pct")

        # 判断是否触发阈值
        triggered = False
        if change_pct is not None and rel.threshold_pct is not None:
            triggered = abs(change_pct) >= rel.threshold_pct

        return RelatedData(
            relationship=rel,
            current_value=current,
            change_pct=change_pct,
            is_triggered=triggered,
            raw_data=raw,
        )

    def _fetch_macro_api(self) -> dict:
        """读取 /api/macro，带 30 秒缓存"""
        import time
        now = int(time.time())
        if self._macro_cache and (now - self._macro_ts) < 30:
            return self._macro_cache
        try:
            with urllib.request.urlopen("http://localhost:3120/api/macro", timeout=3) as r:
                self._macro_cache = json.loads(r.read())
                self._macro_ts = now
                return self._macro_cache
        except Exception:
            return {"data": {}}

    def _fetch_akshare(self, code: str) -> dict:
        """通过 akshare 获取 A 股实时数据"""
        # 可由 stock_quote 模块复用
        from ..data_sources.akshare_client import get_stock_quote
        return get_stock_quote(code) or {}

    def _fetch_futu(self, code: str) -> dict:
        """通过 Futu API 获取港股/美股实时数据"""
        # 可由 futu client 复用
        from ..data_sources.futu_client import get_stock_snapshot
        return get_stock_snapshot(code) or {}

    def _fetch_yfinance(self, code: str) -> dict:
        """通过 yfinance 获取国际期货/指数"""
        import yfinance as yf
        try:
            ticker = yf.Ticker(code)
            hist = ticker.history(period="2d")
            if len(hist) >= 2:
                today = hist["Close"].iloc[-1]
                yesterday = hist["Close"].iloc[-2]
                change_pct = (today - yesterday) / yesterday * 100
                return {"price": today, "change_pct": change_pct}
        except Exception:
            pass
        return {}

    def analyze_symbol(self, symbol: str) -> Dict:
        """主入口：分析某股票的所有关联资产，输出结构化结果"""
        relationships = self.get_relationships(symbol)
        if not relationships:
            return {"symbol": symbol, "relationships": [], "summary": "无关联配置"}

        related_datas = []
        for rel in relationships:
            try:
                rd = self.fetch_related_data(rel)
                related_datas.append(rd)
            except Exception as e:
                related_datas.append(RelatedData(
                    relationship=rel, current_value=None, change_pct=None,
                    is_triggered=False, raw_data={"error": str(e)}
                ))

        # 生成分析摘要
        triggered = [rd for rd in related_datas if rd.is_triggered]
        summary = self._generate_summary(symbol, related_datas)

        return {
            "symbol": symbol,
            "relationships": [
                {
                    "type": rd.relationship.related_type.value,
                    "name": rd.relationship.related_name,
                    "code": rd.relationship.related_code,
                    "current": rd.current_value,
                    "change_pct": rd.change_pct,
                    "triggered": rd.is_triggered,
                    "influence": rd.relationship.influence,
                    "weight": rd.relationship.weight,
                    "reason": rd.relationship.reason,
                }
                for rd in related_datas
            ],
            "triggered_count": len(triggered),
            "summary": summary,
        }

    def _generate_summary(self, symbol: str, datas: List[RelatedData]) -> str:
        """生成人类可读的关联分析摘要"""
        lines = [f"## 关联资产分析 ({symbol})"]
        for rd in datas:
            rel = rd.relationship
            val_str = f"{rd.current_value:.2f}" if rd.current_value else "N/A"
            chg_str = f"{rd.change_pct:+.2f}%" if rd.change_pct else "N/A"
            flag = "🔥" if rd.is_triggered else ""
            direction = "📈" if rel.influence == "positive" else "📉" if rel.influence == "negative" else "➡️"
            lines.append(f"- {direction} **{rel.related_name}** ({rel.related_code}): {val_str} ({chg_str}) {flag}")
            if rd.is_triggered:
                lines.append(f"  ⚠️ 变动超过 {rel.threshold_pct}% 阈值，建议重点关注")
            lines.append(f"  💡 {rel.reason}")
        return "\n".join(lines)
```

---

## 第三层：API 暴露

### 新增 API 端点 `/api/relationship/:symbol`

```typescript
// web/app/api/relationship/[symbol]/route.ts
import { NextRequest } from "next/server";
import { execSync } from "child_process";

export async function GET(
  _req: NextRequest,
  { params }: { params: { symbol: string } }
) {
  const { symbol } = params;
  try {
    // 调用 Python 引擎
    const json = execSync(
      `cd /Users/zhuqiqi/Documents/development/aiSpace/ai-investor && ` +
      `python3 -c "from src.tools.relationship_engine import RelationshipEngine; ` +
      `engine = RelationshipEngine('data/config.db', '.claude/relationships.json'); ` +
      `import json; print(json.dumps(engine.analyze_symbol('${symbol}'), ensure_ascii=False))"`,
      { encoding: "utf8", timeout: 10000 }
    );
    const data = JSON.parse(json);
    return Response.json(data);
  } catch (e: any) {
    return Response.json({ error: e.message, symbol }, { status: 500 });
  }
}
```

**返回示例**：

```json
{
  "symbol": "03858.HK",
  "relationships": [
    {
      "type": "commodity",
      "name": "65%黑钨精矿",
      "code": "tungsten_price",
      "current": 78.05,
      "change_pct": 1.50,
      "triggered": false,
      "influence": "positive",
      "weight": 0.95,
      "reason": "纯钨矿标的，股价与钨价弹性约 1.2-1.5x"
    }
  ],
  "triggered_count": 0,
  "summary": "## 关联资产分析 (03858.HK)\n- 📈 **65%黑钨精矿** (tungsten_price): 78.05 (+1.50%) \n  💡 纯钨矿标的，股价与钨价弹性约 1.2-1.5x"
}
```

---

## 第四层：Agent 集成

### 修改 `investment-advisor.md`

在 **Step 1: Context Gathering** 中增加关联数据拉取：

```markdown
### Step 1: Context Gathering (MUST DO)

1. Read config.db:monitor_watchlist for holdings
2. Read .claude/notes/{CODE}.md for historical AI conclusions
3. Read stocks/{CODE}_{NAME}/ for fundamental analysis reports
4. Read trade_plans.json for existing automated plans
5. ⚠️ **NEW: Read related assets**
   - `curl -s http://localhost:3120/api/relationship/{SYMBOL}`
   - 或 `python3 -c "from src.tools.relationship_engine import RelationshipEngine; ..."`
   - 重点关注 `triggered=true` 的关联资产（超过阈值）
   - 将关联数据写入分析上下文
6. Read macro environment (from /api/macro)
7. Search news via mx-search if material events suspected
```

在 **Step 2: Analysis Framework** 中增加关联影响评估：

```markdown
#### Related Asset Impact (NEW)
| 关联资产 | 当前值 | 变动 | 影响方向 | 权重 | 对个股影响 |
|---------|--------|------|---------|------|-----------|
| COMEX黄金 | 2345.6 | +0.85% | 正向 | 0.8 | 紫金矿业净利润弹性 +1.2% |
| 澜起A股 | 85.3 | -2.1% | 正向 | 0.9 | AH溢价收窄，H股可能跟跌 |
| 65%黑钨精矿 | 78.05 | +1.5% | 正向 | 0.95 | 佳鑫国际成本支撑，建仓位上移 |

**触发阈值的资产**：
- 如果有关联资产 `triggered=true`，必须在分析中**优先讨论**
- 给出明确的仓位/价格调整建议
```

---

## 第五层：Web 前端展示

### 在 Manage / Holdings 页面增加"关联资产"小卡片

```tsx
// 在个股详情页侧边栏或底部增加
function RelatedAssetsCard({ symbol }: { symbol: string }) {
  const [related, setRelated] = useState(null);
  useEffect(() => {
    fetch(`/api/relationship/${symbol}`).then(r => r.json()).then(setRelated);
  }, [symbol]);

  if (!related?.relationships?.length) return null;

  return (
    <div style={{ padding: 16, borderRadius: 12, background: D.currentLine }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: D.fg, marginBottom: 12 }}>
        关联资产
      </div>
      {related.relationships.map((r: any) => (
        <div key={r.code} style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
          <span style={{ fontSize: 12, color: D.comment }}>{r.name}</span>
          <span style={{ fontSize: 12, color: r.change_pct > 0 ? D.red : D.green }}>
            {r.current?.toFixed(2)} ({r.change_pct >= 0 ? "+" : ""}{r.change_pct?.toFixed(2)}%)
            {r.triggered && <span style={{ color: D.orange, marginLeft: 4 }}>⚠️</span>}
          </span>
        </div>
      ))}
    </div>
  );
}
```

---

## 实施路线图

| 阶段 | 任务 | 文件 | 工作量 | 效果 |
|------|------|------|--------|------|
| **Phase 1** | 创建 `relationships.json` 配置 | `.claude/relationships.json` | 10 分钟 | 定义关联关系 |
| **Phase 2** | 实现 `RelationshipEngine` | `src/tools/relationship_engine.py` | 1 小时 | 数据聚合引擎 |
| **Phase 3** | 暴露 API | `web/app/api/relationship/[symbol]/route.ts` | 20 分钟 | Web 可访问 |
| **Phase 4** | 修改 `investment-advisor.md` | `.claude/agents/investment-advisor.md` | 10 分钟 | AI 自动分析 |
| **Phase 5** | 前端卡片 | `web/app/manage/page.tsx` 或独立组件 | 30 分钟 | 可视化 |
| **Phase 6** | 迁移 SQLite | `data/config.db` + `init_db()` | 30 分钟 | 持久化+可管理 |

---

## 验证方法

1. **配置验证**：
   ```bash
   cat .claude/relationships.json | jq '.["03858.HK"]'
   ```

2. **引擎验证**：
   ```bash
   python3 -c "
   from src.tools.relationship_engine import RelationshipEngine
   engine = RelationshipEngine('data/config.db', '.claude/relationships.json')
   result = engine.analyze_symbol('03858.HK')
   print(result['summary'])
   "
   ```

3. **API 验证**：
   ```bash
   curl -s http://localhost:3120/api/relationship/03858.HK | jq
   ```

4. **AI 验证**：
   ```
   User: 看下佳鑫国际
   → AI 输出中应包含："关联资产：65%黑钨精矿 78.05 (+1.50%)，纯钨矿标的，股价与钨价弹性约 1.2-1.5x"
   ```

---

## 扩展方向

1. **板块联动**：半导体板块上涨时，自动提醒板块内所有持仓股票
2. **宏观-个股矩阵**：VIX >25 时，自动降低所有成长股仓位建议
3. **跨市场套利**：A/H 溢价监控，溢价 >40% 时提醒换仓
4. **产业链图谱**：上游（钨矿）→ 中游（硬质合金）→ 下游（刀具），全链条监控
