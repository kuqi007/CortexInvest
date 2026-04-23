# mx-search 机构数据提取规范

> 从 mx_search txt 文件中提取机构目标价、评级、增速数据。Phase 2 执行时使用本文档。

---

## 数据获取优先级

```
优先级 1: mx_search 机构评级/目标价 txt 文件（结构化字段）
     ↓ (若无)
优先级 2: mx_search 机构评级/目标价 txt 文件（文本正则提取）
     ↓ (若无)
优先级 3: 标注"机构数据收集受限"
```

**原则**: 文本提取仅作为兜底，不作为首选。

---

## 文件识别

| 数据需求 | 文件名模式 |
|---------|-----------|
| 机构目标价/评级 | `mx_search_{股票名}机构评级_目标价_券商研报*.txt` |
| 行业竞争格局 | `mx_search_*行业竞争格局*.txt` |
| 公司治理/风险 | `mx_search_{股票名}*风险*.txt` |

---

## JSON 结构（来自实际文件）

```json
[
  {
    "code": "AP202604151821233086_2",
    "title": "立讯精密：业绩符合预期，AI通讯有望成为核心增长极",
    "content": "我们预计公司2025-27年的净利润CAGR达到30%...",
    "date": "2026-04-15 15:07:30",
    "informationType": "REPORT",
    "entityFullName": "立讯精密",
    "insName": "中泰证券",
    "blackListStatus": "0",
    "rankScore": 1.0,
    "rating": "买入",
    "emRatingName": "",
    "authorityLevel": "L2-2",
    "communityFlag": false
  }
]
```

### 关键字段说明

| 字段 | 类型 | 说明 | 使用场景 |
|------|------|------|---------|
| `rating` | str | "买入"/"增持"/"中性"/"减持"/"卖出" | **直接使用**，无需提取 |
| `content` | str | 研报正文，含目标价/增速数字 | 正则提取 |
| `title` | str | 研报标题 | 标题含目标价时提取 |
| `insName` | str | 券商名 | 判断权威度 |
| `date` | str | 日期 | 取最新 |
| `informationType` | str | "REPORT"（券商研报）vs "INV_NEWS"（财经新闻） | 过滤 |
| `authorityLevel` | str | "L2-2"（机构研报）vs "L2-1"（媒体） | 过滤 |

---

## 直接使用字段

`rating` 字段已是结构化的，可直接使用：
```python
for item in data:
    if item.get('rating') in ['买入', '增持', '中性', '减持', '卖出']:
        rating = item['rating']
```

---

## 文本提取正则

### 目标价

```python
TARGET_PRICE_PATTERNS = [
    # 最优先：明确标注"目标价"
    r'目标价至?人民币?([0-9.]+)\s*元',
    r'目标价[：:]\s*([0-9.]+)\s*元',
    r'综合目标价?\s*([0-9.]+)\s*元',
    r'给予.*评级.*目标价\s*([0-9.]+)\s*元',
    r'上调目标价至\s*([0-9.]+)\s*元',
    r'目标价[至～到]\s*([0-9.]+)\s*[元港元]',
    # 次选：从"维持XX元"等
    r'维持\s*([0-9.]+)\s*元\s*(?:目标|评级)',
]

def extract_target_price(content):
    for pattern in TARGET_PRICE_PATTERNS:
        match = re.search(pattern, content)
        if match:
            return float(match.group(1))
    return None
```

### 增速/利润预测

```python
GROWTH_PATTERNS = [
    # 最优先：从正文提取净利润预测
    r'(?:202[5-9]|2030)年.*?净利润[为增长]?\s*([0-9.]+)\s*亿',
    r'预计(?:202[5-9]|2030)年.*?(?:净利润|EPS)\s*[为增长]?\s*([0-9.]+)\s*(?:亿|元)',
    r'预测(?:202[5-9]|2030)年.*?(?:净利润|EPS)\s*[为]?\s*([0-9.]+)\s*(?:亿|元)',
    # CAGR
    r'(?:202[5-9]|2030).*?净利润.*?CAGR.*?(?:达到)?([0-9.]+)\s*%',
    r'净利润C?A?G?R.*?(?:20[0-9]{2}).*?([0-9.]+)\s*%',
    
    # v1.8新增: 下调/上调至负数 + 经调整净利润
    # 注意：必须用"净利润"关键词做前文锚定，避免误匹配"收入增长至XX亿"
    r'(?:经调整(?:Non-?IFRS\s*)?净利润|Non-?IFRS\s*(?:经调整)?净利润|(?:归母)?净利润)[^。]*?下调至\s*(-?[0-9.]+)\s*亿',
    r'(?:经调整(?:Non-?IFRS\s*)?净利润|Non-?IFRS\s*(?:经调整)?净利润|(?:归母)?净利润)[^。]*?调整至\s*(-?[0-9.]+)\s*亿',
    r'(?:经调整(?:Non-?IFRS\s*)?净利润|Non-?IFRS\s*(?:经调整)?净利润)[^。]*?(?:为|达)\s*(-?[0-9.]+)\s*亿',
    
    # "亏损XX亿" 口语化写法
    r'(?:经调整)?(?:净)?亏损\s*([0-9.]+)\s*亿',
]

def extract_growth_from_content(content):
    for pattern in GROWTH_PATTERNS:
        match = re.search(pattern, content)
        if match:
            return float(match.group(1))
    return None
```

### v1.8新增: 前瞻盈利预测（含方向变化、负数、经调整口径）

```python
FORWARD_EARNINGS_PATTERNS = [
    # "从X亿下调至Y亿" — 提取新值Y（至后面的数字）
    {
        "pattern": r'(?:经调整(?:Non-?IFRS\s*)?净利润|(?:归母)?净利润)[^。]*?(?:从[^。]*?)?下调至\s*(-?[0-9.]+)\s*亿',
        "extract": "new_value",  # 取"至"后面的新值
        "description": "净利润下调至新值（含负数）",
    },
    {
        "pattern": r'(?:经调整(?:Non-?IFRS\s*)?净利润|(?:归母)?净利润)[^。]*?(?:从[^。]*?)?上调至\s*([0-9.]+)\s*亿',
        "extract": "new_value",
        "description": "净利润上调至新值",
    },
    # "预计FY1净利润XX亿"
    {
        "pattern": r'预计\s*(?:202[5-9]|2030)\s*年[^。]*?(?:经调整)?(?:归母)?净利润[^。]*?(?:为|达|约)\s*(-?[0-9.]+)\s*亿',
        "extract": "direct",
        "description": "直接预测值（含负数）",
    },
    # "经调整净利润扭亏为盈/转正"
    {
        "pattern": r'经调整净利润[^。]*?(?:扭亏为盈|转正|盈利)\s*([0-9.]+)\s*(?:亿|万?元)',
        "extract": "direct",
        "description": "扭亏为盈场景",
    },
]

def extract_forward_earnings(content):
    """从研报文本中提取前瞻盈利预测（含方向变化和负数）
    
    Returns:
        list of dict: [{"year": "FY2026E", "value": -1.92, "direction": "下调", "metric": "经调整净利润", "source_text": "..."}]
    """
    results = []
    for fe in FORWARD_EARNINGS_PATTERNS:
        matches = re.finditer(fe["pattern"], content)
        for m in matches:
            text = m.group(0)
            try:
                value = float(m.group(1))
            except:
                continue
            
            # 推断年份
            year_match = re.search(r'(?:202[5-9]|2030)', text)
            year = f"FY{year_match.group()[-2:]}E" if year_match else "FY?"
            
            # 推断方向
            direction = "下调" if "下调" in text else "上调" if "上调" in text else "预测"
            
            # 推断口径
            metric = "经调整净利润" if "经调整" in text else "归母净利润" if "归母" in text else "净利润"
            
            results.append({
                "year": year,
                "value": value,
                "direction": direction,
                "metric": metric,
                "source_text": text[:100],
            })
    return results
```

### 评级（非 rating 字段，从文本提取时）

```python
RATING_PATTERNS = [
    r'评级[：:]\s*([买入增持中性减持卖出]+)',
    r'维持\s*([买入增持中性减持卖出]+)\s*评级',
    r'给予.*\s*([买入增持中性减持卖出]+)\s*评级',
]
```

---

## 权威度与优先级

```
权威度排序（高→低）:
  1. authorityLevel = "L2-2" + informationType = "REPORT" + insName = 券商名
  2. authorityLevel = "L2-2" + informationType = "REPORT"
  3. authorityLevel = "L2-1" + informationType = "INV_NEWS"
  4. 其他
```

**多来源合并规则**：
- 多个目标价 → 取中位数或均值
- 多个评级 → 取最多出现的
- 以权威度最高的为主要引用来源

---

## 提取示例

```python
import json, re

def load_mx_search(path):
    with open(path, encoding='utf-8') as f:
        return json.load(f)

def extract_institutional(path):
    """从 mx_search 机构文件中提取机构数据"""
    data = load_mx_search(path)
    results = {
        'target_prices': [],
        'ratings': [],
        'profit_predictions': [],
        'forward_earnings': [],  # v1.8新增
        'source': 'mx_search_txt',
        'latest_date': None
    }

    # 按权威度排序
    def authority_score(item):
        level = item.get('authorityLevel', '')
        itype = item.get('informationType', '')
        score = 0
        if level == 'L2-2' and itype == 'REPORT':
            score = 3
        elif level == 'L2-2':
            score = 2
        elif itype == 'REPORT':
            score = 1
        return score

    data.sort(key=authority_score, reverse=True)

    for item in data:
        content = item.get('content', '')
        title = item.get('title', '')
        combined = title + '\n' + content

        # 直接字段
        if item.get('rating') in ['买入', '增持', '中性', '减持', '卖出']:
            results['ratings'].append(item['rating'])

        # 文本提取目标价
        tp = extract_target_price(combined)
        if tp:
            results['target_prices'].append(tp)

        # 文本提取增速
        gr = extract_growth_from_content(combined)
        if gr:
            results['profit_predictions'].append(gr)

        # v1.8: 前瞻盈利预测
        fe = extract_forward_earnings(combined)
        if fe:
            results['forward_earnings'].extend(fe)

        # 最新日期
        date = item.get('date', '')
        if date and (not results['latest_date'] or date > results['latest_date']):
            results['latest_date'] = date

    return results

# 使用
path = "stocks/002475.SZ_立讯精密/v2_2026-04-19/data/mx_search_立讯精密_机构评级_目标价_券商研报_2026年.txt"
inst = extract_institutional(path)

consensus_target_price = median(inst['target_prices']) if inst['target_prices'] else None
consensus_rating = most_common(inst['ratings']) if inst['ratings'] else None
```

---

## 输出格式

最终填入 valuation_result.json 的格式：

```json
{
  "institutional": {
    "consensus_target_price": 68.45,
    "target_price_source": "mx_search_txt",
    "source_file": "mx_search_立讯精密_机构评级_目标价_券商研报_2026年.txt",
    "rating": "买入",
    "rating_count": 3,
    "upside_pct": 14.6,
    "profit_cagr_2025_27": 30,
    "latest_report_date": "2026-04-15",
    "analysts": ["中泰证券", "华兴证券"],
    "data_completeness": "medium",
    "note": "目标价从3家研报文本提取，中泰/华兴/天风"
  }
}
```

---

## 已知限制

1. **非结构化**：目标价/增速嵌于 `content` 文本中，正则提取可能遗漏或误提取
2. **currency 不明确**：部分研报用"元"（A股），部分用"港元"（港股），需结合股票市场判断
3. **日期格式**：`date` 字段格式为 `"2026-04-15 15:07:30"`，需统一处理
4. **负数提取**：正则支持 `-1.92亿` 格式，但不覆盖 `"亏损 1.92亿"` 这种纯文字负数（因为"亏损"后跟的数字在正则中需要单独模式匹配）
5. **多券商口径不一致**：中金可能说-1.92亿，招商可能说+0.5亿，extract_forward_earnings 会返回所有匹配结果，需外部逻辑取中位数或判断方向一致性
6. **口径对齐**：返回的 `metric` 字段标注了利润口径（经调整/归母/普通），使用时必须确保比较的是相同口径
