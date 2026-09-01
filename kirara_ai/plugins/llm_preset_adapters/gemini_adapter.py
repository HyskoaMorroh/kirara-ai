import asyncio
import base64
import json
from typing import Any, Dict, Iterable, List, Literal, Optional, cast

import aiohttp
import requests
from pydantic import BaseModel, ConfigDict

import kirara_ai.llm.format.tool as tool
from kirara_ai.config.global_config import ModelConfig
from kirara_ai.llm.cancellation import CancellableRequestMixin
from kirara_ai.llm.adapter import (AutoDetectModelsProtocol, LLMBackendAdapter, LLMChatProtocol,
                                   LLMChatStreamProtocol, LLMEmbeddingProtocol)
from kirara_ai.llm.format.message import (LLMChatContentPartType, LLMChatImageContent, LLMChatMessage,
                                          LLMChatTextContent, LLMToolCallContent, LLMToolResultContent, RoleType)
from kirara_ai.llm.format.request import LLMChatRequest, Tool
from kirara_ai.llm.format.response import Function, LLMChatResponse, Message, ToolCall, Usage
from kirara_ai.llm.format.embedding import LLMEmbeddingRequest, LLMEmbeddingResponse
from kirara_ai.llm.model_types import LLMAbility, ModelType
from kirara_ai.logger import get_logger
from kirara_ai.media import MediaManager
from kirara_ai.tracing import trace_llm_chat

from .utils import generate_tool_call_id, pick_tool_calls


def resolve_function_call(calls: list[LLMChatContentPartType]) -> Optional[list[ToolCall]]:
    tool_calls = [
        ToolCall(model="gemini", function=Function(name=call.name, arguments=call.parameters))
        for call in calls
        if isinstance(call, LLMToolCallContent)
    ]
    return tool_calls if tool_calls else None

SAFETY_SETTINGS = [{
    "category": "HARM_CATEGORY_HARASSMENT",
    "threshold": "BLOCK_NONE"
}, {
    "category": "HARM_CATEGORY_HATE_SPEECH",
    "threshold": "BLOCK_NONE"
}, {
    "category": "HARM_CATEGORY_SEXUALLY_EXPLICIT",
    "threshold": "BLOCK_NONE"
}, {
    "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
    "threshold": "BLOCK_NONE"
}, {
    "category": "HARM_CATEGORY_CIVIC_INTEGRITY",
    "threshold": "BLOCK_NONE"
}]

# POST 模式支持最大 20 MB 的 inline data
INLINE_LIMIT_SIZE = 1024 * 1024 * 20

IMAGE_MODAL_MODELS = [
    "gemini-2.0-flash-exp"
]


class GeminiConfig(BaseModel):
    api_key: str
    api_base: str = "https://generativelanguage.googleapis.com/v1beta"
    model_config = ConfigDict(frozen=True)


async def convert_non_tool_message(msg: LLMChatMessage, media_manager: MediaManager) -> dict:
    parts: List[Dict[str, Any]] = []
    elements = cast(list[LLMChatContentPartType], msg.content)
    for element in elements:
        if isinstance(element, LLMChatTextContent):
            parts.append({"text": element.text})
        elif isinstance(element, LLMChatImageContent):
            media = media_manager.get_media(element.media_id)
            if media is None:
                raise ValueError(f"Media {element.media_id} not found")
            parts.append({
                "inline_data": {
                    "mime_type": str(media.mime_type),
                    "data": await media.get_base64()
                }
            })
        elif isinstance(element, LLMToolCallContent):
            parts.append({
                "functionCall": {
                    "name": element.name,
                    "args": element.parameters 
                }
            })
    return {
        "role": "model" if msg.role == "assistant" else "user",
        "parts": parts
    }

async def convert_llm_chat_message_to_gemini_message(msg: LLMChatMessage, media_manager: MediaManager) -> dict:
    if msg.role in ["user", "assistant"]:
        return await convert_non_tool_message(msg, media_manager)
    elif msg.role == "tool":
        results = cast(list[LLMToolResultContent], msg.content)
        return {"role": "user", "parts": [resolve_tool_results(result) for result in results]}
    else:
        raise ValueError(f"Invalid role: {msg.role}")

async def convert_all_messages_to_gemini_format(messages: List[LLMChatMessage], media_manager: MediaManager) -> list[dict]:
    """把对话消息转成 Gemini 的 ``contents``。

    **系统消息不在这里**：Gemini 有专门的 ``systemInstruction`` 字段，
    见 :func:`convert_system_messages_to_gemini_instruction`。此前系统消息和
    ``user`` 一起走 ``convert_non_tool_message``，而后者把非 assistant 的一律映射成
    ``"role": "user"``——于是系统提示词变成了对话里的第一条用户消息。
    请求成功、模型有回复、而人格与规则的权重完全不同。**这种不报错的降级比抛异常
    更难发现**：没有任何迹象表明系统提示词已经不是系统提示词了。
    """
    # gather需要先用异步函数封装，然后才能使用asyncio.run()
    conversation = [msg for msg in messages if msg.role != "system"]
    return await asyncio.gather(*[convert_llm_chat_message_to_gemini_message(msg, media_manager) for msg in conversation])


def convert_system_messages_to_gemini_instruction(
    messages: List[LLMChatMessage],
) -> Optional[dict]:
    """把系统消息转成 Gemini 的 ``systemInstruction``。

    口径与 Claude 侧的同名转换一致（见 `claude_adapter.py`）：合并全部系统消息的
    全部文本部件（只取第一段等于静默丢掉技能目录与工具说明）、跳过非文本部件
    （该字段只接受文本，一张误入的图片不该让整条请求失败）、没有可用文本时返回
    ``None`` 让上层剔除这个键。
    """
    parts: List[Dict[str, Any]] = []
    for message in messages:
        if message.role != "system":
            continue
        for part in message.content:
            if isinstance(part, LLMChatTextContent) and part.text:
                parts.append({"text": part.text})
    if not parts:
        return None
    return {"parts": parts}

def convert_tools_to_gemini_format(tools: list[Tool]) -> list[dict[Literal["function_declarations"], list[dict]]]:
    # 定义允许的字段结构
    allowed_keys = {
        "name": True,
        "description": True,
        "parameters": {
            "type": True,
            "properties": {
                "*": {
                    "type": True,
                    "title": True,
                    "description": True,
                    "enum": True,
                    "default": True,
                    "items": True,
                }
            },
            "required": True
        }
    }

    def filter_dict(data: dict, allowed: dict) -> dict:
        """递归过滤字典，只保留允许的字段"""
        result = {}
        for key, value in allowed.items():
            if key == "*" and isinstance(value, dict):
                # 处理通配符情况，适用于 properties 字典
                for data_key, data_value in data.items():
                    if isinstance(data_value, dict):
                        result[data_key] = filter_dict(data_value, value)
                    else:
                        result[data_key] = data_value
            elif key in data:
                if isinstance(value, dict) and isinstance(data[key], dict):
                    # 如果是嵌套字典，递归处理
                    result[key] = filter_dict(data[key], value)
                else:
                    # 否则直接保留值
                    result[key] = data[key]
        return result

    function_declarations = []
    # 注意：循环变量不要用 tool，否则会遮蔽模块级的 kirara_ai.llm.format.tool 导入
    for tool_item in tools:
        # 将Tool对象转换为字典
        tool_dict = tool_item.model_dump()
        # 过滤出需要的字段
        filtered_tool = filter_dict(tool_dict, allowed_keys)
        function_declarations.append(filtered_tool)

    return [{"function_declarations": function_declarations}]

def resolve_tool_results(element: LLMToolResultContent) -> dict:
    # 全部拼接成字符串
    output = ""
    for content in element.content:
        if isinstance(content, tool.TextContent):
            output += content.text
        elif isinstance(content, tool.MediaContent):
            # FIXME: Gemini 不支持 response 传媒体内容，需要从额外的 message 中传入，类似于 **篡改记忆**
            output += f"<media id={content.media_id} mime_type={content.mime_type} />"
    return {
        "functionResponse": {
            "name": element.name,
            "response": {"error": output} if element.isError else {"output": output}
        }
    }

#: 推理强度档位 → Gemini `thinkingConfig.thinkingBudget`（token 数）。
#:
#: Gemini 与 Claude 一样要一个具体预算而不是档位名，但它的字段在
#: `generationConfig.thinkingConfig` 下，且允许独立于 `maxOutputTokens`。
#: `-1` 是 Gemini 定义的「动态思考」（由模型自行决定预算），恰好对应「最大强度」。
_GEMINI_THINKING_BUDGET: dict[str, int] = {
    "low": 1024,
    "medium": 4096,
    "high": 12288,
    # 动态思考：把上限交给模型，而不是我们猜一个数字。
    "max": -1,
}


def build_gemini_thinking_config(reasoning_effort: Optional[str]) -> Optional[dict]:
    """Translate a vendor-neutral effort tier into Gemini's thinking config.

    返回 ``None`` 表示不写入该字段——未配置时必须保持上游默认行为，
    而且不支持思考的模型收到这个字段会直接报错。
    """
    if not reasoning_effort:
        return None
    budget = _GEMINI_THINKING_BUDGET.get(reasoning_effort)
    if budget is None:
        return None
    return {"thinkingBudget": budget}


class GeminiAdapter(
    LLMBackendAdapter,
    CancellableRequestMixin,
    AutoDetectModelsProtocol,
    LLMChatProtocol,
    LLMChatStreamProtocol,
    LLMEmbeddingProtocol,
):

    media_manager: MediaManager

    def __init__(self, config: GeminiConfig):
        self.config = config
        self.logger = get_logger("GeminiAdapter")

    @trace_llm_chat
    def chat(self, req: LLMChatRequest) -> LLMChatResponse:
        api_url = f"{self.config.api_base}/models/{req.model}:generateContent?key={self.config.api_key}"
        headers = {
            # 这里的 api key 验证方法和 api reference 不一致。本次处理暂时按照api reference写法更正。 Warning: 未进行实际测试
            # "x-goog-api-key": self.config.api_key,
            "Content-Type": "application/json",
        }

        response_modalities = ["text"]
        if req.model in IMAGE_MODAL_MODELS:
            response_modalities.append("image")

        data = {
            "contents": asyncio.run(convert_all_messages_to_gemini_format(req.messages, self.media_manager)),
            # 系统提示词走专门的字段，不混进 contents（见上面那两个转换函数）。
            "systemInstruction": convert_system_messages_to_gemini_instruction(req.messages),
            "generationConfig": {
                "temperature": req.temperature,
                "topP": req.top_p,
                "topK": 40,
                "maxOutputTokens": req.max_tokens,
                "stopSequences": req.stop,
                "responseModalities": response_modalities,
                # 推理强度按 Gemini 自己的 thinkingConfig 表达，不透传档位名。
                # 未配置时该键不出现：不支持思考的模型收到它会直接报错。
                "thinkingConfig": build_gemini_thinking_config(req.reasoning_effort),
            },
            "safetySettings": SAFETY_SETTINGS,
            "tools": convert_tools_to_gemini_format(req.tools) if req.tools else None,
        }
        data["generationConfig"] = {
            key: value
            for key, value in data["generationConfig"].items()
            if value is not None
        }

        self.logger.debug(f"Gemini request: {data}")

        # Remove None fields
        data = {k: v for k, v in data.items() if v is not None}

        response = self._post_with_retry(api_url, json=data, headers=headers)

        # 登记在途响应，让 `cancel_pending_request` 能真正断开这条连接。
        with self._track_response(req, response):
            try:
                response_data = response.json()
            except Exception as e:
                self.logger.error(f"API Response: {response.text}")
                raise e
        content: List[LLMChatContentPartType] = []
        role = "assistant"
        for part in response_data["candidates"][0]["content"]["parts"]:
            if "text" in part:
                content.append(LLMChatTextContent(text=part["text"]))
            elif "inlineData" in part:
                decoded_image_data = base64.b64decode(part["inlineData"]["data"])
                media = asyncio.run(
                    self.media_manager.register_from_data(
                        data=decoded_image_data,
                        format=part["inlineData"]["mimeType"].removeprefix(
                            "image/"),
                        source="gemini response")
                )
                content.append(LLMChatImageContent(media_id=media))
            elif "functionCall" in part:
                content.append(
                    LLMToolCallContent(
                            id=generate_tool_call_id(part["functionCall"]["name"]),
                            name=part["functionCall"]["name"],
                            parameters=part["functionCall"].get("args", None)
                        )
                    )

        # 上游省略 usageMetadata 时交出 None，而不是一份全 0 的 Usage（需求 22.1）。
        # 输出 Token 是 usageMetadata.candidatesTokenCount：此前读的是顶层
        # promptTokensDetails——一个并不存在的键，于是 completion_tokens 恒为 0，
        # 而同一适配器的流式分支读的是正确字段，两条路径口径互相矛盾。
        metadata = response_data.get("usageMetadata")
        usage: Optional[Usage] = None
        if isinstance(metadata, dict) and metadata:
            usage = Usage(
                prompt_tokens=metadata.get("promptTokenCount"),
                cached_tokens=metadata.get("cachedContentTokenCount"),
                completion_tokens=metadata.get("candidatesTokenCount"),
                total_tokens=metadata.get("totalTokenCount"),
            )

        return LLMChatResponse(
            model=req.model,
            usage=usage,
            message=Message(
                content=content,
                role=cast(RoleType, role),
                finish_reason=response_data["candidates"][0].get("finishReason"),
                tool_calls=pick_tool_calls(content)
            ),
        )

    def stream_chat(self, req: LLMChatRequest) -> Iterable[LLMChatResponse]:
        """Stream a Gemini completion as incremental responses.

        Gemini 用的是 `:streamGenerateContent` 加 `alt=sse`，帧格式是 SSE，
        但载荷结构与 OpenAI 完全不同：文本在
        `candidates[0].content.parts[*].text`，用量在 `usageMetadata`
        且每一帧都可能带上累计值。

        这里只在最后一帧（带 `finishReason`）产出用量，避免把中途的累计值
        当成最终结果反复覆盖统计。上游没给就保持 None，由上层估算器标记为估算。
        """
        api_url = (
            f"{self.config.api_base}/models/{req.model}:streamGenerateContent"
            f"?alt=sse&key={self.config.api_key}"
        )
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

        response_modalities = ["text"]
        if req.model in IMAGE_MODAL_MODELS:
            response_modalities.append("image")

        data = {
            "contents": asyncio.run(
                convert_all_messages_to_gemini_format(req.messages, self.media_manager)
            ),
            # 与非流式同一口径：系统提示词走专门的字段。两条路径必须一致——
            # 只修一条会让「同一个 Agent 在流式和非流式下人格权重不同」，
            # 而那个差别没有任何地方会报出来。
            "systemInstruction": convert_system_messages_to_gemini_instruction(req.messages),
            "generationConfig": {
                "temperature": req.temperature,
                "topP": req.top_p,
                "topK": 40,
                "maxOutputTokens": req.max_tokens,
                "stopSequences": req.stop,
                "responseModalities": response_modalities,
            },
            "safetySettings": SAFETY_SETTINGS,
            "tools": convert_tools_to_gemini_format(req.tools) if req.tools else None,
        }
        data = {k: v for k, v in data.items() if v is not None}

        with requests.post(
            api_url, json=data, headers=headers, timeout=(10, 300), stream=True
        ) as response, self._track_response(req, response):
            try:
                response.raise_for_status()
            except Exception as error:
                self.logger.error(f"Stream response: {response.text[:512]}")
                raise error

            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload or payload == "[DONE]":
                    if payload == "[DONE]":
                        break
                    continue
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    self.logger.warning("Skipped an unparsable Gemini stream chunk")
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    detail = chunk["error"]
                    raise RuntimeError(
                        f"Gemini stream error: {detail.get('status', 'unknown')}"
                    )

                candidates = chunk.get("candidates") or []
                if not candidates:
                    continue
                candidate = candidates[0]
                finish_reason = candidate.get("finishReason")
                parts = ((candidate.get("content") or {}).get("parts")) or []
                text = "".join(
                    part["text"]
                    for part in parts
                    if isinstance(part, dict) and isinstance(part.get("text"), str)
                )

                usage = None
                if finish_reason:
                    metadata = chunk.get("usageMetadata") or {}
                    if metadata:
                        usage = Usage(
                            prompt_tokens=metadata.get("promptTokenCount"),
                            cached_tokens=metadata.get("cachedContentTokenCount"),
                            completion_tokens=metadata.get("candidatesTokenCount"),
                            total_tokens=metadata.get("totalTokenCount"),
                        )

                if not text and not finish_reason:
                    continue

                yield LLMChatResponse(
                    model=req.model,
                    usage=usage,
                    message=Message(
                        content=[LLMChatTextContent(text=text)] if text else [],
                        role="assistant",
                        finish_reason=finish_reason or "",
                    ),
                )

    def embed(self, req: LLMEmbeddingRequest) -> LLMEmbeddingResponse:
        # 使用批量嵌入接口，单次嵌入接口:embedContent
        # gemini 的 API reference 是这样定义的很奇怪，居然敢在 url 中传递key
        api_url = f"{self.config.api_base}/models/{req.model}:batchEmbedContents?key={self.config.api_key}"
        headers = {
            "Content-Type": "application/json",
        }
        # 目前 gemini 没有一个嵌入模型支持多模态嵌入
        if  any(isinstance(input, LLMChatImageContent) for input in req.inputs):
            raise ValueError("gemini does not support multi-modal embedding")
        inputs = cast(list[LLMChatTextContent], req.inputs)
        data = [
            {
                "model": req.model,
                "content": {
                    "parts": [{"text": input.text}]
                },
                "outputDimensionality": req.dimension
            } for input in inputs
        ]
        # 移除None字段
        data = [{ k:v for k,v in item.items() if v is not None} for item in data]
        response = self._post_with_retry(url=api_url,json={"requests": data}, headers=headers)
        try:
            # {
            #     "embeddings": [
            #         {"values": [0.1, ...]},
            #         ...
            #     ]
            # }
            response_data: dict[Literal["embeddings"],list[dict[Literal["values"], list[float]]]] = response.json()
        except Exception as e:
            self.logger.error(f"API Response: {response.text}")
            raise e
        return LLMEmbeddingResponse(
            # gemini不返回usage
            vectors=[data["values"] for data in response_data["embeddings"]]
        )

    async def auto_detect_models(self) -> list[ModelConfig]:
        api_url = f"{self.config.api_base}/models"
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                api_url, headers={"x-goog-api-key": self.config.api_key}
            ) as response:
                if response.status != 200:
                    self.logger.error(f"获取模型列表失败: {await response.text()}")
                    response.raise_for_status()
                response_data = await response.json()
                return [
                    ModelConfig(id=model["name"].removeprefix("models/"), type=ModelType.LLM.value, ability=LLMAbility.TextChat.value)
                    for model in response_data["models"]
                    if "generateContent" in model["supportedGenerationMethods"]
                ]

    def _post_with_retry(self, url: str, json: dict, headers: dict, retry_count: int = 3) -> requests.Response: # type: ignore
        for i in range(retry_count):
            try:
                response = requests.post(url, json=json, headers=headers, timeout=(10, 120))
                response.raise_for_status()
                return response
            except requests.exceptions.RequestException as e:
                if i == retry_count - 1:
                    self.logger.error(
                        f"API Response: {response.text if 'response' in locals() else 'No response'}")
                    raise e
                else:
                    self.logger.warning(
                        f"Request failed, retrying {i+1}/{retry_count}: {e}")
