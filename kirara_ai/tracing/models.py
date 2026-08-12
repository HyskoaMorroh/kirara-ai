import json
from datetime import datetime
from typing import Any, Dict, Optional

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from kirara_ai.events.tracing import LLMRequestCompleteEvent, LLMRequestFailEvent, LLMRequestStartEvent
from kirara_ai.tracing.core import TraceEvent, TraceRecord


class LLMRequestTrace(TraceRecord):
    """LLM请求跟踪记录"""
    
    __tablename__ = "llm_request_traces"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String(64), nullable=False, index=True, unique=True)
    model_id = Column(String(64), nullable=False, index=True)
    backend_name = Column(String(64), nullable=False, index=True)
    
    # 时间相关
    request_time = Column(DateTime, nullable=False, index=True)
    response_time = Column(DateTime, nullable=True)
    duration = Column(Float, nullable=True)
    
    # 请求和响应内容
    request_json = Column(Text, nullable=True)
    response_json = Column(Text, nullable=True)
    
    # 令牌使用情况
    prompt_tokens = Column(Integer, nullable=True)
    completion_tokens = Column(Integer, nullable=True)
    total_tokens = Column(Integer, nullable=True)
    cached_tokens = Column(Integer, nullable=True)
    
    # 错误信息
    error = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    
    # 创建索引
    __table_args__ = (
        Index('idx_request_model', 'model_id', 'request_time'),
        Index('idx_backend_time', 'backend_name', 'request_time'),
        Index('idx_status_time', 'status', 'request_time'),
    )
    
    def __repr__(self):
        return f"<LLMRequestTrace id={self.id} trace_id={self.trace_id}>"
    
    def update_from_event(self, event: TraceEvent) -> None:
        """从事件更新记录"""
        if isinstance(event, LLMRequestStartEvent):
            self.trace_id = event.trace_id
            self.model_id = event.model_id
            self.backend_name = event.backend_name
            self.request_time = datetime.fromtimestamp(event.start_time)
            self.status = "pending"
            if event.request:
                self.request = event.request.model_dump()
        
        elif isinstance(event, LLMRequestCompleteEvent):
            self.response_time = datetime.fromtimestamp(event.end_time)
            self.duration = event.duration
            self.status = "success"

            # 记录令牌使用情况
            if event.response and event.response.usage:
                self.prompt_tokens = event.response.usage.prompt_tokens
                self.completion_tokens = event.response.usage.completion_tokens
                self.total_tokens = event.response.usage.total_tokens
                self.cached_tokens = event.response.usage.cached_tokens
            
            # 记录响应内容
            if event.response:
                self.response = event.response.model_dump()
        
        elif isinstance(event, LLMRequestFailEvent):
            self.response_time = datetime.fromtimestamp(event.end_time)
            self.duration = event.duration
            self.error = event.error
            self.status = "failed"
    
    def to_dict(self) -> Dict[str, Any]:
        """将记录转换为基本字典，用于JSON序列化"""
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "model_id": self.model_id,
            "backend_name": self.backend_name,
            "request_time": self.request_time.isoformat() if self.request_time else None, # type: ignore
            "response_time": self.response_time.isoformat() if self.response_time else None, # type: ignore
            "duration": self.duration,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "cached_tokens": self.cached_tokens,
            "status": self.status,
            "error": self.error
        }
    
    def to_detail_dict(self) -> Dict[str, Any]:
        """将记录转换为详细字典，包含请求和响应内容"""
        result = self.to_dict()
        result["request"] = self.request
        result["response"] = self.response
        return result
    
    @property
    def request(self) -> Optional[Dict[str, Any]]:
        """获取请求内容"""
        return json.loads(self.request_json) if self.request_json else None  # type: ignore
    
    @request.setter
    def request(self, value: Any):
        """设置请求内容"""
        if value:
            self.request_json = json.dumps(value, ensure_ascii=False, default=str)
    
    @property
    def response(self) -> Optional[Dict[str, Any]]:
        """获取响应内容"""
        return json.loads(self.response_json) if self.response_json else None  # type: ignore
    
    @response.setter
    def response(self, value: Any):
        """设置响应内容"""
        if value:
            self.response_json = json.dumps(value, ensure_ascii=False, default=str)