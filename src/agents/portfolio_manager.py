from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
import json
from src.utils.logging_config import setup_logger

from src.agents.state import AgentState, show_agent_reasoning, show_workflow_status
from src.tools.openrouter_config import get_chat_completion
from src.utils.api_utils import agent_endpoint, log_llm_interaction

# 初始化 logger
logger = setup_logger('portfolio_management_agent')

##### Portfolio Management Agent #####


@agent_endpoint("portfolio_management", "负责投资组合管理和最终交易决策")
def portfolio_management_agent(state: AgentState):
    """Responsible for portfolio management"""
    agent_name = "portfolio_management_agent"
    logger.info(f"\n--- DEBUG: {agent_name} START ---")
    logger.info(
        f"--- DEBUG: {agent_name} INCOMING messages: {[msg.name for msg in state['messages']]} ---")
    for i, msg in enumerate(state['messages']):
        logger.info(
            f"  DEBUG MSG {i}: name='{msg.name}', content_preview='{str(msg.content)[:100]}...'")

    show_workflow_status(f"{agent_name}: --- Executing Portfolio Manager ---")
    show_reasoning_flag = state["metadata"]["show_reasoning"]
    portfolio = state["data"]["portfolio"]

    # Get messages from other agents
    technical_message = next(
        msg for msg in state["messages"] if msg.name == "technical_analyst_agent")
    fundamentals_message = next(
        msg for msg in state["messages"] if msg.name == "fundamentals_agent")
    sentiment_message = next(
        msg for msg in state["messages"] if msg.name == "sentiment_agent")
    valuation_message = next(
        msg for msg in state["messages"] if msg.name == "valuation_agent")
    risk_message = next(
        msg for msg in state["messages"] if msg.name == "risk_management_agent")

    # Existing macro analysis from macro_analyst_agent (tool-based general macro)
    tool_based_macro_message = next(
        msg for msg in state["messages"] if msg.name == "macro_analyst_agent")
    # Assuming content is JSON string
    tool_based_macro_content = tool_based_macro_message.content

    # NEW: Market-wide news summary from macro_news_agent
    market_wide_news_summary_content = state["data"].get(
        "macro_news_analysis_result", "大盘宏观新闻分析不可用或未提供。")
    show_agent_reasoning(
        agent_name, f"Retrieved market_wide_news_summary_content: {market_wide_news_summary_content[:100]}...")

    system_message_content = """You are a portfolio manager making final trading decisions.
            Your job is to make a trading decision based on the team's analysis while strictly adhering
            to risk management constraints.

            RISK MANAGEMENT CONSTRAINTS:
            - You MUST NOT exceed the max_position_size specified by the risk manager
            - You MUST follow the trading_action (buy/sell/hold) recommended by risk management
            - These are hard constraints that cannot be overridden by other signals

            When weighing the different signals for direction and timing:
            1. Valuation Analysis (30% weight)
            2. Fundamental Analysis (25% weight)
            3. Technical Analysis (20% weight)
            4. Macro Analysis (15% weight) - This encompasses TWO inputs:
               a) General Macro Environment (from Macro Analyst Agent, tool-based)
               b) Daily Market-Wide News Summary (from Macro News Agent)
               Both provide context for external risks and opportunities.
            5. Sentiment Analysis (10% weight)

            The decision process should be:
            1. First check risk management constraints
            2. Then evaluate valuation signal
            3. Then evaluate fundamentals signal
            4. Consider BOTH the General Macro Environment AND the Daily Market-Wide News Summary.
            5. Use technical analysis for timing
            6. Consider sentiment for final adjustment

            Provide the following in your output JSON:
            - "action": "buy" | "sell" | "hold",
            - "quantity": <positive integer>
            - "confidence": <float between 0 and 1>
            - "agent_signals": <list of agent signals including agent name, signal (bullish | bearish | neutral), and their confidence>.
              IMPORTANT: Your 'agent_signals' list MUST include entries for:
                - "technical_analysis"
                - "fundamental_analysis"
                - "sentiment_analysis"
                - "valuation_analysis"
                - "risk_management"
                - "general_macro_analysis" (representing the tool-based macro input)
                - "market_wide_news_summary" (representing the daily news summary input - provide a brief signal like bullish/bearish/neutral for the news summary itself, or state if it was primarily factored into overall reasoning with confidence reflecting its impact)
            - "reasoning": <concise explanation of the decision including how you weighted ALL signals, including both macro inputs>

            Trading Rules:
            - Never exceed risk management position limits
            - Only buy if you have available cash
            - Only sell if you have shares to sell
            - Quantity must be ≤ current position for sells
            - Quantity must be ≤ max_position_size from risk management"""
    system_message = {
        "role": "system",
        "content": system_message_content
    }

    user_message_content = f"""Based on the team's analysis below, make your trading decision.

            Technical Analysis Signal: {technical_message.content}
            Fundamental Analysis Signal: {fundamentals_message.content}
            Sentiment Analysis Signal: {sentiment_message.content}
            Valuation Analysis Signal: {valuation_message.content}
            Risk Management Signal: {risk_message.content}
            General Macro Analysis (from Macro Analyst Agent): {tool_based_macro_content}
            Daily Market-Wide News Summary (from Macro News Agent):
            {market_wide_news_summary_content}

            Current Portfolio:
            Cash: {portfolio['cash']:.2f}
            Current Position: {portfolio['stock']} shares

            Output JSON only. Ensure 'agent_signals' includes all required agents as per system prompt."""
    user_message = {
        "role": "user",
        "content": user_message_content
    }

    show_agent_reasoning(
        agent_name, f"Preparing LLM. User msg includes: TA, FA, Sent, Val, Risk, GeneralMacro, MarketNews.")

    llm_interaction_messages = [system_message, user_message]
    llm_response_content = get_chat_completion(llm_interaction_messages)

    current_metadata = state["metadata"]
    current_metadata["current_agent_name"] = agent_name

    def get_llm_result_for_logging_wrapper():
        return llm_response_content
    log_llm_interaction(state)(get_llm_result_for_logging_wrapper)()

    if llm_response_content is None:
        show_agent_reasoning(
            agent_name, "LLM call failed. Using default conservative decision.")
        llm_response_content = json.dumps({
            "action": "hold",
            "quantity": 0,
            "confidence": 0.7,
            "agent_signals": [
                {"agent_name": "technical_analysis",
                    "signal": "neutral", "confidence": 0.0},
                {"agent_name": "fundamental_analysis",
                    "signal": "bullish", "confidence": 1.0},
                {"agent_name": "sentiment_analysis",
                    "signal": "bullish", "confidence": 0.6},
                {"agent_name": "valuation_analysis",
                    "signal": "bearish", "confidence": 0.67},
                {"agent_name": "risk_management",
                    "signal": "hold", "confidence": 1.0},
                {"agent_name": "general_macro_analysis",
                    "signal": "neutral", "confidence": 0.0},
                {"agent_name": "market_wide_news_summary",
                    "signal": "unavailable_or_llm_error", "confidence": 0.0}
            ],
            "reasoning": "LLM API error. Defaulting to conservative hold based on risk management."
        })

    final_decision_message = HumanMessage(
        content=llm_response_content,
        name=agent_name,
    )

    if show_reasoning_flag:
        show_agent_reasoning(
            agent_name, f"Final LLM decision JSON: {llm_response_content}")

    # Prepare the specific decision details for metadata
    agent_decision_details_value = {}
    try:
        decision_json = json.loads(llm_response_content)
        agent_decision_details_value = {
            "action": decision_json.get("action"),
            "quantity": decision_json.get("quantity"),
            "confidence": decision_json.get("confidence"),
            "reasoning_snippet": decision_json.get("reasoning", "")[:150] + "..."
        }
    except json.JSONDecodeError:
        agent_decision_details_value = {
            "error": "Failed to parse LLM decision JSON from portfolio manager",
            "raw_response_snippet": llm_response_content[:200] + "..."
        }

    show_workflow_status(f"{agent_name}: --- Portfolio Manager Completed ---")

    return {
        "messages": state["messages"] + [final_decision_message],
        # Data is passed through as portfolio changes are not made by this agent directly
        "data": state["data"],
        "metadata": {
            # This includes current_agent_name which was set by aliasing and modifying state["metadata"]
            **state["metadata"],
            f"{agent_name}_decision_details": agent_decision_details_value,
            # Storing the full LLM response as reasoning for this agent
            "agent_reasoning": llm_response_content
        }
    }


def format_decision(action: str, quantity: int, confidence: float, agent_signals: list, reasoning: str, market_wide_news_summary: str = "未提供") -> dict:
    """Format the trading decision into a standardized output format.
    Think in English but output analysis in Chinese."""

    fundamental_signal = next(
        (s for s in agent_signals if s["agent_name"] == "fundamental_analysis"), None)
    valuation_signal = next(
        (s for s in agent_signals if s["agent_name"] == "valuation_analysis"), None)
    technical_signal = next(
        (s for s in agent_signals if s["agent_name"] == "technical_analysis"), None)
    sentiment_signal = next(
        (s for s in agent_signals if s["agent_name"] == "sentiment_analysis"), None)
    risk_signal = next(
        (s for s in agent_signals if s["agent_name"] == "risk_management"), None)
    # Existing macro signal from macro_analyst_agent (tool-based)
    general_macro_signal = next(
        (s for s in agent_signals if s["agent_name"] == "general_macro_analysis"), None)
    # New market-wide news summary signal from macro_news_agent
    market_wide_news_signal = next(
        (s for s in agent_signals if s["agent_name"] == "market_wide_news_summary"), None)

    def signal_to_chinese(signal_data):
        if not signal_data:
            return "无数据"
        if signal_data.get("signal") == "bullish":
            return "看多"
        if signal_data.get("signal") == "bearish":
            return "看空"
        return "中性"

    detailed_analysis = f"""
====================================
          投资分析报告
====================================

一、策略分析

1. 基本面分析 (权重30%):
   信号: {signal_to_chinese(fundamental_signal)}
   置信度: {fundamental_signal['confidence']*100:.0f if fundamental_signal else 0}%
   要点:
   - 盈利能力: {fundamental_signal.get('reasoning', {}).get('profitability_signal', {}).get('details', '无数据') if fundamental_signal else '无数据'}
   - 增长情况: {fundamental_signal.get('reasoning', {}).get('growth_signal', {}).get('details', '无数据') if fundamental_signal else '无数据'}
   - 财务健康: {fundamental_signal.get('reasoning', {}).get('financial_health_signal', {}).get('details', '无数据') if fundamental_signal else '无数据'}
   - 估值水平: {fundamental_signal.get('reasoning', {}).get('price_ratios_signal', {}).get('details', '无数据') if fundamental_signal else '无数据'}

2. 估值分析 (权重35%):
   信号: {signal_to_chinese(valuation_signal)}
   置信度: {valuation_signal['confidence']*100:.0f if valuation_signal else 0}%
   要点:
   - DCF估值: {valuation_signal.get('reasoning', {}).get('dcf_analysis', {}).get('details', '无数据') if valuation_signal else '无数据'}
   - 所有者收益法: {valuation_signal.get('reasoning', {}).get('owner_earnings_analysis', {}).get('details', '无数据') if valuation_signal else '无数据'}

3. 技术分析 (权重25%):
   信号: {signal_to_chinese(technical_signal)}
   置信度: {technical_signal['confidence']*100:.0f if technical_signal else 0}%
   要点:
   - 趋势跟踪: ADX={technical_signal.get('strategy_signals', {}).get('trend_following', {}).get('metrics', {}).get('adx', 0.0):.2f if technical_signal else 0.0:.2f}
   - 均值回归: RSI(14)={technical_signal.get('strategy_signals', {}).get('mean_reversion', {}).get('metrics', {}).get('rsi_14', 0.0):.2f if technical_signal else 0.0:.2f}
   - 动量指标:
     * 1月动量={technical_signal.get('strategy_signals', {}).get('momentum', {}).get('metrics', {}).get('momentum_1m', 0.0):.2% if technical_signal else 0.0:.2%}
     * 3月动量={technical_signal.get('strategy_signals', {}).get('momentum', {}).get('metrics', {}).get('momentum_3m', 0.0):.2% if technical_signal else 0.0:.2%}
     * 6月动量={technical_signal.get('strategy_signals', {}).get('momentum', {}).get('metrics', {}).get('momentum_6m', 0.0):.2% if technical_signal else 0.0:.2%}
   - 波动性: {technical_signal.get('strategy_signals', {}).get('volatility', {}).get('metrics', {}).get('historical_volatility', 0.0):.2% if technical_signal else 0.0:.2%}

4. 宏观分析 (综合权重15%):
   a) 常规宏观分析 (来自 Macro Analyst Agent):
      信号: {signal_to_chinese(general_macro_signal)}
      置信度: {general_macro_signal['confidence']*100:.0f if general_macro_signal else 0}%
      宏观环境: {general_macro_signal.get('macro_environment', '无数据') if general_macro_signal else '无数据'}
      对股票影响: {general_macro_signal.get('impact_on_stock', '无数据') if general_macro_signal else '无数据'}
      关键因素: {', '.join(general_macro_signal.get('key_factors', ['无数据']) if general_macro_signal else ['无数据'])}

   b) 大盘宏观新闻分析 (来自 Macro News Agent):
      信号: {signal_to_chinese(market_wide_news_signal)} 
      置信度: {market_wide_news_signal['confidence']*100:.0f if market_wide_news_signal else 0}%
      摘要或结论: {market_wide_news_signal.get('reasoning', market_wide_news_summary) if market_wide_news_signal else market_wide_news_summary}

5. 情绪分析 (权重10%):
   信号: {signal_to_chinese(sentiment_signal)}
   置信度: {sentiment_signal['confidence']*100:.0f if sentiment_signal else 0}%
   分析: {sentiment_signal.get('reasoning', '无详细分析') if sentiment_signal else '无详细分析'}

二、风险评估
风险评分: {risk_signal.get('risk_score', '无数据') if risk_signal else '无数据'}/10
主要指标:
- 波动率: {risk_signal.get('risk_metrics', {}).get('volatility', 0.0)*100:.1f if risk_signal else 0.0}%
- 最大回撤: {risk_signal.get('risk_metrics', {}).get('max_drawdown', 0.0)*100:.1f if risk_signal else 0.0}%
- VaR(95%): {risk_signal.get('risk_metrics', {}).get('value_at_risk_95', 0.0)*100:.1f if risk_signal else 0.0}%
- 市场风险: {risk_signal.get('risk_metrics', {}).get('market_risk_score', '无数据') if risk_signal else '无数据'}/10

三、投资建议
操作建议: {'买入' if action == 'buy' else '卖出' if action == 'sell' else '持有'}
交易数量: {quantity}股
决策置信度: {confidence*100:.0f}%

四、决策依据
{reasoning}

===================================="""

    return {
        "action": action,
        "quantity": quantity,
        "confidence": confidence,
        "agent_signals": agent_signals,
        "分析报告": detailed_analysis
    }
