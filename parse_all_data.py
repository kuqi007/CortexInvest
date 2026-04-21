#!/usr/bin/env python3
"""Parse all raw.json files for 4 stocks and extract key financial data."""
import json
import os
import sys

def parse_mx_data(filepath):
    """Parse mx_data raw.json file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f, strict=False)
    
    try:
        dto = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    except (KeyError, TypeError):
        return None
    
    results = []
    for table in dto:
        sheet_name = table.get('tableName', 'Unknown')
        headers = []
        rows = []
        
        # Try to get headers and rows from the table
        if 'tableHeader' in table:
            for h in table['tableHeader']:
                if isinstance(h, dict):
                    headers.append(h.get('headerName', h.get('name', '')))
                else:
                    headers.append(str(h))
        
        if 'tableBody' in table:
            for row in table['tableBody']:
                if isinstance(row, list):
                    rows.append([str(cell) if cell is not None else '' for cell in row])
                elif isinstance(row, dict):
                    rows.append(row)
        
        results.append({
            'sheet_name': sheet_name,
            'headers': headers,
            'rows': rows
        })
    
    return results

def parse_mx_search(filepath):
    """Parse mx_search json file."""
    with open(filepath, 'r', encoding='utf-8') as f:
        data = json.load(f, strict=False)
    
    # Try to extract text content
    text = data.get('data', {}).get('text', '')
    if not text:
        text = data.get('text', '')
    if not text:
        text = json.dumps(data, ensure_ascii=False, indent=2)[:5000]
    return text

stocks = {
    '德赛西威': '/Users/zhul1/Documents/aiWorkspace/ai-investor/德赛西威_分析数据',
    '比亚迪股份': '/Users/zhul1/Documents/aiWorkspace/ai-investor/比亚迪股份_分析数据',
    '澜起科技': '/Users/zhul1/Documents/aiWorkspace/ai-investor/澜起科技_分析数据',
    '禾赛-W': '/Users/zhul1/Documents/aiWorkspace/ai-investor/禾赛-W_分析数据',
}

for stock_name, stock_dir in stocks.items():
    print(f"\n{'='*80}")
    print(f"  {stock_name}")
    print(f"{'='*80}")
    
    # List all files
    for f in sorted(os.listdir(stock_dir)):
        if f.endswith('_raw.json'):
            print(f"\n--- {f} ---")
            filepath = os.path.join(stock_dir, f)
            result = parse_mx_data(filepath)
            if result:
                for table in result:
                    print(f"\n  Sheet: {table['sheet_name']}")
                    if table['headers']:
                        print(f"  Headers: {table['headers']}")
                    for i, row in enumerate(table['rows']):
                        if i < 15:  # Limit rows
                            if isinstance(row, dict):
                                print(f"  Row {i}: {json.dumps(row, ensure_ascii=False)[:300]}")
                            else:
                                print(f"  Row {i}: {row}")
                        else:
                            print(f"  ... ({len(table['rows'])} total rows)")
                            break
        
        elif f.startswith('mx_search_') and f.endswith('.json'):
            print(f"\n--- {f} ---")
            filepath = os.path.join(stock_dir, f)
            text = parse_mx_search(filepath)
            print(f"  {text[:3000]}")
