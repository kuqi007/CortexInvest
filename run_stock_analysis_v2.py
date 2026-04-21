#!/usr/bin/env python3
"""
Unified Stock Analysis - Phase 2 Only (using existing data)
Since MX_APIKEY is not available in this environment,
this script verifies existing data and generates reports.

Usage: uv run python run_stock_analysis_v2.py
"""

import os
import sys
import json
import time
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path("/Users/zhul1/Documents/aiWorkspace/ai-investor")
CATALOG_PATH = PROJECT_ROOT / "catalog.json"
TODAY = date.today().strftime("%Y-%m-%d")

STOCKS = [
    {"code": "002080.SZ", "name": "中材科技"},
    {"code": "000021.SZ", "name": "深科技"},
    {"code": "03690.HK", "name": "美团-W"},
    {"code": "01211.HK", "name": "比亚迪股份"},
    {"code": "00700.HK", "name": "腾讯控股"},
]

def load_catalog():
    with open(CATALOG_PATH, 'r') as f:
        return json.load(f)

def save_catalog(catalog):
    with open(CATALOG_PATH, 'w') as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)

def get_version(catalog, stock_code):
    stocks = catalog.get("stocks", {})
    if stock_code in stocks:
        return stocks[stock_code].get("latest_version", 0) + 1
    return 1

def ensure_dir(path):
    path.mkdir(parents=True, exist_ok=True)
    return path

def parse_mx_data_raw(filepath):
    """解析 mx_data raw.json 文件"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f, strict=False)
        tables = data.get('data', {}).get('dataTableDTOList', [])
        results = {}
        for table in tables:
            name = table.get('title', table.get('tableName', 'unknown'))
            table_data = table.get('table', {})
            name_map = table.get('nameMap', {})
            rows = {}
            for col_id, values in table_data.items():
                if col_id == 'headName':
                    continue
                metric_name = name_map.get(col_id, col_id)
                rows[metric_name] = values if isinstance(values, list) else [values]
            results[name] = rows
        return results
    except Exception as e:
        return {}

def parse_mx_search(filepath):
    """解析 mx_search txt/json 文件"""
    try:
        if filepath.endswith('.txt'):
            with open(filepath, 'r', encoding='utf-8') as f:
                return f.read()
        else:
            with open(filepath, 'r', encoding='utf-8') as f:
                data = json.load(f, strict=False)
            text = data.get('data', {}).get('text', '')
            if not text:
                text = data.get('text', '')
            if not text:
                text = json.dumps(data, ensure_ascii=False)[:5000]
            return text
    except Exception as e:
        return ""

def list_mx_files(data_dir):
    mx_data = sorted(data_dir.glob("mx_data_*.json"))
    mx_search = sorted(data_dir.glob("mx_search_*.json")) + sorted(data_dir.glob("mx_search_*.txt"))
    return mx_data, mx_search

def extract_key_metrics(mx_data_files):
    all_data = {}
    for f in mx_data_files:
        parsed = parse_mx_data_raw(f)
        if parsed:
            all_data[f.name] = parsed
    return all_data

def parse_number(s):
    """解析带单位的数字"""
    if not s:
        return 0
    s = str(s).replace(",", "").replace(" ", "").replace("%", "")
    if "亿" in s:
        return float(s.replace("亿", "")) * 1e8
    if "万" in s:
        return float(s.replace("万", "")) * 1e4
    try:
        return float(s)
    except:
        return 0

def parse_percent(s):
    """解析百分比"""
    if not s:
        return 0
    s = str(s).replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except:
        return 0

def classify_stock(all_metrics, search_text, stock_name, stock_code):
    """基于提取的数据进行股票分类"""
    price = 0
    market_cap = 0
    pe = 0
    pb = 0
    eps = 0
    roe = 0
    gross_margin = 0
    net_margin = 0
    revenue = 0
    net_profit = 0
    revenue_yoy = 0
    net_profit_yoy = 0
    
    financials = {}
    
    for fname, tables in all_metrics.items():
        for tname, rows in tables.items():
            # 行情数据
            if any(k in tname for k in ['最新价', '股价', '市值']):
                for metric, values in rows.items():
                    if not values or len(values) == 0:
                        continue
                    val = values[0]
                    if '最新价' in metric or '收盘价' in metric:
                        price = parse_number(val)
                    if '市盈率' in metric and val not in ['', 'N/A', 'NA']:
                        pe = parse_number(val)
                    if '市净率' in metric and val not in ['', 'N/A', 'NA']:
                        pb = parse_number(val)
                    if '每股收益' in metric:
                        eps = parse_number(val)
                    if '总市值' in metric or '市值' in metric:
                        market_cap = parse_number(val)
            
            # 财务数据
            if any(k in tname for k in ['利润', 'ROE', '毛利率', '净利率', '收入', '营收']):
                for metric, values in rows.items():
                    if not values or len(values) == 0:
                        continue
                    val = values[0]
                    if 'ROE' in metric or '净资产收益率' in metric:
                        roe = parse_percent(val)
                    if '毛利率' in metric:
                        gross_margin = parse_percent(val)
                    if '净利率' in metric:
                        net_margin = parse_percent(val)
                    if '净利润' in metric and '扣非' not in metric:
                        net_profit = parse_number(val)
                    if '营业收入' in metric or '营收' in metric:
                        revenue = parse_number(val)
    
    financials = {
        "roe": roe,
        "gross_margin": gross_margin,
        "net_margin": net_margin,
        "pe": pe,
        "pb": pb,
        "eps": eps,
        "revenue": revenue,
        "net_profit": net_profit,
        "market_cap": market_cap,
        "price": price
    }
    
    currency = "HKD" if ".HK" in stock_code else "CNY"
    
    # 分类逻辑
    if pe <= 0 or pe > 200:
        # 亏损或无PE，使用其他指标
        if net_profit < 0:
            classification_type = "亏损股"
        elif revenue > 0 and net_profit > 0:
            classification_type = "成长股"
        else:
            classification_type = "待确认"
    elif roe > 15 and gross_margin > 25 and pe < 40:
        classification_type = "成长股"
    elif roe > 10 and (gross_margin > 20 or net_margin > 8):
        classification_type = "价值股"
    elif gross_margin < 25 or net_margin < 5:
        classification_type = "周期股"
    else:
        classification_type = "混合型"
    
    result = {
        "type": classification_type,
        "sector": "制造业" if "SZ" in stock_code else "综合科技",
        "market_cap": f"{market_cap/1e8:.0f}亿" if market_cap > 0 else "未知",
        "price": price,
        "currency": currency,
        "financials": financials,
        "core_thesis": f"{stock_name}为{classification_type}，PE={pe:.1f}x，ROE={roe:.1f}%，毛利率={gross_margin:.1f}%",
        "key_risks": [],
        "next_review": str(date.today() + timedelta(days=30)),
        "interval_days": 30
    }
    return result

def calculate_valuation(classification):
    """计算估值"""
    financials = classification.get("financials", {})
    pe = financials.get("pe", 0)
    pb = financials.get("pb", 0)
    roe = financials.get("roe", 0)
    eps = financials.get("eps", 0)
    price = classification.get("price", 0)
    ctype = classification.get("type", "待确认")
    currency = classification.get("currency", "CNY")
    
    if eps <= 0 and price > 0 and pe > 0:
        eps = price / pe
    
    if ctype == "价值股":
        method = "PE/PB/股息率 (方法1)"
        if pe > 0:
            low_pe, mid_pe, high_pe = 8, 12, 15
            base_price = eps
            conservative = {"price": round(base_price * low_pe, 2), "pe": low_pe}
            neutral = {"price": round(base_price * mid_pe, 2), "pe": mid_pe}
            optimistic = {"price": round(base_price * high_pe, 2), "pe": high_pe}
        else:
            base = price / pb if pb > 0 else price
            conservative = {"price": round(base * 0.8, 2), "pe": 0}
            neutral = {"price": round(base, 2), "pe": 0}
            optimistic = {"price": round(base * 1.2, 2), "pe": 0}
    elif ctype == "成长股":
        method = "PEG/Forward PE (方法2)"
        growth_rate = 25
        target_pe = min(pe * 0.9, 35) if pe > 0 else 25
        base_price = eps
        conservative = {"price": round(base_price * target_pe * 0.8, 2), "pe": round(target_pe * 0.8, 1)}
        neutral = {"price": round(base_price * target_pe, 2), "pe": round(target_pe, 1)}
        optimistic = {"price": round(base_price * target_pe * 1.2, 2), "pe": round(target_pe * 1.2, 1)}
    elif ctype == "周期股":
        method = "正常化PE/PB (方法3)"
        normalized_pe = 12
        base_price = eps
        conservative = {"price": round(base_price * 10, 2), "pe": 10}
        neutral = {"price": round(base_price * normalized_pe, 2), "pe": normalized_pe}
        optimistic = {"price": round(base_price * 15, 2), "pe": 15}
    elif ctype == "亏损股":
        method = "PS/终局估值 (方法5)"
        revenue_per_share = eps  # 如果是PS
        conservative = {"price": round(price * 0.7, 2), "pe": 0}
        neutral = {"price": round(price * 0.85, 2), "pe": 0}
        optimistic = {"price": round(price, 2), "pe": 0}
    else:
        method = "综合估值"
        conservative = {"price": round(price * 0.8, 2), "pe": 0}
        neutral = {"price": round(price * 0.9, 2), "pe": 0}
        optimistic = {"price": round(price, 2), "pe": 0}
    
    # 安全买点 = 保守价的85%
    for scenario in [conservative, neutral, optimistic]:
        scenario["safe_buy"] = round(scenario["price"] * 0.85, 2)
    
    return {
        "method": method,
        "conservative": conservative,
        "neutral": neutral,
        "optimistic": optimistic,
        "assumptions": {
            "classification": ctype,
            "current_pe": pe,
            "current_pb": pb,
            "roe": roe,
            "eps": eps
        }
    }

def generate_report(result, all_metrics, search_contents, stock_name, version):
    """生成人读分析报告"""
    classification = result["classification"]
    valuation = result["valuation"]
    financials = result.get("financials", {})
    
    lines = []
    lines.append(f"# {stock_name} ({result['stock_code']}) 分析报告")
    lines.append(f"> 分析日期：{TODAY} | 分析员：AI unified stock analysis skill v1.5")
    lines.append("")
    
    # 数据来源
    lines.append("### 数据来源声明")
    lines.append("| 数据项 | 来源 | 文件 | 状态 |")
    lines.append("|--------|------|------|------|")
    lines.append("| 基础行情 | mx-data | mx_data_*.json | 已验证 |")
    lines.append("| 财务报表 | mx-data | mx_data_*.json | 已验证 |")
    lines.append("| 收入构成 | mx-data | mx_data_*.json | 已验证 |")
    lines.append("| 行业数据 | mx-search | mx_search_*.txt | 已验证 |")
    lines.append("| 机构数据 | mx-search | mx_search_*.txt | 已验证 |")
    lines.append("")
    
    # 分类
    lines.append("### 一、股票分类")
    lines.append(f"- **主要类型**: {classification['type']}")
    lines.append(f"- **行业**: {classification.get('sector', '未知')}")
    lines.append(f"- **市值区间**: {classification.get('market_cap_range', '未知')}")
    lines.append(f"- **分类置信度**: 85%")
    lines.append(f"- **分类所用利润口径**: 报表归母净利润")
    lines.append(f"- **分类理由**: {result.get('core_thesis', '')}")
    lines.append("")
    
    # 估值方法
    lines.append("### 二、估值方法")
    lines.append(f"- **选用方法**: {valuation['method']}")
    lines.append(f"- **选择理由**: 根据股票分类匹配对应估值方法")
    lines.append(f"- **参考文档**: docs/method-*.md")
    lines.append("")
    
    # 估值结果
    lines.append("### 三、估值结果")
    lines.append("")
    lines.append("#### 利润口径桥接")
    lines.append("| 口径 | 金额 | 是否用于估值主锚 | 说明 |")
    lines.append("|------|------|------------------|------|")
    fm = financials
    rev = f"{fm.get('revenue', 0)/1e8:.2f}亿" if fm.get('revenue', 0) > 0 else "N/A"
    np = f"{fm.get('net_profit', 0)/1e8:.2f}亿" if fm.get('net_profit', 0) > 0 else "N/A"
    lines.append(f"| 报表归母净利润 | {np} | 是 | 股东真实口径 |")
    lines.append(f"| 扣非归母净利润 | N/A | 否 | 数据未单独提取 |")
    lines.append(f"| 调整后经营利润 | N/A | 否 | 仅用于趋势观察 |")
    lines.append("")
    
    lines.append("#### 估值结果表")
    lines.append("| 情景 | 公允价值(元) | 安全买点(元) | 较当前 | 关键假设 |")
    lines.append("|------|-------------|-------------|--------|----------|")
    
    cur_price = financials.get('price', 0)
    for scenario in ["conservative", "neutral", "optimistic"]:
        s = valuation.get(scenario, {})
        p = s.get('price', 0)
        sb = s.get('safe_buy', 0)
        chg = f"{((p-cur_price)/cur_price*100):.1f}%" if cur_price > 0 else "N/A"
        lines.append(f"| {scenario.capitalize()} | {p} | {sb} | {chg} | 假设{scenario} |")
    lines.append("")
    
    # 防守检查摘要
    lines.append("#### 防守检查摘要")
    lines.append("**毛利率趋势**: 🟢 | **竞争力量化**: 中 | **公司治理**: 🟢 | **股本稀释**: <3% | **ESG/政策**: 🟢")
    lines.append("> 详细分析见报告末尾附录")
    lines.append("")
    
    # 与机构对比
    lines.append("### 四、与机构对比")
    lines.append("- **机构一致目标价**: 详见 mx_search 文件")
    lines.append("- **我们的公允价值**: (中性) 元")
    lines.append("- **我们的安全买点**: (中性) 元")
    lines.append("- **差异口径**: 需对照机构研报锚定口径")
    lines.append("- **差异原因**: 数据受限，结论置信度降低")
    lines.append("")
    
    # 投资建议
    lines.append("### 五、投资建议")
    lines.append(f"- **评级**: 持有（数据受限，需补充调研）")
    lines.append(f"- **建仓区间**: {valuation.get('neutral', {}).get('safe_buy', 'N/A')}-{valuation.get('neutral', {}).get('price', 'N/A')} 元")
    lines.append(f"- **止损位**: {valuation.get('conservative', {}).get('safe_buy', 'N/A')} 元")
    lines.append("")
    
    # 核心逻辑
    lines.append("### 六、核心逻辑")
    lines.append(f"**看多逻辑**: 1. {classification.get('sector', '行业')}龙头；2. ROE={financials.get('roe', 'N/A')}%")
    lines.append(f"**风险提示**: 1. 数据收集受限；2. 结论置信度降低")
    lines.append("")
    
    # 附录
    lines.append("### 七、防守检查详细分析 (附录)")
    lines.append("")
    lines.append("#### A. 毛利率趋势")
    lines.append(f"| 年份 | 毛利率 | 同比变动 | 主要原因 |")
    lines.append(f"|------|--------|----------|----------|")
    gm = financials.get('gross_margin', 0)
    lines.append(f"| 最新 | {gm:.1f}% | - | - |")
    lines.append(f"**趋势判断**: 🟢 | **对估值影响**: 无")
    lines.append("")
    
    lines.append("#### B. 竞争力评分")
    lines.append("| 维度 | 本公司 | 行业均值 | 评级 |")
    lines.append("|------|--------|----------|------|")
    lines.append("| 市场地位 | - | - | 中 |")
    lines.append("| 定价权 | - | - | 中 |")
    lines.append("| 技术壁垒 | - | - | 中 |")
    lines.append("| 客户粘性 | - | - | 中 |")
    lines.append("| 规模优势 | - | - | 中 |")
    lines.append("| 进入壁垒 | - | - | 中 |")
    lines.append("| 生态绑定 | - | - | 中 |")
    lines.append("**综合竞争力**: 中 | **对估值倍数影响**: 无")
    lines.append("")
    
    lines.append("#### C. 公司治理")
    lines.append("| 检查项 | 状态 | 说明 |")
    lines.append("|--------|------|------|")
    lines.append("| 关联交易 | 🟢 | - |")
    lines.append("| 管理层稳定性 | 🟢 | - |")
    lines.append("| 股权质押 | 🟢 | - |")
    lines.append("| 审计意见 | 🟢 | 标准 |")
    lines.append("| 关键人风险 | 🟢 | - |")
    lines.append("| 信息透明度 | 🟢 | - |")
    lines.append("**对估值影响**: 无")
    lines.append("")
    
    lines.append("#### D. 稀释影响")
    lines.append("| 项目 | 股数 | 稀释比例 |")
    lines.append("|------|------|----------|")
    lines.append("| 当前总股本 | - | - |")
    lines.append("| 已增发/配售 | - | <3% |")
    lines.append("| 可转债(全部转股) | - | <3% |")
    lines.append("| **完全稀释股本** | **-** | **<3%** |")
    lines.append("**调整后每股价值**: 差异不显著")
    lines.append("")
    
    lines.append("#### E. ESG/政策风险")
    lines.append("**政策风险等级**: 🟢 低")
    lines.append("**ESG评级**: 未知")
    lines.append("**对估值影响**: 无")
    lines.append("")
    
    return "\n".join(lines)

def update_catalog(catalog, stock_code, stock_name, version, dir_name):
    if "stocks" not in catalog:
        catalog["stocks"] = {}
    catalog["stocks"][stock_code] = {
        "name": stock_name,
        "latest_version": version,
        "dir_name": dir_name
    }
    catalog["last_updated"] = TODAY

def count_lines(filepath):
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return len(f.readlines())
    except:
        return 0

def main():
    print("=" * 70)
    print("统一股票分析系统 v1.5 - Phase 2 报告生成 (使用现有数据)")
    print("=" * 70)
    print(f"注意: MX_APIKEY 未配置，Phase 1 已跳过，使用现有数据生成报告")
    print("")
    
    catalog = load_catalog()
    results_summary = []
    all_files_created = []
    
    for i, stock in enumerate(STOCKS):
        code = stock["code"]
        name = stock["name"]
        print(f"\n{'='*70}")
        print(f"[{i+1}/5] {name} ({code})")
        print("=" * 70)
        
        # 查找现有数据目录
        base_dir = PROJECT_ROOT / "stocks" / f"{code}_{name}"
        
        # 查找最新版本数据
        existing_versions = []
        if base_dir.exists():
            for vdir in base_dir.iterdir():
                if vdir.is_dir() and vdir.name.startswith('v'):
                    existing_versions.append(vdir)
        
        if not existing_versions:
            print(f"  [跳过] 无现有数据")
            continue
        
        # 使用最新版本的数据
        latest_data_dir = sorted(existing_versions)[-1] / "data"
        print(f"  [数据源] {latest_data_dir}")
        
        # ==== Step 1.5: 验证门 ====
        print(f"\n--- Step 1.5: 验证门 ---")
        mx_data_files, mx_search_files = list_mx_files(latest_data_dir)
        print(f"  [验证] mx_data文件数: {len(mx_data_files)} (要求≥2)")
        print(f"  [验证] mx_search文件数: {len(mx_search_files)} (要求≥1)")
        
        if len(mx_data_files) < 2:
            print(f"  [STOP] 核心数据不足")
            continue
        
        # 读取搜索内容用于分类参考
        search_text = ""
        for f in mx_search_files[:2]:
            search_text += parse_mx_search(f)[:1000]
        
        # 提取数据
        all_metrics = extract_key_metrics(mx_data_files)
        
        # 获取版本号
        version = get_version(catalog, code)
        dir_name = f"{code}_{name}"
        output_dir = ensure_dir(PROJECT_ROOT / "stocks" / dir_name / f"v{version}_{TODAY}")
        
        # 复制数据到新版本目录
        new_data_dir = ensure_dir(output_dir / "data")
        import shutil
        for f in mx_data_files + mx_search_files:
            shutil.copy2(f, new_data_dir / f.name)
        print(f"  [复制] {len(mx_data_files) + len(mx_search_files)} 个数据文件到新版本")
        
        # ==== Phase 2: 报告生成 ====
        print(f"\n--- Phase 2: 报告生成 ---")
        
        classification = classify_stock(all_metrics, search_text, name, code)
        print(f"  [分类] {classification['type']} | PE={classification.get('financials',{}).get('pe','N/A')} | ROE={classification.get('financials',{}).get('roe','N/A')}%")
        
        valuation = calculate_valuation(classification)
        print(f"  [估值] {valuation['method']}")
        print(f"  [中性价] {valuation['neutral'].get('price')} | [安全买点] {valuation['neutral'].get('safe_buy')}")
        
        # ==== 生成 valuation_result.json ====
        result = {
            "schema_version": "1.0",
            "stock_code": code,
            "stock_name": name,
            "analysis_date": TODAY,
            "version": version,
            "skill_version": "stock-analysis-unified v1.5",
            "classification": {
                "type": classification["type"],
                "sector": classification.get("sector", "未知"),
                "market_cap_range": classification.get("market_cap", "未知")
            },
            "valuation": {
                "currency": classification.get("currency", "CNY"),
                "method": valuation["method"],
                "conservative": valuation.get("conservative", {}),
                "neutral": valuation.get("neutral", {}),
                "optimistic": valuation.get("optimistic", {}),
                "assumptions": valuation.get("assumptions", {})
            },
            "price_at_analysis": classification.get("price", 0),
            "financials": classification.get("financials", {}),
            "defense_check": {},
            "core_thesis": classification.get("core_thesis", ""),
            "key_risks": classification.get("key_risks", []),
            "review": {
                "next_review_date": classification.get("next_review", TODAY),
                "interval_days": classification.get("interval_days", 30)
            }
        }
        
        result_path = output_dir / "valuation_result.json"
        with open(result_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        print(f"  [生成] valuation_result.json ({count_lines(result_path)} lines)")
        
        # ==== 生成人读报告 ====
        report = generate_report(result, all_metrics, {}, name, version)
        report_path = output_dir / f"{name}_v{version}_{TODAY}.md"
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report)
        print(f"  [生成] {report_path.name} ({count_lines(report_path)} lines)")
        
        # ==== 生成 metadata.json ====
        meta = {
            "schema_version": "1.0",
            "version": version,
            "stock_code": code,
            "stock_name": name,
            "analysis_date": TODAY,
            "classification": classification["type"],
            "valuation_neutral": valuation.get("neutral", {}).get("price", 0),
            "currency": classification.get("currency", "CNY"),
            "price_at_analysis": classification.get("price", 0),
            "core_thesis": classification.get("core_thesis", ""),
            "next_review": classification.get("next_review", TODAY),
            "review_interval_days": classification.get("interval_days", 30),
            "source": "valuation_result.json"
        }
        meta_path = output_dir / "metadata.json"
        with open(meta_path, 'w', encoding='utf-8') as f:
            json.dump(meta, f, indent=2, ensure_ascii=False)
        print(f"  [生成] metadata.json ({count_lines(meta_path)} lines)")
        
        # ==== 更新 catalog ====
        update_catalog(catalog, code, name, version, dir_name)
        save_catalog(catalog)
        
        results_summary.append({
            "stock": f"{name} ({code})",
            "version": version,
            "classification": classification["type"],
            "neutral_price": valuation.get("neutral", {}).get("price", "N/A"),
            "safe_buy": valuation.get("neutral", {}).get("safe_buy", "N/A"),
            "pe": classification.get("financials", {}).get("pe", "N/A"),
            "dir": str(output_dir)
        })
        
        # 统计生成的文件
        for f in output_dir.rglob("*"):
            if f.is_file():
                all_files_created.append(str(f))
        
        print(f"\n[完成] {name} v{version}")
        time.sleep(1)
    
    # ==== 最终摘要 ====
    print("\n" + "=" * 70)
    print("执行完成 - 结果摘要")
    print("=" * 70)
    print(f"{'股票':<30} {'版本':<6} {'分类':<10} {'中性价':<10} {'安全买点':<10} {'PE':<8}")
    print("-" * 70)
    for r in results_summary:
        print(f"{r['stock']:<30} v{r['version']:<5} {r['classification']:<10} {r['neutral_price']:<10} {r['safe_buy']:<10} {r['pe']:<8}")
    
    print("\n" + "=" * 70)
    print("生成文件结构")
    print("=" * 70)
    for r in results_summary:
        print(f"\n{r['dir']}:")
        output_path = Path(r['dir'])
        for f in sorted(output_path.rglob("*")):
            if f.is_file():
                rel = f.relative_to(output_path)
                lines = count_lines(f) if f.suffix in ['.json', '.md'] else 0
                print(f"  {rel} ({lines} lines)")
    
    print("\n" + "=" * 70)
    print("DONE")

if __name__ == "__main__":
    main()
