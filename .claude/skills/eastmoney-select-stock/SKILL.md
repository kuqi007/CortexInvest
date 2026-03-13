---
name: eastmoney-select-stock
description: 东方财富智能选股，基于行情/财务指标筛选股票，支持A股/港股/美股
argument-hint: [选股条件]
---

# 东方财富智能选股 Skill

通过自然语言查询进行选股，返回符合条件的股票列表。

## 适用场景

- 按行情指标选股（涨幅、市值、成交量等）
- 按财务指标选股（净利润、ROE、负债率等）
- 查询指定行业/板块内的股票
- 查询板块指数成分股
- 股票推荐

## 使用前提

1. 获取 API Key：在东方财富 Skills 页面申请
2. 设置环境变量：`EASTMONEY_APIKEY`
3. 检查本地 API 是否可用，若存在可直接使用
4. **⚠️ 每个 Skill 每天限调用 50 次，省着点用**

**设置环境变量**：
```bash
export EASTMONEY_APIKEY="你的APIKey"
```

> 建议将上述命令加到 `~/.zshrc` 或 `~/.bashrc` 永久生效。

## API 调用方式

```bash
curl -X POST "https://mkapi2.dfcfs.com/finskillshub/api/claw/stock-screen" \
  -H "Content-Type: application/json" \
  -H "apikey: $EASTMONEY_APIKEY" \
  -d '{"keyword": "<选股条件>", "pageNo": 1, "pageSize": 20}'
```

## 查询示例

| 类型 | 示例 |
|-----|------|
| 涨幅选股 | "今日涨幅2%的股票" |
| 行业选股 | "半导体行业股票" |
| 资金选股 | "主力资金净流入前10" |
| 综合选股 | "市值大于500亿且PE小于20" |
| 板块成分 | "新能源汽车板块成分股" |

## 返回数据解析

核心路径 `data.data.result`：

| 字段 | 含义 |
|-----|------|
| `total` | 符合条件的股票数量 |
| `columns` | 列定义（key → 中文标题） |
| `dataList` | 股票数据列表 |

**常用列键**：

| 键 | 含义 |
|-----|------|
| `SECURITY_CODE` | 股票代码 |
| `SECURITY_SHORT_NAME` | 股票简称 |
| `MARKET_SHORT_NAME` | 市场（SH/SZ） |
| `NEWEST_PRICE` | 最新价 |
| `CHG` | 涨跌幅 |
| `PCHG` | 涨跌额 |

## 实现代码

```python
import os
import requests
import json

def select_stock(keyword: str, page: int = 1, page_size: int = 20) -> dict:
    """Screen stocks based on keyword."""
    api_key = os.environ.get("EASTMONEY_APIKEY")
    if not api_key:
        return {"error": "请设置环境变量 EASTMONEY_APIKEY"}

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/stock-screen"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key
    }
    payload = {
        "keyword": keyword,
        "pageNo": page,
        "pageSize": page_size
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=30)
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"error": str(e)}

def parse_screening_result(data: dict) -> str:
    """Parse stock screening result to readable format."""
    if "error" in data:
        return f"选股失败: {data['error']}"

    result = data.get("data", {}).get("result", {})
    total = result.get("total", 0)
    columns = result.get("columns", [])
    data_list = result.get("dataList", [])

    if total == 0:
        return "未找到符合条件的股票，请到东方财富妙想AI选股"

    # Build column key -> title map
    col_map = {col.get("key"): col.get("title") for col in columns}

    # Parse each stock
    stocks = []
    for item in data_list:
        code = item.get("SECURITY_CODE", "")
        name = item.get("SECURITY_SHORT_NAME", "")
        market = item.get("MARKET_SHORT_NAME", "")
        price = item.get("NEWEST_PRICE", "-")
        chg = item.get("CHG", "-")

        full_code = f"{code}.{market}" if market else code
        stocks.append(f"{name} {full_code} 最新价:{price} 涨跌幅:{chg}%")

    return f"共找到 {total} 只符合条件的股票:\n" + "\n".join(stocks)
```

## 使用示例

```python
# 选涨幅2%的股票
result = select_stock("今日涨幅2%的股票")
print(parse_screening_result(result))

# 半导体行业股票
result = select_stock("半导体行业股票", page_size=30)
print(parse_screening_result(result))
```

## 数据为空

若返回空结果，提示用户到东方财富妙想 AI 直接选股。
