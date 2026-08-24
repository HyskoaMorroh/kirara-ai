import functools
import contextvars
from contextlib import contextmanager
from typing import Callable

from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.llm.format.response import UsageSource
from kirara_ai.tracing.llm_tracer import LLMTracer


_SUPPRESS_LLM_CHAT_TRACE = contextvars.ContextVar("suppress_llm_chat_trace", default=False)


@contextmanager
def suppress_llm_chat_tracing():
    token = _SUPPRESS_LLM_CHAT_TRACE.set(True)
    try:
        yield
    finally:
        _SUPPRESS_LLM_CHAT_TRACE.reset(token)


def mark_provider_usage(response: LLMChatResponse) -> LLMChatResponse:
    if response.usage is None or response.usage.source != UsageSource.UNKNOWN:
        return response
    return response.model_copy(
        deep=True,
        update={
            "usage": response.usage.model_copy(update={"source": UsageSource.PROVIDER})
        },
    )


def trace_llm_chat(func: Callable):
    
    """装饰器，用于追踪LLM请求"""
    from kirara_ai.llm.adapter import LLMBackendAdapter
    @functools.wraps(func)
    def wrapper(self: LLMBackendAdapter, req: LLMChatRequest) -> LLMChatResponse:
        if _SUPPRESS_LLM_CHAT_TRACE.get():
            return func(self, req)
        tracer: LLMTracer = self.tracer
        # 开始追踪
        trace_id = tracer.start_request_tracking(self.backend_name, req)
        
        try:
            # 调用原始方法
            response = func(self, req)
        except Exception as e:
            # 记录错误
            tracer.fail_request_tracking(trace_id, req, str(e))
            raise e
        else:
            response = mark_provider_usage(response)
            # 完成追踪
            tracer.complete_request_tracking(trace_id, req, response)
            return response
            
    return wrapper
