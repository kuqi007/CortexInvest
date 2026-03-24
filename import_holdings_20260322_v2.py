#!/usr/bin/env python3
"""持仓更新脚本 - 2026-03-22 截图对比后增量更新"""

import time
import requests

BASE = "http://localhost:3120/api"

HEADERS = {
    "Content-Type": "application/json",
    "x-ads-token-data": '{"access_token":{"userid":"B8CJF562MZ96SVDT"},"client_id":"gAbnwKAccrMgJ2SAVD7tBODLxii8ijVt"}',
    "x-ads-gateway-secret": "b4e09b5cc70f469860d8a6aa7f4553c5",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def api_call(action, code, data):
    payload = {"action": action, "code": code, "data": data}
    r = SESSION.post(f"{BASE}/config", json=payload, timeout=10)
    return r.json()


def update_or_add(code, data, label):
    result = api_call("update", code, data)
    if not result.get("success", False) and "not in watchlist" in result.get("message", ""):
        result = api_call("add", code, data)
    msg = result.get("message", "")
    tag = "OK" if result.get("success", False) else "FAIL"
    print(f"  [{tag}] {label} {code}: {msg}")
    time.sleep(1)


# 已有持仓更新（触发 change_log source=manual）
print("=== 已有持仓更新 ===")
api_call("update", "600673", {"cost": 29.144, "shares": 1400, "lot": 100})
time.sleep(1)
api_call("update", "HK02590", {"cost": 30.948, "shares": 1200, "lot": 100})
time.sleep(1)

# 新增港股持仓（熊猫证券）
hk_new = [
    ("HK02259", "紫金黄金国际",    100, 229.374,  100),
    ("HK00700", "腾讯控股",        100, 623.097,  100),
    ("HK02196", "大麦娱乐",      20000,   1.370,  100),
    ("HK03690", "美团-W",        1000, 126.408,  100),
    ("HK06809", "澜起科技",        300, 150.700,  100),
    ("HK07262", "XL二南方看多日经",  160, 135.344,  100),
    ("HK07709", "XL二南方海力士",   800,  21.703,  100),
    ("HK09927", "赛力斯",          100, 131.500,  100),
    ("HK03288", "海天味业",        100,  47.686,  100),
    ("HK02577", "英诺赛科",        500,  63.814,  100),
    ("HK06060", "众安在线",        300,  17.665,  100),
    ("HK09988", "阿里巴巴-W",      500, 161.300,  100),
    ("HK12111", "比亚迪股份",      700, 139.127,  100),
]

# 新增 A股 ETF（国泰海通）
a_etf_new = [
    ("159915", "易方达创业板ETF",   16500, 2.333,   0),
    ("588050", "华夏科创板50ETF",   18400, 1.632,   0),
]

# 新增 A股（国泰海通）
a_stock_new = [
    ("002436", "兴森科技",        10000, 23.425,  0),
]

# 金科 ETF 成本更新
kj_update = ("159851", {"cost": 1.482, "shares": 4000, "lot": 0})

print("\n=== 新增港股持仓（熊猫证券）===")
for code, name, shares, cost, lot in hk_new:
    update_or_add(code, {"name": name, "shares": shares, "cost": cost, "lot": lot, "type": "holding"}, "panda")

print("\n=== 新增 A股 ETF（国泰海通）===")
for code, name, shares, cost, lot in a_etf_new:
    update_or_add(code, {"name": name, "shares": shares, "cost": cost, "lot": lot, "type": "holding"}, "haitong")

print("\n=== 新增 A股（国泰海通）===")
for code, name, shares, cost, lot in a_stock_new:
    update_or_add(code, {"name": name, "shares": shares, "cost": cost, "lot": lot, "type": "holding"}, "haitong")

print("\n=== 金科 ETF 成本更新 ===")
update_or_add(kj_update[0], kj_update[1], "haitong")

print("\n=== 完成 ===")
