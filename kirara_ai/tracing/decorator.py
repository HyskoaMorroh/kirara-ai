import functools
from typing import Callable

from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.tracing.llm_tracer import LLMTracer


def trace_llm_chat(func: Callable):
    
    """装饰器，用于追踪LLM请求"""
    from kirara_ai.llm.adapter import LLMBackendAdapter
    @functools.wraps(func)
    def wrapper(self: LLMBackendAdapter, req: LLMChatRequest) -> LLMChatResponse:
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
            # 完成追踪
            tracer.complete_request_tracking(trace_id, req, response)
            return response
            
    return wrapper
