import time
from typing import Union

from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse

from .base import TraceCompleteEvent, TraceEvent, TraceFailEvent, TraceStartEvent


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
                start_time: float):
        super().__init__(trace_id, model_id, backend_name)
        self.request = request
        self.response = response
        self.start_time = start_time
        self.end_time = time.time()
        self.duration = int((self.end_time - start_time) * 1000)

class LLMRequestFailEvent(LLMTraceEvent, TraceFailEvent):
    """LLM请求失败事件"""

    def __init__(self,
                trace_id: str,
                model_id: str,
                backend_name: str,
                request: LLMChatRequest,
                error: Union[str, Exception],
                start_time: float):
        super().__init__(trace_id, model_id, backend_name)
        self.request = request
        self.error = str(error)
        self.start_time = start_time
        self.end_time = time.time()
        self.duration = int((self.end_time - start_time) * 1000)
