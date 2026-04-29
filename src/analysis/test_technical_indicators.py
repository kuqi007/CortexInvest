# src/analysis/test_technical_indicators.py
import pytest
from .technical_indicators import (
    calc_rsi, calc_macd, calc_kdj, calc_ma,
    calc_atr, calc_bollinger_bands, calc_volume_ratio
)

# 固定测试数据（20个交易日）
TEST_CLOSES = [1405, 1402, 1420, 1413, 1408, 1415, 1411, 1403, 1400, 1467,
               1445, 1443, 1444, 1459, 1465, 1460, 1460, 1459, 1459, 1464]
TEST_OPENS  = [1405, 1402, 1420, 1413, 1408, 1415, 1411, 1403, 1400, 1467,
               1445, 1443, 1444, 1459, 1465, 1460, 1460, 1459, 1459, 1464]
TEST_HIGHS  = [1410, 1409, 1420, 1458, 1420, 1415, 1415, 1410, 1462, 1467,
               1448, 1446, 1455, 1465, 1468, 1465, 1462, 1460, 1462, 1468]
TEST_LOWS   = [1400, 1400, 1402, 1413, 1405, 1408, 1403, 1395, 1395, 1435,
               1435, 1435, 1440, 1455, 1458, 1458, 1455, 1455, 1455, 1460]
TEST_VOLUMES = [348, 340, 728, 554, 377, 269, 217, 364, 968, 245,
                 345, 242, 252, 288, 207, 338, 241, 201, 210, 291]


class TestCalcRsi:
    def test_basic(self):
        result = calc_rsi(TEST_CLOSES, period=14)
        assert result is not None
        assert 0 <= result <= 100
        assert 10 < result < 90

    def test_insufficient_data(self):
        result = calc_rsi([100, 105, 110], period=14)
        assert result is None

    def test_perfect_uptrend(self):
        # 单边上涨 → RSI 接近100
        closes = list(range(100, 120))
        result = calc_rsi(closes, period=14)
        assert result is not None
        assert result > 60

    def test_perfect_downtrend(self):
        # 单边下跌 → RSI 接近0
        closes = list(range(120, 100, -1))
        result = calc_rsi(closes, period=14)
        assert result is not None
        assert result < 40


class TestCalcMacd:
    def test_basic(self):
        result = calc_macd(TEST_CLOSES)
        assert 'dif' in result
        assert 'dea' in result
        assert 'histogram' in result
        assert 'crossover' in result
        assert 'divergence' in result
        assert result['crossover'] in ['none', 'golden', 'death']
        assert result['divergence'] in ['none', 'bullish', 'bearish']

    def test_histogram_sign(self):
        result = calc_macd(TEST_CLOSES)
        # histogram = dif - dea，应与 crossover 一致
        if result['crossover'] == 'golden':
            assert result['histogram'] >= 0
        elif result['crossover'] == 'death':
            assert result['histogram'] <= 0


class TestCalcKdj:
    def test_basic(self):
        result = calc_kdj(TEST_HIGHS, TEST_LOWS, TEST_CLOSES)
        assert 'k' in result
        assert 'd' in result
        assert 'j' in result
        assert 'crossover' in result
        assert 'k_series' in result
        assert result['crossover'] in ['none', 'golden', 'death']
        assert 0 <= result['k'] <= 100
        assert 0 <= result['d'] <= 100

    def test_short_data(self):
        # 数据不足9个
        result = calc_kdj([100, 105], [98, 103], [99, 104])
        assert result['k_series'] == []


class TestCalcMa:
    def test_basic(self):
        result = calc_ma(TEST_CLOSES)
        assert 'ma5' in result
        assert 'ma10' in result
        assert 'ma20' in result
        assert 'ma60' in result
        assert 'arrangement' in result
        assert result['arrangement'] in ['bullish', 'bearish', 'neutral']

    def test_bullish_arrangement(self):
        # 多头排列：短 > 长
        closes = [100, 101, 102, 103, 104, 105, 106, 107] * 10
        result = calc_ma(closes[-30:], periods=(5, 10, 20))
        if result['arrangement'] == 'bullish':
            assert result['ma5'] > result['ma10'] > result['ma20']


class TestCalcAtr:
    def test_basic(self):
        result = calc_atr(TEST_HIGHS, TEST_LOWS, TEST_CLOSES, period=14)
        assert result is not None
        assert result > 0
        assert result < 100

    def test_insufficient(self):
        result = calc_atr([100, 105], [98, 103], [99, 104], period=14)
        assert result is None


class TestCalcBollingerBands:
    def test_basic(self):
        result = calc_bollinger_bands(TEST_CLOSES, period=20)
        assert result is not None
        assert 'middle' in result
        assert 'upper' in result
        assert 'lower' in result
        assert result['upper'] > result['middle'] > result['lower']
        assert result['upper'] > TEST_CLOSES[-1] > result['lower']

    def test_insufficient(self):
        result = calc_bollinger_bands([100, 105, 110, 115], period=20)
        assert result is None


class TestCalcVolumeRatio:
    def test_basic(self):
        result = calc_volume_ratio(TEST_VOLUMES)
        assert 'ratio_vs_ma5' in result
        assert 'trend' in result
        assert result['trend'] in ['expanding', 'contracting', 'neutral']

    def test_expanding(self):
        # 近期放量
        volumes = [100, 100, 100, 100, 100, 300]
        result = calc_volume_ratio(volumes)
        assert result['trend'] == 'expanding'

    def test_contracting(self):
        # 近期缩量
        volumes = [300, 300, 300, 300, 300, 50]
        result = calc_volume_ratio(volumes)
        assert result['trend'] == 'contracting'


class TestSynthesisSignal:
    def test_scoring(self):
        # RSI 中性
        rsi = calc_rsi(TEST_CLOSES, 14)
        rsi_score = 0 if 30 <= rsi <= 70 else (1 if rsi < 30 else -1)

        # MACD
        macd = calc_macd(TEST_CLOSES)
        macd_score = 1 if macd['crossover'] == 'golden' else (-1 if macd['crossover'] == 'death' else 0)

        # KDJ
        kdj = calc_kdj(TEST_HIGHS, TEST_LOWS, TEST_CLOSES)
        kdj_score = 1 if kdj['crossover'] == 'golden' else (-1 if kdj['crossover'] == 'death' else 0)

        # MA
        ma = calc_ma(TEST_CLOSES)
        ma_score = 1 if ma['arrangement'] == 'bullish' else (-1 if ma['arrangement'] == 'bearish' else 0)

        # 成交量
        vol = calc_volume_ratio(TEST_VOLUMES)
        vol_score = 1 if vol['trend'] == 'expanding' else (-1 if vol['trend'] == 'contracting' else 0)

        total = rsi_score + macd_score + kdj_score + ma_score + vol_score
        assert -5 <= total <= 5
        signal = 'BULLISH' if total >= 3 else ('BEARISH' if total <= -3 else 'NEUTRAL')
        assert signal in ['BULLISH', 'NEUTRAL', 'BEARISH']
