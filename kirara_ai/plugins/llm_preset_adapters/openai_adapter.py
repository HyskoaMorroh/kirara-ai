import asyncio
import json
from typing import Optional, cast

import aiohttp
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from pydantic import BaseModel, ConfigDict

from kirara_ai.llm.adapter import AutoDetectModelsProtocol, LLMBackendAdapter
from kirara_ai.llm.format.message import (LLMChatContentPartType, LLMChatImageContent, LLMChatMessage,
                                          LLMChatTextContent, LLMToolCallContent, LLMToolResultContent)
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import Function, LLMChatResponse, Message, ToolCall, Usage
from kirara_ai.logger import get_logger
from kirara_ai.media import MediaManager
from kirara_ai.tracing import trace_llm_chat

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
        # 保证 content 为一个字符串
        return [{"role": "tool", "tool_call_id": result.id, "content": str(result.content)} for result in results]
    else:
        parts = []
        elements = cast(list[LLMChatContentPartType], messages.content)
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
                # 忽略tool_call_content，openai api不需要。
                # 保留这个判断分支，防止openai api接口出现变动。
                continue
        return [{"role": messages.role, "content": parts}]

def convert_llm_chat_message_to_openai_message(messages: list[LLMChatMessage], media_manager: MediaManager, loop: asyncio.AbstractEventLoop) -> list[dict]:
    results = loop.run_until_complete(
        asyncio.gather(*[convert_parts_factory(msg, media_manager) for msg in messages])
    )
    # 扁平化结果, 展开所有列表
    return [item for sublist in results for item in sublist]

def resolve_tool_calls_from_response(tool_calls: Optional[list[dict]]):
    if tool_calls is None:
        return None
    else:
        return [ToolCall(
            id=call["id"],
            type=call["type"],
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


class OpenAIAdapter(LLMBackendAdapter, AutoDetectModelsProtocol):
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
            "tools": [tool.model_dump() for tool in req.tools] if req.tools else None,
            "tool_choice": "auto" if req.tools else None,
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

    async def auto_detect_models(self) -> list[str]:
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