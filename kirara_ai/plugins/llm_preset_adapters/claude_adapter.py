import asyncio
import base64
import json
from typing import Any, Dict, Iterable, List, Optional

import aiohttp
import requests
from pydantic import BaseModel, ConfigDict

import kirara_ai.llm.format.tool as tools
from kirara_ai.llm.adapter import (AutoDetectModelsProtocol, LLMBackendAdapter, LLMChatProtocol,
                                   LLMChatStreamProtocol)
from kirara_ai.llm.cancellation import CancellableRequestMixin
from kirara_ai.llm.format.message import (LLMChatContentPartType, LLMChatImageContent, LLMChatMessage,
                                          LLMChatTextContent, LLMToolCallContent, LLMToolResultContent)
from kirara_ai.llm.format.request import LLMChatRequest, Tool
from kirara_ai.llm.format.response import Function, LLMChatResponse, Message, ToolCall, Usage
from kirara_ai.llm.rectifier import rectify_request
from kirara_ai.logger import get_logger
from kirara_ai.media.manager import MediaManager
from kirara_ai.tracing.decorator import trace_llm_chat

from .utils import generate_tool_call_id, pick_tool_calls


def resolve_tool_calls(content: list[dict]) -> Optional[list[ToolCall]]:
    tool_calls = []
    for part in content:
        if part.get("type") == "tool_use":
            tool_calls.append(ToolCall(
                model="claude",
                id=part.get("id"),
                type=part.get("type"),
                function=Function(name=part.get("name"), arguments=part.get("input")),
            ))
    return tool_calls if tool_calls else None


class ClaudeConfig(BaseModel):
    api_key: str
    api_base: str = "https://api.anthropic.com/v1"
    model_config = ConfigDict(frozen=True)


#: 推理强度档位 → Claude `thinking.budget_tokens` 占 `max_tokens` 的比例。
#:
#: Claude 没有 `reasoning_effort`：它要一个**具体的 token 预算**，而且该预算
#: 必须小于 `max_tokens`（否则整个请求被拒，正文一个字都出不来）。因此这里按
#: 比例换算并再留一层安全边界，而不是把档位名直接透传。
_CLAUDE_THINKING_RATIO: Dict[str, float] = {
    "low": 0.25,
    "medium": 0.5,
    "high": 0.7,
    "max": 0.8,
}

#: 未指定 `max_tokens` 时用于换算 thinking 预算的基准。
#: Claude 的 `max_tokens` 是必填项，上层没给时按这个值兜底。
_CLAUDE_DEFAULT_MAX_TOKENS = 8192

#: Claude 要求 thinking 预算不低于 1024 tokens；低于该值等于没开。
_CLAUDE_MIN_THINKING_TOKENS = 1024


def build_claude_thinking(
    reasoning_effort: Optional[str], max_tokens: Optional[int]
) -> Optional[Dict[str, Any]]:
    """Translate a vendor-neutral effort tier into Claude's thinking budget.

    返回 ``None`` 表示不开启扩展思考——未配置时必须保持上游默认行为。
    预算被夹在 ``[1024, max_tokens - 1024]``：上界留出正文空间，
    下界满足 Claude 对最小预算的要求；区间为空时返回 ``None`` 而不是硬塞一个
    会被拒绝的值。
    """
    if not reasoning_effort:
        return None
    ratio = _CLAUDE_THINKING_RATIO.get(reasoning_effort)
    if ratio is None:
        return None
    budget_ceiling = (max_tokens or _CLAUDE_DEFAULT_MAX_TOKENS) - _CLAUDE_MIN_THINKING_TOKENS
    if budget_ceiling < _CLAUDE_MIN_THINKING_TOKENS:
        return None
    budget = int((max_tokens or _CLAUDE_DEFAULT_MAX_TOKENS) * ratio)
    budget = max(_CLAUDE_MIN_THINKING_TOKENS, min(budget, budget_ceiling))
    return {"type": "enabled", "budget_tokens": budget}


async def convert_llm_chat_message_to_claude_message(messages: list[LLMChatMessage], media_manager: MediaManager) -> list[dict]:
    content: List[Dict[str, Any]] = []
    for msg in [msg for msg in messages if msg.role in ["user", "assistant", "tool"]]:
        parts: List[Dict[str, Any]] = []
        for part in msg.content:
            if isinstance(part, LLMChatTextContent):
                parts.append({"type": "text", "text": part.text})
            elif isinstance(part, LLMToolResultContent):
                parts.append(await resolve_tool_result(part, media_manager))
            elif isinstance(part, LLMToolCallContent):
                continue
            elif isinstance(part, LLMChatImageContent):
                media = media_manager.get_media(part.media_id)
                if media is None:
                    raise ValueError(f"Media {part.media_id} not found")
                parts.append({"source": {"media_type": str(media.mime_type), "data": await media.get_base64()}, "type": "image"})
        content.append({
            "role": "user" if msg.role == "tool" else msg.role,
            "content": parts
        })
    return content

def convert_tools_to_claude_format(tools: list[Tool]) -> list[dict]:
    # 使用 pydantic 的 model_dump 方法，高级排除项`exclude`排除 openai 专属项
    return [tool.model_dump(exclude={"strict": True, 'parameters': {'additionalProperties': True}}) for tool in tools]

async def resolve_tool_result(element: LLMToolResultContent, media_manager: MediaManager) -> dict:
    tool_result: List[Dict[str, Any]] = []
    for item in element.content:
        if isinstance(item, tools.TextContent):
            tool_result.append({"type": "text", "text": item.text})
        elif isinstance(item, tools.MediaContent):
            media = media_manager.get_media(item.media_id)
            if media is None:
                raise ValueError(
                    f"Media {item.media_id} not found")
            tool_result.append({
                "type": media.media_type.value.lower(),
                "source": {
                    "type": "base64", "media_type": str(media.mime_type), "data": await media.get_base64()
                }
            })
    return {"type": "tool_result", "tool_use_id": element.id, "content": tool_result, "is_error": element.isError}
    
class ClaudeAdapter(
    LLMBackendAdapter,
    CancellableRequestMixin,
    AutoDetectModelsProtocol,
    LLMChatProtocol,
    LLMChatStreamProtocol,
):

    media_manager: MediaManager

    def __init__(self, config: ClaudeConfig):
        self.config = config
        self.logger = get_logger("ClaudeAdapter")

    @trace_llm_chat
    def chat(self, req: LLMChatRequest) -> LLMChatResponse:
        api_url = f"{self.config.api_base}/messages"
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        # Claude 的系统消息比较特殊
        system_messages = [msg for msg in req.messages if msg.role == "system"]
        if system_messages:
            system_message = system_messages[0].content
        else:
            system_message = None

        # 构建请求数据

        data = {
            "model": req.model,
            "messages": asyncio.run(convert_llm_chat_message_to_claude_message(req.messages, self.media_manager)),
            "max_tokens": req.max_tokens,
            "system": system_message,
            "temperature": req.temperature,
            "top_p": req.top_p,
            "stream": req.stream,
            # claude tools格式中参数部分命名与openai api不同，不能简单使用model_dumps，在这里进行转换
            "tools": convert_tools_to_claude_format(req.tools) if req.tools else None,
            # claude默认如果使用了tools字段，这里需要指定tool_choice， claude默认为{"type": "auto"}.
            # 可考虑后续给用户暴露此接口， 目前此处各模型定义不太统一
            "tool_choice": {"type": "auto"} if req.tools else None,
            # 推理强度按 Claude 自己的 thinking 预算表达，不透传档位名。
            "thinking": build_claude_thinking(req.reasoning_effort, req.max_tokens),
        }
        # Remove None fields
        data = {k: v for k, v in data.items() if v is not None}

        # 登记在途响应，让 `cancel_pending_request` 能真正断开这条连接；
        # 非流式路径同样会等上游几十秒，不能只有流式可取消。
        #
        # 整流（需求 8）：上游因参数约束拒绝时，按白名单改一处再重试一次。
        # 修的是「同一个 API 在不同模型上约束不同」这类必然失败——
        # thinking 预算与 max_tokens 的关系、换模型后失效的思考签名、
        # 不支持图片的模型收到图片。不改就必然失败，而原因既不在错误里说清，
        # 也不是用户能自己改的。
        #
        # 只对**真实的上游拒绝**生效，且每类最多一次：改完仍失败就抛原始错误。
        # 反复整流会把「参数错」变成「一直在转」，后者更难查。
        response_data = None
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

        content: List[LLMChatContentPartType] = []

        for res in response_data["content"]:
            if res["type"] == "text":
                content.append(LLMChatTextContent(text=res["text"]))
            elif res["type"] == "image":
                image_data = base64.b64decode(res["source"]["data"])
                media = asyncio.run(self.media_manager.register_from_data(
                    image_data, res["source"]["media_type"], source="claude response"))
                content.append(LLMChatImageContent(media_id=media))
            elif res["type"] == "tool_use":
                # tool_call 时 只会额外返回一个 text 的深度思考。
                content.append(LLMToolCallContent(id=res.get("id", generate_tool_call_id(res["name"])), name=res["name"], parameters=res.get("input", None)))
        # 上游省略 usage 时必须交出 None：一份全 0 的 Usage 会被标成
        # 「供应商返回」并跳过估算，让这条请求永久记为 0 Token（需求 22.1）。
        # 缓存读（cache_read_input_tokens）与缓存写（cache_creation_input_tokens）
        # 单价不同且都已在定价表里建模，必须分别落库。
        raw_usage = response_data.get("usage")
        usage: Optional[Usage] = None
        if isinstance(raw_usage, dict) and raw_usage:
            input_tokens = raw_usage.get("input_tokens")
            output_tokens = raw_usage.get("output_tokens")
            total_tokens = (
                input_tokens + output_tokens
                if isinstance(input_tokens, int) and isinstance(output_tokens, int)
                else None
            )
            usage = Usage(
                prompt_tokens=input_tokens,
                completion_tokens=output_tokens,
                total_tokens=total_tokens,
                cached_tokens=raw_usage.get("cache_read_input_tokens"),
                cache_write_tokens=raw_usage.get("cache_creation_input_tokens"),
            )

        return LLMChatResponse(
            model=req.model,
            usage=usage,
            message=Message(
                content=content,
                role=response_data.get("role", "assistant"),
                finish_reason=response_data.get("stop_reason", "stop"),
                # claude tool_call混合在content字段中，需要提取
                tool_calls=pick_tool_calls(content),
            )
        )

    def stream_chat(self, req: LLMChatRequest) -> Iterable[LLMChatResponse]:
        """Stream a Claude Messages completion as incremental responses.

        需求要求「必须实现流式」，而此前只有 OpenAI 兼容适配器实现了
        ``LLMChatStreamProtocol``：配置了 Claude 的部署即使把 ``reply_stream_mode``
        打开也会静默回落到非流式，于是流式首字节超时、静默超时与「首字节之前的
        故障转移」这三条容错路径对它们全部无效。

        Claude 的 SSE 与 OpenAI 不同：它按 ``event:`` 分类型推送，
        文本增量在 ``content_block_delta`` 的 ``delta.text``；用量分两处——
        ``message_start`` 给 input_tokens，``message_delta`` 给 output_tokens。
        因此这里累加两侧，只在拿到 output 时产出一次带用量的分片，
        避免把「还没算完的用量」当成最终值。绝不在这里编造数字：上游没给就留 None，
        由上层估算器标记为 estimated。
        """
        api_url = f"{self.config.api_base}/messages"
        headers = {
            "x-api-key": self.config.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            "accept": "text/event-stream",
        }

        system_messages = [msg for msg in req.messages if msg.role == "system"]
        system_message = system_messages[0].content if system_messages else None

        data = {
            "model": req.model,
            "messages": asyncio.run(
                convert_llm_chat_message_to_claude_message(req.messages, self.media_manager)
            ),
            "max_tokens": req.max_tokens,
            "system": system_message,
            "temperature": req.temperature,
            "top_p": req.top_p,
            # 流式是这条路径的前提，不接受调用方把它关掉。
            "stream": True,
            "tools": convert_tools_to_claude_format(req.tools) if req.tools else None,
            "tool_choice": {"type": "auto"} if req.tools else None,
            # 推理强度与非流式路径同一处翻译，两条路径口径必须一致。
            "thinking": build_claude_thinking(req.reasoning_effort, req.max_tokens),
        }
        data = {k: v for k, v in data.items() if v is not None}

        input_tokens: Optional[int] = None
        cache_read_tokens: Optional[int] = None
        cache_write_tokens: Optional[int] = None
        # 整流与非流式路径同一套判定（需求 8）。只修非流式是半个修复：
        # 参数约束错误在两条路径上完全一样，`reply_stream_mode=aggregate`
        # 的部署换个开关就又会硬失败。
        #
        # 建连阶段就要判完：流已经开始产出内容之后再重试会让用户看到两段回复
        # 拼在一起，那比一次失败更难解释。因此整流只发生在 `raise_for_status`
        # 这一跳，之后的流内异常照原样抛出。
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
                if not line or not line.startswith("data:"):
                    continue
                payload = line[len("data:"):].strip()
                if not payload:
                    continue
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    # 单个坏帧不应终止整条流。
                    self.logger.warning("Skipped an unparsable Claude stream chunk")
                    continue
                if not isinstance(chunk, dict):
                    continue

                event_type = chunk.get("type")
                if event_type == "error":
                    detail = chunk.get("error") or {}
                    raise RuntimeError(
                        f"Claude stream error: {detail.get('type', 'unknown')}"
                    )
                if event_type == "message_stop":
                    break

                if event_type == "message_start":
                    usage_data = (chunk.get("message") or {}).get("usage") or {}
                    value = usage_data.get("input_tokens")
                    if isinstance(value, int):
                        input_tokens = value
                    # 缓存读/写只在首帧出现，且单价不同，两个维度都要留到末帧
                    # 与 output_tokens 一起产出——口径与非流式分支保持一致。
                    cache_read = usage_data.get("cache_read_input_tokens")
                    if isinstance(cache_read, int):
                        cache_read_tokens = cache_read
                    cache_write = usage_data.get("cache_creation_input_tokens")
                    if isinstance(cache_write, int):
                        cache_write_tokens = cache_write
                    continue

                if event_type == "content_block_delta":
                    delta = chunk.get("delta") or {}
                    text = delta.get("text")
                    if isinstance(text, str) and text:
                        yield LLMChatResponse(
                            model=req.model,
                            message=Message(
                                content=[LLMChatTextContent(text=text)],
                                role="assistant",
                                finish_reason="",
                            ),
                        )
                    continue

                if event_type == "message_delta":
                    delta = chunk.get("delta") or {}
                    usage_data = chunk.get("usage") or {}
                    output_tokens = usage_data.get("output_tokens")
                    usage = None
                    if isinstance(output_tokens, int):
                        usage = Usage(
                            prompt_tokens=input_tokens,
                            completion_tokens=output_tokens,
                            total_tokens=(
                                (input_tokens or 0) + output_tokens
                                if input_tokens is not None
                                else None
                            ),
                            cached_tokens=cache_read_tokens,
                            cache_write_tokens=cache_write_tokens,
                        )
                    stop_reason = delta.get("stop_reason")
                    if usage is not None or stop_reason:
                        yield LLMChatResponse(
                            model=req.model,
                            usage=usage,
                            message=Message(
                                content=[],
                                role="assistant",
                                finish_reason=stop_reason or "",
                            ),
                        )

    async def auto_detect_models(self) -> list[str]:
        # {
        #   "data": [
        #     {
        #       "type": "model",
        #       "id": "claude-3-5-sonnet-20241022",
        #       "display_name": "Claude 3.5 Sonnet (New)",
        #       "created_at": "2024-10-22T00:00:00Z"
        #     }
        #   ],
        #   "has_more": true,
        #   "first_id": "<string>",
        #   "last_id": "<string>"
        # }
        # claude3 全系支持工具调用，支持多模态tool_result
        api_url = f"{self.config.api_base}/models"
        async with aiohttp.ClientSession(trust_env=True) as session:
            async with session.get(
                api_url, headers={"x-api-key": self.config.api_key}
            ) as response:
                response.raise_for_status()
                response_data = await response.json()
                return [model["id"] for model in response_data["data"]]
