# 🤖 AI Architecture & Multi-Agent Design

## Overview

CortexInvest 采用 **Multi-Agent Architecture**，以 LLM（大语言模型）为核心驱动力，构建智能投研系统。

```
┌─────────────────────────────────────────────────────────────┐
│                    Multi-Agent 协作层                        │
├─────────────┬─────────────┬──────────────┬──────────────────┤
│  DataAgent  │ AlertAgent  │  TradeAgent  │   SummaryAgent   │
│  (数据获取)  │  (告警分析)  │  (交易决策)   │  (日报生成)      │
├─────────────┼─────────────┼──────────────┼──────────────────┤
│             │             │              │                  │
│  ┌──────────┴─────────────┴──────────────┴──────────────┐  │
│  │              LLM Client Abstraction Layer              │  │
│  │         (Gemini / OpenAI / Kimi / MiMo Ready)         │  │
│  └────────────────────────────────────────────────────────┘  │
│                              │                                │
│              ┌───────────────┼───────────────┐                │
│              ▼               ▼               ▼                │
│         ┌─────────┐    ┌─────────┐    ┌─────────┐           │
│         │ Gemini  │    │ OpenAI  │    │  MiMo   │           │
│         │ 1.5/2.0 │    │ GPT-4   │    │ V2.5    │           │
│         └─────────┘    └─────────┘    └─────────┘           │
└─────────────────────────────────────────────────────────────┘
```

---

## Agent 设计

### 1. DataAgent - 数据获取 Agent

**职责**: 负责行情数据获取、清洗、存储

**LLM 应用**:
- 数据异常检测（AI 识别异常行情）
- 数据补全建议（缺失数据时提供合理推测）

**输入**: 原始行情数据
**输出**: 清洗后的结构化数据

### 2. AlertAgent - 告警分析 Agent

**职责**: 实时监控、分级告警、异常检测

**LLM 应用**:
- 告警语义理解（将技术信号转化为自然语言）
- 告警优先级评估（AI 判断告警重要性）
- 关联分析（识别多股票联动异常）

**输入**: 实时行情流
**输出**: 分级告警事件

### 3. TradeAgent - 交易决策 Agent

**职责**: 策略执行、风险评估、仓位管理

**LLM 应用**:
- 策略信号解读（将技术指标转化为交易建议）
- 风险评估报告（AI 生成风险分析）
- 仓位优化建议（基于市场状态的动态调整）

**输入**: 策略信号、市场状态
**输出**: 交易指令、风险报告

### 4. SummaryAgent - 日报生成 Agent

**职责**: 盘后综合分析、市场摘要、投资报告

**LLM 应用**:
- **Daily Summary**: 盘后综合分析 (~50K tokens)
- **Morning Briefing**: 早间市场简报 (~30K tokens)
- **Stock Analysis**: 个股深度分析 (~20K tokens)

**输入**: 全日行情、告警、交易记录
**输出**: 结构化分析报告

---

## LLM Client Abstraction Layer

### 设计原则

```python
# src/utils/llm_clients.py

class LLMClient(ABC):
    """LLM 客户端抽象基类
    
    支持多种 LLM 后端：
    - Gemini (Google)
    - OpenAI (GPT-4)
    - Kimi (Moonshot)
    - MiMo (Xiaomi) - 预留接口
    """
    
    @abstractmethod
    def get_completion(self, messages, **kwargs):
        """获取模型回答"""
        pass
    
    @abstractmethod
    def get_completion_with_tools(self, messages, tools, tool_handlers, **kwargs):
        """带 function calling 的多轮对话"""
        pass
    
    @abstractmethod
    def get_completion_stream(self, messages, on_chunk=None, **kwargs):
        """流式获取模型回答"""
        pass
```

### 模型路由策略

```python
class LLMClientFactory:
    """LLM 客户端工厂
    
    根据任务类型自动选择最优模型：
    - 中文金融分析 → MiMo (预留)
    - 代码生成 → Gemini / Kimi
    - 通用推理 → OpenAI GPT-4
    """
    
    @staticmethod
    def create(provider: str, **kwargs) -> LLMClient:
        if provider == "gemini":
            return GeminiClient(**kwargs)
        elif provider == "openai":
            return OpenAICompatibleClient(**kwargs)
        elif provider == "kimi":
            return OpenAICompatibleClient(
                base_url="https://api.moonshot.cn/v1",
                **kwargs
            )
        elif provider == "mimo":
            # 预留 MiMo 接口
            return MiMoClient(**kwargs)
        else:
            raise ValueError(f"Unknown provider: {provider}")
```

---

## Token 消耗统计

### 按功能模块统计

| 模块 | 功能 | 单次 Token | 日调用次数 | 日消耗 |
|------|------|-----------|-----------|--------|
| **Daily Summary** | 盘后综合分析 | ~50K | 1 | ~50K |
| **Morning Brief** | 早间简报 | ~30K | 1 | ~30K |
| **Stock Analysis** | 个股分析 | ~20K | 5 | ~100K |
| **Signal Interpret** | 信号解读 | ~10K | 10 | ~100K |
| **Risk Assessment** | 风险评估 | ~15K | 2 | ~30K |

**日均总消耗**: ~310K tokens
**月均总消耗**: ~9.3M tokens

### 按模型统计

| 模型 | 占比 | 用途 |
|------|------|------|
| Gemini 1.5 Flash | 60% | 日报、简报、通用分析 |
| Kimi K2.6 | 30% | 中文文本、长上下文 |
| OpenAI GPT-4 | 10% | 复杂推理、代码生成 |
| **MiMo V2.5** | 0% | 待接入，预期替代 60% |

---

## MiMo 接入方案

### 现状

- ✅ 多模型抽象层已完成
- ✅ OpenAI 兼容 API 格式支持
- 🔄 MiMo V2.5 模型适配（待接入）

### 接入计划

```python
class MiMoClient(LLMClient):
    """Xiaomi MiMo API 客户端
    
    适配 MiMo V2.5 API：
    - Base URL: https://api.mimo.xiaomi.com/v1
    - 模型: mimo-v2.5-chat
    - 支持 function calling
    - 支持 SSE streaming
    """
    
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.getenv("MIMO_API_KEY")
        self.model = model or "mimo-v2.5-chat"
        self.base_url = "https://api.mimo.xiaomi.com/v1"
        
        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key
        )
```

### 预期收益

| 指标 | 当前 | 接入 MiMo 后 | 提升 |
|------|------|-------------|------|
| **月均成本** | ~$150 | ~$60 | -60% |
| **中文准确率** | 85% | 95% | +10% |
| **响应速度** | 2-5s | 1-3s | +40% |
| **上下文长度** | 128K | 256K | +100% |

---

## AI 安全与伦理

### 数据隐私

- 所有个人持仓数据本地存储（SQLite）
- LLM API 调用仅传输脱敏后的市场数据
- 支持本地模型部署（未来规划）

### 风险提示

- AI 分析结果仅供参考，不构成投资建议
- 模拟交易盈亏不代表真实交易结果
- 用户需自行承担投资风险

---

## 技术栈

| 层级 | 技术 | 用途 |
|------|------|------|
| **LLM 层** | Gemini / OpenAI / Kimi / MiMo | 模型推理 |
| **抽象层** | Python ABC + Factory | 多模型切换 |
| **应用层** | Python 3.13 + FastAPI | 业务逻辑 |
| **前端层** | Next.js 15 + TypeScript | 可视化 |
| **数据层** | SQLite + JSONL | 本地存储 |

---

## 开发规范

### Agent 开发原则

1. **单一职责**: 每个 Agent 只负责一个核心功能
2. **LLM 优先**: 优先使用 LLM 解决复杂判断问题
3. **降级策略**: LLM 不可用时，规则引擎兜底
4. **可观测性**: 所有 LLM 调用记录 Token 消耗和响应时间

### 代码规范

```python
# 示例：Agent 实现模板
class BaseAgent(ABC):
    """Agent 基类"""
    
    def __init__(self, llm_client: LLMClient):
        self.llm = llm_client
        self.logger = setup_logger(self.__class__.__name__)
    
    @abstractmethod
    def process(self, input_data: dict) -> dict:
        """处理输入数据，返回结果"""
        pass
    
    def _call_llm(self, prompt: str, **kwargs) -> str:
        """调用 LLM，记录 Token 消耗"""
        start_time = time.time()
        response = self.llm.get_completion(
            messages=[{"role": "user", "content": prompt}],
            **kwargs
        )
        elapsed = time.time() - start_time
        self.logger.info(f"LLM call: {elapsed:.2f}s, tokens: {len(prompt)}")
        return response
```

---

## 未来规划

### 短期（1-3个月）

- [ ] 接入 MiMo V2.5 API
- [ ] 优化中文金融文本 Prompt
- [ ] 增加更多 Agent（NewsAgent、ReportAgent）

### 中期（3-6个月）

- [ ] Agent 间协作机制（Agent 通信协议）
- [ ] 本地模型部署（Llama / Qwen）
- [ ] RAG 知识库（财报、研报检索）

### 长期（6-12个月）

- [ ] 自主决策 Agent（闭环交易）
- [ ] 多模态分析（图表、新闻图片）
- [ ] 联邦学习（隐私保护下的模型优化）
