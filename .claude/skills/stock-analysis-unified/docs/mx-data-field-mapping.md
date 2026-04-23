# mx-data 字段映射表

> 完整字段映射 + 提取函数。Phase 2 执行时使用本文档。

## JSON 结构

```
data.data.searchDataResultDTO.dataTableDTOList[N]
├── nameMap: dict   # col_id → 指标中文名
├── table: dict     # col_id → [值列表]   ⚠️ 是 dict 不是 list！
├── rawTable: dict  # col_id → [原始数值列表]（无单位）
└── entityTagDTO.marketChar  # 市场标识，如 ".HK"、".SZ"

⚠️ table 是 dict，索引用 col_id（字符串），不是 table[0]
⚠️ 多 table 时，每个 table 的 col_id 独立，不能跨 table 通用
```

---

## 行情文件（文件名含"最新价/总市值/市净率/市盈率"）

> ⚠️ **关键发现**：同一文件内有多个 table，共用同一批 col_id 会指向不同数据！
> - Table 0 = **最新一期**快照：f2/f20/f23/f115
> - Table 1 = 历史序列（多年）：326809/325898

### Table 0 — 最新一期快照（用这些！）

| col_id | 指标名 | 腾讯实测值 | 说明 |
|--------|--------|-----------|------|
| `f2` | 最新价 | 508.000港元 | **优先用这个取最新价** |
| `f20` | 总市值 | 4.636万亿 | 最新一期市值 |
| `f23` | 市净率 | 3.63 | 最新一期 PB |
| `f115` | 市盈率(TTM) | 18.62 | 最新一期 PE |

### Table 1 — 历史序列（多年）

| col_id | 指标名 | 腾讯实测值 | 说明 |
|--------|--------|-----------|------|
| `325898` | 收盘价 | 517/499/493.2/490/504.5港元 | 历史收盘价序列 |
| `326809` | 总市值 | 4.718/4.554/4.501/4.472/4.604万亿港元 | 历史市值序列 |

### 其他 Table

| col_id | 所在Table | 指标名 | 说明 |
|--------|-----------|--------|------|
| `100000000046860` | Table 2 | 每股收益EPS(基本) | 单位：港元，不是元！ |
| `100000000047160` | Table 3 | 市盈率(PE,TTM) | 同 f115，历史序列 |
| `100000000045682` | — | 股息率(TTM) | ⚠️ 腾讯文件中未出现，可能为分红后补字段 |

**EPS 注意**：Table 2 的 `100000000046860` 单位是港元（如腾讯27.4港元），不是元！
也可用计算公式：`归母净利润(亿元)×1e8 / 总股本(万股)×1e4`（结果为元）。

---

## 近三年年报文件（A股/H股共用，col_id 不同）

> ⚠️ **H股年报字段 ID 与 A股完全不同**，不能混用！

### 腾讯（H股）实测 col_id

| col_id (H股) | 指标名 | 腾讯实测值 | 说明 |
|-------------|--------|-----------|------|
| `100000000046675` | 归属于母公司股东的净利润 | 2489亿港元 | 归母口径，**主用** |
| `100000000047277` | 净利润 | 2544亿港元 | 报表口径 |
| `100000000051343` | 营业收入 | 8323亿港元 | 主收入口径 |
| `100000000046897` | 销售毛利率(%) | 56.21 | %形式，直接用 |
| `100000000047139` | 净资产收益率ROE(摊薄)(%) | 19.48 | %形式，直接用 |

### A股参考 col_id（未实测，仅供对照）

| col_id (A股) | 指标名 |
|-------------|--------|
| `100000000003705` | 归属于母公司股东的净利润 |
| `100000000003520` | 扣除非经常性损益后归属于母公司股东的净利润 |
| `100000000003703` | 净利润 |
| `100000000000415` | 营业收入 |
| `100000000002972` | 销售毛利率 |
| `100000000003466` | 净资产收益率ROE |
| `100000000004686` | 归属母公司股东的净利润同比增长率 |
| `100000000004687` | 归属母公司股东的净利润同比增长率(扣除非经常性损益) |

**已知差异**：A股用 `销售毛利率`（数值如 11.91 表示 11.91%），H股用 `销售毛利率(%)`（数值同样带%）。

---

## 每股数据文件（文件名含"每股净资产/每股未分配利润/资产负债率"）

> 腾讯实测（1个 table）

| col_id | 指标名 | 腾讯实测值 | 说明 |
|--------|--------|-----------|------|
| `100000000046871` | 每股净资产BPS | 140.1港元 | 单位：港元 |
| `100000000046942` | 资产负债率(%) | 39.13 | %形式 |

---

## 预测 table（在近三年年报文件内部，单独一个 dataTableDTO）

| col_id | 指标名 | 说明 |
|--------|--------|------|
| `100000000004890` | 预测归属于母公司的净利润中值 | 日期标注年份 |
| `100000000004891` | 预测归属于母公司的净利润增长率 | 日期标注年份 |

**注**：若需 EPS/FY1/FY2 等分年预测，用 `预测净利润 / 总股本` 计算。

---

## 单位处理

| 后缀 | 含义 | 转换为元 |
|------|------|----------|
| `亿元` | 亿元 × 1e8 | 数值 × 1e8 |
| `万亿美元` | 万亿美元 × 1e8 | 数值 × 1e12 |
| `亿港元` | 亿港元 × 1e8 | 数值 × 1e8 |
| `万元` | 万元 × 1e4 | 数值 × 1e4 |
| `%` | 百分比 | 数值直接用（已是小数或%形式） |
| `元` | 元 | × 1 |
| `港元` | 港元 | × 1（H股财报单位） |
| `万股` | 万股 | 数值 × 1e4 |

---

## 提取函数（已修复）

```python
import json
import re
import glob
import os

def strip_unit(value_str):
    """去除单位，返回数值（已修复复合单位 bug，如"亿港元""万亿港元"）"""
    if value_str is None:
        return None
    s = str(value_str).strip()
    # % 单独处理
    if s.endswith('%'):
        try:
            return float(s[:-1])
        except:
            return None
    # 复合单位按长度降序匹配（避免"亿港元"被误匹配为"亿"）
    for unit in ['万亿港元', '亿万港元', '万亿美元', '亿美元', '亿港元',
                 '万港元', '万美元', '万亿元', '亿万', '万亿',
                 '亿元', '万元', '万', '亿', '港元', '美元', '元']:
        if s.endswith(unit):
            num_str = s[:-len(unit)].strip()
            try:
                num = float(num_str)
            except:
                return None
            multipliers = {
                '兆': 1e12, '万亿': 1e12, '亿万': 1e8, '万亿港元': 1e12, '亿万港元': 1e8,
                '亿美元': 1e8, '亿港元': 1e8, '万亿美元': 1e8, '万港元': 1e4,
                '万美元': 1e4, '万亿元': 1e12, '亿元': 1e8, '万元': 1e4,
                '万': 1e4, '亿': 1e8, '港元': 1, '美元': 1, '元': 1
            }
            return num * multipliers.get(unit, 1)
    # 无单位
    try:
        return float(s)
    except:
        return None

def load_mx_data(path):
    """加载 mx_data JSON"""
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def get_table(data, table_index=0):
    """获取指定 table 的数据"""
    dt_list = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    if table_index >= len(dt_list):
        return None
    return dt_list[table_index]

def get_latest_by_name(data, metric_name, table_index=0):
    """从指定 table 按指标名提取最新值

    ⚠️ 实测发现：mx-data 年报数据最新一期在 [0]（数组头部），最老在 [-1]（数组尾部）
    行情数据（f系列）最新一期同样在 [0]。

    Args:
        data: 解析后的 mx_data JSON
        metric_name: 指标中文名（如"最新价"、"归属于母公司股东的净利润"）
        table_index: 目标 table 序号（默认0）

    Returns:
        float 或 None
    """
    dt = get_table(data, table_index)
    if not dt:
        return None
    name_map = dt.get('nameMap', {})
    table = dt.get('table', {})
    for col_id, name in name_map.items():
        if name == metric_name:
            vals = table.get(col_id, [])
            if vals:
                return strip_unit(vals[0])   # 最新一期在 [0]，不是 [-1]！
    return None

def get_all_by_name(data, metric_name, table_index=0):
    """提取指标所有期次值（用于趋势/CAGR）

    ⚠️ 返回顺序：[最新, 中间, 最老]。CAGR 计算时取首尾两元素。
    """
    dt = get_table(data, table_index)
    if not dt:
        return None
    name_map = dt.get('nameMap', {})
    table = dt.get('table', {})
    for col_id, name in name_map.items():
        if name == metric_name:
            vals = table.get(col_id, [])
            return [strip_unit(v) for v in vals if v and str(v).strip()]
    return None

def calc_eps(net_profit_yi, total_shares_wan):
    """计算 EPS（单位：元）

    Args:
        net_profit_yi: 归母净利润（亿元）
        total_shares_wan: 总股本（万股）

    Returns:
        EPS 元
    """
    if not net_profit_yi or not total_shares_wan:
        return None
    return net_profit_yi * 1e8 / (total_shares_wan * 1e4)

def calc_cagr(vals, years=None):
    """计算 CAGR

    Args:
        vals: [最新值, 中间值, ..., 最老值]（已去除单位）。
              CAGR = (vals[0]/vals[-1])^(1/n) - 1
              其中 vals[0]=最新期，vals[-1]=最老期，n=年数
        years: 年数，默认 len(vals)-1
    """
    if len(vals) < 2 or vals[-1] <= 0:
        return None
    n = years if years else (len(vals) - 1)
    if n < 1:
        return None   # 单期数据无法计算 CAGR
    return ((vals[0] / vals[-1]) ** (1 / n) - 1) * 100  # vals[0]=最新 / vals[-1]=最老
```

---

## 提取步骤（操作清单）

```python
import glob, os

DATA_DIR = "stocks/00700.HK_腾讯控股/v2_2026-04-19/data"

# ============================================================
# 1. 行情数据（用 Table 0，col_id 为 f 系列）
# ============================================================
mkt_file = glob.glob(f"{DATA_DIR}/mx_data_*最新价*总市值*市盈率*市净率*raw.json")[0]
mkt_data = load_mx_data(mkt_file)

price     = get_latest_by_name(mkt_data, "最新价", table_index=0)     # 508.0 港元
market_cap = get_latest_by_name(mkt_data, "总市值", table_index=0)    # 4.636e12 港元
pe_ttm    = get_latest_by_name(mkt_data, "市盈率(TTM)", table_index=0)  # 18.62
pb        = get_latest_by_name(mkt_data, "市净率", table_index=0)     # 3.63

# ============================================================
# 2. 年报数据（用 Table 0，col_id 为 H 股 ID）
# ============================================================
fin_file = glob.glob(f"{DATA_DIR}/mx_data_*近三年*收入*净利润*ROE*毛利率*raw.json")[0]
fin_data = load_mx_data(fin_file)

net_profit  = get_latest_by_name(fin_data, "归属于母公司股东的净利润", table_index=0)  # 2489e8 港元
revenue     = get_latest_by_name(fin_data, "营业收入", table_index=0)                    # 8323e8 港元
gross_margin = get_latest_by_name(fin_data, "销售毛利率(%)", table_index=0)             # 56.21
roe         = get_latest_by_name(fin_data, "净资产收益率ROE(摊薄)(%)", table_index=0)    # 19.48

# ============================================================
# 3. 计算派生指标
# ============================================================
# 净利率（无独立字段，需计算）
net_margin = (net_profit / revenue * 100) if (revenue and net_profit) else None  # %

# EPS（可用 Table 2 直接字段，或计算）
eps_actual = get_latest_by_name(mkt_data, "每股收益EPS(基本)", table_index=2)  # 27.4 港元（直接）
# 或计算: eps_calc = calc_eps(net_profit / 1e8, total_shares_wan)  # 结果为元

# ============================================================
# 4. CAGR（取所有期次值）
# ============================================================
revenue_vals = get_all_by_name(fin_data, "营业收入", table_index=0)
# revenue_vals = [6088e8, 6608e8, 8323e8]  (3年)
cagr_3yr = calc_cagr(revenue_vals) if revenue_vals else None  # 2019-2022 3年CAGR

# ============================================================
# 5. 前瞻预测（预测 table 在 Table 1）
# ============================================================
# 注意：腾讯年报文件 Table 1 不是预测数据，需从 mx_search 或研报补充
```

---

## 货币单位检测 (v1.8 新增)

### 问题

港股上市公司（如明略科技 02718.HK）的 mx-data 返回数据存在**双口径问题**：
- **Table 0**（数据浏览器）：自动汇率转换为港币，单位标注"亿港元"
- **Table 1**（F10利润表）：原始币种，如"人民币"
- 同一指标在两个 table 中数值不同（差汇率 ~10%）

### detect_currency() 函数

```python
def detect_currency(data):
    """从mx-data JSON中检测报表货币单位和table口径
    
    优先级:
    1. table中"原始币种"字段（F10 table特有，最可靠）
    2. table值中的单位后缀（"亿港元" → HKD, "亿元" → RMB）
    3. entityTagDTO.marketChar 推断（.HK → 港元, .SZ/.SH → 人民币）
    
    Returns:
        dict: {
            "reporting_currency": "RMB" | "HKD" | "USD",
            "mx_data_table0_unit": "HKD" | "RMB",  # Table 0 的口径
            "exchange_rate_applied": bool,            # Table 0 是否做了汇率转换
        }
    """
    dt_list = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    
    reporting_currency = None
    table0_unit = None
    
    for dt in dt_list:
        table = dt.get('table', {})
        name_map = dt.get('nameMap', {})
        
        # 优先级1: 原始币种字段
        for col_id, name in name_map.items():
            if name == '原始币种':
                vals = table.get(col_id, [])
                if vals and vals[0]:
                    orig = str(vals[0])
                    if '人民币' in orig:
                        reporting_currency = 'RMB'
                    elif '港元' in orig:
                        reporting_currency = 'HKD'
                    elif '美元' in orig:
                        reporting_currency = 'USD'
        
        # 优先级2: 从值后缀推断table口径
        if table0_unit is None:
            for col_id, vals in table.items():
                for v in vals[:3]:  # 只看前3个值
                    v_str = str(v)
                    if '港元' in v_str:
                        table0_unit = 'HKD'
                        break
                    elif '亿元' in v_str and '港元' not in v_str:
                        table0_unit = 'RMB'
                        break
                if table0_unit:
                    break
    
    # 优先级3: marketChar fallback
    if reporting_currency is None:
        market = dt_list[0].get('entityTagDTO', {}).get('marketChar', '')
        reporting_currency = 'HKD' if market == '.HK' else 'RMB'
    
    if table0_unit is None:
        table0_unit = reporting_currency
    
    return {
        "reporting_currency": reporting_currency,
        "mx_data_table0_unit": table0_unit,
        "exchange_rate_applied": (reporting_currency != table0_unit),
    }
```

### currency_source 标注规则

在 valuation_result.json 的 financials 字段中，必须添加:

```json
{
  "financials": {
    "currency_source": {
      "reporting_currency": "RMB",
      "mx_data_table0_unit": "HKD",
      "exchange_rate_applied": true,
      "hkdrmb_rate": 0.88,
      "note": "报表原始币种为RMB, mx-data Table 0 自动转为HKD, 数值差异约10%"
    }
  }
}
```

**硬规则**:
- 不重命名现有字段（`revenue_rmb` 等保持不变）
- 在 financials 顶层添加 `currency_source` 对象即可
- 若 `exchange_rate_applied = true`，所有从 Table 0 提取的数值必须标注单位为 table 口径（通常为HKD）
- OCF、净利润等绝对值指标的单位以 `currency_source` 为准，不得假设为 RMB

---

## 多 entity（A+H）处理

```python
def get_primary_entity_data(data, prefer_a_share=True):
    """获取主 entity 数据

    规则:
      - 优先 .SZ / .SH（A 股主体）
      - 纯港股直接使用
      - 识别方式: entityTagDTO.marketChar 字段

    Returns:
        dataTableDTO dict
    """
    dt_list = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    for dt in dt_list:
        tag = dt.get('entityTagDTO', {})
        market = tag.get('marketChar', '')
        if prefer_a_share and market in ['.SZ', '.SH']:
            return dt
    # 无 A 股则取第一个（非空 table）
    for dt in dt_list:
        if dt.get('nameMap'):
            return dt
    return dt_list[0] if dt_list else None
```

---

## 已知限制

1. **近五年数据不保证**：mx-data 系统实际只返回 3 年，`5yr_cagr_revenue` 降级为 3yr
2. **分红率无独立字段**：需从 mx-search 或财报提取（`100000000045682` 股息率字段在腾讯文件中不存在）
3. **历史 PE/PB Band 无独立文件**：需单独查询 PE Band 数据
4. **H股毛利率/ROE**：用 `销售毛利率(%)` 和 `净资产收益率ROE(摊薄)(%)`，A 股用 `销售毛利率` 和 `净资产收益率ROE`
5. **EPS 单位**：Table 2 `每股收益EPS(基本)` 单位是港元，不是元！计算时注意单位统一
