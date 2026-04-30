from __future__ import annotations

from src.tools.screenshot_import.models import ClassificationResult, Platform


PLATFORM_LABELS: dict[Platform, str] = {
    "ths": "同花顺",
    "eastmoney": "东方财富",
    "hk_panda": "香港熊猫证券",
    "other": "其他券商",
    "unknown": "未知券商",
}


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
        "",
    ]

    if st == "holding":
        lines.extend(
            [
                "## 持仓截图",
                "- 逐行提取：证券代码、证券名称、成本价（持仓成本/成本价）、持股数量（当前持股/股份余额）。",
                "- 不要把当前价、最新价、市值、浮动盈亏、可用数量、可卖数量、涨跌幅百分比等与成本价混淆；"
                "成本价必须与截图中明确表示「成本」「持仓成本」「成本价」等列对应。",
                "- 若某行无法可靠识别成本或股数，请在 warnings 中说明，该行宁可不收或降低 confidence。",
                "",
            ]
        )
    elif st == "watchlist":
        lines.extend(
            [
                "## 自选 / 列表截图（非持仓）",
                "- 每行仅输出证券代码与证券名称（is_holding=false）。",
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
