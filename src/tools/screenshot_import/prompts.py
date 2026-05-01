from __future__ import annotations

from src.tools.screenshot_import.models import ClassificationResult, Platform


PLATFORM_LABELS: dict[Platform, str] = {
    "ths": "同花顺",
    "eastmoney": "东方财富",
    "hk_panda": "香港熊猫证券",
    "other": "其他券商",
    "unknown": "未知券商",
}


def _holding_layout_guidance(classification: ClassificationResult) -> list[str]:
    platform = classification.platform
    signals = set(classification.signals)

    if platform == "eastmoney":
        return [
            "## 东方财富持仓版式提示",
            "- 暗色模式样本：黑色背景，顶部偏深蓝/黑，字段通常是「名称/市值」「持仓/可用」「现价/成本」「当日盈亏」。",
            "- 亮色（浅色）持仓样本：顶部可能是深色横幅（含「普通/信用」等），也可能是东方财富橙红色品牌块；正文白底，底部通常有「首页/社区/自选/行情/理财/交易」，「交易」常为橙色高亮。",
            "- 持仓 Tab 激活时，列表上方可能有总资产摘要卡片（例如证券市值、持仓盈亏、可用）；这些是汇总信息，不要当成某一行的成本或股数。",
            "- 分组标题可能出现「港股通 HKD」及仓位百分比；分组汇总行的市值/盈亏与下方个股行的「名称/市值」不要混淆，导入时每只股票只看该行自身列。",
            "- 东方财富持仓截图通常不显示股票代码；名称旁边的「沪」「深」「港」只是市场标签，不是代码。",
            "- 每个股票通常占两行：名称列下方是市值；「持仓/可用」第一行是持仓数量、第二行是可用数量；"
            "「现价/成本」第一行是现价、第二行是成本价；最右侧是当日盈亏金额和涨跌百分比。",
            "- 「名称/市值」第二行是市值，带 HKD 或人民币单位；不要把市值当作成本，也不要把市值除以股数后的隐含价当作成本。",
            "- 对东方财富截图，code 应设为 null；只输出截图中可见股票名称，由系统后续按名称查码并进入人工确认。",
            "",
        ]

    if platform == "ths":
        if "dark_theme" in signals:
            theme_line = "- 暗黑模式样本：背景黑色，红字表示上涨/盈利，蓝字表示下跌/亏损。"
        else:
            theme_line = "- 浅色模式样本：顶部为红色区块，白色背景，红字表示上涨/盈利，蓝字表示下跌/亏损。"
        return [
            "## 同花顺持仓版式提示",
            theme_line,
            "- 字段通常按列排列为「市值」「盈亏」「持仓/可用」「成本/现价」「当日盈亏」。",
            "- 「持仓/可用」第一行是当前持仓数量，第二行是可用数量；导入 shares 使用第一行。",
            "- 「成本/现价」第一行是成本价，第二行是现价；不要把两者反过来。",
            "- 股票名称在左侧，代码可能不总是显示；若代码不可见但名称可唯一匹配，"
            "可输出规范代码并降低 code 置信度，否则跳过该行并写入 warnings。",
            "",
        ]

    if platform == "hk_panda":
        return [
            "## 香港熊猫证券持仓版式提示",
            "- 白底资产页，顶部通常无明显红/橙/深色券商色块，底部有「关注/市场/动态/资产/我的」。",
            "- 行内左侧是股票名称，名称下方是 5 位港股代码；代码输出为 HK + 5 位数字。",
            "- 字段通常为「市值/数量」「现价/成本」「当日盈亏」："
            "市值/数量第一行是 market_value、第二行是 shares；现价/成本第一行是 current_price、第二行是 cost。",
            "- 右侧青绿色通常表示亏损，橙红色通常表示盈利；盈亏颜色不能用于判断成本或数量。",
            "",
        ]

    return [
        "## 通用持仓版式提示",
        "- 先识别表头，再逐行读取；不要跨行串列。",
        "- 股票代码、名称、持仓数量、成本价必须来自同一行。",
        "- market_value、current_price、available_shares、daily_pnl 可作为辅助字段输出；"
        "但导入持仓主要依赖 cost 与 shares。",
        "",
    ]


def build_upload_disclosure(provider_name: str) -> str:
    """中文提示：截图将发往推理服务商，可能包含真实持仓/成本/股数。"""
    name = (provider_name or "").strip()
    return (
        f"注意：您即将把截图上传至 **{name}** 推理服务处理。"
        "截图中可能包含真实持仓、成本价、持股数量等敏感信息；"
        "请确认服务商与会话环境可信后再继续。"
    )


def build_vision_prompt(classification: ClassificationResult) -> str:
    """按券商与截图类型生成中文视觉解析指令（严格 JSON 输出）。"""
    label = PLATFORM_LABELS.get(classification.platform, PLATFORM_LABELS["unknown"])
    st = classification.screenshot_type

    lines: list[str] = [
        f"你正在分析「{label}」股票软件截图。",
        "请仅根据截图可见内容提取信息，不要编造。",
        "",
        "## 代码规范化（必须遵守）",
        "- A 股：输出 6 位数字代码，必要时根据画面交易所提示推断上海（6 开头等）或深圳/北京市场；"
        "不要输出带交易所后缀混排的非法代码。",
        "- 港股：使用 5 位数字代码（不足补前导零）或画面中与港交所一致的代码形式，保持与行情软件一致。",
        "- HK 与 A 股列混排时，严格按行归属市场，勿串行。",
        "- 必须优先复制截图中可见代码；如果可见代码包含字母或不是标准股票/ETF/基金代码，"
        "不要强行改写成看似合理的 6 位股票代码。此时可将 code 设为 null，仅用名称识别，并降低或省略 code 置信度。",
        "- 特别注意：同花顺自选页中 `1B` / `IB` 开头的板块或指数代码不是股票代码，"
        "不要把 `1B0685`、`IB0685` 之类误写成 `688685`；这类行应 code=null 或跳过。",
        "- 不要根据名称猜测代码；代码不可见或不确定时，宁可输出 name-only。",
        "",
    ]

    if st == "holding":
        lines.extend(
            [
                "## 持仓截图",
                "- 导入持仓的必要信息只有三类：能确定是哪只股票（代码或名称至少一个可靠）、成本价、持股数量。",
                "- 若代码不可见但名称清晰且可唯一识别，允许只用名称确定股票；此时 code 置信度应降低，"
                "并在 warnings 中说明需要用系统已有股票或名称匹配补全代码。",
                "- 逐行提取：证券代码、证券名称、市值、当前价、成本价、持股数量、可用数量、当日盈亏（若可见）。",
                "- 先读表头；若平台提示与截图表头冲突，以截图表头为准，不要机械套用平台模板。",
                "- 不要把当前价、最新价、市值、浮动盈亏、可用数量、可卖数量、涨跌幅百分比等与成本价混淆；"
                "成本价必须与截图中明确表示「成本」「持仓成本」「成本价」等列对应。",
                "- JSON 中持仓行必须输出 cost 与 shares；若能可靠读取，也输出 current_price、market_value、"
                "available_shares、daily_pnl、daily_pnl_pct 作为辅助校验字段。",
                "- current_price、market_value、available_shares、daily_pnl、daily_pnl_pct 都是可选辅助字段；"
                "看不清时不要猜，也不要因为这些字段缺失而丢弃一条已确定股票身份、成本和股数的持仓。",
                "- daily_pnl 只填当日盈亏金额；daily_pnl_pct 只填当日盈亏百分比，例如 0.42% 输出 0.42。",
                "- 若某行无法可靠识别成本或股数，请在 warnings 中说明，该行宁可不收或降低 confidence。",
                "",
            ]
        )
        lines.extend(_holding_layout_guidance(classification))
    elif st == "watchlist":
        lines.extend(
            [
                "## 自选 / 列表截图（非持仓）",
                "- 导入自选只需要能确定是哪只股票：代码或名称至少一个可靠即可。",
                "- 每行仅输出证券代码与证券名称（is_holding=false）；代码不可见但名称清晰时，可以输出名称并降低 code 置信度。",
                "- 行业、板块、概念或指数项若代码不是标准股票/ETF/基金代码，不要伪造股票代码；"
                "可以只输出名称，或在无法确定时跳过并写入 warnings。",
                "- **禁止**输出 cost、shares 字段或任何成本、数量信息，即使画面中有现价或涨跌。",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "## 截图类型不确定",
                "- 若能区分持仓列（有成本/数量）则按持仓提取；否则仅提取代码与名称，并在 warnings 中说明不确定。",
                "",
            ]
        )

    lines.extend(
        [
            "## 输出格式",
            "- 仅输出一个 JSON 对象，符合给定的 JSON Schema；不要 Markdown、不要代码围栏、不要前后说明文字。",
            "- `platform` 应与分类一致或从画面推断为 ths / eastmoney / hk_panda / other / unknown。",
            f"- `screenshot_type` 优先与分类一致（当前：{st}）。",
            "- `stocks` 中每条需包含 `field_confidence`（对 code/name/cost/shares 的 0~1 置信度，自选无 cost/shares 则对应项省略）。",
        ]
    )

    return "\n".join(lines)
