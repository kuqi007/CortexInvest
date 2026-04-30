# src/analysis/test_shareholder_analyzer.py
import pytest
from .shareholder_analyzer import assess_shareholder_risk, classify_top10_holders


class TestAssessShareholderRisk:
    def test_ashare_low(self):
        result = assess_shareholder_risk(
            non_institutional_ratio_pct=20.0,  # < 25% → not triggered
            institutional_ratio_qoq_change_pp=-1.59,
            shareholder_count_qoq_change_pct=3.2,
            top10_concentration_pct=68.0,
            controlling_shareholder_pct=54.40,
            market='A'
        )
        assert result['risk_level'] == 'LOW'
        assert result['high_count'] == 0
        assert result['medium_count'] == 0

    def test_high(self):
        result = assess_shareholder_risk(
            non_institutional_ratio_pct=42.0,  # > 35%
            institutional_ratio_qoq_change_pp=-8.0,  # < -5pp
            shareholder_count_qoq_change_pct=35.0,  # > 30%
            top10_concentration_pct=60.0,
            controlling_shareholder_pct=40.0,
            market='A'
        )
        assert result['risk_level'] == 'HIGH'
        assert result['high_count'] >= 3

    def test_hk_threshold(self):
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

    def test_partial_data(self):
        result = assess_shareholder_risk(
            non_institutional_ratio_pct=20.0,  # < 25% → not triggered
            institutional_ratio_qoq_change_pp=None,
            shareholder_count_qoq_change_pct=None,
            top10_concentration_pct=None,
            controlling_shareholder_pct=None,
            market='A'
        )
        assert result['risk_level'] == 'LOW'
        assert result['high_count'] == 0

    def test_three_medium_escalates(self):
        # 三项均 MEDIUM → 升为 HIGH
        result = assess_shareholder_risk(
            non_institutional_ratio_pct=30.0,  # MEDIUM (25~35)
            institutional_ratio_qoq_change_pp=-4.0,  # MEDIUM (-3~-5)
            shareholder_count_qoq_change_pct=20.0,  # MEDIUM (15~30)
            top10_concentration_pct=None,
            controlling_shareholder_pct=None,
            market='A'
        )
        assert result['risk_level'] == 'HIGH'
        assert result['medium_count'] == 3

    def test_only_non_institutional_medium(self):
        # 只有非机构持股比例在 MEDIUM 区间
        result = assess_shareholder_risk(
            non_institutional_ratio_pct=30.0,
            institutional_ratio_qoq_change_pp=0.0,
            shareholder_count_qoq_change_pct=5.0,
            top10_concentration_pct=60.0,
            controlling_shareholder_pct=50.0,
            market='A'
        )
        assert result['risk_level'] == 'MEDIUM'
        assert result['medium_count'] == 1

    def test_hk_high_threshold(self):
        # 港股非机构持股 52% > 50% → HIGH
        result = assess_shareholder_risk(
            non_institutional_ratio_pct=52.0,
            institutional_ratio_qoq_change_pp=None,
            shareholder_count_qoq_change_pct=None,
            top10_concentration_pct=None,
            controlling_shareholder_pct=None,
            market='HK'
        )
        assert result['risk_level'] == 'HIGH'


class TestClassifyTop10Holders:
    def test_basic(self):
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

    def test_hkscc_isolation(self):
        holders = [
            {'name': '香港中央结算有限公司', 'ratio_pct': 30.0, 'type_raw': '其它'},
            {'name': '控股股东A', 'ratio_pct': 20.0, 'type_raw': '其它'},
        ]
        result = classify_top10_holders(holders)
        assert result['institutional_breakdown']['hkscc'] == 30.0
        assert result['institutional_breakdown']['fund_etf'] == 0.0
        # HKSCC 不参与有效机构持股
        assert result['controlling_shareholder_pct'] == 20.0

    def test_etf_by_name(self):
        holders = [
            {'name': '华夏上证50ETF', 'ratio_pct': 1.5, 'type_raw': '其它'},
        ]
        result = classify_top10_holders(holders)
        assert result['institutional_breakdown']['fund_etf'] == 1.5

    def test_broker(self):
        holders = [
            {'name': '中信证券股份有限公司', 'ratio_pct': 2.0, 'type_raw': '证券公司'},
            {'name': '华泰证券', 'ratio_pct': 1.0, 'type_raw': '其它'},
        ]
        result = classify_top10_holders(holders)
        assert result['institutional_breakdown']['broker'] == 3.0

    def test_national_capital(self):
        holders = [
            {'name': '国有石油集团', 'ratio_pct': 5.0, 'type_raw': '国有企业'},
            {'name': '国资运营公司', 'ratio_pct': 2.0, 'type_raw': '其它'},
        ]
        result = classify_top10_holders(holders)
        assert result['institutional_breakdown']['national_capital'] == 7.0
