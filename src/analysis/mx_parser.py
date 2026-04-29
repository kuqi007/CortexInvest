# src/analysis/mx_parser.py
"""mx-data raw JSON 解析工具 — 机构持股比例、K线 OHLCV"""

import json
from pathlib import Path


def parse_institutional_ratio(raw_json_path: str) -> dict:
    """解析 mx-data 机构持股比例数据

    Args:
        raw_json_path: mx-data raw JSON 文件路径

    Returns:
        dict: {
            'latest_ratio_pct': float,   # 最新一期机构持股比例(%)
            'prev_ratio_pct': float|None, # 上一期机构持股比例(%)
            'qoq_change_pp': float|None,  # 环比变化(百分点)
            'non_institutional_ratio_pct': float,  # 非机构持股比例(= 100 - latest)
            'all_dates': list[tuple(date, ratio)],  # 所有期次
        }

    Raises:
        ValueError: col_id 100000000003145 不存在
    """
    with open(raw_json_path) as f:
        data = json.load(f)

    dt = data['data']['data']['searchDataResultDTO']['dataTableDTOList'][0]
    raw = dt['rawTable']

    col_id = '100000000003145'
    if col_id not in raw:
        raise ValueError(f"Column {col_id} not found in mx-data response")
    ratios = raw[col_id]
    dates = raw.get('headName', [])

    latest_ratio = float(ratios[0])
    prev_ratio = float(ratios[1]) if len(ratios) > 1 else None
    qoq_change = round(latest_ratio - prev_ratio, 3) if prev_ratio is not None else None

    all_dates = list(zip(dates, ratios)) if dates else []

    return {
        'latest_ratio_pct': latest_ratio,
        'prev_ratio_pct': prev_ratio,
        'qoq_change_pp': qoq_change,
        'non_institutional_ratio_pct': round(100.0 - latest_ratio, 3),
        'all_dates': all_dates,
    }


def parse_kline_ohlcv(raw_json_path: str) -> list[dict]:
    """解析 mx-data 日K线数据，提取 OHLCV 列表

    Args:
        raw_json_path: mx-data raw JSON 文件路径

    Returns:
        list[dict]: 每行为 {'date': str, '开盘价': float, '收盘价': float,
                           '最高价': float, '最低价': float, '成交量': float, ...}
        按日期升序排列（最老在前，最新在后）。
    """
    with open(raw_json_path) as f:
        data = json.load(f)

    dt_list = data['data']['data']['searchDataResultDTO']['dataTableDTOList']
    # Table 1 = 历史序列；若无 Table 1 则用 Table 0
    dt = dt_list[1] if len(dt_list) > 1 else dt_list[0]

    raw = dt['rawTable']
    dates = raw.get('headName', [])
    fields = [k for k in raw.keys() if k != 'headName']

    rows = []
    for i, date in enumerate(dates):
        row = {'date': date}
        for f in fields:
            val_str = raw[f][i] if i < len(raw[f]) else None
            if val_str is None:
                continue
            # 去除常见单位
            val_clean = (
                str(val_str)
                .replace('万股', '')
                .replace('万手', '')
                .replace('手', '')
                .replace('元', '')
                .replace('%', '')
                .replace(',', '')
                .replace(' ', '')
            )
            if val_clean == '':
                continue
            try:
                row[dt['nameMap'].get(f, f)] = float(val_clean)
            except ValueError:
                continue
        rows.append(row)

    return rows
