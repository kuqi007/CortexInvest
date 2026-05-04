# Macro 数据与 AI 分析集成方案

## 问题

当前 `investment-advisor` agent 的 Step 1 读取：
- 持仓（config.db）
- 价格（market_data.json）
- 研报（stocks/）
- 历史结论（.claude/notes/）
- 交易计划（trade_plans.json）

**缺少：宏观市场环境**。导致 AI 分析个股时看不到北向资金流向、大宗商品价格、VIX 恐慌指数等全局信号。

---

## 方案：三层集成

### Level 1 — Agent 定义修改（5 分钟，立即生效）

**修改** `investment-advisor.md` Step 1，增加 macro 数据拉取：

```markdown
### Step 1: Context Gathering (MUST DO)
...
5. Read .claude/notes/{CODE}.md if exists
6. Read trade_plans.json for existing automated plans
7. ⚠️ **NEW: Read macro environment**
   - If localhost:3120 is reachable: `curl -s http://localhost:3120/api/macro | jq`
   - If offline or API down: read `.claude/macro/latest.json` as fallback
   - Extract key signals:
     * 北向资金方向（净流入/流出，与个股外资属性关联）
     * 黄金价格 + 铜价（判断大宗商品周期，影响周期股估值）
     * VIX（恐慌指数 >25 时降低仓位，<15 时提高风险偏好）
     * 离岸人民币（贬值 >0.5% 利好出口/港股，利空北向流入）
     * 美债10Y（>4.5% 压制成长股估值）
     * 钨价（直接影响 HK03858 佳鑫国际，钨价涨 5% → 股价弹性约 8-12%）
8. Search news via mx-search if material events suspected
```

**在 Step 2 的 Market Context 维度中增加 macro 影响评估**：

```markdown
#### Market Context (with Macro Integration)
| 维度 | 评估 |
|------|------|
| 北向资金 | 净流入 45.2亿 → 外资情绪积极，利好蓝筹/白马 |
| 大宗商品 | 黄金 +0.85%、铜 -1.15% → 滞胀信号，周期股分化 |
| 恐慌指数 | VIX 18.5 → 低风险，可维持正常仓位 |
| 汇率 | USDCNH -0.12% → 人民币微贬，港股出口受益 |
| 利率 | 美债10Y 4.35% → 成长股估值承压，价值股相对优势 |
| 关联商品 | 钨价 +1.5% → HK03858 成本支撑，关注 80万/标吨关键位 |
```

---

### Level 2 — 持久化缓存（15 分钟，离线可用）

**新增文件** `.claude/hooks/macro_snapshot.py`：

```python
#!/usr/bin/env python3
"""Pull /api/macro and write to .claude/macro/latest.json + history.ndjson"""
import json, urllib.request, os, datetime

API = "http://localhost:3120/api/macro"
OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "macro")
os.makedirs(OUT_DIR, exist_ok=True)

latest_path = os.path.join(OUT_DIR, "latest.json")
history_path = os.path.join(OUT_DIR, "history.ndjson")

try:
    with urllib.request.urlopen(API, timeout=5) as r:
        data = json.loads(r.read())
    with open(latest_path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    with open(history_path, "a") as f:
        f.write(json.dumps({"ts": datetime.datetime.now().isoformat(), **data}, ensure_ascii=False) + "\n")
    print("macro snapshot OK")
except Exception as e:
    print(f"macro snapshot FAILED: {e}")
    exit(1)
```

**触发方式**（三选一）：
1. **Poller 集成**：在 `market_data_poller.py` 的 `_poll_macro_once()` 末尾调用 `subprocess.run(["python3", ".../macro_snapshot.py"])`
2. **Cron 定时**：`*/5 * * * * cd /path && python3 .claude/hooks/macro_snapshot.py`
3. **Claude Code 启动 hook**：在 `.claude/hooks/` 里加一个 `pre-analysis.sh` 在 agent 启动前自动拉取

**Agent 回退读取**：
```markdown
7. Read macro environment
   - Primary: `curl -s http://localhost:3120/api/macro`
   - Fallback: read `.claude/macro/latest.json` (if API down)
```

---

### Level 3 — Daily Review 自动注入（30 分钟，历史可追溯）

**修改 daily review 模板**，在顶部增加"市场环境"section：

```markdown
# 2026-05-04 分析记录

> 假期日，港股5/4正常交易，A股/港股通休市

## 市场环境（宏观快照）

| 指标 | 数值 | 变动 | 信号 |
|------|------|------|------|
| 北向净流入 | 45.2亿 | — | 外资积极 |
| COMEX黄金 | 2345.6 | +0.85% | 滞胀对冲 |
| LME铜 | 4.32 | -1.15% | 周期降温 |
| VIX | 18.5 | — | 低风险 |
| 离岸人民币 | 7.2345 | -0.12% | 微贬 |
| 美债10Y | 4.35% | — | 成长股承压 |
| 65%黑钨精矿 | 78.05 | +1.50% | 佳鑫支撑 |

---

## 今日分析
...
```

**自动化**：写一个 `daily_macro_header.py`，每天开盘前自动把 macro 数据格式化成 markdown 表格，写入当日 `.claude/daily/YYYY-MM-DD.md` 的 header。

**好处**：
- AI 读 daily review 时**自然获得**宏观上下文，不需要额外 API 调用
- 历史可追溯：回看 4/29 的 daily review，能看到当时的宏观环境
- 与个股分析在同一文档里，关联性强

---

### Level 4 — 个股自动关联（高价值，需要后端支持）

**钨价 → 佳鑫国际自动触发**：

在 `market_data_poller.py` 的 `_poll_macro_once()` 里，当 `tungsten_change_pct > 2%` 时：
1. 自动追加到 `.claude/notes/HK03858.md` 的"宏观关联"section
2. 触发一个 lightweight 的 AI 评估："钨价大涨 +3%，重新评估佳鑫国际持仓"

**实现方式**：
```python
# 在 _poll_macro_once() 末尾
if tungsten_change_pct and abs(tungsten_change_pct) > 2:
    note_path = os.path.join(CLAUDE_DIR, "notes", "HK03858.md")
    with open(note_path, "a") as f:
        f.write(f"\n## {datetime.now().strftime('%Y-%m-%d')} 宏观触发\n")
        f.write(f"钨价变动 {tungsten_change_pct:+.2f}% → 当前 {tungsten_price} 万元/标吨\n")
        f.write(f"建议：重新评估佳鑫国际持仓/建仓计划\n")
```

---

## 推荐实施顺序

| 优先级 | 级别 | 工作量 | 效果 |
|--------|------|--------|------|
| 🔴 P0 | Level 1 — 改 investment-advisor.md | 5 分钟 | 每次分析自动拉 macro |
| 🟡 P1 | Level 2 — macro_snapshot.py | 15 分钟 | API down 时也能读 |
| 🟡 P1 | Level 3 — daily review 注入 | 30 分钟 | 历史可追溯 |
| 🟢 P2 | Level 4 — 个股自动触发 | 1 小时 | 钨价异动自动提醒 |

---

## 验证方法

修改 `investment-advisor.md` 后，在 Claude Code 里测试：

```
User: 看下佳鑫国际
→ AI 应该自动：
   1. 读 config.db → 持仓状态
   2. 读 .claude/notes/HK03858.md → 历史结论
   3. 读 stocks/HK03858/ → 研报
   4. curl /api/macro → 当前钨价 78.05 +1.5%
   5. 输出：钨价上涨支撑佳鑫，但当前已清仓，第一批建仓 90-93...
```

如果 AI 输出里包含"当前钨价 78.05 万元/标吨，上涨 1.5%"，说明集成成功。
