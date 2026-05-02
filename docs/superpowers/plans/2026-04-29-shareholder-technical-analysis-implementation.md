# 股东结构分析 + 短期技术信号实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.
>
> **Goal:** 为 stock-analysis-unified skill 新增股东结构分析（Step 2.5-F）和短期技术信号（Step 3.5）两个功能

**Architecture:** 在 `src/analysis/` 下新增 Python helper 模块，SKILL.md 新增步骤定义，报告模板新增章节。Python 模块供 AI 在 skill 执行时调用。

**Tech Stack:** Python 3.13, numpy, json（标准库）

---

## 文件结构

```
src/analysis/
├── __init__.py          # 导出所有公开函数
├── mx_parser.py         # mx-data raw JSON 解析（共享）
├── technical_indicators.py  # 技术指标计算（RSI/MACD/KDJ/ATR/Bollinger/MA）
├── shareholder_analyzer.py    # 股东结构分析
└── test_technical_indicators.py
└── test_shareholder_analyzer.py

.claude/skills/stock-analysis-unified/
└── SKILL.md            # 新增 Phase 1 步骤、Step 2.5-F、Step 3.5

.claude/skills/stock-analysis-unified/docs/
└── report-template-reference.md  # 新增报告章节
```

---

## Task 1: mx_parser.py — mx-data raw JSON 解析工具

**Files:**
- Create: `src/analysis/mx_parser.py`
- Test: `src/analysis/test_mx_parser.py`

- [ ] **Step 1: 写测试 — parse_institutional_ratio**

```python
# src/analysis/test_mx_parser.py
import json, tempfile, os

def test_parse_institutional_ratio_basic():
    """测试基本解析功能"""
    # 创建临时 mock JSON
    raw = {
        'data': {
            'data': {
                'searchDataResultDTO': {
                    'dataTableDTOList': [{
                        'rawTable': {
                            '100000000003145': ['72.548', '74.143', '75.86'],
                            'headName': ['2026一季报', '2025年报', '2025一季报']
                        },
                        'nameMap': {'100000000003145': '机构持股比例合计'}
                    }]
                }
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(raw, f)
        path = f.name
    try:
        result = parse_institutional_ratio(path)
        assert result['latest_ratio_pct'] == 72.548
        assert result['prev_ratio_pct'] == 74.143
        assert abs(result['qoq_change_pp'] - (-1.595)) < 0.01
        assert abs(result['non_institutional_ratio_pct'] - 27.452) < 0.01
    finally:
        os.unlink(path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest src/analysis/test_mx_parser.py::test_parse_institutional_ratio_basic -v`
Expected: FAIL — `parse_institutional_ratio not defined`

- [ ] **Step 3: Write parse_institutional_ratio**

```python
# src/analysis/mx_parser.py
import json
from pathlib import Path

def parse_institutional_ratio(raw_json_path: str) -> dict:
    """解析 mx-data 机构持股比例数据"""
    with open(raw_json_path) as f:
        data = json.load(f)

    dt = data['data']['data']['searchDataResultDTO']['dataTableDTOList'][0]
    raw = dt['rawTable']

    col_id = '100000000003145'
    if col_id not in raw:
        raise ValueError(f"Column {col_id} not found in mx-data response")
    ratios = raw[col_id]
    dates = raw['headName']

    latest_ratio = float(ratios[0])
    prev_ratio = float(ratios[1]) if len(ratios) > 1 else None

    return {
        'latest_ratio_pct': latest_ratio,
        'prev_ratio_pct': prev_ratio,
        'qoq_change_pp': round(latest_ratio - prev_ratio, 3) if prev_ratio else None,
        'non_institutional_ratio_pct': round(100 - latest_ratio, 3),
        'all_dates': list(zip(dates, ratios))
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest src/analysis/test_mx_parser.py::test_parse_institutional_ratio_basic -v`
Expected: PASS

- [ ] **Step 5: 写测试 — parse_institutional_ratio 错误处理**

```python
def test_parse_institutional_ratio_missing_col():
    """测试 col_id 不存在时的错误处理"""
    raw = {
        'data': {
            'data': {
                'searchDataResultDTO': {
                    'dataTableDTOList': [{
                        'rawTable': {
                            'headName': ['2026一季报']
                        },
                        'nameMap': {}
                    }]
                }
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(raw, f)
        path = f.name
    try:
        result = parse_institutional_ratio(path)
        assert False, "Should raise ValueError"
    except ValueError as e:
        assert "Column 100000000003145 not found" in str(e)
    finally:
        os.unlink(path)
```

- [ ] **Step 6: Run test**

Run: `uv run pytest src/analysis/test_mx_parser.py::test_parse_institutional_ratio_missing_col -v`
Expected: PASS (parse_institutional_ratio already has the check)

- [ ] **Step 7: 写测试 — parse_kline_ohlcv**

```python
def test_parse_kline_ohlcv_basic():
    """测试日K线解析"""
    raw = {
        'data': {
            'data': {
                'searchDataResultDTO': {
                    'dataTableDTOList': [
                        {'rawTable': {}, 'nameMap': {}, 'table': {}},  # Table 0
                        {  # Table 1 = 历史序列
                            'rawTable': {
                                'headName': ['2026-04-29', '2026-04-28', '2026-04-27'],
                                '成交量': ['348.1', '340', '728'],
                                '最低价': ['1400', '1400', '1402'],
                                '最高价': ['1410', '1409', '1420'],
                                '开盘价': ['1405', '1402', '1420'],
                                '收盘价': ['1401', '1405', '1403']
                            },
                            'nameMap': {
                                '成交量': '成交量', '最低价': '最低价',
                                '最高价': '最高价', '开盘价': '开盘价', '收盘价': '收盘价'
                            },
                            'table': {}
                        }
                    ]
                }
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(raw, f)
        path = f.name
    try:
        rows = parse_kline_ohlcv(path)
        assert len(rows) == 3
        assert rows[0]['date'] == '2026-04-29'
        assert rows[0]['成交量'] == 348.1
        assert rows[0]['收盘价'] == 1401.0
    finally:
        os.unlink(path)
```

- [ ] **Step 8: Run test to verify it fails**

Run: `uv run pytest src/analysis/test_mx_parser.py::test_parse_kline_ohlcv_basic -v`
Expected: FAIL

- [ ] **Step 9: Write parse_kline_ohlcv**

```python
def parse_kline_ohlcv(raw_json_path: str) -> list[dict]:
    """解析 mx-data 日K线数据，提取 OHLCV 列表"""
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
            # 去除单位
            val_clean = (str(val_str)
                .replace('万股', '').replace('万手', '').replace('手', '')
                .replace('元', '').replace('%', '').replace(',', '').replace(' ', ''))
            if val_clean == '':
                continue
            try:
                row[dt['nameMap'].get(f, f)] = float(val_clean)
            except ValueError:
                continue
        rows.append(row)

    return rows
```

- [ ] **Step 10: Run test**

Run: `uv run pytest src/analysis/test_mx_parser.py::test_parse_kline_ohlcv_basic -v`
Expected: PASS

- [ ] **Step 11: 写测试 — parse_kline_ohlcv 单位剥离**

```python
def test_parse_kline_ohlcv_units():
    """测试各种单位剥离"""
    raw = {
        'data': {
            'data': {
                'searchDataResultDTO': {
                    'dataTableDTOList': [
                        {'rawTable': {}, 'nameMap': {}, 'table': {}},
                        {
                            'rawTable': {
                                'headName': ['2026-04-29'],
                                '成交量': ['348.1万股'],
                                '收盘价': ['1401.17元'],
                                '开盘价': ['1,405']  # 带逗号
                            },
                            'nameMap': {'成交量': '成交量', '收盘价': '收盘价', '开盘价': '开盘价'},
                            'table': {}
                        }
                    ]
                }
            }
        }
    }
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        json.dump(raw, f)
        path = f.name
    try:
        rows = parse_kline_ohlcv(path)
        assert rows[0]['成交量'] == 348.1
        assert rows[0]['收盘价'] == 1401.17
        assert rows[0]['开盘价'] == 1405.0  # 逗号被去除
    finally:
        os.unlink(path)
```

- [ ] **Step 12: Run test**

Run: `uv run pytest src/analysis/test_mx_parser.py::test_parse_kline_ohlcv_units -v`
Expected: PASS

- [ ] **Step 13: Write __init__.py**

```python
# src/analysis/__init__.py
from .mx_parser import parse_institutional_ratio, parse_kline_ohlcv

__all__ = ['parse_institutional_ratio', 'parse_kline_ohlcv']
```

- [ ] **Step 14: Commit**

```bash
git add src/analysis/__init__.py src/analysis/mx_parser.py src/analysis/test_mx_parser.py
git commit -m "feat(analysis): add mx-data raw JSON parser for institutional ratio and kline"
```

---

## Task 2: technical_indicators.py — 技术指标计算

**Files:**
- Create: `src/analysis/technical_indicators.py`
- Test: `src/analysis/test_technical_indicators.py`

**测试数据（标准 OHLCV）：** 使用以下固定数据，确保结果可复现：

```python
# 固定测试数据（贵州茅台约20个交易日，含完整波动）
TEST_DATES = ['2026-04-29', '2026-04-28', '2026-04-27', '2026-04-24', '2026-04-23',
              '2026-04-22', '2026-04-21', '2026-04-20', '2026-04-17', '2026-04-16',
              '2026-04-15', '2026-04-14', '2026-04-11', '2026-04-10', '2026-04-09',
              '2026-04-08', '2026-04-07', '2026-04-03', '2026-04-02', '2026-04-01']
TEST_OPENS  = [1405, 1402, 1420, 1413, 1408, 1415, 1411, 1403, 1400, 1467,
               1445, 1443, 1444, 1459, 1465, 1460, 1460, 1459, 1459, 1464]
TEST_CLOSES = [1401, 1405, 1403, 1458, 1419, 1411, 1408, 1400, 1458, 1443,
               1442, 1441, 1450, 1458, 1460, 1459, 1459, 1459, 1460, 1463]
TEST_HIGHS  = [1410, 1409, 1420, 1458, 1420, 1415, 1415, 1410, 1462, 1467,
               1448, 1446, 1455, 1465, 1468, 1465, 1462, 1460, 1462, 1468]
TEST_LOWS   = [1400, 1400, 1402, 1413, 1405, 1408, 1403, 1395, 1395, 1435,
               1435, 1435, 1440, 1455, 1458, 1458, 1455, 1455, 1455, 1460]
TEST_VOLUMES = [348, 340, 728, 554, 377, 269, 217, 364, 968, 245,
                 345, 242, 252, 288, 207, 338, 241, 201, 210, 291]  # 单位：万
```

- [ ] **Step 1: 写测试 — calc_rsi basic**

```python
def test_calc_rsi_basic():
    """RSI(14) 基本计算"""
    result = calc_rsi(TEST_CLOSES, period=14)
    assert result is not None
    assert 0 <= result <= 100
    # RSI 应该是一个合理的值（不是0也不是100）
    assert 10 < result < 90
```

- [ ] **Step 2: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_rsi_basic -v`
Expected: FAIL — `calc_rsi not defined`

- [ ] **Step 3: Write calc_rsi (Wilder smoothing)**

```python
def calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """计算 RSI(14) — 使用 Wilder 平滑公式"""
    import numpy as np
    closes_arr = np.array(closes)
    deltas = np.diff(closes_arr)
    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    if len(gains) < period:
        return None

    # 首次平均使用 SMA 作为种子
    avg_gain = np.mean(gains[:period])
    avg_loss = np.mean(losses[:period])

    # 后续使用 Wilder 平滑公式
    for i in range(period + 1, len(gains)):
        avg_gain = avg_gain * (period - 1) / period + gains[i] / period
        avg_loss = avg_loss * (period - 1) / period + losses[i] / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)
```

- [ ] **Step 4: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_rsi_basic -v`
Expected: PASS

- [ ] **Step 5: 写测试 — calc_rsi 数据不足**

```python
def test_calc_rsi_insufficient_data():
    """数据不足时返回 None"""
    result = calc_rsi([100, 105, 110], period=14)  # 仅3个数据点
    assert result is None
```

- [ ] **Step 6: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_rsi_insufficient_data -v`
Expected: PASS

- [ ] **Step 7: 写测试 — calc_macd**

```python
def test_calc_macd_basic():
    """MACD(12,26,9) 基本计算"""
    result = calc_macd(TEST_CLOSES)
    assert 'dif' in result
    assert 'dea' in result
    assert 'histogram' in result
    assert 'crossover' in result
    assert 'divergence' in result
    assert result['crossover'] in ['none', 'golden', 'death']
    assert result['divergence'] in ['none', 'bullish', 'bearish']
```

- [ ] **Step 8: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_macd_basic -v`
Expected: FAIL

- [ ] **Step 9: Write calc_macd + _ema**

```python
def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    """计算 EMA"""
    ema = [float(arr[0])]
    k = 2.0 / (period + 1)
    for price in arr[1:]:
        ema.append(float(price) * k + ema[-1] * (1 - k))
    return np.array(ema)


def calc_macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """计算 MACD(12,26,9) — 含背离检测和金叉死叉"""
    import numpy as np
    closes_arr = np.array(closes, dtype=float)
    ema_fast = _ema(closes_arr, fast)
    ema_slow = _ema(closes_arr, slow)
    dif = ema_fast - ema_slow
    dea = _ema(dif, signal)

    # 背离检测
    divergence = 'none'
    if len(dif) >= 20:
        price_trend = closes_arr[-1] - closes_arr[-20]
        macd_trend = dif[-1] - dif[-20]
        if price_trend < 0 and macd_trend > 0:
            divergence = 'bullish'
        elif price_trend > 0 and macd_trend < 0:
            divergence = 'bearish'

    # 金叉死叉检测
    crossover = 'none'
    if len(dif) >= 2:
        if dif[-2] < dea[-2] and dif[-1] > dea[-1]:
            crossover = 'golden'
        elif dif[-2] > dea[-2] and dif[-1] < dea[-1]:
            crossover = 'death'

    return {
        'dif': round(float(dif[-1]), 2),
        'dea': round(float(dea[-1]), 2),
        'histogram': round(float(dif[-1] - dea[-1]), 2),
        'crossover': crossover,
        'divergence': divergence
    }
```

- [ ] **Step 10: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_macd_basic -v`
Expected: PASS

- [ ] **Step 11: 写测试 — calc_kdj**

```python
def test_calc_kdj_basic():
    """KDJ(9,3,3) 基本计算"""
    result = calc_kdj(TEST_HIGHS, TEST_LOWS, TEST_CLOSES)
    assert 'k' in result
    assert 'd' in result
    assert 'j' in result
    assert 'crossover' in result
    assert 'k_series' in result  # 返回完整时间序列
    assert result['crossover'] in ['none', 'golden', 'death']
    # KDJ 值应该在 0-100 之间
    assert 0 <= result['k'] <= 100
    assert 0 <= result['d'] <= 100
```

- [ ] **Step 12: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_kdj_basic -v`
Expected: FAIL

- [ ] **Step 13: Write calc_kdj**

```python
def calc_kdj(highs: list[float], lows: list[float], closes: list[float], n: int = 9) -> dict:
    """计算 KDJ(9,3,3) — 返回完整时间序列及金叉死叉信号"""
    import numpy as np
    highs_arr = np.array(highs, dtype=float)
    lows_arr = np.array(lows, dtype=float)
    closes_arr = np.array(closes, dtype=float)

    # 计算 RSV 时间序列
    rsv_values = []
    for i in range(n - 1, len(closes)):
        low_n = float(min(lows_arr[i - n + 1:i + 1]))
        high_n = float(max(highs_arr[i - n + 1:i + 1]))
        rsv = (closes_arr[i] - low_n) / (high_n - low_n + 1e-9) * 100.0
        rsv_values.append(rsv)

    # K, D 递归计算
    k_series, d_series = [], []
    k, d = 50.0, 50.0
    for rsv in rsv_values:
        k = 2.0 / 3.0 * k + 1.0 / 3.0 * rsv
        d = 2.0 / 3.0 * d + 1.0 / 3.0 * k
        k_series.append(k)
        d_series.append(d)

    j_series = [3.0 * ki - 2.0 * di for ki, di in zip(k_series, d_series)]

    # 当前值
    k_val, d_val, j_val = k_series[-1], d_series[-1], j_series[-1]

    # 金叉死叉
    crossover = 'none'
    if len(k_series) >= 2:
        if k_series[-2] < d_series[-2] and k_series[-1] > d_series[-1]:
            crossover = 'golden'
        elif k_series[-2] > d_series[-2] and k_series[-1] < d_series[-1]:
            crossover = 'death'

    return {
        'k': round(k_val, 1),
        'd': round(d_val, 1),
        'j': round(j_val, 1),
        'k_series': [round(v, 1) for v in k_series],
        'd_series': [round(v, 1) for v in d_series],
        'j_series': [round(v, 1) for v in j_series],
        'crossover': crossover
    }
```

- [ ] **Step 14: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_kdj_basic -v`
Expected: PASS

- [ ] **Step 15: 写测试 — calc_ma**

```python
def test_calc_ma_basic():
    """MA 系统计算"""
    result = calc_ma(TEST_CLOSES)
    assert 'ma5' in result
    assert 'ma10' in result
    assert 'ma20' in result
    assert 'ma60' in result
    assert result['ma5'] > result['ma10']  # 当前数据：近期价格在均线附近
    assert 'arrangement' in result
    assert result['arrangement'] in ['bullish', 'bearish', 'neutral']
```

- [ ] **Step 16: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_ma_basic -v`
Expected: FAIL

- [ ] **Step 17: Write calc_ma**

```python
def calc_ma(closes: list[float], periods: tuple = (5, 10, 20, 60)) -> dict:
    """计算 MA 均线系统"""
    import numpy as np
    closes_arr = np.array(closes, dtype=float)
    result = {}
    for p in periods:
        if len(closes_arr) >= p:
            result[f'ma{p}'] = round(float(np.mean(closes_arr[-p:])), 2)
        else:
            result[f'ma{p}'] = None

    # 判断均线排列
    ma_vals = {p: result.get(f'ma{p}') for p in periods}
    valid_mas = {p: v for p, v in ma_vals.items() if v is not None}
    if len(valid_mas) < 2:
        arrangement = 'neutral'
    elif all(valid_mas[p1] > valid_mas[p2] for p1, p2 in zip(sorted(valid_mas), sorted(valid_mas)[1:])):
        arrangement = 'bullish'
    elif all(valid_mas[p1] < valid_mas[p2] for p1, p2 in zip(sorted(valid_mas), sorted(valid_mas)[1:])):
        arrangement = 'bearish'
    else:
        arrangement = 'neutral'
    result['arrangement'] = arrangement

    return result
```

- [ ] **Step 18: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_ma_basic -v`
Expected: PASS

- [ ] **Step 19: 写测试 — calc_atr**

```python
def test_calc_atr_basic():
    """ATR 计算"""
    result = calc_atr(TEST_HIGHS, TEST_LOWS, TEST_CLOSES, period=14)
    assert result is not None
    assert result > 0
    assert result < 100  # 合理范围
```

- [ ] **Step 20: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_atr_basic -v`
Expected: FAIL

- [ ] **Step 21: Write calc_atr**

```python
def calc_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """计算 ATR (Average True Range) — 使用 Wilder 平滑"""
    import numpy as np
    highs_arr = np.array(highs, dtype=float)
    lows_arr = np.array(lows, dtype=float)
    closes_arr = np.array(closes, dtype=float)

    if len(closes) < period + 1:
        return None

    # 计算 True Range
    tr_values = []
    for i in range(1, len(closes)):
        high_low = highs_arr[i] - lows_arr[i]
        high_pc = abs(highs_arr[i] - closes_arr[i - 1])
        low_pc = abs(lows_arr[i] - closes_arr[i - 1])
        tr = max(high_low, high_pc, low_pc)
        tr_values.append(tr)

    if len(tr_values) < period:
        return None

    # 首次 ATR 使用 SMA
    atr = float(np.mean(tr_values[:period]))

    # 后续 Wilder 平滑
    for i in range(period, len(tr_values)):
        atr = atr * (period - 1) / period + tr_values[i] / period

    return round(atr, 2)
```

- [ ] **Step 22: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_atr_basic -v`
Expected: PASS

- [ ] **Step 23: 写测试 — calc_bollinger_bands**

```python
def test_calc_bollinger_bands_basic():
    """布林带计算"""
    result = calc_bollinger_bands(TEST_CLOSES, period=20)
    assert result is not None
    assert 'middle' in result
    assert 'upper' in result
    assert 'lower' in result
    assert result['upper'] > result['middle'] > result['lower']
    assert result['upper'] > TEST_CLOSES[-1] > result['lower']  # 当前价格在带内
```

- [ ] **Step 24: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_bollinger_bands_basic -v`
Expected: FAIL

- [ ] **Step 25: Write calc_bollinger_bands**

```python
def calc_bollinger_bands(closes: list[float], period: int = 20, std_dev: float = 2) -> dict | None:
    """计算布林带"""
    import numpy as np
    closes_arr = np.array(closes, dtype=float)
    if len(closes) < period:
        return None

    ma = float(np.mean(closes_arr[-period:]))
    std = float(np.sqrt(np.sum((closes_arr[-period:] - ma) ** 2) / period))
    upper = ma + std_dev * std
    lower = ma - std_dev * std

    return {
        'middle': round(ma, 2),
        'upper': round(upper, 2),
        'lower': round(lower, 2),
        'std': round(std, 2)
    }
```

- [ ] **Step 26: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_bollinger_bands_basic -v`
Expected: PASS

- [ ] **Step 27: 写测试 — calc_volume_ratio**

```python
def test_calc_volume_ratio_basic():
    """成交量分析"""
    result = calc_volume_ratio(TEST_VOLUMES)
    assert 'ratio_vs_ma5' in result
    assert 'trend' in result
    assert result['trend'] in ['expanding', 'contracting', 'neutral']
```

- [ ] **Step 28: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_volume_ratio_basic -v`
Expected: FAIL

- [ ] **Step 29: Write calc_volume_ratio**

```python
def calc_volume_ratio(volumes: list[float]) -> dict:
    """计算成交量比率和趋势"""
    import numpy as np
    volumes_arr = np.array(volumes, dtype=float)
    ma5 = float(np.mean(volumes_arr[-5:])) if len(volumes_arr) >= 5 else float(np.mean(volumes_arr))
    ratio = volumes_arr[-1] / ma5 if ma5 > 0 else 1.0

    trend = 'neutral'
    if ratio > 2.0:
        trend = 'expanding'
    elif ratio < 0.5:
        trend = 'contracting'

    return {
        'ratio_vs_ma5': round(ratio, 2),
        'trend': trend
    }
```

- [ ] **Step 30: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_calc_volume_ratio_basic -v`
Expected: PASS

- [ ] **Step 31: 写测试 — 综合信号 5 指标计分**

```python
def test_synthesis_signal_scoring():
    """5 指标计分算法"""
    from datetime import date, timedelta
    closes = TEST_CLOSES
    highs = TEST_HIGHS
    lows = TEST_LOWS
    volumes = TEST_VOLUMES

    # RSI 中性
    rsi = calc_rsi(closes, 14)
    rsi_score = 0 if 30 <= rsi <= 70 else (1 if rsi < 30 else -1)

    # MACD 金叉
    macd = calc_macd(closes)
    macd_score = 1 if macd['crossover'] == 'golden' else (-1 if macd['crossover'] == 'death' else 0)

    # KDJ
    kdj = calc_kdj(highs, lows, closes)
    kdj_score = 1 if kdj['crossover'] == 'golden' else (-1 if kdj['crossover'] == 'death' else 0)

    # MA
    ma = calc_ma(closes)
    ma_score = 1 if ma['arrangement'] == 'bullish' else (-1 if ma['arrangement'] == 'bearish' else 0)

    # 成交量
    vol = calc_volume_ratio(volumes)
    vol_score = 1 if vol['trend'] == 'expanding' else (-1 if vol['trend'] == 'contracting' else 0)

    total = rsi_score + macd_score + kdj_score + ma_score + vol_score

    # 验证总分在合理范围内
    assert -5 <= total <= 5
    # 验证最终信号
    signal = 'BULLISH' if total >= 3 else ('BEARISH' if total <= -3 else 'NEUTRAL')
    assert signal in ['BULLISH', 'NEUTRAL', 'BEARISH']
```

- [ ] **Step 32: Run test**

Run: `uv run pytest src/analysis/test_technical_indicators.py::test_synthesis_signal_scoring -v`
Expected: PASS

- [ ] **Step 33: 更新 __init__.py**

```python
# src/analysis/__init__.py
from .mx_parser import parse_institutional_ratio, parse_kline_ohlcv
from .technical_indicators import (
    calc_rsi, calc_macd, calc_kdj, calc_ma, calc_atr,
    calc_bollinger_bands, calc_volume_ratio
)

__all__ = [
    'parse_institutional_ratio', 'parse_kline_ohlcv',
    'calc_rsi', 'calc_macd', 'calc_kdj', 'calc_ma',
    'calc_atr', 'calc_bollinger_bands', 'calc_volume_ratio'
]
```

- [ ] **Step 34: Run all tests**

Run: `uv run pytest src/analysis/test_technical_indicators.py -v`
Expected: ALL PASS

- [ ] **Step 35: Commit**

```bash
git add src/analysis/technical_indicators.py src/analysis/test_technical_indicators.py src/analysis/__init__.py
git commit -m "feat(analysis): add technical indicator calculations — RSI, MACD, KDJ, MA, ATR, Bollinger Bands"
```

---

## Task 3: shareholder_analyzer.py — 股东结构分析

**Files:**
- Create: `src/analysis/shareholder_analyzer.py`
- Test: `src/analysis/test_shareholder_analyzer.py`

- [ ] **Step 1: 写测试 — assess_shareholder_risk basic**

```python
def test_assess_shareholder_risk_ashare_low():
    """A股低风险案例（贵州茅台型）"""
    result = assess_shareholder_risk(
        non_institutional_ratio_pct=27.45,       # 非机构持股比例
        institutional_ratio_qoq_change_pp=-1.59,  # 环比变化
        shareholder_count_qoq_change_pct=3.2,     # 股东户数变化
        top10_concentration_pct=68.0,
        controlling_shareholder_pct=54.40,
        market='A'
    )
    assert result['risk_level'] == 'LOW'
    assert result['risk_level'] in ['HIGH', 'MEDIUM', 'LOW']
```

- [ ] **Step 2: Run test**

Run: `uv run pytest src/analysis/test_shareholder_analyzer.py::test_assess_shareholder_risk_ashare_low -v`
Expected: FAIL

- [ ] **Step 3: Write assess_shareholder_risk**

```python
def assess_shareholder_risk(
    non_institutional_ratio_pct: float,
    institutional_ratio_qoq_change_pp: float | None,
    shareholder_count_qoq_change_pct: float | None,
    top10_concentration_pct: float | None,
    controlling_shareholder_pct: float | None,
    market: str = 'A'  # 'A' = A股, 'HK' = 港股
) -> dict:
    """
    评估股东结构风险等级

    阈值（A股）：
      - 非机构持股比例 > 35% → HIGH, 25%~35% → MEDIUM
      - 机构持股环比 < -5pp → HIGH, -3pp~-5pp → MEDIUM
      - 股东户数环比 > 30% → HIGH, 15%~30% → MEDIUM
      - 十大股东集中度 > 85% → HIGH, 75%~85% → MEDIUM
      - 控股股东持股 > 70% → HIGH, 55%~70% → MEDIUM

    阈值（港股）：
      - 非机构持股比例 > 50% → HIGH, 35%~50% → MEDIUM

    触发逻辑：任一 HIGH 即 HIGH；三项均 MEDIUM 也升为 HIGH
    """
    high_count = 0
    medium_count = 0
    triggers = []

    # 非机构持股比例
    if market == 'A':
        ni_threshold_high = 35.0
        ni_threshold_medium = 25.0
    else:  # 港股
        ni_threshold_high = 50.0
        ni_threshold_medium = 35.0

    if non_institutional_ratio_pct > ni_threshold_high:
        high_count += 1
        triggers.append(f"非机构持股比例 {non_institutional_ratio_pct:.1f}% > {ni_threshold_high}%")
    elif non_institutional_ratio_pct > ni_threshold_medium:
        medium_count += 1
        triggers.append(f"非机构持股比例 {non_institutional_ratio_pct:.1f}% ({ni_threshold_medium}~{ni_threshold_high}%)")

    # 机构持股环比
    if institutional_ratio_qoq_change_pp is not None:
        if market == 'A':
            inst_h = -5.0
            inst_m = -3.0
        else:  # 港股半年报，允许更大波动
            inst_h = -10.0
            inst_m = -5.0

        if institutional_ratio_qoq_change_pp < inst_h:
            high_count += 1
            triggers.append(f"机构持股环比 {institutional_ratio_qoq_change_pp:.2f}pp < {inst_h}pp")
        elif institutional_ratio_qoq_change_pp < inst_m:
            medium_count += 1
            triggers.append(f"机构持股环比 {institutional_ratio_qoq_change_pp:.2f}pp ({inst_m}~{inst_h}pp)")

    # 股东户数环比
    if shareholder_count_qoq_change_pct is not None:
        if shareholder_count_qoq_change_pct > 30.0:
            high_count += 1
            triggers.append(f"股东户数环比 {shareholder_count_qoq_change_pct:.1f}% > 30%")
        elif shareholder_count_qoq_change_pct > 15.0:
            medium_count += 1
            triggers.append(f"股东户数环比 {shareholder_count_qoq_change_pct:.1f}% (15%~30%)")

    # 十大股东集中度
    if top10_concentration_pct is not None:
        if top10_concentration_pct > 85.0:
            high_count += 1
            triggers.append(f"十大股东集中度 {top10_concentration_pct:.1f}% > 85%")
        elif top10_concentration_pct > 75.0:
            medium_count += 1
            triggers.append(f"十大股东集中度 {top10_concentration_pct:.1f}% (75%~85%)")

    # 控股股东持股
    if controlling_shareholder_pct is not None:
        if controlling_shareholder_pct > 70.0:
            high_count += 1
            triggers.append(f"控股股东持股 {controlling_shareholder_pct:.1f}% > 70%")
        elif controlling_shareholder_pct > 55.0:
            medium_count += 1
            triggers.append(f"控股股东持股 {controlling_shareholder_pct:.1f}% (55%~70%)")

    # 风险等级判断
    if high_count > 0:
        risk_level = 'HIGH'
    elif medium_count >= 3:
        risk_level = 'HIGH'
    elif medium_count > 0:
        risk_level = 'MEDIUM'
    else:
        risk_level = 'LOW'

    return {
        'risk_level': risk_level,
        'trigger_conditions': triggers,
        'high_count': high_count,
        'medium_count': medium_count
    }
```

- [ ] **Step 4: Run test**

Run: `uv run pytest src/analysis/test_shareholder_analyzer.py::test_assess_shareholder_risk_ashare_low -v`
Expected: PASS

- [ ] **Step 5: 写测试 — 高风险案例**

```python
def test_assess_shareholder_risk_high():
    """高风险案例（筹码高度分散）"""
    result = assess_shareholder_risk(
        non_institutional_ratio_pct=42.0,  # > 35%
        institutional_ratio_qoq_change_pp=-8.0,  # < -5pp
        shareholder_count_qoq_change_pct=35.0,  # > 30%
        top10_concentration_pct=60.0,
        controlling_shareholder_pct=40.0,
        market='A'
    )
    assert result['risk_level'] == 'HIGH'
    assert len(result['trigger_conditions']) >= 3
```

- [ ] **Step 6: Run test**

Run: `uv run pytest src/analysis/test_shareholder_analyzer.py::test_assess_shareholder_risk_high -v`
Expected: PASS

- [ ] **Step 7: 写测试 — 港股阈值**

```python
def test_assess_shareholder_risk_hk():
    """港股高阈值"""
    result = assess_shareholder_risk(
        non_institutional_ratio_pct=48.0,  # 港股 > 50% 才 HIGH, 48% 是 MEDIUM
        institutional_ratio_qoq_change_pp=-8.0,  # 港股 -8pp 是 MEDIUM（需 < -10pp 才 HIGH）
        shareholder_count_qoq_change_pct=10.0,
        top10_concentration_pct=70.0,
        controlling_shareholder_pct=30.0,
        market='HK'
    )
    assert result['risk_level'] == 'MEDIUM'
    assert result['high_count'] == 0
```

- [ ] **Step 8: Run test**

Run: `uv run pytest src/analysis/test_shareholder_analyzer.py::test_assess_shareholder_risk_hk -v`
Expected: PASS

- [ ] **Step 9: 写测试 — None 值处理**

```python
def test_assess_shareholder_risk_partial_data():
    """部分数据缺失时的处理"""
    result = assess_shareholder_risk(
        non_institutional_ratio_pct=27.45,
        institutional_ratio_qoq_change_pp=None,  # 数据缺失
        shareholder_count_qoq_change_pct=None,
        top10_concentration_pct=None,
        controlling_shareholder_pct=None,
        market='A'
    )
    assert result['risk_level'] == 'LOW'
    assert result['high_count'] == 0
    assert result['medium_count'] == 1  # 非机构持股比例 27.45% < 35%
```

- [ ] **Step 10: Run test**

Run: `uv run pytest src/analysis/test_shareholder_analyzer.py::test_assess_shareholder_risk_partial_data -v`
Expected: PASS

- [ ] **Step 11: 写测试 — classify_top10_holders**

```python
def test_classify_top10_holders():
    """十大流通股东分类"""
    holders = [
        {'name': '中国贵州茅台酒厂', 'ratio_pct': 54.40, 'type_raw': '其它'},
        {'name': '香港中央结算有限公司', 'ratio_pct': 4.69, 'type_raw': '其它'},
        {'name': '招商中证白酒指数基金', 'ratio_pct': 0.41, 'type_raw': '证券投资基金'},
        {'name': '华泰柏瑞沪深300ETF', 'ratio_pct': 0.40, 'type_raw': '证券投资基金'},
        {'name': '私募基金', 'ratio_pct': 0.33, 'type_raw': '私募基金'},
    ]
    result = classify_top10_holders(holders)

    assert result['controlling_shareholder_pct'] == 54.40
    assert result['institutional_breakdown']['fund_etf'] == 0.81
    assert result['institutional_breakdown']['private_fund'] == 0.33
    assert result['institutional_breakdown']['hkscc'] == 4.69
    assert result['top10_concentration_pct'] == 60.23
```

- [ ] **Step 12: Run test**

Run: `uv run pytest src/analysis/test_shareholder_analyzer.py::test_classify_top10_holders -v`
Expected: FAIL

- [ ] **Step 13: Write classify_top10_holders**

```python
def classify_top10_holders(holders: list[dict]) -> dict:
    """
    分类十大流通股东，返回机构持仓分解和集中度

    holder 类型映射：
      - 包含"控股股东"/"实控人"/"集团" → 控股股东
      - 包含"香港中央结算" → HKSCC
      - type_raw 含"国有"/"国资" → 国资
      - type_raw 含"证券投资基金"/"ETF"/"指数" → 证券投资基金
      - type_raw 含"私募" → 私募
      - type_raw 含"社保"/"保险" → 社保/保险
      - 其他 → 其他
    """
    controlling_pct = 0.0
    fund_etf = 0.0
    private_fund = 0.0
    social_security = 0.0
    hkscc = 0.0
    other = 0.0
    classified_holders = []

    for h in holders:
        name = h.get('name', '')
        ratio = h.get('ratio_pct', 0.0)
        type_raw = h.get('type_raw', '')

        if '控股股东' in name or '实控人' in name or ('集团' in name and '有限' in name):
            holder_type = '控股股东'
            controlling_pct += ratio
        elif '香港中央结算' in name:
            holder_type = 'HKSCC'
            hkscc += ratio
        elif '国有' in name or '国资' in type_raw:
            holder_type = '国资'
            other += ratio
        elif '证券投资基金' in type_raw or 'ETF' in name or '指数' in name:
            holder_type = '证券投资基金'
            fund_etf += ratio
        elif '私募' in type_raw or '私募" in name:
            holder_type = '私募'
            private_fund += ratio
        elif '社保' in name or '保险' in type_raw:
            holder_type = '社保/保险'
            social_security += ratio
        else:
            holder_type = '其他'
            other += ratio

        classified_holders.append({**h, 'type': holder_type})

    top10_concentration = sum(h.get('ratio_pct', 0) for h in holders)

    return {
        'controlling_shareholder_pct': round(controlling_pct, 2),
        'institutional_breakdown': {
            'fund_etf': round(fund_etf, 2),
            'private_fund': round(private_fund, 2),
            'social_security': round(social_security, 2),
            'hkscc': round(hkscc, 2),
            'other': round(other, 2)
        },
        'top10_concentration_pct': round(top10_concentration, 2),
        'classified_holders': classified_holders
    }
```

- [ ] **Step 14: Run test**

Run: `uv run pytest src/analysis/test_shareholder_analyzer.py::test_classify_top10_holders -v`
Expected: PASS

- [ ] **Step 15: 更新 __init__.py**

```python
# src/analysis/__init__.py
from .mx_parser import parse_institutional_ratio, parse_kline_ohlcv
from .technical_indicators import (
    calc_rsi, calc_macd, calc_kdj, calc_ma, calc_atr,
    calc_bollinger_bands, calc_volume_ratio
)
from .shareholder_analyzer import assess_shareholder_risk, classify_top10_holders

__all__ = [
    'parse_institutional_ratio', 'parse_kline_ohlcv',
    'calc_rsi', 'calc_macd', 'calc_kdj', 'calc_ma',
    'calc_atr', 'calc_bollinger_bands', 'calc_volume_ratio',
    'assess_shareholder_risk', 'classify_top10_holders'
]
```

- [ ] **Step 16: Run all tests**

Run: `uv run pytest src/analysis/test_shareholder_analyzer.py -v`
Expected: ALL PASS

- [ ] **Step 17: Commit**

```bash
git add src/analysis/shareholder_analyzer.py src/analysis/test_shareholder_analyzer.py src/analysis/__init__.py
git commit -m "feat(analysis): add shareholder structure risk assessment and top10 holder classification"
```

---

## Task 4: 更新 SKILL.md — 新增 Phase 1 步骤、Step 2.5-F、Step 3.5

**Files:**
- Modify: `.claude/skills/stock-analysis-unified/SKILL.md`

- [ ] **Step 1: 在 Step 1 数据收集表末尾新增股东和技术数据项**

找到 Step 1 数据收集表的第 13 项后，新增 F1-F3 和 T1-T2。

old_string（在 Step 1 数据收集表末尾，"13. **货币单位确认**" 之后）:
```
| 13 | **前瞻盈利预测** | FY1/FY2经调整净利润（含方向变化：上调/下调至±XX亿） | mx-data预测table + mx-search |
| 14 | **货币单位确认** | **报表原始币种 vs mx-data标准化口径（港股必需，防止单位混淆）** | mx-data |
```

new_string:
```
| 13 | **前瞻盈利预测** | FY1/FY2经调整净利润（含方向变化：上调/下调至±XX亿） | mx-data预测table + mx-search |
| 14 | **货币单位确认** | **报表原始币种 vs mx-data标准化口径（港股必需，防止单位混淆）** | mx-data |
| **F1** | **机构持股比例合计** | 近3年报 机构持股比例合计 | mx-data |
| **F2** | **股东户数** | 股东户数总股本流通股本 | mx-data |
| **F3** | **十大流通股东明细** | 十大流通股东 | mx-data |
| **T1** | **每日 OHLCV** | 近{N}日每日开盘价收盘价最高价最低价成交量 | mx-data |
| **T2** | **主力资金流向**（可选） | 近{N}日 主力资金流向 | mx-data |
```

- [ ] **Step 2: 在 Step 1 末尾新增数据范围说明**

在 Step 1 的 "前瞻数据双源优先级链" 之后、进入 Step 1.5 之前，新增：

```markdown
### Step 1 补充：股东数据与技术数据收集（与 F1-F3 / T1-T2 并行执行）

#### 股东数据（F1-F3）
```
收集时机：Step 1 原有 14 项完成后，作为补充步骤。

数据范围确定：
  - A股 (.SH/.SZ)：使用季度数据（最近4期）
  - 港股 (.HK)：使用半年度数据（最近2期）

保存路径：
  - mx_data_*机构持股比例*.json → stocks/{CODE}_{NAME}/v{N}_{DATE}/data/
  - mx_data_*股东户数*.json → 同上
  - mx_data_*十大流通股东*.json → 同上
```

#### 技术数据（T1-T2）
```
数据范围确定：
  - 主板股：近35日（确保≥30交易日）
  - 创业板/科创板：近25日
  - 识别方式：检查 entityTagDTO.className
    · 含"创业板" → 25日
    · 含"科创板" → 25日
    · 其他 → 35日

保存路径：
  - mx_data_*近{N}日每日*.json → stocks/{CODE}_{NAME}/v{N}_{DATE}/data/
```

- [ ] **Step 3: 在 Step 2.5 末尾新增 Step 2.5-F**

在 Step 2.5 末尾（"→ 继续 Step 2.5 防守检查（A-E五项均为必做），方可进入 Step 3。" 之后）新增：

```markdown
### Step 2.5-F: 股东结构分析（新增 F 项 — 必做）

> 评估筹码分散度和主力动向。所有股票都执行此检查。

**数据来源**（已由 Phase 1 收集）：
  - F1: mx_data_*机构持股比例*.json
  - F2: mx_data_*股东户数*.json
  - F3: mx_data_*十大流通股东*.json

**执行步骤**：

1. **解析机构持股比例** — 调用 `parse_institutional_ratio()`，提取最新一期和上一期数据
2. **计算非机构持股比例** — `non_institutional_ratio_pct = 100 - latest_ratio_pct`
3. **计算环比变化** — 机构持股环比、股东户数环比
4. **分类十大股东** — 调用 `classify_top10_holders()`，识别控股股东/基金/HKSCC 等类型
5. **评估风险等级** — 调用 `assess_shareholder_risk()`，使用市场差异化阈值
6. **写入 valuation_result.json** — shareholder_signal 字段
7. **输出报告章节** — `## 2.5-F 股东结构与筹码分析`

**HKSCC 特殊处理**：
  - 港股十大股东中"香港中央结算有限公司"为代名人，记录但单独标记
  - 不参与"有效机构持股"计算
  - 报告中注明"港股 HKSCC 持股为代表持有人，实际投资者结构未知"

**错误处理**：
  - 机构持股数据缺失 → 标注"数据受限，无法计算股东风险"
  - 仅有单一报告期 → 标注"数据不足"，环比变化记为 None
  - 股东户数不可用 → 跳过，不影响整体风险评级

**⚠️ 硬性规则**：Step 2.5-F 必须完成，才能进入 Step 3。即使数据受限，也必须标注后继续。
```

- [ ] **Step 4: 在 Step 3 之后新增 Step 3.5**

在 Step 3 末尾（"Step 4:" 之前）新增：

```markdown
### Step 3.5: 短期技术信号（新增 — 估值后入场时机参考）

> 基于 20-30 个交易日的技术指标，提供短期入场价位建议。**技术信号服从基本面**——不改变基本面方向，只辅助判断入场时机。

**数据来源**（已由 Phase 1 收集）：
  - T1: mx_data_*近{N}日每日*.json
  - 数据范围：主板 30+ 日，创业板/科创板 20+ 日

**执行步骤**：

1. **解析 K 线数据** — 调用 `parse_kline_ohlcv()`，提取 OHLCV 列表
2. **计算技术指标**（调用 `src/analysis/technical_indicators.py`）：
   - `calc_rsi(closes, 14)` — RSI(14)
   - `calc_macd(closes)` — MACD(12,26,9)
   - `calc_kdj(highs, lows, closes)` — KDJ(9,3,3)
   - `calc_ma(closes)` — MA(5/10/20/60)
   - `calc_volume_ratio(volumes)` — 成交量分析
   - `calc_atr(highs, lows, closes, 14)` — ATR（用于止损）
   - `calc_bollinger_bands(closes, 20)` — 布林带（用于支撑位）
3. **综合信号评分**（5 指标计分）：
   ```
   指标: [RSI方向, MACD方向, KDJ方向, MA排列, 成交量方向]
   得分: 看多=+1, 看空=-1, 中性=0
   总分 = sum(各指标得分)
   if 总分 >= 3: synthesis_signal = "BULLISH"
   elif 总分 <= -3: synthesis_signal = "BEARISH"
   else: synthesis_signal = "NEUTRAL"
   ```
4. **计算入场价位**：
   ```
   支撑位 = min(MA20, 近期低点, 布林下轨)
   阻力位 = max(MA60, 近期高点)
   技术止损 = 支撑位 - 2% × ATR
   目标位 = max(MA60, 近期高点, 阻力位)
   ```
5. **冲突检测**：若 `technical_signal.synthesis_signal == "BEARISH"` 且基本面方向为 BUY，则 `conflicts_with_fundamentals = True`
6. **写入 valuation_result.json** — technical_signal + fundamental_signal 字段
7. **输出报告章节** — `## 3.5 短期技术信号参考`

**信号有效期**：所有技术信号有效期为 5 个交易日（数据截止日 + 5）。

**禁止**：技术信号不得抬高基本面止损位，不得覆盖基本面卖出信号。

**⚠️ 硬性规则**：技术面服从基本面——Step 3.5 只提供入场时机参考，不改变 Step 3 的估值方向。
```

- [ ] **Step 5: 更新版本号**

在 SKILL.md 顶部 version 声明处：
```
*Version: Unified v1.9*
```

- [ ] **Step 6: Commit**

```bash
git add .claude/skills/stock-analysis-unified/SKILL.md
git commit -m "feat(skill): add Step 2.5-F shareholder analysis and Step 3.5 technical signals to v1.9"
```

---

## Task 5: 更新报告模板 — 新增股东结构和短期技术信号章节

**Files:**
- Modify: `.claude/skills/stock-analysis-unified/docs/report-template-reference.md`

- [ ] **Step 1: 在防守检查章节末尾（D/E 之后）新增 F 项**

在报告模板的 `#### E. ESG/政策风险` 之后，`---` 之前新增：

```markdown
#### F. 股东结构与筹码分析

**筹码集中度评估**
| 指标 | 数值 | 风险 |
|------|------|------|
| 非机构持股比例 | {non_institutional_ratio_pct}% | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |
| 机构持股环比 | {institutional_ratio_qoq_change_pp}pp | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |
| 股东户数环比 | {shareholder_count_qoq_change_pct}% | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |
| 十大股东集中度 | {top10_concentration_pct}% | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |
| 控股股东持股 | {controlling_shareholder_pct}% | ✅ LOW / ⚠️ MEDIUM / 🔴 HIGH |

**综合风险等级**: {risk_level}

> 解读：{AI 根据各指标综合撰写解读文本}

**十大流通股东结构**
| 类型 | 持股合计 | 环比变化 |
|------|---------|---------|
| 控股股东 | {pct}% | {变化} |
| 证券投资基金/ETF | {pct}% | {变化} |
| 私募/券商 | {pct}% | {变化} |
| HKSCC（港股通） | {pct}% | {变化} |

**风险提示**（若无 HIGH 风险则省略此节）：
- 🔴 HIGH: {具体触发条件}
- ⚠️ MEDIUM: {具体触发条件}
```

- [ ] **Step 2: 在报告末尾投资建议章节后新增技术信号章节**

在投资建议章节末尾、核心逻辑章节之前新增：

```markdown
## 3.5 短期技术信号参考

> ⚠️ 技术信号有效期：{data_end_date} 至 {signal_valid_until}（5个交易日）

### 3.5.1 技术指标现状
| 指标 | 数值 | 信号 |
|------|------|------|
| RSI(14) | {rsi_value} | {超买/超卖/中性} |
| MACD | DIF={dif}, DEA={dea}, 柱={histogram} | {金叉/死叉/收敛} |
| KDJ | K={k}, D={d}, J={j} | {金叉/死叉/中性} |
| MA系统 | {ma5}>{ma10}>{ma20}>{ma60} | **{多头排列/空头排列/混乱}** |
| 成交量 | 量比{ratio_vs_ma5} | {放量/缩量/正常} |

**综合信号**: {synthesis_signal}（{signal_consistency_score}）

### 3.5.2 入场参考价位
| 类型 | 价位 | 逻辑 |
|------|------|------|
| 第一支撑 | {support_1}元 | {MA20/布林下轨/近期低点} |
| 第二支撑 | {support_2}元 | {布林下轨/近期低点} |
| 建议入场区间 | {recommended_entry_range}元 | 回调至支撑位附近 |
| 技术止损位 | {technical_stop_loss}元 | 支撑-{atr_pct}%ATR |
| 目标位 | {target_price}元 | {MA60/前高}，R:R={risk_reward_ratio} |

### 3.5.3 技术与基本面共振评估
| 维度 | 方向 | 说明 |
|------|------|------|
| 基本面方向 | {fundamental_signal.direction} | {fundamental_signal.rationale} |
| 技术面方向 | {synthesis_signal} | {AI 描述技术面状态} |
| **综合结论** | **{共振/分歧}，服从基本面** | {综合操作建议} |

> ⚠️ 技术止损位（{technical_stop_loss}元）不低于基本面止损位（{fundamental_stop_loss}元）。
```

- [ ] **Step 3: 更新 valuation_result.json 模板 — 新增字段**

在 `defense_check` 字段后新增：
```json
  "shareholder_signal": {
    "non_institutional_ratio_pct": "number",
    "institutional_ratio_qoq_change_pp": "number",
    "shareholder_count_qoq_change_pct": "number",
    "top10_concentration_pct": "number",
    "controlling_shareholder_pct": "number",
    "risk_level": "HIGH | MEDIUM | LOW",
    "trigger_conditions": ["string"],
    "top10_holders": [{"name": "string", "type": "string", "ratio_pct": "number", "change": "string"}],
    "institutional_breakdown": {"fund_etf": "number", "private_fund": "number", "social_security": "number", "hkscc": "number", "other": "number"},
    "report_date": "string"
  },
  "technical_signal": {
    "data_range_days": "number",
    "data_end_date": "string",
    "signal_valid_until": "string",
    "board_type": "main_board | chinext | star",
    "indicators": {
      "rsi_14": {"value": "number", "oversold": "boolean", "overbought": "boolean"},
      "macd": {"dif": "number", "dea": "number", "histogram": "number", "crossover": "string", "divergence": "string"},
      "kdj": {"k": "number", "d": "number", "j": "number", "crossover": "string"},
      "ma": {"ma5": "number", "ma10": "number", "ma20": "number", "ma60": "number", "arrangement": "string"},
      "volume": {"ratio_vs_ma5": "number", "trend": "string"}
    },
    "synthesis_signal": "BULLISH | NEUTRAL | BEARISH",
    "signal_consistency_score": "string",
    "entry_levels": {
      "support_1": "number", "support_2": "number", "resistance_1": "number",
      "recommended_entry_range": "string", "technical_stop_loss": "number",
      "target_price": "number", "risk_reward_ratio": "number"
    },
    "conflicts_with_fundamentals": "boolean"
  },
  "fundamental_signal": {
    "direction": "BUY | HOLD | SELL",
    "conviction": "HIGH | MEDIUM | LOW",
    "rationale": "string"
  }
```

- [ ] **Step 4: Commit**

```bash
git add .claude/skills/stock-analysis-unified/docs/report-template-reference.md
git commit -m "docs: add shareholder analysis and technical signal sections to report template"
```

---

## Task 6: 全量测试验证

**Files:**
- Run: `src/analysis/test_technical_indicators.py`, `src/analysis/test_shareholder_analyzer.py`, `src/analysis/test_mx_parser.py`

- [ ] **Step 1: 运行所有测试**

Run: `uv run pytest src/analysis/ -v`
Expected: ALL PASS

- [ ] **Step 2: 集成测试 — 用真实 mx-data 数据**

```bash
# 使用之前实测的贵州茅台数据测试完整流程
python3 -c "
from src.analysis import parse_institutional_ratio, parse_kline_ohlcv
from src.analysis.technical_indicators import calc_rsi, calc_macd, calc_kdj, calc_ma, calc_atr, calc_bollinger_bands
from src.analysis.shareholder_analyzer import assess_shareholder_risk

# 股东数据
r = parse_institutional_ratio('/Users/zhul1/Documents/aiWorkspace/ai-investor/tmp/mx_test/mx_data_贵州茅台机构持股比例_raw.json')
print(f'非机构持股比例: {r[\"non_institutional_ratio_pct\"]:.2f}%')
print(f'环比变化: {r[\"qoq_change_pp\"]}pp')

# 风险评估
risk = assess_shareholder_risk(
    non_institutional_ratio_pct=r['non_institutional_ratio_pct'],
    institutional_ratio_qoq_change_pp=r['qoq_change_pp'],
    shareholder_count_qoq_change_pct=3.2,
    top10_concentration_pct=68.0,
    controlling_shareholder_pct=54.40,
    market='A'
)
print(f'风险等级: {risk[\"risk_level\"]}')

# 技术指标
rows = parse_kline_ohlcv('/Users/zhul1/Documents/aiWorkspace/ai-investor/tmp/mx_test/mx_data_贵州茅台近30日每日开盘价收盘价最高价最低价成交量_raw.json')
closes = [r['收盘价'] for r in rows if '收盘价' in r]
highs = [r['最高价'] for r in rows if '最高价' in r]
lows = [r['最低价'] for r in rows if '最低价' in r]
volumes = [r['成交量'] for r in rows if '成交量' in r]

print(f'K线数据: {len(rows)} 行')
print(f'RSI(14): {calc_rsi(closes)}')
print(f'MACD: {calc_macd(closes)}')
print(f'KDJ: {calc_kdj(highs, lows, closes)}')
print(f'MA: {calc_ma(closes)}')
print(f'ATR: {calc_atr(highs, lows, closes)}')
print(f'布林带: {calc_bollinger_bands(closes)}')
"
```

Expected: 所有函数正常输出数值，无异常

- [ ] **Step 3: 提交最终状态**

```bash
git add -A && git commit -m "feat: complete shareholder analysis + technical signals implementation"
```

---

## 实施顺序

| Task | 说明 | 依赖 |
|------|------|------|
| Task 1 | mx_parser.py（解析工具） | 无 |
| Task 2 | technical_indicators.py（技术指标） | Task 1 |
| Task 3 | shareholder_analyzer.py（股东分析） | Task 1 |
| Task 4 | 更新 SKILL.md | Task 1, 2, 3 |
| Task 5 | 更新报告模板 | Task 4 |
| Task 6 | 全量测试验证 | Task 1, 2, 3, 4, 5 |
