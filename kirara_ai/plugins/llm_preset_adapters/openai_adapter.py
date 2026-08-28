import asyncio
import json
from typing import Any, Dict, Iterable, Optional, cast, Literal, TypedDict

import aiohttp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pydantic import BaseModel, ConfigDict

import kirara_ai.llm.format.tool as tools
from kirara_ai.config.global_config import ModelConfig
from kirara_ai.llm.adapter import (AutoDetectModelsProtocol, LLMBackendAdapter, LLMChatProtocol,
                                   LLMChatStreamProtocol, LLMEmbeddingProtocol)
from kirara_ai.llm.format.message import (LLMChatContentPartType, LLMChatImageContent, LLMChatMessage,
                                          LLMChatTextContent, LLMToolCallContent, LLMToolResultContent)
from kirara_ai.llm.format.request import LLMChatRequest, Tool
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage
from kirara_ai.llm.format.tool import Function, ToolCall
from kirara_ai.llm.format.embedding import LLMEmbeddingRequest, LLMEmbeddingResponse
from kirara_ai.logger import get_logger
from kirara_ai.media import MediaManager
from kirara_ai.tracing import trace_llm_chat

from .utils import guess_openai_model, pick_tool_calls

logger = get_logger("OpenAIAdapter")
def _raise_for_business_error(response_data: dict, endpoint: str) -> None:
    """检查 OpenAI 兼容接口在 HTTP 200 响应中返回的业务错误"""
    if "error" not in response_data or response_data["error"] is None:
        return

    error = response_data["error"]

    if isinstance(error, dict):
        error_message = error.get("message") or json.dumps(
            error, ensure_ascii=False
        )
    else:
        error_message = str(error)

    rid = response_data.get("rid")
    rid_message = f", rid={rid}" if rid else ""

    raise RuntimeError(
        f"{endpoint} API returned a business error: "
        f"{error_message}{rid_message}"
    )
async def convert_parts_factory(messages: LLMChatMessage, media_manager: MediaManager) -> list[dict]:
    if messages.role == "tool":
        # typing.cast 指定类型，避免mypy报错
        results = cast(list[LLMToolResultContent], messages.content)
        outputs = []
        for element in results:
            # 保证 content 为一个字符串
            output = ""
            for content in element.content:
                if isinstance(content, tools.TextContent):
                    output += content.text
                elif isinstance(content, tools.MediaContent):
                    media = media_manager.get_media(content.media_id)
                    if media is None:
                        raise ValueError(f"Media {content.media_id} not found")
                    output += f"<media id={content.media_id} mime_type={content.mime_type} />"
                else:
                    raise ValueError(f"Unsupported content type: {type(content)}")
            if element.isError:
                output = f"Error: {element.name}\n{output}"
            outputs.append({
                "role": "tool",
                "tool_call_id": element.id,
                "content": output,
            })
        return outputs
    else:
        parts: list[dict[str, Any]] = []
        elements = cast(list[LLMChatContentPartType], messages.content)
        tool_calls: list[dict[str, Any]] = []
        for element in elements:
            if isinstance(element, LLMChatTextContent):
                parts.append(element.model_dump(mode="json"))
            elif isinstance(element, LLMChatImageContent):
                media = media_manager.get_media(element.media_id)
                if media is None:
                    raise ValueError(f"Media {element.media_id} not found")
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": await media.get_base64_url()
                    }
                })
            elif isinstance(element, LLMToolCallContent):
                # 回传模型此前发起的工具调用，保证多轮工具调用上下文完整
                tool_calls.append({
                    "type": "function",
                    "id": element.id,
                    "function": {
                        "name": element.name,
                        "arguments": json.dumps(element.parameters or {}, ensure_ascii=False),
                    }
                })
        response: Dict[str, Any] = {"role": messages.role}
        if parts:
            response["content"] = parts
        if tool_calls:
            response["tool_calls"] = tool_calls
        return [response]

def convert_llm_chat_message_to_openai_message(messages: list[LLMChatMessage], media_manager: MediaManager, loop: asyncio.AbstractEventLoop) -> list[dict]:
    results = loop.run_until_complete(
        asyncio.gather(*[convert_parts_factory(msg, media_manager) for msg in messages])
    )
    # 扁平化结果, 展开所有列表
    return [item for sublist in results for item in sublist]

def convert_tools_to_openai_format(tools: list[Tool]) -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.parameters if isinstance(tool.parameters, dict) else tool.parameters.model_dump(),
            "strict": tool.strict,
        }
    } for tool in tools]

def resolve_tool_calls_from_response(tool_calls: Optional[list[dict]]):
    if tool_calls is None:
        return None
    else:
        return [ToolCall(
            id=call["id"],
            type=call.get("type"),
            function=Function(
                name=call["function"]["name"],
                # openai api 的 arguments 值是一个长得像dict的字符串, 交给 pydantic 验证器转换
                arguments=call["function"].get("arguments", None)
            )
        ) for call in tool_calls]

class OpenAIConfig(BaseModel):
    api_key: str
    api_base: str = "https://api.openai.com/v1"
    model_config = ConfigDict(frozen=True)


class OpenAIAdapterChatBase(
    LLMBackendAdapter,
    LLMChatProtocol,
    LLMChatStreamProtocol,
    AutoDetectModelsProtocol,
):
    media_manager: MediaManager

    def __init__(self, config: OpenAIConfig):
        self.config = config
        # 创建带重试机制的 session
        self._session = self._create_session_with_retries()

    def _create_session_with_retries(self) -> requests.Session:
        """
        创建带有重试机制的 requests Session

        :return: 配置了重试策略的 Session 对象
        """
        session = requests.Session()
        # 配置重试策略：对 524 等服务器错误进行重试
        retry_strategy = Retry(
            total=3,  # 最多重试3次
            status_forcelist=[429, 500, 502, 503, 504, 524],  # 这些状态码会触发重试
            backoff_factor=1,  # 重试间隔：1秒、2秒、4秒
            raise_on_status=False  # 不在重试后抛出异常，由 raise_for_status 处理
        )
        adapter = HTTPAdapter(max_retries=retry_strategy)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        return session

    @trace_llm_chat
    def chat(self, req: LLMChatRequest) -> LLMChatResponse:
        api_url = f"{self.config.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        data = {
            "messages": convert_llm_chat_message_to_openai_message(req.messages, self.media_manager, loop),
            "model": req.model,
            "frequency_penalty": req.frequency_penalty,
            "max_tokens": req.max_tokens,
            "presence_penalty": req.presence_penalty,
            "response_format": req.response_format,
            "stop": req.stop,
            "stream": req.stream,
            "stream_options": req.stream_options,
            "temperature": req.temperature,
            "top_p": req.top_p,
            # tool pydantic 模型按照 openai api 格式进行的建立。所以这里直接dump
            "tools": convert_tools_to_openai_format(req.tools) if req.tools else None,
            # 若上层显式指定 tool_choice（例如最后一轮迭代禁止调用工具），则以上层为准
            "tool_choice": req.tool_choice or ("auto" if req.tools else None),
            "logprobs": req.logprobs,
            "top_logprobs": req.top_logprobs,
        }

        # Remove None fields
        data = {k: v for k, v in data.items() if v is not None}

        logger.debug(f"Request: {data}")

        # 使用带重试机制的 session，并设置超时时间
        # timeout 参数: (连接超时, 读取超时) 单位：秒
        response = self._session.post(api_url, json=data, headers=headers, timeout=(10, 120))
        try:
            response.raise_for_status()
            response_data: dict = response.json()
        except Exception as e:
            logger.error(f"Response: {response.text}")
            raise e
        logger.debug(f"Response: {response_data}")

        # OpenAI 兼容接口可能使用 HTTP 200 返回业务错误
        _raise_for_business_error(response_data, "chat/completions")

        choices = response_data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise RuntimeError(
                "chat/completions API returned no choices"
            )

        first_choice = choices[0]
        if not isinstance(first_choice, dict):
            raise RuntimeError(
                "chat/completions API returned an invalid first choice"
            )

        message: dict = first_choice.get("message", {})
        if not message:
            raise RuntimeError(
                "chat/completions API returned no assistant message"
            )

        # 检测tool_calls字段是否存在和是否不为None. tool_call时content字段无有效信息，暂不记录
        content: list[LLMChatContentPartType] = []
        if tool_calls := message.get("tool_calls", None):
            content = [LLMToolCallContent(
                id=call["id"],
                name=call["function"]["name"],
                # OpenAI API 标准字段名为 arguments，值为 JSON 字符串
                parameters=json.loads(call["function"].get("arguments", "{}"))
            ) for call in tool_calls]
        else:
            text_content = message.get("content", "")

            # 某些兼容接口可能返回内容分片列表
            if isinstance(text_content, list):
                text_content = "".join(
                    part.get("text", "")
                    for part in text_content
                    if isinstance(part, dict)
                    and isinstance(part.get("text", ""), str)
                )
            elif text_content is None:
                text_content = message.get("refusal", "") or ""
            elif not isinstance(text_content, str):
                text_content = str(text_content)

            if not text_content.strip():
                raise RuntimeError(
                    "chat/completions API returned an empty assistant message"
                )

            content = [LLMChatTextContent(text=text_content)]

        usage_data = response_data.get("usage", {})
        if not isinstance(usage_data, dict):
            usage_data = {}

        return LLMChatResponse(
            model=req.model,
            usage=Usage(
                prompt_tokens=usage_data.get("prompt_tokens", 0),
                completion_tokens=usage_data.get("completion_tokens", 0),
                total_tokens=usage_data.get("total_tokens", 0),
            ),
            message=Message(
                content=content,
                role=message.get("role", "assistant"),
                # tool_calls 应从 message 中获取，而非 response_data 顶层
                tool_calls = resolve_tool_calls_from_response(message.get("tool_calls", None)),
                finish_reason=first_choice.get("finish_reason", ""),
            ),
        )

    def stream_chat(self, req: LLMChatRequest) -> Iterable[LLMChatResponse]:
        """Stream an OpenAI-compatible completion as incremental responses.

        ``LLMChatStreamProtocol`` 此前没有任何适配器实现，`execute_stream` 因此
        只被测试调用过——「流式 / 非流式回复模式」在产品上不可选。这里补上
        OpenAI 兼容接口的 SSE 解析。

        产出的每个元素都是一个只含增量文本的 ``LLMChatResponse``，
        与非流式的整体响应形状一致，调用方无需分支处理。
        用量只在上游最后一帧给出时附带（``stream_options.include_usage``），
        没给就保持 ``None``，由上层估算器标记为估算——绝不在这里编造数字。
        """
        api_url = f"{self.config.api_base}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            messages = convert_llm_chat_message_to_openai_message(
                req.messages, self.media_manager, loop
            )
        finally:
            loop.close()

        data: dict[str, Any] = {
            "messages": messages,
            "model": req.model,
            "frequency_penalty": req.frequency_penalty,
            "max_tokens": req.max_tokens,
            "presence_penalty": req.presence_penalty,
            "stop": req.stop,
            "temperature": req.temperature,
            "top_p": req.top_p,
            "tools": convert_tools_to_openai_format(req.tools) if req.tools else None,
            "tool_choice": req.tool_choice or ("auto" if req.tools else None),
            # 流式是这条路径的前提，不接受调用方把它关掉。
            "stream": True,
            # 请求上游在最后一帧带上真实用量；不支持的实现会忽略该字段。
            "stream_options": req.stream_options or {"include_usage": True},
        }
        data = {k: v for k, v in data.items() if v is not None}

        with self._session.post(
            api_url,
            json=data,
            headers=headers,
            timeout=(10, 300),
            stream=True,
        ) as response:
            try:
                response.raise_for_status()
            except Exception as error:
                logger.error(f"Stream response: {response.text[:512]}")
                raise error

            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload or payload == "[DONE]":
                    if payload == "[DONE]":
                        break
                    continue
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    # 单个坏帧不应终止整条流；跳过并继续读下一帧。
                    logger.warning("Skipped an unparsable stream chunk")
                    continue
                if not isinstance(chunk, dict):
                    continue
                _raise_for_business_error(chunk, "chat/completions")

                usage = None
                usage_data = chunk.get("usage")
                if isinstance(usage_data, dict) and usage_data:
                    usage = Usage(
                        prompt_tokens=usage_data.get("prompt_tokens"),
                        completion_tokens=usage_data.get("completion_tokens"),
                        total_tokens=usage_data.get("total_tokens"),
                    )

                choices = chunk.get("choices")
                delta_text = ""
                finish_reason = None
                if isinstance(choices, list) and choices:
                    first = choices[0]
                    if isinstance(first, dict):
                        finish_reason = first.get("finish_reason")
                        delta = first.get("delta")
                        if isinstance(delta, dict):
                            raw = delta.get("content")
                            if isinstance(raw, str):
                                delta_text = raw
                            elif isinstance(raw, list):
                                delta_text = "".join(
                                    part.get("text", "")
                                    for part in raw
                                    if isinstance(part, dict)
                                    and isinstance(part.get("text"), str)
                                )

                if not delta_text and usage is None and finish_reason is None:
                    # 心跳帧或纯角色声明帧，没有可交付内容。
                    continue

                yield LLMChatResponse(
                    model=req.model,
                    usage=usage,
                    message=Message(
                        content=[LLMChatTextContent(text=delta_text)] if delta_text else [],
                        role="assistant",
                        finish_reason=finish_reason or "",
                    ),
                )

    async def auto_detect_models(self) -> list[ModelConfig]:
        models = await self.get_models()
        all_models: list[ModelConfig] = []
        for model in models:
            guess_result = guess_openai_model(model)
            if guess_result is None:
                continue
            all_models.append(ModelConfig(id=model, type=guess_result[0].value, ability=guess_result[1]))
        return all_models

    async def get_models(self) -> list[str]:
        api_url = f"{self.config.api_base}/models"
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                api_url, headers={"Authorization": f"Bearer {self.config.api_key}"}
            ) as response:
                response.raise_for_status()
                response_data = await response.json()
                _raise_for_business_error(response_data, "models")

                models = response_data.get("data")
                if not isinstance(models, list):
                    raise RuntimeError(
                        "models API returned an invalid data field"
                    )

                return [
                    model["id"]
                    for model in models
                    if isinstance(model, dict)
                    and isinstance(model.get("id"), str)
                ]

class EmbeddingData(TypedDict):
    object: Literal["embedding"]
    embedding: list[float]
    index: int

class EmbeddingResponse(TypedDict):
    # 用于描述类型定义
    object: Literal["list"]
    data: list[EmbeddingData]
    model: str
    usage: dict[Literal["prompt_tokens", "total_tokens"], int]

class OpenAIAdapter(OpenAIAdapterChatBase, LLMEmbeddingProtocol):
    def embed(self, req: LLMEmbeddingRequest) -> LLMEmbeddingResponse:
        """
        此为openai api嵌入式模型接口

        Tips: openai仅在 text-embedding-3 及以后模型中支持设定输出向量维度
        """

        api_url = f"{self.config.api_base}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        if len(req.inputs) > 2048:
            # text数组不能超过2048个元素，openai api限制
            raise ValueError("Text list has too many dimensions, max dimension is 2048")
        if any(isinstance(input, LLMChatImageContent) for input in req.inputs):
            # 未在api中发现多模态嵌入api, 等待后续更新
            raise ValueError("openai does not support multi-modal embedding")
        # mypy 类型检查修复，如果添加多模态请去除这个标注
        inputs = cast(list[LLMChatTextContent], req.inputs)
        data = {
            "input": [input.text for input in inputs],
            "model": req.model,
            "dimensions": req.dimension,
            "encoding_format": req.encoding_format
        }
        # 删除 None 字段
        data = {k: v for k, v in data.items() if v is not None}
        logger.debug(f"Request: {data}")
        response = requests.post(api_url, headers=headers, json=data)
        try:
            response.raise_for_status()
            response_data: EmbeddingResponse = response.json()
        except Exception as e:
            logger.error(f"Response: {response.text}")
            raise e
        logger.debug(f"Response: {response_data}")
        return LLMEmbeddingResponse(
            vectors=[data["embedding"] for data in response_data["data"]],
            usage=Usage(
                prompt_tokens=response_data["usage"].get("prompt_tokens", 0),
                total_tokens=response_data["usage"].get("total_tokens", 0)
            )
        )