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
from kirara_ai.llm.cancellation import CancellableRequestMixin
from kirara_ai.llm.adapter import (AutoDetectModelsProtocol, LLMBackendAdapter, LLMChatProtocol,
                                   LLMChatStreamProtocol, LLMEmbeddingProtocol)
from kirara_ai.llm.format.message import (LLMChatContentPartType, LLMChatImageContent, LLMChatMessage,
                                          LLMChatTextContent, LLMToolCallContent, LLMToolResultContent)
from kirara_ai.llm.rectifier import rectify_request
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

def accumulate_stream_tool_calls(
    state: dict[int, dict[str, Any]], deltas: Any
) -> None:
    """把一帧里的 `delta.tool_calls` 增量并进累积状态。

    流式协议同时下发两种增量：文本增量与工具调用增量。此前这里只读
    `delta.content`，于是「带工具的请求」被上层一刀切成非流式
    （`executor.py` 的 `and not candidate_request.tools`）——而绑了 MCP 的 Agent
    是本项目最常见的形态，结果是流式首字节超时与静默超时在主流部署上从未生效。

    三条协议事实决定了累积方式：

    * `index` 是归属键。一轮可以并行调多个工具，它们的帧交错到达。
    * `id` 与 `function.name` 通常只在该调用的**第一帧**出现。
    * `function.arguments` 是**分片**到达的 JSON 文本，必须按序拼接；
      每帧当成独立 JSON 解析会在第一个分片上就失败。

    坏帧跳过而不抛：与文本增量同一约定——单个坏帧不该终止整条流。
    """
    if not isinstance(deltas, list):
        return
    for item in deltas:
        if not isinstance(item, dict):
            continue
        try:
            index = int(item.get("index", 0))
        except (TypeError, ValueError):
            continue
        slot = state.setdefault(index, {"id": None, "name": None, "arguments": ""})
        call_id = item.get("id")
        if isinstance(call_id, str) and call_id:
            slot["id"] = call_id
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name:
            slot["name"] = name
        fragment = function.get("arguments")
        if isinstance(fragment, str):
            slot["arguments"] += fragment


def resolve_stream_tool_calls(
    state: dict[int, dict[str, Any]],
) -> list[LLMToolCallContent]:
    """把累积状态变成与非流式路径同一形状的工具调用列表。

    按 `index` 升序返回：到达顺序是网络顺序，而调用顺序是模型的意图。

    没有函数名的调用**丢弃**——它无法执行，交出去只会在下游变成一次 KeyError。
    参数拼不成合法 JSON 时按空参数处理：一个参数错的调用还有机会被工具自己拒绝
    并给出可读错误，而让整轮对话抛异常的话用户只看到「请求失败」。
    """
    calls: list[LLMToolCallContent] = []
    for index in sorted(state):
        slot = state[index]
        name = slot.get("name")
        if not name:
            continue
        raw = slot.get("arguments") or "{}"
        try:
            parameters = json.loads(raw) if raw.strip() else {}
        except ValueError:
            logger.warning(
                "Stream tool call arguments were not valid JSON; using empty parameters"
            )
            parameters = {}
        if not isinstance(parameters, dict):
            parameters = {}
        calls.append(
            LLMToolCallContent(id=slot.get("id"), name=str(name), parameters=parameters)
        )
    return calls


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
    CancellableRequestMixin,
    LLMChatProtocol,
    LLMChatStreamProtocol,
    AutoDetectModelsProtocol,
):
    media_manager: MediaManager

    #: 本适配器的流式解析会累积 `delta.tool_calls` 并交出 `LLMToolCallContent`。
    #:
    #: 上层据此决定「带工具的请求要不要走流式」。只读文本增量的实现声明为假时，
    #: 那类请求保持非流式——工具调用完整，只是失去首字节与静默超时保护；
    #: 声明为真却不实现累积，工具调用会静默消失，那是更糟的失败形态。
    supports_stream_tool_calls: bool = True

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
            # OpenAI 系接口用 `reasoning_effort` 字符串枚举表达推理强度。
            # 未指定时不出现该键——部分兼容网关对未知字段直接 400。
            "reasoning_effort": req.reasoning_effort,
        }

        # Remove None fields
        data = {k: v for k, v in data.items() if v is not None}

        logger.debug(f"Request: {data}")

        # 使用带重试机制的 session，并设置超时时间
        # timeout 参数: (连接超时, 读取超时) 单位：秒
        #
        # 登记在途响应，让 `cancel_pending_request` 能真正断开这条连接。
        # 非流式请求同样会等上游几十秒，只支持流式取消等于让默认配置
        # （`reply_stream_mode: off`）完全没有取消能力。
        #
        # 整流（需求 8）：与 Claude 路径同一套语义。此前 `rectify_request` 只在
        # `claude_adapter.py` 被调用过，而十个 OpenAI 兼容适配器全部继承本基类——
        # 于是供应商编辑页上的四个整流开关对它们**从未参与任何决策**。
        # 用户看到的是一次硬失败（「请求失败」），而真正的原因是一张图或一个
        # 上游不认识的字段，两者都不是他能自己改的。
        #
        # 只对**真实的上游拒绝**生效，且每类最多一次：改完仍失败就抛原始错误。
        response_data: dict
        applied_rectifications: set[str] = set()
        while True:
            response = self._session.post(api_url, json=data, headers=headers, timeout=(10, 120))
            with self._track_response(req, response):
                try:
                    response.raise_for_status()
                    response_data = response.json()
                    break
                except Exception as e:
                    body_text = response.text
                    logger.error(f"Response: {body_text}")
                    rectified, record = rectify_request(
                        data,
                        body_text,
                        req.rectifier,
                        already_applied=frozenset(applied_rectifications),
                    )
                    if rectified is None or record is None:
                        raise e
                    logger.warning(
                        "整流器改写请求后重试：{} {}", record.kind, record.details
                    )
                    applied_rectifications.add(record.kind)
                    data = rectified
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

        # 上游省略 usage 时必须交出 None，而不是一份全 0 的 Usage：
        # 后者会被 mark_provider_usage 标成「供应商返回」，同时让
        # attach_estimated_usage 跳过估算，于是这条请求永久记为 0 Token、
        # 0 成本，且看起来像是上游亲口说的（需求 22.1）。
        raw_usage = response_data.get("usage")
        usage: Optional[Usage] = None
        if isinstance(raw_usage, dict) and raw_usage:
            prompt_details = raw_usage.get("prompt_tokens_details")
            cached_tokens = (
                prompt_details.get("cached_tokens")
                if isinstance(prompt_details, dict)
                else None
            )
            usage = Usage(
                prompt_tokens=raw_usage.get("prompt_tokens"),
                completion_tokens=raw_usage.get("completion_tokens"),
                total_tokens=raw_usage.get("total_tokens"),
                cached_tokens=cached_tokens,
            )

        return LLMChatResponse(
            model=req.model,
            usage=usage,
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
            # 推理强度与非流式路径同一字段，两条路径口径必须一致。
            "reasoning_effort": req.reasoning_effort,
        }
        data = {k: v for k, v in data.items() if v is not None}

        # 整流（需求 8）：与非流式路径同一套语义。此前 `stream_chat` 在
        # `raise_for_status()` 失败后直接抛，于是同一个请求换成流式就不整流了——
        # 而 `reply_stream_mode` 配成 aggregate/incremental 之后（文档推荐这么配，
        # 因为流式超时与首字节前的故障转移才生效），供应商页上的四个整流开关
        # 对这条路径从未参与任何决策。用户看到「请求失败」，真正的原因是一张图
        # 或一个上游不认识的字段，两者都不是他能自己改的。
        #
        # 与非流式一致的两条边界：只对**真实的上游拒绝**生效；每类最多改一次，
        # 改完仍失败就抛原始错误。流式路径上每次重试都要重新建连、重新等首字节，
        # 无界重试会把一次必然失败拖成一串超时。
        applied_rectifications: set[str] = set()
        while True:
            response = self._session.post(
                api_url,
                json=data,
                headers=headers,
                timeout=(10, 300),
                stream=True,
            )
            try:
                response.raise_for_status()
                break
            except Exception as error:
                body_text = response.text[:512]
                logger.error(f"Stream response: {body_text}")
                rectified, record = rectify_request(
                    data,
                    response.text,
                    req.rectifier,
                    already_applied=frozenset(applied_rectifications),
                )
                if rectified is None or record is None:
                    response.close()
                    raise error
                logger.warning(
                    "整流器改写流式请求后重试：{} {}", record.kind, record.details
                )
                applied_rectifications.add(record.kind)
                data = rectified
                # 关掉这条失败连接再重试：不关会让失败的响应体一直占着连接池。
                response.close()

        with response, self._track_response(req, response):

            # 工具调用增量按 `index` 累积，与文本增量并行处理。缺了它，
            # 「带工具的请求」只能被上层一刀切成非流式，而绑了 MCP 的 Agent
            # 是最常见的形态——流式超时保护因此在主流部署上从未生效。
            tool_call_state: dict[int, dict[str, Any]] = {}

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
                    # 缓存命中量在 prompt_tokens_details.cached_tokens，
                    # 与非流式分支读同一个字段——两条路径的计量口径必须一致，
                    # 否则同一模型的缓存成本会取决于是否开了流式。
                    prompt_details = usage_data.get("prompt_tokens_details")
                    cached_tokens = (
                        prompt_details.get("cached_tokens")
                        if isinstance(prompt_details, dict)
                        else None
                    )
                    usage = Usage(
                        prompt_tokens=usage_data.get("prompt_tokens"),
                        completion_tokens=usage_data.get("completion_tokens"),
                        total_tokens=usage_data.get("total_tokens"),
                        cached_tokens=cached_tokens,
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
                            accumulate_stream_tool_calls(
                                tool_call_state, delta.get("tool_calls")
                            )
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
                    # 工具调用增量已经并进累积状态，不必为它单独产出一帧——
                    # 那会让上层把「参数拼到一半」当成一次可见输出。
                    continue

                # 工具调用只在收尾帧交付：`arguments` 是分片拼接的，
                # 中途交出去的是半截 JSON。没有工具调用时这里是空列表，
                # 纯文本回复的形状因此逐字不变。
                tool_calls = (
                    resolve_stream_tool_calls(tool_call_state)
                    if finish_reason is not None
                    else []
                )
                content: list[Any] = (
                    [LLMChatTextContent(text=delta_text)] if delta_text else []
                )
                content.extend(tool_calls)

                yield LLMChatResponse(
                    model=req.model,
                    usage=usage,
                    message=Message(
                        content=content,
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
        # 上游省略 usage 时保持未知：嵌入常用于记忆检索，一次调用可能处理上千条
        # 文本，把它记成 0 token 会让「记忆功能不花钱」这个错误结论看起来有数据支撑。
        raw_usage = response_data.get("usage")
        usage: Optional[Usage] = None
        if isinstance(raw_usage, dict) and raw_usage:
            usage = Usage(
                prompt_tokens=raw_usage.get("prompt_tokens"),
                total_tokens=raw_usage.get("total_tokens"),
            )
        return LLMEmbeddingResponse(
            vectors=[data["embedding"] for data in response_data["data"]],
            usage=usage,
        )