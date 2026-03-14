---
name: mx_selfselect
description: 基于东方财富妙想自选股API，管理用户的自选股列表（查询、添加、删除），返回JSON格式结果。
user_invocable: true
---

# 妙想自选股管理 (mx_selfselect)

## Overview

本 Skill 基于**东方财富通行证账户数据**，支持通过**自然语言**实现以下三个功能：
- 查询我的自选股列表
- 添加指定股票到我的自选股列表
- 从我的自选股列表中删除指定股票

## When to Use

- 用户询问"我的自选股有哪些"、"查询自选股"时
- 用户说"把XXX股票加入自选"、"添加XXX到自选"时
- 用户说"把XXX从自选删除"、"删除XXX自选"时

## API 调用方式

### 1. API Key 配置

将 apikey 存到环境变量，命名为 `MX_APIKEY`，或检查本地 api 是否存在。

```bash
# 设置环境变量（临时）
export MX_APIKEY="your_api_key_here"
```

### 2. 查询自选股列表

```bash
curl -X POST --location 'https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get' \
--header 'Content-Type: application/json' \
--header 'apikey: your_api_key_here' \
--data '{}'
```

### 3. 添加/删除自选股

```bash
curl -X POST --location 'https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/manage' \
--header 'Content-Type: application/json' \
--header 'apikey: your_api_key_here' \
--data '{"query": "把东方财富加入自选"}'
```

## 问句示例

| 类型 | query |
|------|-------|
| 查询自选股 | 查询我的自选股列表 |
| 添加自选股 | 把贵州茅台添加到我的自选股列表 |
| 删除自选股 | 把贵州茅台从我的自选股列表删除 |

## Python 调用封装

```python
import os
import json
import requests

def mx_selfselect_query() -> dict:
    """查询自选股列表"""
    api_key = os.environ.get("MX_APIKEY")
    if not api_key:
        # 尝试读取环境变量 EASTMONEY_APIKEY 作为 fallback
        api_key = os.environ.get("EASTMONEY_APIKEY")

    if not api_key:
        raise ValueError("请配置 MX_APIKEY 或 EASTMONEY_APIKEY 环境变量")

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/get"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key
    }

    response = requests.post(url, json={}, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def mx_selfselect_manage(query: str) -> dict:
    """添加或删除自选股

    Args:
        query: 操作指令，如 "把贵州茅台加入自选" 或 "把贵州茅台从自选删除"

    Returns:
        操作结果字典
    """
    api_key = os.environ.get("MX_APIKEY")
    if not api_key:
        api_key = os.environ.get("EASTMONEY_APIKEY")

    if not api_key:
        raise ValueError("请配置 MX_APIKEY 或 EASTMONEY_APIKEY 环境变量")

    url = "https://mkapi2.dfcfs.com/finskillshub/api/claw/self-select/manage"
    headers = {
        "Content-Type": "application/json",
        "apikey": api_key
    }
    data = {"query": query}

    response = requests.post(url, json=data, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


# 使用示例
if __name__ == "__main__":
    # 查询自选股
    result = mx_selfselect_query()
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 添加自选
    result = mx_selfselect_manage("把贵州茅台加入自选")
    print(json.dumps(result, ensure_ascii=False, indent=2))

    # 删除自选
    result = mx_selfselect_manage("把贵州茅台从自选删除")
    print(json.dumps(result, ensure_ascii=False, indent=2))
```

## 接口结果释义

### 查询自选股接口返回格式

```json
{
  "status": 0,
  "message": "ok",
  "data": {
    "allResults": {
      "result": {
        "columns": [
          {"title": "股票代码", "key": "SECURITY_CODE"},
          {"title": "股票简称", "key": "SECURITY_SHORT_NAME"},
          {"title": "最新价(元)", "key": "NEWEST_PRICE"},
          {"title": "涨跌幅(%)", "key": "CHG", "redGreenAble": true},
          ...
        ],
        "dataList": [
          {"SECURITY_CODE": "600519", "SECURITY_SHORT_NAME": "贵州茅台", "NEWEST_PRICE": 1800.00, "CHG": 2.5},
          ...
        ]
      },
      "title": "我的自选"
    }
  }
}
```

### 添加/删除操作返回格式

```json
{
  "status": 0,
  "message": "ok",
  "requestId": "xxx"
}
```

## 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| API Key 无效 | 提示用户配置正确的 MX_APIKEY |
| 网络超时 | 重试一次，仍失败则提示用户稍后重试 |
| 查询结果为空 | 提示用户到东方财富App查看自选股 |
| 操作失败 | 返回具体错误信息 |

## 注意事项

1. **查询自选股不带任何 body**（空 JSON `{}`）
2. **添加/删除需要带 query 字段**
3. API Key 可以用 `EASTMONEY_APIKEY` 作为 fallback
