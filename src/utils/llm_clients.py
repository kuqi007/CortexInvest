import json
import os
import time
import backoff
from abc import ABC, abstractmethod
from dotenv import load_dotenv
from openai import OpenAI
from google import genai
from src.utils.logging_config import setup_logger, SUCCESS_ICON, ERROR_ICON, WAIT_ICON

logger = setup_logger('llm_clients')


class LLMClient(ABC):
    """LLM 客户端抽象基类"""

    @abstractmethod
    def get_completion(self, messages, **kwargs):
        """获取模型回答"""
        pass

    @abstractmethod
    def get_completion_with_tools(
        self, messages, tools, tool_handlers, **kwargs
    ):
        """带 function calling 的多轮对话

        Returns:
            str: 最终文本回答
        """
        pass

    @abstractmethod
    def get_completion_stream(self, messages, on_chunk=None, **kwargs):
        """流式获取模型回答

        Args:
            messages: 消息列表
            on_chunk: callback(token: str) — 每收到一个 token 调用一次
            **kwargs: 透传到 API 的其他参数

        Returns:
            str: 完整回答文本
        """
        pass


class GeminiClient(LLMClient):
    """Google Gemini API 客户端"""

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-1.5-flash")

        if not self.api_key:
            logger.error(f"{ERROR_ICON} 未找到 GEMINI_API_KEY 环境变量")
            raise ValueError(
                "GEMINI_API_KEY not found in environment variables")

        # 初始化 Gemini 客户端
        self.client = genai.Client(api_key=self.api_key)
        logger.info(f"{SUCCESS_ICON} Gemini 客户端初始化成功")

    @backoff.on_exception(
        backoff.expo,
        (Exception),
        max_tries=5,
        max_time=300,
        giveup=lambda e: "AFC is enabled" not in str(e)
    )
    def generate_content_with_retry(self, contents, config=None):
        """带重试机制的内容生成函数"""
        try:
            logger.info(f"{WAIT_ICON} 正在调用 Gemini API...")
            logger.debug(f"请求内容: {contents}")
            logger.debug(f"请求配置: {config}")

            response = self.client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config
            )

            logger.info(f"{SUCCESS_ICON} API 调用成功")
            logger.debug(f"响应内容: {response.text[:500]}...")
            return response
        except Exception as e:
            error_msg = str(e)
            if "location" in error_msg.lower():
                logger.info(
                    f"\033[91m❗ Gemini API 地理位置限制错误: 请使用美国节点VPN后重试\033[0m")
                logger.error(f"详细错误: {error_msg}")
            elif "AFC is enabled" in error_msg:
                logger.warning(
                    f"{ERROR_ICON} 触发 API 限制，等待重试... 错误: {error_msg}")
                time.sleep(5)
            else:
                logger.error(f"{ERROR_ICON} API 调用失败: {error_msg}")
            raise e

    def get_completion(self, messages, max_retries=3, initial_retry_delay=1, **kwargs):
        """获取聊天完成结果，包含重试逻辑和模型回退机制"""
        try:
            # 备选模型列表
            fallback_models = [
                self.model,
                "gemini-2.0-flash",
                "gemini-1.5-flash-latest",
                "gemini-flash-latest",
                "gemini-pro-latest"
            ]
            # 去重并保持顺序
            models_to_try = []
            for m in fallback_models:
                if m and m not in models_to_try:
                    models_to_try.append(m)

            for current_model in models_to_try:
                logger.info(f"{WAIT_ICON} 尝试使用 Gemini 模型: {current_model}")
                
                for attempt in range(max_retries):
                    try:
                        # 转换消息格式
                        prompt = ""
                        system_instruction = None

                        for message in messages:
                            role = message["role"]
                            content = message["content"]
                            if role == "system":
                                system_instruction = content
                            elif role == "user":
                                prompt += f"User: {content}\n"
                            elif role == "assistant":
                                prompt += f"Assistant: {content}\n"

                        # 准备配置
                        config = {}
                        if system_instruction:
                            config['system_instruction'] = system_instruction

                        # 调用 API
                        # 临时更新 self.model 以便 generate_content_with_retry 使用
                        original_model = self.model
                        self.model = current_model
                        try:
                            response = self.generate_content_with_retry(
                                contents=prompt.strip(),
                                config=config
                            )
                        finally:
                            self.model = original_model

                        if response is None:
                            _log_debug(f"Model {current_model} returned None", {"attempt": attempt}, "A")
                            logger.warning(
                                f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries}: API 返回空值")
                            if attempt < max_retries - 1:
                                retry_delay = initial_retry_delay * (2 ** attempt)
                                logger.info(
                                    f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                                time.sleep(retry_delay)
                                continue
                            break # 尝试下一个模型

                        logger.debug(f"API 原始响应: {response.text}")
                        logger.info(f"{SUCCESS_ICON} 成功使用 {current_model} 获取 Gemini 响应")
                        _log_debug(f"Success with model {current_model}", {"model": current_model}, "C")

                        # 直接返回文本内容
                        return response.text

                    except Exception as e:
                        error_str = str(e)
                        _log_debug(f"Error with model {current_model}", {"error": error_str, "attempt": attempt}, "A")
                        # 如果是 404 或 400 (模型不支持)，则尝试下一个模型
                        if "404" in error_str or "not found" in error_str.lower() or "not supported" in error_str.lower():
                            logger.warning(f"{ERROR_ICON} 模型 {current_model} 不可用 (404)，准备尝试下一个备选模型")
                            break # 跳出重试循环，尝试下一个模型
                        
                        logger.error(
                            f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries} 失败: {error_str}")
                        
                        # 如果是 429 (配额耗尽)，在重试几次后也尝试换个模型（虽然可能没用，但万一模型配额不同呢）
                        if "429" in error_str or "quota" in error_str.lower():
                            if attempt < max_retries - 1:
                                retry_delay = initial_retry_delay * (2 ** attempt) + 2 # 额外多等一点
                                logger.info(f"{WAIT_ICON} 配额限制，等待 {retry_delay} 秒后重试...")
                                time.sleep(retry_delay)
                                continue
                            else:
                                logger.warning(f"{ERROR_ICON} 模型 {current_model} 配额耗尽，尝试下一个备选模型")
                                break # 尝试下一个模型

                        if attempt < max_retries - 1:
                            retry_delay = initial_retry_delay * (2 ** attempt)
                            logger.info(f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                            time.sleep(retry_delay)
                        else:
                            logger.error(f"{ERROR_ICON} 模型 {current_model} 的重试次数已达上限")
                            break # 尝试下一个模型

            logger.error(f"{ERROR_ICON} 所有备选模型均调用失败")
            return None

        except Exception as e:
            logger.error(f"{ERROR_ICON} get_completion 发生总错误: {str(e)}")
            return None

    def get_completion_with_tools(
        self, messages, tools, tool_handlers, **kwargs
    ):
        """Gemini 不支持原生 function calling loop，降级为普通调用"""
        logger.warning(
            f"{WAIT_ICON} GeminiClient 不支持 function calling，降级为普通调用"
        )
        return self.get_completion(messages, **kwargs)

    def get_completion_stream(self, messages, on_chunk=None, **kwargs):
        """Gemini 不支持 SSE streaming，降级为普通调用"""
        logger.warning(
            f"{WAIT_ICON} GeminiClient 不支持 streaming，降级为普通调用"
        )
        result = self.get_completion(messages, **kwargs)
        if result and on_chunk:
            on_chunk(result)
        return result


class OpenAICompatibleClient(LLMClient):
    """OpenAI 兼容 API 客户端（含 Kimi K2.6 高级能力）。

    支持:
    - json_schema 结构化输出 (response_format)
    - function calling (tools + tool_choice)
    - thinking 推理链 (extra_body.thinking)
    - prompt 缓存 (extra_body.prompt_cache_key)
    - SSE streaming (stream=True)
    """

    def __init__(self, api_key=None, base_url=None, model=None):
        self.api_key = api_key or os.getenv("OPENAI_COMPATIBLE_API_KEY")
        self.base_url = base_url or os.getenv("OPENAI_COMPATIBLE_BASE_URL")
        self.model = model or os.getenv("OPENAI_COMPATIBLE_MODEL")

        if not self.api_key:
            logger.error(f"{ERROR_ICON} 未找到 OPENAI_COMPATIBLE_API_KEY 环境变量")
            raise ValueError(
                "OPENAI_COMPATIBLE_API_KEY not found in environment variables")

        if not self.base_url:
            logger.error(f"{ERROR_ICON} 未找到 OPENAI_COMPATIBLE_BASE_URL 环境变量")
            raise ValueError(
                "OPENAI_COMPATIBLE_BASE_URL not found in environment variables")

        if not self.model:
            logger.error(f"{ERROR_ICON} 未找到 OPENAI_COMPATIBLE_MODEL 环境变量")
            raise ValueError(
                "OPENAI_COMPATIBLE_MODEL not found in environment variables")

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key
        )
        logger.info(f"{SUCCESS_ICON} OpenAI Compatible 客户端初始化成功 "
                    f"(model={self.model}, base_url={self.base_url})")

    @backoff.on_exception(
        backoff.expo,
        (Exception),
        max_tries=5,
        max_time=300,
        base=4,  # 起始4秒（Kimi 429 要求等1s）
        jitter=None,  # 避免惊群
    )
    def call_api_with_retry(
        self,
        messages,
        stream=False,
        response_format=None,
        tools=None,
        tool_choice=None,
        extra_body=None,
    ):
        """带重试机制的 API 调用函数，透传所有高级参数"""
        try:
            logger.info(f"{WAIT_ICON} 正在调用 OpenAI Compatible API...")
            logger.debug(f"模型: {self.model}, 流式: {stream}")

            kwargs = dict(
                model=self.model,
                messages=messages,
                stream=stream,
            )
            if response_format is not None:
                kwargs["response_format"] = response_format
            if tools is not None:
                kwargs["tools"] = tools
            if tool_choice is not None:
                kwargs["tool_choice"] = tool_choice
            if extra_body is not None:
                kwargs["extra_body"] = extra_body

            response = self.client.chat.completions.create(**kwargs)

            logger.info(f"{SUCCESS_ICON} API 调用成功")
            return response
        except Exception as e:
            error_msg = str(e)
            logger.error(f"{ERROR_ICON} API 调用失败: {error_msg}")
            raise e

    def get_completion(self, messages, max_retries=3, initial_retry_delay=1,
                         return_extra=False, **kwargs):
        """获取聊天完成结果，包含重试逻辑。

        Args:
            messages: OpenAI 格式消息列表
            max_retries: 最大重试次数
            initial_retry_delay: 初始重试延迟（秒）
            return_extra: 如果 True，返回 {"content": str, "reasoning": str|None}
            **kwargs: 透传参数 — response_format, tools, tool_choice, extra_body

        Returns:
            str | dict | None: 模型回答内容
        """
        try:
            logger.info(f"{WAIT_ICON} 使用 OpenAI Compatible 模型: {self.model}")

            for attempt in range(max_retries):
                try:
                    response = self.call_api_with_retry(messages, **kwargs)

                    if response is None:
                        logger.warning(
                            f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries}: API 返回空值")
                        if attempt < max_retries - 1:
                            retry_delay = initial_retry_delay * (2 ** attempt)
                            logger.info(f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                            time.sleep(retry_delay)
                            continue
                        return None

                    choice = response.choices[0]
                    msg = choice.message
                    content = msg.content
                    reasoning = getattr(msg, "reasoning_content", None)
                    logger.debug(f"API 原始响应: {content[:500] if content else '(no content)'}...")
                    if reasoning:
                        logger.info(
                            f"{SUCCESS_ICON} 获取到思考链 ({len(reasoning)} 字符)"
                        )
                    # 如果有 tool_calls，记录但不返回（get_completion 不处理 tool_calls）
                    if msg.tool_calls:
                        logger.info(
                            f"{WAIT_ICON} 模型请求 tool_calls: "
                            f"{[tc.function.name for tc in msg.tool_calls]} "
                            f"(使用 get_completion_with_tools 处理)"
                        )
                    logger.info(f"{SUCCESS_ICON} 成功获取 OpenAI Compatible 响应")

                    if return_extra:
                        # content=None 时返回 None 而非 {"content": None}
                        if content is None:
                            return None
                        return {
                            "content": content,
                            "reasoning": reasoning,
                        }
                    return content

                except Exception as e:
                    logger.error(
                        f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries} 失败: {str(e)}")
                    if attempt < max_retries - 1:
                        retry_delay = initial_retry_delay * (2 ** attempt)
                        logger.info(f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                    else:
                        logger.error(f"{ERROR_ICON} 最终错误: {str(e)}")
                        return None

        except Exception as e:
            logger.error(f"{ERROR_ICON} get_completion 发生错误: {str(e)}")
            return None

    def get_completion_with_tools(
        self,
        messages,
        tools,
        tool_handlers,
        max_tool_rounds=5,
        max_retries=3,
        initial_retry_delay=1,
        **kwargs,
    ):
        """带 function calling 的多轮对话循环。

        Args:
            messages: 消息列表（会被原地修改，追加 assistant/tool 消息）
            tools: ToolDefinition 列表
            tool_handlers: {tool_name: callable(arguments) -> str} 映射
            max_tool_rounds: 最大工具调用轮数，防止死循环
            max_retries: 最大重试次数
            initial_retry_delay: 初始重试延迟
            **kwargs: 透传到 API 的额外参数

        Returns:
            str | None: 最终文本回答
        """
        try:
            logger.info(
                f"{WAIT_ICON} 开始 function calling 对话 "
                f"(tools={list(tool_handlers.keys())}, max_rounds={max_tool_rounds})"
            )

            for round_idx in range(max_tool_rounds):
                response = None
                for attempt in range(max_retries):
                    try:
                        response = self.call_api_with_retry(
                            messages,
                            tools=tools,
                            tool_choice="auto",
                            **kwargs,
                        )
                        break
                    except Exception as e:
                        logger.error(
                            f"{ERROR_ICON} Round {round_idx + 1}, "
                            f"attempt {attempt + 1} 失败: {str(e)}"
                        )
                        if attempt < max_retries - 1:
                            time.sleep(initial_retry_delay * (2 ** attempt))
                        else:
                            return None

                if response is None:
                    return None

                choice = response.choices[0]
                msg = choice.message

                # 如果没有 tool_calls，说明模型已给出最终回答
                if not msg.tool_calls:
                    logger.info(f"{SUCCESS_ICON} function calling 完成，返回文本回答")
                    return msg.content

                # 处理 tool_calls
                logger.info(
                    f"{WAIT_ICON} Round {round_idx + 1}: "
                    f"模型请求 {len(msg.tool_calls)} 个工具调用"
                )

                # 追加 assistant 消息（含 tool_calls）
                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                # 执行每个 tool call 并追加结果
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    handler = tool_handlers.get(tool_name)
                    if handler is None:
                        tool_result = f"Error: unknown tool '{tool_name}'"
                        logger.warning(f"{ERROR_ICON} 未知工具: {tool_name}")
                    else:
                        try:
                            import json as _json

                            args = _json.loads(tc.function.arguments)
                            logger.info(
                                f"  → 调用 {tool_name}({_json.dumps(args, ensure_ascii=False)[:200]})"
                            )
                            tool_result = handler(args)
                            logger.info(
                                f"  ← {tool_name} 返回 {len(tool_result)} 字符"
                            )
                        except Exception as e:
                            tool_result = f"Error: {str(e)}"
                            logger.error(f"{ERROR_ICON} 工具执行失败: {e}")

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })

            logger.warning(
                f"{ERROR_ICON} 达到最大工具调用轮数 {max_tool_rounds}，强制返回"
            )
            return None

        except Exception as e:
            logger.error(f"{ERROR_ICON} get_completion_with_tools 发生错误: {str(e)}")
            return None

    def get_completion_stream(self, messages, on_chunk=None, max_retries=3,
                               initial_retry_delay=1, **kwargs):
        """流式获取模型回答。

        Args:
            messages: 消息列表
            on_chunk: callback(token_text: str) — 每收到一个 content token 调用
            max_retries: 最大重试次数
            initial_retry_delay: 初始重试延迟
            **kwargs: 透传到 API 的额外参数

        Returns:
            str | None: 完整回答文本
        """
        try:
            logger.info(f"{WAIT_ICON} 开始流式调用 {self.model}")

            for attempt in range(max_retries):
                try:
                    stream = self.call_api_with_retry(
                        messages, stream=True, **kwargs
                    )
                    if stream is None:
                        if attempt < max_retries - 1:
                            time.sleep(initial_retry_delay * (2 ** attempt))
                            continue
                        return None

                    full_text = ""
                    for chunk in stream:
                        if chunk.choices and chunk.choices[0].delta:
                            delta = chunk.choices[0].delta
                            token = delta.content
                            if token:
                                full_text += token
                                if on_chunk:
                                    on_chunk(token)

                    logger.info(
                        f"{SUCCESS_ICON} 流式调用完成，共 {len(full_text)} 字符"
                    )
                    return full_text

                except Exception as e:
                    logger.error(
                        f"{ERROR_ICON} 流式调用 attempt {attempt + 1} 失败: {str(e)}"
                    )
                    if attempt < max_retries - 1:
                        time.sleep(initial_retry_delay * (2 ** attempt))
                    else:
                        return None

        except Exception as e:
            logger.error(f"{ERROR_ICON} get_completion_stream 发生错误: {str(e)}")
            return None


class MiMoClient(LLMClient):
    """Xiaomi MiMo API 客户端 (OpenAI Compatible)

    支持 MiMo V2.5 系列模型：
    - mimo-v2.5-chat: 通用对话模型
    - mimo-v2.5-reasoning: 推理模型
    - mimo-v2.5-vision: 多模态模型

    特性:
    - OpenAI 兼容 API 格式
    - 中文金融文本优化
    - 支持 function calling
    - 支持 SSE streaming
    - 256K 上下文窗口

    使用示例:
        client = MiMoClient(api_key="your_key", model="mimo-v2.5-chat")
        response = client.get_completion([{"role": "user", "content": "分析茅台股票"}])
    """

    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.getenv("MIMO_API_KEY")
        self.model = model or os.getenv("MIMO_MODEL", "mimo-v2.5-chat")
        self.base_url = os.getenv("MIMO_BASE_URL", "https://api.mimo.xiaomi.com/v1")

        if not self.api_key:
            logger.error(f"{ERROR_ICON} 未找到 MIMO_API_KEY 环境变量")
            raise ValueError(
                "MIMO_API_KEY not found in environment variables. "
                "Get your key from https://platform.xiaomimimo.com"
            )

        self.client = OpenAI(
            base_url=self.base_url,
            api_key=self.api_key
        )
        logger.info(f"{SUCCESS_ICON} MiMo 客户端初始化成功 "
                    f"(model={self.model}, base_url={self.base_url})")

    def get_completion(self, messages, max_retries=3, initial_retry_delay=1, **kwargs):
        """获取 MiMo 模型回答"""
        try:
            for attempt in range(max_retries):
                try:
                    logger.info(f"{WAIT_ICON} 正在调用 MiMo API (model={self.model})...")

                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        **kwargs
                    )

                    result = response.choices[0].message.content
                    logger.info(f"{SUCCESS_ICON} MiMo API 调用成功")
                    return result

                except Exception as e:
                    error_str = str(e)
                    logger.error(
                        f"{ERROR_ICON} 尝试 {attempt + 1}/{max_retries} 失败: {error_str}"
                    )
                    if attempt < max_retries - 1:
                        retry_delay = initial_retry_delay * (2 ** attempt)
                        logger.info(f"{WAIT_ICON} 等待 {retry_delay} 秒后重试...")
                        time.sleep(retry_delay)
                    else:
                        return None

        except Exception as e:
            logger.error(f"{ERROR_ICON} MiMo get_completion 发生错误: {str(e)}")
            return None

    def get_completion_with_tools(self, messages, tools, tool_handlers, **kwargs):
        """MiMo 支持 function calling"""
        try:
            logger.info(f"{WAIT_ICON} 调用 MiMo function calling...")

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=tools,
                tool_choice="auto",
                **kwargs
            )

            message = response.choices[0].message

            # 处理 tool calls
            if message.tool_calls:
                for tool_call in message.tool_calls:
                    tool_name = tool_call.function.name
                    tool_args = json.loads(tool_call.function.arguments)

                    if tool_name in tool_handlers:
                        handler = tool_handlers[tool_name]
                        result = handler(**tool_args)

                        # 添加 tool response 到 messages
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "content": str(result)
                        })

                # 获取最终回答
                final_response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages
                )
                return final_response.choices[0].message.content

            return message.content

        except Exception as e:
            logger.error(f"{ERROR_ICON} MiMo function calling 失败: {str(e)}")
            return None

    def get_completion_stream(self, messages, on_chunk=None, **kwargs):
        """MiMo 支持 SSE streaming"""
        try:
            logger.info(f"{WAIT_ICON} 调用 MiMo streaming...")

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                stream=True,
                **kwargs
            )

            full_text = ""
            for chunk in response:
                if chunk.choices and chunk.choices[0].delta:
                    delta = chunk.choices[0].delta
                    token = delta.content
                    if token:
                        full_text += token
                        if on_chunk:
                            on_chunk(token)

            logger.info(f"{SUCCESS_ICON} MiMo streaming 完成，共 {len(full_text)} 字符")
            return full_text

        except Exception as e:
            logger.error(f"{ERROR_ICON} MiMo streaming 失败: {str(e)}")
            return None


class LLMClientFactory:
    """LLM 客户端工厂类

    支持多种 LLM 后端：
    - Gemini (Google)
    - OpenAI Compatible (OpenAI / Kimi / 其他)
    - MiMo (Xiaomi) - 中文金融优化

    使用示例:
        # 自动选择
        client = LLMClientFactory.create_client()

        # 指定模型
        client = LLMClientFactory.create_client("mimo")
        client = LLMClientFactory.create_client("gemini")
        client = LLMClientFactory.create_client("openai_compatible")
    """

    @staticmethod
    def create_client(client_type="auto", **kwargs):
        """
        创建 LLM 客户端

        Args:
            client_type: 客户端类型 ("auto", "gemini", "openai_compatible", "mimo")
            **kwargs: 特定客户端的配置参数

        Returns:
            LLMClient: 实例化的 LLM 客户端
        """
        # 如果设置为 auto，自动检测可用的客户端
        if client_type == "auto":
            # 优先级：MiMo > Kimi > OpenAI > Gemini
            mimo_key = os.getenv("MIMO_API_KEY", "")
            kimi_key = os.getenv("KIMI_API_KEY", "")
            openai_key = os.getenv("OPENAI_COMPATIBLE_API_KEY", "")
            gemini_key = os.getenv("GEMINI_API_KEY", "")

            is_placeholder = lambda s: s.startswith("your_") or s.startswith("sk-your")

            if mimo_key and not is_placeholder(mimo_key):
                client_type = "mimo"
                kwargs.setdefault("api_key", mimo_key)
                kwargs.setdefault("model", os.getenv("MIMO_MODEL", "mimo-v2.5-chat"))
                logger.info(f"{SUCCESS_ICON} 自动选择 MiMo API (Xiaomi)")
            elif kimi_key and not is_placeholder(kimi_key):
                client_type = "openai_compatible"
                kwargs.setdefault("api_key", kimi_key)
                kwargs.setdefault("base_url", os.getenv("KIMI_BASE_URL"))
                kwargs.setdefault("model", os.getenv("KIMI_MODEL"))
                logger.info(f"{WAIT_ICON} 自动选择 Kimi API (OpenAI Compatible)")
            elif openai_key and not is_placeholder(openai_key):
                client_type = "openai_compatible"
                logger.info(f"{WAIT_ICON} 自动选择 OpenAI Compatible API")
            elif gemini_key and not is_placeholder(gemini_key):
                client_type = "gemini"
                logger.info(f"{WAIT_ICON} 自动选择 Gemini API")
            else:
                # 没有任何有效 key，报错而非静默降级
                raise ValueError(
                    "No valid LLM API key found. Set one of: "
                    "MIMO_API_KEY, KIMI_API_KEY, OPENAI_COMPATIBLE_API_KEY, GEMINI_API_KEY"
                )

        if client_type == "gemini":
            return GeminiClient(
                api_key=kwargs.get("api_key"),
                model=kwargs.get("model")
            )
        elif client_type == "openai_compatible":
            return OpenAICompatibleClient(
                api_key=kwargs.get("api_key"),
                base_url=kwargs.get("base_url"),
                model=kwargs.get("model")
            )
        elif client_type == "mimo":
            return MiMoClient(
                api_key=kwargs.get("api_key"),
                model=kwargs.get("model")
            )
        else:
            raise ValueError(f"不支持的客户端类型: {client_type}")
