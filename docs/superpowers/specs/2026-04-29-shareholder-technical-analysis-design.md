# 设计文档：股东结构分析 + 短期技术信号

> 日期：2026-04-29
> 版本：v1.9
> 目标：为 stock-analysis-unified skill 增加股东结构风险检查和短期技术信号两个功能

---

## 1. 背景与目标

在现有 6 步基本面估值流程基础上，新增两个分析维度：

1. **股东结构分析**（Step 2.5-F）：在估值前防守检查中增加第六项，评估筹码分散度和主力动向
2. **短期技术信号**（Step 3.5）：在估值完成后，结合技术指标给出短期入场时机建议

---

## 2. 工作流修订

```
Step 1:   数据收集 (mx-data + mx-search) — 含股东数据 + 日K线
Step 1.5: 数据收集验证门 (HARD GATE)
Step 1.6: 字段提取规范
Step 2:   股票分类
Step 2.5: 估值前防守检查 (A-F六项)  ← F项=股东结构分析
Step 3:   估值方法执行
Step 3.5: 短期技术信号  ← 新增，估值后
Step 4:   交叉验证
Step 5:   输出报告
Step 6:   最终校验
```

**关键原则**：技术信号服从基本面——技术面只辅助判断入场时机，不改变基本面方向。

---

## 3. 数据收集（Phase 1 新增步骤）

### 3.1 新增股东数据收集

**收集时机**：Step 1 原有 13 项数据收集完成后，作为补充步骤。

**收集项与查询语句**：

| # | 数据项 | mx-data 查询语句 | 说明 |
|---|--------|----------------|------|
| F1 | 机构持股比例合计 | `{股票}近3年报 机构持股比例合计` | 季度/年度序列，计算散户占比 |
| F2 | 股东户数 | `{股票}股东户数总股本流通股本` | 取最新一期和上期，计算环比变化 |
| F3 | 十大流通股东明细 | `{股票}十大流通股东` | 含股东性质（证券投资基金/私募/券商等），分析主力动向 |

**数据存储**：保存到 `stocks/{CODE}_{NAME}/v{N}_{DATE}/data/mx_data_*股东*.xlsx` 和 `_raw.json`。

**数据解析关键字段**：

```
机构持股比例合计 (col_id: 100000000003145)
  - rawTable['headName'] = ['2026一季报', '2025年报', ...]
  - rawTable['100000000003145'] = ['72.548', '74.143', ...]  # 数值（无%）

散户占比 = 1 - 机构持股比例合计
  - 贵州茅台 2026一季报: 1 - 72.55% = 27.45%
```

**HKSCC 处理**：
- 港股十大流通股东中"香港中央结算有限公司"为代名人，记录其持股比例但单独标记
- 计算"有效机构持股"时不包含 HKSCC
- 报告中注明"港股 HKSCC 持股为代表持有人，实际投资者结构未知"

**市场差异处理**：

| 市场 | 披露频率 | QoQ 可用性 |
|------|---------|-----------|
| A股 (.SH/.SZ) | 季报 (4次/年) | ✅ 可用 |
| 港股 (.HK) | 半年报 (2次/年) | ⚠️ 半年对比，非严格 QoQ |

港股计算环比变化时，使用最近两个可用报告期。

---

### 3.2 新增技术数据收集

**收集时机**：Step 1 末尾，与股东数据一起收集。

**收集项与查询语句**：

| # | 数据项 | mx-data 查询语句 | 数据范围 |
|---|--------|----------------|---------|
| T1 | 每日 OHLCV | `近{N}日每日开盘价收盘价最高价最低价成交量` | 主板 35日，创业板/科创板 25日 |
| T2 | 主力资金流向 | `近{N}日 主力资金流向` | 同上 |

**数据范围确定**：

```
if entityTagDTO.className in ['创业板股票', '科创板股票']:
    days = 25
else:
    days = 35   # 主板，确保≥30交易日
```

**解析关键字段**（Table 1 为历史序列）：

```
Fields: ['成交量', '最低价', '最高价', '开盘价', '收盘价']
rawTable['headName'] = ['2026-04-29', '2026-04-28', ...]  # 日期序列
table['收盘价'] = ['1401.17元', '1405元', ...]  # 带单位，需去除
rawTable['收盘价'] = ['1401.17', '1405', ...]  # 纯数值，用这个

⚠️ 注意：收盘价在 table 中带"元"，rawTable 中不带单位。
```

**数据范围验证**：收集完成后检查交易日数量，< 20 个交易日的标注"数据不足"。

---

## 4. Step 2.5-F：股东结构分析（新增 F 项）

### 4.1 评估指标

| 指标 | 计算方式 | HIGH 风险 | MEDIUM 风险 |
|------|---------|-----------|-------------|
| **散户占比** | `1 - 机构持股比例合计` | > 35% | 25% ~ 35% |
| **机构持股环比变化** | `(本期 - 上期) / 上期 × 100%` | < -5pp（连续撤离） | -3pp ~ -5pp |
| **股东户数环比变化** | `(本期户数 - 上期户数) / 上期户数 × 100%` | > 30% | 15% ~ 30% |

**触发逻辑**：任一指标触发 HIGH 即 HIGH；三项均触发 MEDIUM 也升为 HIGH。

**港股特殊处理**：
- 机构持股环比变化：因半年报粒度，< -10pp 才触发 HIGH（允许更大波动）
- HKSCC 持股变化单独列示，不参与上述指标计算

### 4.2 十大流通股东分析

分析最新一期十大流通股东构成：

```
分类统计：
  - 控股股东/实控人: 持股合计 %
  - 证券投资基金: 持股合计 %（含指数基金）
  - 私募/券商/信托: 持股合计 %
  - 社保/保险: 持股合计 %
  - HKSCC: 持股合计 %（港股）
  - 其他: 持股合计 %

变动方向：
  - 对比上一报告期，各类型股东持股变化
  - 识别"机构增仓/减仓"趋势
```

### 4.3 输出字段（valuation_result.json）

```json
{
  "shareholder_signal": {
    "retail_ratio_pct": 27.45,
    "institutional_ratio_qoq_change_pp": -1.59,
    "shareholder_count_qoq_change_pct": 3.2,
    "risk_level": "LOW",
    "trigger_conditions": [],
    "top10_holders": [
      {"name": "中国贵州茅台酒厂(集团)有限责任公司", "type": "控股股东", "ratio_pct": 54.40, "change": "不变"},
      {"name": "香港中央结算有限公司", "type": "HKSCC", "ratio_pct": 4.69, "change": "+0.29pp"},
      {"name": "贵州省国有资本运营有限责任公司", "type": "国资", "ratio_pct": 4.55, "change": "不变"}
    ],
    "institutional_breakdown": {
      "fund_etf": 1.50,
      "private_fund": 0.33,
      "social_security": 0.0,
      "hkscc": 4.69,
      "other": 0.0
    },
    "report_date": "2026-03-31",
    "data_source": "mx-data 机构持股比例合计 + 十大流通股东"
  }
}
```

### 4.4 报告章节（Step 5 输出）

```markdown
## 2.5-F 股东结构与筹码分析

### 2.5-F.1 筹码集中度评估
| 指标 | 数值 | 风险 |
|------|------|------|
| 散户占比 | 27.45% | ✅ LOW |
| 机构持股环比 | -1.59pp | ⚠️ MEDIUM |
| 股东户数环比 | +3.2% | ✅ LOW |

**综合风险等级**: LOW

> 解读：贵州茅台筹码高度集中（控股股东持股54.40%），散户占比27.45%处于正常区间。
> 机构持股近一季度小幅下降1.59pp，主要因指数基金被动减持，非主动撤离信号。

### 2.5-F.2 十大流通股东结构
| 类型 | 持股合计 | 环比变化 |
|------|---------|---------|
| 控股股东 | 54.40% | 不变 |
| 证券投资基金/ETF | 1.50% | -0.50pp |
| 私募/券商 | 0.33% | +0.10pp |
| HKSCC（港股通） | 4.69% | +0.29pp |

### 2.5-F.3 风险提示
- ⚠️ MEDIUM: 机构持股环比下降1.59pp，需关注是否为主动减仓
```

---

## 5. Step 3.5：短期技术信号（新增）

### 5.1 计算指标与参数

| 指标 | 参数 | 公式/说明 |
|------|------|----------|
| **RSI(14)** | Period = 14 | RSI = 100 - 100/(1+RS)，RS = avg(涨幅)/avg(跌幅) |
| **MACD** | Fast=12, Slow=26, Signal=9 | DIF = EMA12 - EMA26，DEA = EMA(DIF,9)，柱状 = DIF - DEA |
| **KDJ** | N=9, M1=3, M2=3 | RSV = (C-Low9)/(High9-Low9)×100，K/D = 移动均值，J = 3K-2D |
| **MA 系统** | 5/10/20/60 日 | 收盘价均线 |
| **成交量** | MA5/MA20 对比 | 量比 = 当日成交量 / MA5 |

**技术信号定义**：

| 信号 | 条件 |
|------|------|
| RSI 超买 | RSI(14) > 70 |
| RSI 超卖 | RSI(14) < 30 |
| MACD 金叉 | DIF 上穿 DEA |
| MACD 死叉 | DIF 下穿 DEA |
| MACD 底背离 | 价格新低，但 DIF 未新低 |
| MACD 顶背离 | 价格新高，但 DIF 未新高 |
| KDJ 金叉 | K 上穿 D |
| KDJ 死叉 | K 下穿 D |
| KDJ 超买 | J > 90 |
| KDJ 超卖 | J < 10 |
| MA 多头排列 | 5日 > 10日 > 20日 > 60日 |
| MA 空头排列 | 5日 < 10日 < 20日 < 60日 |
| 放量 | 量比 > 2.0 |
| 缩量 | 量比 < 0.5 |

### 5.2 信号有效期

**所有技术信号有效期：5 个交易日**。报告中注明：
- 信号生成日期（K线数据截止日）
- 过期日期（截止日 + 5 个交易日）
- 过期后需重新确认

### 5.3 综合信号判断

AI 综合以下因素给出综合信号：

```
看多信号 (BULLISH):
  - RSI < 30 或 RSI 从低位回升
  - MACD 金叉 且 DIF > 0
  - KDJ 金叉 且 K/D 在低位
  - MA 多头排列 或 均线收敛
  - 放量突破

看空信号 (BEARISH):
  - RSI > 70 或 RSI 从高位回落
  - MACD 死叉 且 DIF < 0
  - KDJ 死叉 且 K/D 在高位
  - MA 空头排列
  - 缩量下跌

中性信号 (NEUTRAL):
  - 信号混杂
  - 趋势不明
```

**信号一致性评分**：统计各指标方向一致性，如 5 个指标中 4 个看多 = 强势，3:2 = 中性，1:4 = 弱势。

### 5.4 入场参考价位

| 类型 | 计算方式 |
|------|---------|
| 支撑位 | MA20、近期低点、布林下轨（三者取最近） |
| 阻力位 | MA60、近期高点 |
| 建议入场区间 | 回调至支撑位附近 |
| 技术止损位 | 支撑位 - 2% ATR（真实波幅） |
| 目标位 | MA60 / 前高 / R:R ≥ 2:1 |

### 5.5 与基本面冲突处理

**核心原则**：技术服从基本面。

| 基本面信号 | 技术信号 | 操作 |
|-----------|---------|------|
| 买入/增持 | 技术看多 | 标准仓位，共振做多 |
| 买入/增持 | 技术中性 | 标准仓位，不等技术回调 |
| 买入/增持 | 技术看空 | 减仓或等待，不追高；不改变止损位 |
| 持有/观望 | 技术看空 | 不新建仓；现有仓位严格止损 |
| 卖出/减持 | 技术看多 | 忽略技术；基本面优先 |
| 卖出/减持 | 技术看空 | 强化卖出信心 |

**禁止**：技术信号不得抬高基本面止损位，不得覆盖基本面卖出信号。

### 5.6 输出字段（valuation_result.json）

```json
{
  "technical_signal": {
    "data_range_days": 22,
    "data_end_date": "2026-04-29",
    "signal_valid_until": "2026-05-09",
    "board_type": "main_board",
    "indicators": {
      "rsi_14": {
        "value": 45.2,
        "signal": "neutral",
        "oversold": false,
        "overbought": false
      },
      "macd": {
        "dif": 12.5,
        "dea": 10.3,
        "histogram": 2.2,
        "signal": "bullish_crossover",
        "divergence": "none"
      },
      "kdj": {
        "k": 52.3,
        "d": 48.1,
        "j": 60.5,
        "signal": "neutral",
        "crossover": "none"
      },
      "ma": {
        "ma5": 1420.5,
        "ma10": 1415.2,
        "ma20": 1398.7,
        "ma60": 1350.3,
        "arrangement": "bullish"  // or bearish / neutral
      },
      "volume": {
        "ratio_vs_ma5": 0.85,
        "trend": "neutral"  // expanding / contracting / neutral
      }
    },
    "synthesis_signal": "NEUTRAL",
    "signal_consistency_score": "3/5",
    "entry_levels": {
      "support_1": 1398.0,
      "support_2": 1375.0,
      "resistance_1": 1450.0,
      "recommended_entry_range": "1398-1420",
      "technical_stop_loss": 1370.0,
      "target_price": 1500.0,
      "risk_reward_ratio": 2.0
    },
    "conflicts_with_fundamentals": false,
    "data_source": "mx-data 日K线，AI 计算指标"
  }
}
```

### 5.7 报告章节（Step 5 输出）

```markdown
## 3.5 短期技术信号参考

> ⚠️ 技术信号有效期：2026-04-29 至 2026-05-09（5个交易日）

### 3.5.1 技术指标现状

| 指标 | 数值 | 信号 |
|------|------|------|
| RSI(14) | 45.2 | 中性 |
| MACD | DIF=12.5, DEA=10.3, 柱=+2.2 | 金叉，看多 |
| KDJ | K=52.3, D=48.1, J=60.5 | 中性 |
| MA系统 | 5日>10日>20日>60日 | **多头排列** |
| 成交量 | 量比0.85 | 缩量，观望 |

**综合信号**: 中性偏多（3/5 指标看多）

### 3.5.2 入场参考价位

| 类型 | 价位 | 逻辑 |
|------|------|------|
| 第一支撑 | 1398元 | MA20 |
| 第二支撑 | 1375元 | 布林下轨 |
| 建议入场区间 | 1398-1420元 | 回调至MA20附近 |
| 技术止损位 | 1370元 | 支撑-2%ATR |
| 目标位 | 1500元 | MA60，R:R=2.0:1 |

### 3.5.3 技术与基本面共振评估

| 维度 | 方向 | 说明 |
|------|------|------|
| 基本面方向 | 买入 | PE处于历史低位，机构目标价较现价有30%空间 |
| 技术面方向 | 中性偏多 | MA多头排列，但RSI中性、成交量萎缩 |
| **综合结论** | **分歧，服从基本面** | 基本面支持买入；技术面非必需回调，可在现价或1398-1420区间分批建仓 |

> 技术止损位（1370元）不低于基本面止损位（1350元）。若基本面逻辑未变，技术回调不触发止损。
```

---

## 6. valuation_result.json 字段变更汇总

### 新增字段

```json
{
  // Step 2.5-F 新增
  "shareholder_signal": {
    "retail_ratio_pct": "number",           // 散户占比 %
    "institutional_ratio_qoq_change_pp": "number",  // 机构持股环比变化 pp
    "shareholder_count_qoq_change_pct": "number",   // 股东户数环比变化 %
    "risk_level": "HIGH | MEDIUM | LOW",
    "trigger_conditions": ["string"],
    "top10_holders": [{
      "name": "string",
      "type": "控股股东 | 国资 | 证券投资基金 | 私募 | 券商 | HKSCC | 其他",
      "ratio_pct": "number",
      "change": "string"
    }],
    "institutional_breakdown": {
      "fund_etf": "number",
      "private_fund": "number",
      "social_security": "number",
      "hkscc": "number",
      "other": "number"
    },
    "report_date": "string",
    "data_source": "string"
  },

  // Step 3.5 新增
  "technical_signal": {
    "data_range_days": "number",
    "data_end_date": "string",
    "signal_valid_until": "string",
    "board_type": "main_board | chinext | star",
    "indicators": {
      "rsi_14": { "value": "number", "signal": "string", "oversold": "boolean", "overbought": "boolean" },
      "macd": { "dif": "number", "dea": "number", "histogram": "number", "signal": "string", "divergence": "string" },
      "kdj": { "k": "number", "d": "number", "j": "number", "signal": "string", "crossover": "string" },
      "ma": { "ma5": "number", "ma10": "number", "ma20": "number", "ma60": "number", "arrangement": "string" },
      "volume": { "ratio_vs_ma5": "number", "trend": "string" }
    },
    "synthesis_signal": "BULLISH | NEUTRAL | BEARISH",
    "signal_consistency_score": "string",
    "entry_levels": {
      "support_1": "number",
      "support_2": "number",
      "resistance_1": "number",
      "recommended_entry_range": "string",
      "technical_stop_loss": "number",
      "target_price": "number",
      "risk_reward_ratio": "number"
    },
    "conflicts_with_fundamentals": "boolean",
    "data_source": "string"
  }
}
```

### 变更字段

| 字段 | 变更类型 | 说明 |
|------|---------|------|
| `defense_check` | 扩展 | 新增 F 项：股东结构分析 |
| `classification.data_gate` | 不变 | — |
| `classification.method` | 不变 | — |
| `assumptions` | 不变 | — |

---

## 7. 报告模板变更

### 7.1 新增章节

在现有报告模板中增加两个新章节：

**位置 1**：Step 2.5 防守检查章节末尾，追加 `## 2.5-F 股东结构与筹码分析`

**位置 2**：Step 3.5 新增 `## 3.5 短期技术信号参考`

### 7.2 新增依赖

报告生成时新增以下依赖：
- `mx_data_*机构持股比例*.json` — 股东数据
- `mx_data_*十大流通股东*.json` — 股东明细
- `mx_data_*近{N}日每日*.json` — 日K线数据

---

## 8. 数据解析函数参考

### 8.1 机构持股比例解析

```python
import json

def parse_institutional_ratio(raw_json_path: str) -> dict:
    """解析 mx-data 机构持股比例数据"""
    with open(raw_json_path) as f:
        data = json.load(f)

    dt = data['data']['data']['searchDataResultDTO']['dataTableDTOList'][0]
    raw = dt['rawTable']

    col_id = '100000000003145'
    ratios = raw[col_id]           # ['72.548', '74.143', ...]
    dates = raw['headName']         # ['2026一季报', '2025年报', ...]

    # 最新一期
    latest_ratio = float(ratios[0])
    prev_ratio = float(ratios[1]) if len(ratios) > 1 else None

    return {
        'latest_ratio_pct': latest_ratio,
        'prev_ratio_pct': prev_ratio,
        'qoq_change_pp': (latest_ratio - prev_ratio) if prev_ratio else None,
        'retail_ratio_pct': 100 - latest_ratio,
        'all_dates': list(zip(dates, ratios))
    }
```

### 8.2 日K线解析

```python
def parse_kline_ohlcv(raw_json_path: str) -> list[dict]:
    """解析 mx-data 日K线数据，提取 OHLCV 列表"""
    with open(raw_json_path) as f:
        data = json.load(f)

    dt_list = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    # Table 1 = 历史序列
    dt = dt_list[1] if len(dt_list) > 1 else dt_list[0]

    raw = dt['rawTable']
    table = dt['table']

    dates = raw['headName']  # ['2026-04-29', ...]
    fields = [k for k in raw.keys() if k != 'headName']
    field_names = [dt['nameMap'][f] for f in fields]
    # field_names: ['成交量', '最低价', '最高价', '开盘价', '收盘价']

    # 构建 OHLCV 列表
    rows = []
    for i, date in enumerate(dates):
        row = {'date': date}
        for f in fields:
            val_str = raw[f][i]
            # 去除单位: '348.1万股' -> 348.1, '1401.17元' -> 1401.17
            val_clean = float(val_str.replace('万股', '').replace('万手', '')
                              .replace('元', '').replace('%', ''))
            row[dt['nameMap'][f]] = val_clean
        rows.append(row)

    return rows
```

### 8.3 技术指标计算

```python
import numpy as np

def calc_rsi(closes: list[float], period: int = 14) -> float:
    """计算 RSI(14)"""
    deltas = np.diff(closes)
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains[-period:])
    avg_loss = np.mean(losses[-period:])
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))

def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """计算 MACD(12,26,9)"""
    closes_arr = np.array(closes)
    ema_fast = _ema(closes_arr, fast)
    ema_slow = _ema(closes_arr, slow)
    dif = ema_fast - ema_slow
    dea = _ema(np.array([dif]), signal)[-1] if len(dif) >= signal else dif[-1]
    return {'dif': round(float(dif[-1]), 2), 'dea': round(float(dea), 2), 'histogram': round(float(dif[-1] - dea), 2)}

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """计算 EMA"""
    ema = [arr[0]]
    k = 2 / (period + 1)
    for price in arr[1:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return np.array(ema)

def calc_kdj(highs: list[float], lows: list[float], closes: list[float], n: int = 9) -> dict:
    """计算 KDJ(9,3,3) — 标准递归 EMA 公式"""
    highs_arr, lows_arr = np.array(highs), np.array(lows)
    # RSV = (C - LowN) / (HighN - LowN) × 100
    rsv_values = []
    for i in range(n - 1, len(closes)):
        low_n = min(lows_arr[i - n + 1:i + 1])
        high_n = max(highs_arr[i - n + 1:i + 1])
        rsv = (closes_arr[i] - low_n) / (high_n - low_n + 1e-9) * 100
        rsv_values.append(rsv)

    # K, D 递归 EMA，初始值 50
    k, d = 50.0, 50.0
    for rsv in rsv_values:
        k = 2 / 3 * k + 1 / 3 * rsv
        d = 2 / 3 * d + 1 / 3 * k
    j = 3 * k - 2 * d
    return {'k': round(float(k), 1), 'd': round(float(d), 1), 'j': round(float(j), 1)}
```

---

## 9. 实现检查清单

### Phase 1 数据收集新增

- [ ] F1: mx-data 机构持股比例合计查询 + 解析函数
- [ ] F2: mx-data 股东户数查询 + 解析函数
- [ ] F3: mx-data 十大流通股东查询 + 解析函数
- [ ] T1: mx-data 日K线 OHLCV 查询 + 解析函数
- [ ] T2: mx-data 主力资金流向查询（可选）
- [ ] 更新 Step 1.5 验证门：检查新增文件存在性

### Step 2.5-F

- [ ] 散户占比计算函数
- [ ] 环比变化计算函数（区分 A股/港股粒度）
- [ ] 十大股东类型分类逻辑
- [ ] HKSCC 特殊处理
- [ ] 风险等级判断函数
- [ ] valuation_result.json 字段写入
- [ ] 报告章节输出

### Step 3.5

- [ ] RSI 计算函数（含参数 14）
- [ ] MACD 计算函数（含参数 12,26,9）
- [ ] KDJ 计算函数（含参数 9,3,3）
- [ ] MA 系统计算函数（5/10/20/60）
- [ ] 成交量分析函数（量比、趋势）
- [ ] 信号判断逻辑（RSI 超买超卖、金叉死叉、背离）
- [ ] 综合信号评分逻辑
- [ ] 入场价位计算（支撑/阻力/止损/目标）
- [ ] 信号有效期逻辑（+5交易日）
- [ ] 基本面 vs 技术面冲突处理规则
- [ ] valuation_result.json 字段写入
- [ ] 报告章节输出

### Step 6 校验更新

- [ ] 新增文件类型检查（股东数据文件、K线文件）

---

## 10. 待实测确认项 [PENDING_VERIFY]

以下项需在实现阶段实测验证：

| 项 | 问题 | 验证方法 |
|----|------|---------|
| 日K线数据深度 | mx-data "近35日" 实际返回多少交易日？ | 实测贵州茅台/港股腾讯 |
| 港股日K线 | `.HK` 股票日K线查询是否有效？ | 用腾讯(00700.HK)测试 |
| 港股机构持股 | 港股是否有"机构持股比例合计"字段？ | 用腾讯测试 |
| 港股股东户数 | 港股是否有股东户数数据？ | 用腾讯测试 |
| 主力资金流向 | "近N日 主力资金流向"是否返回有效数据？ | 用贵州茅台测试 |
| 创业板识别 | `entityTagDTO.className` 是否可靠区分创业板/科创板？ | 检查不同股票返回的 className |

---

## 11. 变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.9 | 2026-04-29 | 初始版本。基于实测结果确定：股东数据全可用（散户占比=1-机构持股），日K线可获取（近N日每日OHLCV）。港股数据待实测。 |
