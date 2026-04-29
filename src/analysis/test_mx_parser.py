# src/analysis/test_mx_parser.py
import json, tempfile, os, pytest

from .mx_parser import parse_institutional_ratio, parse_kline_ohlcv


class TestParseInstitutionalRatio:
    def test_basic(self):
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

    def test_missing_col(self):
        raw = {
            'data': {
                'data': {
                    'searchDataResultDTO': {
                        'dataTableDTOList': [{
                            'rawTable': {'headName': ['2026一季报']},
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
            with pytest.raises(ValueError, match="Column 100000000003145 not found"):
                parse_institutional_ratio(path)
        finally:
            os.unlink(path)

    def test_single_period(self):
        raw = {
            'data': {
                'data': {
                    'searchDataResultDTO': {
                        'dataTableDTOList': [{
                            'rawTable': {
                                '100000000003145': ['72.548'],
                                'headName': ['2026一季报']
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
            assert result['prev_ratio_pct'] is None
            assert result['qoq_change_pp'] is None
            assert result['non_institutional_ratio_pct'] == 27.452
        finally:
            os.unlink(path)


class TestParseKlineOhlcv:
    def test_basic(self):
        raw = {
            'data': {
                'data': {
                    'searchDataResultDTO': {
                        'dataTableDTOList': [
                            {'rawTable': {}, 'nameMap': {}, 'table': {}},  # Table 0
                            {
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
            # 最新在 Table 1 行[0] = 2026-04-29
            assert rows[-1]['date'] == '2026-04-27'
        finally:
            os.unlink(path)

    def test_units(self):
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
                                    '开盘价': ['1,405']
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

    def test_single_table_fallback(self):
        raw = {
            'data': {
                'data': {
                    'searchDataResultDTO': {
                        'dataTableDTOList': [
                            {
                                'rawTable': {
                                    'headName': ['2026-04-29', '2026-04-28'],
                                    '收盘价': ['1401', '1405'],
                                    '开盘价': ['1405', '1402'],
                                },
                                'nameMap': {'收盘价': '收盘价', '开盘价': '开盘价'},
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
            assert len(rows) == 2
            assert rows[0]['收盘价'] == 1401.0
        finally:
            os.unlink(path)
