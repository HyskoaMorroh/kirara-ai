import asyncio
import json
from typing import Any, Iterable, List, Optional, cast

import aiohttp
import requests
from pydantic import BaseModel, ConfigDict

import kirara_ai.llm.format.tool as tools
from kirara_ai.config.global_config import ModelConfig
from kirara_ai.llm.cancellation import CancellableRequestMixin
from kirara_ai.llm.adapter import (AutoDetectModelsProtocol, LLMBackendAdapter, LLMChatProtocol,
                                   LLMChatStreamProtocol, LLMEmbeddingProtocol)
from kirara_ai.llm.format.message import (LLMChatContentPartType, LLMChatImageContent, LLMChatMessage,
                                          LLMChatTextContent, LLMToolCallContent, LLMToolResultContent)
from kirara_ai.llm.format.request import LLMChatRequest, Tool
from kirara_ai.llm.format.response import Function, LLMChatResponse, Message, ToolCall, Usage
from kirara_ai.llm.format.embedding import LLMEmbeddingRequest, LLMEmbeddingResponse
from kirara_ai.llm.model_types import LLMAbility, ModelType
from kirara_ai.llm.rectifier import rectify_request
from kirara_ai.logger import get_logger
from kirara_ai.media.manager import MediaManager
from kirara_ai.tracing import trace_llm_chat

from .openai_adapter import convert_tools_to_openai_format
from .utils import generate_tool_call_id, pick_tool_calls


#: 与厂商无关的推理强度档位到 Ollama `think` 取值的映射。
#:
#: Ollama 用**顶层** `think` 字段表达思考（不是 `options` 里的一项），
#: 取值是布尔或 `"low"` / `"medium"` / `"high"`。它没有 `"max"` 这一档，
#: 因此 `max` 映射到 `"high"`：把上游不认识的字面量透传过去会被拒，
#: 而降一档仍然满足「最高可用强度」这个语义。
_OLLAMA_THINK_LEVELS = {
    "low": "low",
    "medium": "medium",
    "high": "high",
    "max": "high",
}


def resolve_ollama_think(req: LLMChatRequest) -> Optional[str]:
    """Translate the vendor-neutral effort tier into Ollama's ``think`` value.

    未指定时返回 ``None``，调用方据此**不写入该键**——不支持思考的模型收到
    `think` 会直接报错，而「没配置」必须逐字节保持旧请求体。
    """
    effort = getattr(req, "reasoning_effort", None)
    if not effort:
        return None
    return _OLLAMA_THINK_LEVELS.get(effort)


def resolve_tool_calls(response_data: dict[str, dict]) -> Optional[list[ToolCall]]:
    if tool_calls := response_data["message"].get("tool_calls"):
        return [
            ToolCall(
                model="ollama",
                function=Function(
                    name=call["function"]["name"],
                    arguments=call["function"].get("arguments"),
                ),
            )
            for call in tool_calls
        ]
    return None


class OllamaConfig(BaseModel):
    api_base: str = "http://localhost:11434"
    model_config = ConfigDict(frozen=True)


async def resolve_media_ids(media_ids: list[str], media_manager: MediaManager) -> List[str]:
    result = []
    for media_id in media_ids:
        media = media_manager.get_media(media_id)
        if media is not None:
            base64_data = await media.get_base64()
            result.append(base64_data)
    return result

def convert_llm_response(response_data: dict[str, dict[str, Any]]) -> list[LLMChatContentPartType]:
    # 通过实践证明 llm 调用工具时 content 字段为空字符串没有任何有效信息不进行记录
    if calls := response_data["message"].get("tool_calls", None):
        return [LLMToolCallContent(
            id=generate_tool_call_id(call["function"]["name"]),
            name=call["function"]["name"],
            parameters=call["function"].get("arguments", None)
        ) for call in calls
        ]
    else:
        return [LLMChatTextContent(text=response_data["message"].get("content", ""))]

def convert_non_tool_message(msg: LLMChatMessage, media_manager: MediaManager, loop: asyncio.AbstractEventLoop) -> dict[str, Any]:
    text_content = ""
    images: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    messages: dict[str, Any] = {
        "role": msg.role,
        "content": "",
    }
    for part in msg.content:
        if isinstance(part, LLMChatTextContent):
            text_content += part.text
        elif isinstance(part, LLMChatImageContent):
            images.append(part.media_id)
        elif isinstance(part, LLMToolCallContent):
            tool_calls.append({
                "function": {
                    "name": part.name,
                    "arguments": part.parameters,
                }
            })
    messages["content"] = text_content
    if images:
        messages["images"] = loop.run_until_complete(
            resolve_media_ids(images, media_manager))
    if tool_calls:
        messages["tool_calls"] = tool_calls
    return messages


def convert_tool_result_message(msg: LLMChatMessage, media_manager: MediaManager, loop: asyncio.AbstractEventLoop) -> list[dict]:
    """
    将工具调用结果转换为 Ollama 格式
    """
    elements = cast(list[LLMToolResultContent], msg.content)
    messages = []
    for element in elements:
        output = ""
        for item in element.content:
            if isinstance(item, tools.TextContent):
                output += f"{item.text}\n"
            elif isinstance(item, tools.MediaContent):
                output += f"<media id={item.media_id} mime_type={item.mime_type} />\n"
        if element.isError:
            output = f"Error: {element.name}\n{output}"
        messages.append({"role": "tool", "content": output,
                        "tool_call_id": element.id})
    return messages

def convert_tools_to_ollama_format(tools: list[Tool]) -> list[dict]:
    # 这里将其独立出来方便应对后续接口改动
    return convert_tools_to_openai_format(tools)

class OllamaAdapter(
    LLMBackendAdapter,
    CancellableRequestMixin,
    AutoDetectModelsProtocol,
    LLMChatProtocol,
    LLMChatStreamProtocol,
    LLMEmbeddingProtocol,
):
    def __init__(self, config: OllamaConfig):
        self.config = config
        self.logger = get_logger("OllamaAdapter")

    @trace_llm_chat
    def chat(self, req: LLMChatRequest) -> LLMChatResponse:
        api_url = f"{self.config.api_base}/api/chat"
        headers = {"Content-Type": "application/json"}

        # 将消息转换为 Ollama 格式
        messages = []
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        for msg in req.messages:
            # 收集每条消息中的文本内容和图像
            if msg.role == "tool":
                messages.extend(convert_tool_result_message(
                    msg, self.media_manager, loop))
            else:
                messages.append(convert_non_tool_message(
                    msg, self.media_manager, loop))

        data = {
            "model": req.model,
            "messages": messages,
            "stream": False,
            # 推理强度：Ollama 的思考开关是**顶层** `think`，不是 options 里的
            # 一项。塞进 options 会被当成未知采样参数忽略——那正是
            # 「界面上配好了、那个值从未生效」这一类静默失效。
            "think": resolve_ollama_think(req),
            "options": {
                "temperature": req.temperature,
                "top_p": req.top_p,
                "num_predict": req.max_tokens,
                "stop": req.stop,
                "tools": convert_tools_to_ollama_format(req.tools) if req.tools else None,
            },
        }

        # Remove None fields
        data = {k: v for k, v in data.items() if v is not None}
        if "options" in data:
            data["options"] = {
                k: v for k, v in data["options"].items() if v is not None # type: ignore
            }

        # 登记在途响应，让 `cancel_pending_request` 能真正断开这条连接。
        #
        # 整流（需求 8）：与其他适配器同一套语义，按 Ollama 自己的请求体形状改
        # 字段（并列的 `images` 数组、顶层 `think`）。此前这个适配器连
        # `rectify_request` 都没有 import，供应商编辑页上的整流开关对它
        # **从未参与任何决策**。
        #
        # 不能照搬 `messages` 形状的规则：Ollama 的 `content` 是纯字符串而不是
        # 块列表，按块遍历一次也命中不了；`thinking` / `max_tokens` 更是
        # Anthropic 的字段，写进去只是多两个被忽略的键，真正的位置没被改。
        response_data: dict
        applied_rectifications: set[str] = set()
        while True:
            response = requests.post(api_url, json=data, headers=headers, timeout=(10, 120))
            with self._track_response(req, response):
                try:
                    response.raise_for_status()
                    response_data = response.json()
                    break
                except Exception as e:
                    body_text = response.text
                    self.logger.error(f"API Response: {body_text}")
                    rectified, record = rectify_request(
                        data,
                        body_text,
                        req.rectifier,
                        already_applied=frozenset(applied_rectifications),
                    )
                    if rectified is None or record is None:
                        raise e
                    self.logger.warning(
                        "整流器改写请求后重试：%s %s", record.kind, record.details
                    )
                    applied_rectifications.add(record.kind)
                    data = rectified
        # https://github.com/ollama/ollama/blob/main/docs/api.md#generate-a-chat-completion
        content = convert_llm_response(response_data)
        # 统计字段缺失时交出 None：此前用 response_data['prompt_eval_count']
        # 直接下标，字段缺失会让整条请求以 KeyError 失败，而这本该只是
        # 「用量未知」，由上层估算器标记（需求 22.1）。
        prompt_eval = response_data.get("prompt_eval_count")
        eval_count = response_data.get("eval_count")
        usage: Optional[Usage] = None
        if prompt_eval is not None or eval_count is not None:
            total_tokens = (
                prompt_eval + eval_count
                if isinstance(prompt_eval, int) and isinstance(eval_count, int)
                else None
            )
            usage = Usage(
                prompt_tokens=prompt_eval,
                completion_tokens=eval_count,
                total_tokens=total_tokens,
            )
        return LLMChatResponse(
            model=req.model,
            message=Message(
                content=content,
                role="assistant",
                finish_reason="stop",
                tool_calls=pick_tool_calls(content),
            ),
            usage=usage,
        )

    def stream_chat(self, req: LLMChatRequest) -> Iterable[LLMChatResponse]:
        """Stream an Ollama chat completion as incremental responses.

        Ollama 不用 SSE：`stream: true` 时它按行返回 JSON（NDJSON），
        每行一个对象，最后一行 `done: true` 并带上统计。因此这里按行解析，
        而不是找 `data:` 前缀——照搬 OpenAI 的解析会一个分片都读不到。

        用量只在最后一帧出现（`prompt_eval_count` / `eval_count`），
        缺字段时保持 None，由上层估算器标记为估算，不在这里补 0。
        """
        api_url = f"{self.config.api_base}/api/chat"
        headers = {"Content-Type": "application/json"}

        messages = []
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            for msg in req.messages:
                if msg.role == "tool":
                    messages.extend(
                        convert_tool_result_message(msg, self.media_manager, loop)
                    )
                else:
                    messages.append(
                        convert_non_tool_message(msg, self.media_manager, loop)
                    )
        finally:
            loop.close()

        data: dict[str, Any] = {
            "model": req.model,
            "messages": messages,
            # 流式是这条路径的前提，不接受调用方把它关掉。
            "stream": True,
            # 与非流式路径同一口径。两条路径给出不同强度是一个无法自查的差异：
            # 同一个 Agent 在 `off` 与 `aggregate` 两档下会得到不同质量的回答，
            # 而配置里看不出任何区别。
            "think": resolve_ollama_think(req),
            "options": {
                "temperature": req.temperature,
                "top_p": req.top_p,
                "num_predict": req.max_tokens,
                "stop": req.stop,
                "tools": convert_tools_to_ollama_format(req.tools) if req.tools else None,
            },
        }
        data = {k: v for k, v in data.items() if v is not None}
        if "options" in data:
            data["options"] = {
                k: v for k, v in data["options"].items() if v is not None  # type: ignore
            }

        # 整流与非流式路径同一套判定（需求 8）。只修非流式是半个修复：
        # 参数约束错误在两条路径上完全一样，换个 `reply_stream_mode` 就又会硬失败。
        #
        # 建连阶段就要判完：流已经开始产出内容之后再重试会让用户看到两段回复
        # 拼在一起，那比一次失败更难解释。
        applied_rectifications: set[str] = set()
        while True:
            response = requests.post(
                api_url, json=data, headers=headers, timeout=(10, 300), stream=True
            )
            try:
                response.raise_for_status()
            except Exception as error:
                body_text = response.text[:512]
                self.logger.error(f"Stream response: {body_text}")
                response.close()
                rectified, record = rectify_request(
                    data,
                    body_text,
                    req.rectifier,
                    already_applied=frozenset(applied_rectifications),
                )
                if rectified is None or record is None:
                    raise error
                self.logger.warning(
                    "整流器改写流式请求后重试：%s %s", record.kind, record.details
                )
                applied_rectifications.add(record.kind)
                data = rectified
                continue
            break

        with response, self._track_response(req, response):
            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except ValueError:
                    # 单个坏行不应终止整条流。
                    self.logger.warning("Skipped an unparsable Ollama stream line")
                    continue
                if not isinstance(chunk, dict):
                    continue
                if chunk.get("error"):
                    raise RuntimeError(f"Ollama stream error: {chunk['error']}")

                message = chunk.get("message") or {}
                text = message.get("content")
                done = bool(chunk.get("done"))

                usage = None
                if done:
                    prompt_tokens = chunk.get("prompt_eval_count")
                    completion_tokens = chunk.get("eval_count")
                    if isinstance(prompt_tokens, int) or isinstance(completion_tokens, int):
                        usage = Usage(
                            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
                            completion_tokens=(
                                completion_tokens if isinstance(completion_tokens, int) else None
                            ),
                            total_tokens=(
                                prompt_tokens + completion_tokens
                                if isinstance(prompt_tokens, int)
                                and isinstance(completion_tokens, int)
                                else None
                            ),
                        )

                if not text and not done:
                    continue

                yield LLMChatResponse(
                    model=req.model,
                    usage=usage,
                    message=Message(
                        content=[LLMChatTextContent(text=text)] if text else [],
                        role="assistant",
                        finish_reason=(chunk.get("done_reason") or "stop") if done else "",
                    ),
                )
                if done:
                    break

    def embed(self, req: LLMEmbeddingRequest) -> LLMEmbeddingResponse:
        # https://github.com/ollama/ollama/blob/main/docs/api.md#generate-embeddings api文档地址
        api_url = f"{self.config.api_base}/api/embed"
        headers = {"Content-Type": "application/json"}
        if any(isinstance(input, LLMChatImageContent) for input in req.inputs):
            raise ValueError("ollama api does not support multi-modal embedding")
        inputs = cast(list[LLMChatTextContent], req.inputs)
        data = {
            "model": req.model,
            "input": [input.text for input in inputs],
            # 禁止自动截断输入数据用以适应上下文长度
            "truncate": req.truncate
        }
        data = { k:v for k, v in data.items() if v is not None }
        response = requests.post(api_url, json=data, headers=headers, timeout=(10, 120))
        try:
            response.raise_for_status()
            response_data = response.json()
        except Exception as e:
            self.logger.error(f"API Response: {response.text}")
            raise e
        # 缺统计字段时保持未知，不写 0——与聊天路径同一口径（需求 22.1）。
        prompt_eval = response_data.get("prompt_eval_count")
        return LLMEmbeddingResponse(
            vectors=response_data["embeddings"],
            usage=(
                Usage(prompt_tokens=prompt_eval) if prompt_eval is not None else None
            ),
        )

    async def auto_detect_models(self) -> list[ModelConfig]:
        api_url = f"{self.config.api_base}/api/tags"
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(api_url) as response:
                response.raise_for_status()
                response_data = await response.json()
                return [ModelConfig(id=tag["name"], type=ModelType.LLM.value, ability=LLMAbility.TextChat.value) for tag in response_data["models"]]
