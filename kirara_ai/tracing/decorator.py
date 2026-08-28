import functools
import contextvars
from contextlib import contextmanager
from typing import Callable

from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.llm.format.response import UsageSource
from kirara_ai.llm.token_estimator import estimate_usage
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


def attach_estimated_usage(
    request: LLMChatRequest,
    response: LLMChatResponse,
) -> LLMChatResponse:
    """Fill in an ``ESTIMATED`` usage when the provider reported none.

    此前 ``UsageSource.ESTIMATED`` 没有任何生产者：供应商不返回 usage 时，
    这条请求既没有 token 数也不参与计费，最终呈现为一条「免费请求」——
    「0」是一个断言，「未知」不是，前者更糟。

    这里只在**完全没有** usage 时补估算值，并明确标记为估算；
    供应商返回过任何 usage 都保持原样，绝不覆盖实测数据。
    完全无可测内容时仍返回原响应（保持 usage 为 None），不硬造数字。
    """
    if response.usage is not None:
        return response
    estimated = estimate_usage(
        request_messages=getattr(request, "messages", None),
        response_message=response.message,
    )
    if estimated is None:
        return response
    return response.model_copy(deep=True, update={"usage": estimated})


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
            # 供应商没给 usage 时补一个明确标记为估算的值，
            # 而不是留下一条没有 token、也不计费的「免费请求」。
            response = attach_estimated_usage(req, response)
            # 完成追踪
            tracer.complete_request_tracking(trace_id, req, response)
            return response
            
    return wrapper
