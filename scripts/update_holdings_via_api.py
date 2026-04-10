#!/usr/bin/env python3
"""
通过 API 更新持仓配置

Usage:
    uv run python scripts/update_holdings_via_api.py
"""

import json
import requests
from pathlib import Path

# API 基础 URL
API_BASE = "http://localhost:3120/api"

def add_or_update_stock(code: str, name: str, stock_type: str, cost: float = None, shares: int = None, star: bool = False):
    """通过 API 添加或更新股票"""
    url = f"{API_BASE}/config"
    
    data = {
        "action": "add",
        "code": code,
        "data": {
            "name": name,
            "type": stock_type,
            "star": star
        }
    }
    
    if cost is not None:
        data["data"]["cost"] = cost
    if shares is not None:
        data["data"]["shares"] = shares
    
    try:
        response = requests.post(url, json=data, timeout=10)
        result = response.json()
        if result.get("success"):
            print(f"✅ {code} ({name}) - {result.get('message', 'Updated')}")
            return True
        else:
            print(f"❌ {code} ({name}) - {result.get('message', 'Failed')}")
            return False
    except Exception as e:
        print(f"❌ {code} ({name}) - Error: {e}")
        return False


def main():
    """更新所有持仓"""
    
    # 从截图导入的持仓数据
    holdings = [
        # 同花顺 A股持仓
        ("002436", "兴森科技", "holding", 24.617, 4700, False),
        ("000338", "潍柴动力", "holding", 26.45, 2000, True),
        ("603266", "宏和科技", "holding", 64.512, 500, False),
        ("000063", "中兴通讯", "holding", 51.314, 500, False),
        ("000875", "电投绿能", "holding", 7.281, 3000, False),
        ("601233", "桐昆股份", "holding", 18.715, 100, False),
        ("601288", "农业银行", "holding", 7.376, 600, False),
        ("000001", "平安银行", "holding", 16.865, 200, False),
        ("600036", "招商银行", "holding", 56.223, 100, False),
        ("601166", "兴业银行", "holding", 25.611, 200, False),
        ("600016", "民生银行", "holding", 6.109, 1100, False),
        ("603596", "伯特利", "holding", 59.768, 300, False),
        ("601689", "拓普集团", "holding", 73.772, 200, False),
        ("002920", "德赛西威", "holding", 105.74, 500, False),
        ("601138", "工业富联", "holding", 69.706, 600, False),
        ("600673", "东阳光", "holding", 30.766, 6200, False),
        ("002080", "中材科技", "holding", 48.063, 2200, True),
        # 同花顺港股
        ("HK01810", "小米集团-W", "holding", 51.665, 200, False),
        # 同花顺ETF
        ("588200", "科创芯片ETF", "holding", 1.614, 14100, False),
        ("159915", "创业板ETF", "holding", 2.332, 16500, False),
        # 东方财富港股
        ("HK02359", "药明康德", "holding", 17.546, 200, False),
        ("HK03896", "金山云", "holding", 6.64, 4000, False),
        ("HK06088", "鸿腾精密", "holding", 8.22, 2000, False),
        ("HK02577", "英诺赛科", "holding", 63.872, 600, False),
        ("HK00700", "腾讯控股", "holding", 627.101, 100, False),
        ("HK09988", "阿里巴巴-W", "holding", 169.942, 400, False),
        ("HK03690", "美团-W", "holding", 127.124, 700, False),
        ("HK01211", "比亚迪股份", "holding", 129.907, 1000, True),
        # 长桥证券持仓
        ("HK06809", "澜起科技", "holding", 133.533, 300, True),
        ("HK03858", "佳鑫国际资源", "holding", 118.9, 400, False),
        ("HK07709", "XL二南方海力士", "holding", 21.523, 1500, True),
        ("HK07262", "南方两倍看多日经", "holding", 132.319, 130, True),
        ("HK02590", "极智嘉-W", "holding", 38.062, 800, True),
        ("HK09927", "赛力斯", "holding", 131.5, 100, True),
        ("HK03288", "海天味业", "holding", 42.783, 200, True),
        ("HK02525", "禾赛-W", "holding", 171.9, 40, False),
    ]
    
    print("通过 API 更新持仓配置")
    print("=" * 70)
    
    success_count = 0
    fail_count = 0
    
    for code, name, stock_type, cost, shares, star in holdings:
        if add_or_update_stock(code, name, stock_type, cost, shares, star):
            success_count += 1
        else:
            fail_count += 1
    
    print()
    print("=" * 70)
    print(f"更新完成: 成功 {success_count} 只, 失败 {fail_count} 只")


if __name__ == "__main__":
    main()
