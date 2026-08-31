from abc import ABC
from typing import Iterable, List, Protocol, runtime_checkable

from kirara_ai.config.global_config import ModelConfig
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.llm.format.embedding import LLMEmbeddingRequest, LLMEmbeddingResponse
from kirara_ai.llm.format.rerank import LLMReRankRequest, LLMReRankResponse
from kirara_ai.media.manager import MediaManager
from kirara_ai.tracing.llm_tracer import LLMTracer


@runtime_checkable
class AutoDetectModelsProtocol(Protocol):
    async def auto_detect_models(self) -> List[ModelConfig]: ...

@runtime_checkable
class LLMChatProtocol(Protocol):
    def chat(self, req: LLMChatRequest) -> LLMChatResponse: ...

@runtime_checkable
class LLMChatStreamProtocol(Protocol):
    def stream_chat(self, req: LLMChatRequest) -> Iterable[LLMChatResponse]: ...

@runtime_checkable
class LLMStreamToolCallProtocol(Protocol):
    """流式解析同时累积 `tool_calls` 增量的适配器。

    实现 :class:`LLMChatStreamProtocol` **不等于**能在流式下正确交出工具调用：
    流式协议同时下发文本增量与工具调用增量，而只读文本增量的实现会让工具调用
    静默消失——上层于是把「模型想调工具」当成「模型答完了」，且没有任何错误。
    那比不支持流式更糟。

    因此把「能流式」与「流式也能带工具」拆成两个协议：带工具的请求只在实现了
    这一个的适配器上走流式，其余适配器保持非流式（工具调用完整，只是失去
    首字节与静默超时保护）。

    这是一个**声明式标记**：属性值为真即表示该适配器的 `stream_chat` 会把
    工具调用作为 `LLMToolCallContent` 交出。做成属性而不是方法，是因为它描述的
    是能力而不是行为——第三方适配器补上累积逻辑后只需声明它。
    """

    supports_stream_tool_calls: bool

@runtime_checkable
class LLMEmbeddingProtocol(Protocol):
    def embed(self, req: LLMEmbeddingRequest) -> LLMEmbeddingResponse: ...

@runtime_checkable
class LLMReRankProtocol(Protocol):
    def rerank(self, req: LLMReRankRequest) -> LLMReRankResponse: ...

class LLMBackendAdapter(ABC):
    backend_name: str
    media_manager: MediaManager
    tracer: LLMTracer
