import time
from typing import TYPE_CHECKING, Iterable, Optional, Union

from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse

from .base import TraceCompleteEvent, TraceEvent, TraceFailEvent, TraceStartEvent

if TYPE_CHECKING:
    from kirara_ai.llm.pricing import CostSnapshot
    from kirara_ai.llm.resilience import ProviderAttempt


class LLMTraceEvent(TraceEvent):
    """LLM追踪事件基类"""

    def __init__(self,
                trace_id: str,
                model_id: str,
                backend_name: str):
        super().__init__(trace_id)
        self.model_id = model_id
        self.backend_name = backend_name

    def __repr__(self):
        return f"{self.__class__.__name__}(trace_id={self.trace_id}, model={self.model_id}, backend={self.backend_name})"


class LLMRequestStartEvent(LLMTraceEvent, TraceStartEvent):
    """LLM请求开始事件"""

    def __init__(self,
                trace_id: str,
                model_id: str,
                backend_name: str,
                request: LLMChatRequest):
        super().__init__(trace_id, model_id, backend_name)
        self.request = request
        self.start_time = time.time()


class LLMRequestCompleteEvent(LLMTraceEvent, TraceCompleteEvent):
    """LLM请求完成事件"""

    def __init__(self,
                trace_id: str,
                model_id: str,
                backend_name: str,
                request: LLMChatRequest,
                response: LLMChatResponse,
                start_time: float,
                attempts: Optional[Iterable["ProviderAttempt"]] = None,
                cost_snapshot: Optional["CostSnapshot"] = None,
                ttft_ms: Optional[int] = None):
        super().__init__(trace_id, model_id, backend_name)
        self.request = request
        self.response = response
        self.start_time = start_time
        self.end_time = time.time()
        self.duration = int((self.end_time - start_time) * 1000)
        self.attempts = list(attempts or ())
        self.cost_snapshot = cost_snapshot
        self.ttft_ms = ttft_ms

class LLMRequestFailEvent(LLMTraceEvent, TraceFailEvent):
    """LLM请求失败事件"""

    def __init__(self,
                trace_id: str,
                model_id: str,
                backend_name: str,
                request: LLMChatRequest,
                error: Union[str, Exception],
                start_time: float,
                attempts: Optional[Iterable["ProviderAttempt"]] = None,
                ttft_ms: Optional[int] = None):
        super().__init__(trace_id, model_id, backend_name)
        self.request = request
        self.error = str(error)
        self.start_time = start_time
        self.end_time = time.time()
        self.duration = int((self.end_time - start_time) * 1000)
        self.attempts = list(attempts or ())
        self.ttft_ms = ttft_ms
