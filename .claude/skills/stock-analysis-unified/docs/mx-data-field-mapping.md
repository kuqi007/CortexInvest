# mx-data 字段映射表

> 完整字段映射 + 提取函数。Phase 2 执行时使用本文档。

## JSON 结构

```
data.data.searchDataResultDTO.dataTableDTOList[N]
├── nameMap: dict   # col_id → 指标中文名
├── table: dict     # col_id → [值列表]   ⚠️ 是 dict 不是 list！
└── rawTable: dict  # col_id → [原始数值列表]（无单位）

⚠️ table 是 dict，索引用 col_id（字符串），不是 table[0]
⚠️ entity 过滤用 entityTagDTO.marketChar 字段
```

---

## 单位处理

| 后缀 | 含义 | 转换为元 |
|------|------|----------|
| `亿元` | 亿元 × 1e8 | 数值 × 1e8 |
| `万元` | 万元 × 1e4 | 数值 × 1e4 |
| `%` | 百分比 | 数值直接用（已是小数或%形式） |
| `元` | 元 | × 1 |
| `万股` | 万股 | 数值 × 1e4 |

---

## 已验证的 col_id 映射

以下 col_id 均从实际 mx_data 文件中确认（通过 nameMap 验证）。

### 行情文件（文件名含"最新价/总市值/市净率/市盈率"）

| col_id | 指标名 | 说明 |
|--------|--------|------|
| `325898` | 收盘价 | 最新价（取最新一期） |
| `326809` | 总市值 | 元/港元 |
| `328664` | 市净率PB | |
| `328773` | 市盈率PE(TTM) | |
| `100000000045682` | 股息率(TTM) | 单独一个 table |

**EPS 无独立字段**，需计算：
```
EPS(元) = 归母净利润(亿元) × 1e8 / 总股本(万股) × 1e4
```

### 近三年年报文件（A股，H股共用不同 col_id）

| col_id (A股) | col_id (H股) | 指标名 | 说明 |
|-------------|-------------|--------|------|
| `100000000003705` | `100000000046675` | 归属于母公司股东的净利润 | 归母口径，主用 |
| `100000000003520` | `100000000047278` | 扣除非经常性损益后归属于母公司股东的净利润 | 扣非口径 |
| `100000000003703` | `100000000047277` | 净利润 | 报表口径 |
| `100000000000415` | `100000000051343` | 营业收入 | 主收入口径 |
| `100000000000210` | — | 营业总收入 | 含其他业务收入 |
| `100000000002972` | — | 销售毛利率 | % |
| `100000000003466` | — | 净资产收益率ROE | % |
| `100000000004686` | — | 归属母公司股东的净利润同比增长率 | % YoY |
| `100000000004687` | — | 归属母公司股东的净利润同比增长率(扣除非经常性损益) | % YoY |
| `100000000000688` | — | 单季度.营业总收入 | |
| `100000000003199` | — | 单季度.营业总收入同比增长率 | |
| `100000000000626` | — | 单季度.归属母公司股东的净利润 | |
| `100000000003207` | — | 单季度.归属母公司股东的净利润同比增长率 | |

**预测 table（在近三年年报文件内部，单独一个 dataTableDTO）**：

| col_id | 指标名 | 说明 |
|--------|--------|------|
| `100000000004890` | — | 预测归属于母公司的净利润中值（日期标注） |
| `100000000004891` | — | 预测归属于母公司的净利润增长率（日期标注） |

**注**：若需 EPS/FY1/FY2 等分年预测，用 `100000000004890 / 总股本` 计算。

### 近五年年报文件

> ⚠️ 系统限制：实际只返回近 3 年数据（文件内含警告）。CAGR 计算降级为 3yr。

| col_id | 指标名 | 说明 |
|--------|--------|------|
| `100000000000259` | 经营活动产生的现金流量净额 | 亿元，经营现金流质量 |
| 其余字段同近三年年报文件 | | |

---

## 提取函数

```python
import json
import re

def strip_unit(value_str):
    """去除单位，返回数值"""
    if value_str is None:
        return None
    match = re.match(r'([0-9.-]+)([亿万港元万美元]*)', str(value_str))
    if not match:
        return None
    num = float(match.group(1))
    unit = match.group(2)
    multipliers = {'亿': 1e8, '万': 1e4, '元': 1, '': 1}
    return num * multipliers.get(unit, 1)

def load_mx_data(path):
    """加载 mx_data JSON"""
    with open(path, encoding='utf-8') as f:
        return json.load(f, strict=False)

def extract_metric(data, metric_name, entity_market=None):
    """从 mx_data JSON 提取指定指标

    Args:
        data: 解析后的 mx_data JSON
        metric_name: 指标中文名（如"归属于母公司股东的净利润"）
        entity_market: 限定市场，如 ".SZ" / ".SH" / ".HK"（可选）

    Returns:
        list[float]: 所有期次的值（已去除单位），最新一期在最后
    """
    dt_list = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    for dt in dt_list:
        # 多 entity 时按市场过滤
        if entity_market:
            tag = dt.get('entityTagDTO', {})
            market_char = tag.get('marketChar', '')
            if entity_market not in market_char:
                continue
        name_map = dt.get('nameMap', {})
        table = dt.get('table', {})
        for col_id, name in name_map.items():
            if name == metric_name:
                raw_vals = table.get(col_id, [])
                return [strip_unit(v) for v in raw_vals if v and str(v).strip()]
    return None

def get_latest_value(data, metric_name, entity_market=None):
    """取最新一期的值"""
    vals = extract_metric(data, metric_name, entity_market)
    if vals:
        return vals[-1]  # 最新一期在最后
    return None

def calc_eps(net_profit_yi, total_shares_wan):
    """计算 EPS

    Args:
        net_profit_yi: 归母净利润（亿元）
        total_shares_wan: 总股本（万股）

    Returns:
        EPS 元
    """
    if not net_profit_yi or not total_shares_wan:
        return None
    return net_profit_yi * 1e8 / (total_shares_wan * 1e4)

def calc_cagr(vals, years):
    """计算 CAGR

    Args:
        vals: [期初值, 期末值]
        years: 年数

    Returns:
        CAGR 百分比
    """
    if len(vals) < 2 or years <= 0 or vals[0] <= 0:
        return None
    return ((vals[-1] / vals[0]) ** (1 / years) - 1) * 100
```

---

## 提取示例

```python
import glob, os

DATA_DIR = "stocks/002475.SZ_立讯精密/v2_2026-04-19/data"

# 1. 行情
price_file = glob.glob(f"{DATA_DIR}/mx_data_*最新价*总市值*PE*")[0]
price_data = load_mx_data(price_file)
price = get_latest_value(price_data, "收盘价")            # → 59.75
pe = get_latest_value(price_data, "市盈率PE(TTM)")        # → 18.xx
market_cap = get_latest_value(price_data, "总市值")        # → 4.35e11

# 2. 财务
fin_file = glob.glob(f"{DATA_DIR}/mx_data_*近三年年报*净利润*")[0]
fin_data = load_mx_data(fin_file)
net_profit = get_latest_value(fin_data, "归属于母公司股东的净利润")  # → 165.99亿
adj_profit = get_latest_value(fin_data, "扣除非经常性损益后归属于母公司股东的净利润")  # → 141.7亿
revenue = get_latest_value(fin_data, "营业收入")            # → 3323亿
gross_margin = get_latest_value(fin_data, "销售毛利率")     # → 11.91
roe = get_latest_value(fin_data, "净资产收益率ROE")         # → 21.52
profit_growth = get_latest_value(fin_data, "归属母公司股东的净利润同比增长率")  # → YoY

# 3. EPS 计算
eps = calc_eps(net_profit, total_shares_wan=72859800)      # 需查总股本

# 4. 前瞻预测
# 近三年年报文件含预测 table，可按同样方式提取
fwd_profit = get_latest_value(fin_data, "预测归属于母公司的净利润中值")  # → 169.8亿

# 5. CAGR
revenue_vals = extract_metric(fin_data, "营业收入")
# revenue_vals = [271.7e8, 385.0e8, 1458.2e8, 2319.9e8, 3323.4e8]  (近3-5年)
if len(revenue_vals) >= 2:
    cagr = calc_cagr(revenue_vals, len(revenue_vals) - 1)  # 实际年数
```

---

## 多 entity（A+H）处理

```python
def get_primary_entity_data(data):
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
        if market in ['.SZ', '.SH']:
            return dt
    # 无 A 股则取第一个
    return dt_list[0] if dt_list else None
```

---

## 净利率

mx-data **无独立"净利率"字段**。需计算：
```
净利率 = 归母净利润(亿元) / 营业收入(亿元) × 100%
```

---

## 已知限制

1. **近五年数据不保证**：mx-data 系统实际只返回 3 年，`5yr_cagr_revenue` 降级为 3yr
2. **分红率无独立字段**：需从 mx-search 或财报提取
3. **历史 PE/PB Band 无独立文件**：需单独查询 PE Band 数据
4. **H 股部分字段缺失**：毛利率/ROE 等在 H 股 table 中可能不存在
