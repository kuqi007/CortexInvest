# src/analysis/shareholder_analyzer.py
"""股东结构分析 — 风险评估、十大股东分类"""


def assess_shareholder_risk(
    non_institutional_ratio_pct: float,
    institutional_ratio_qoq_change_pp: float | None,
    shareholder_count_qoq_change_pct: float | None,
    top10_concentration_pct: float | None,
    controlling_shareholder_pct: float | None,
    market: str = 'A',
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
    else:
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
            inst_h, inst_m = -5.0, -3.0
        else:
            inst_h, inst_m = -10.0, -5.0

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
        'medium_count': medium_count,
    }


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

        if '控股股东' in name or '实控人' in name or '集团' in name or '酒厂' in name or '总公司' in name:
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
        elif '私募' in type_raw or '私募' in name:
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
            'other': round(other, 2),
        },
        'top10_concentration_pct': round(top10_concentration, 2),
        'classified_holders': classified_holders,
    }
