import json
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text

from kirara_ai.events.tracing import LLMRequestCompleteEvent, LLMRequestFailEvent, LLMRequestStartEvent
from kirara_ai.tracing.core import TraceEvent, TraceRecord


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


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
    cache_write_tokens = Column(Integer, nullable=True)
    usage_source = Column(String(20), nullable=True)
    ttft_ms = Column(Integer, nullable=True)
    attempt_count = Column(Integer, nullable=True)
    attempts_json = Column(Text, nullable=True)
    cost_snapshot_json = Column(Text, nullable=True)
    
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
            self.backend_name = event.backend_name
            self.response_time = datetime.fromtimestamp(event.end_time)
            self.duration = event.duration
            self.status = "success"

            # 记录令牌使用情况
            if event.response and event.response.usage:
                self.prompt_tokens = event.response.usage.prompt_tokens
                self.completion_tokens = event.response.usage.completion_tokens
                self.total_tokens = event.response.usage.total_tokens
                self.cached_tokens = event.response.usage.cached_tokens
                self.cache_write_tokens = event.response.usage.cache_write_tokens
                self.usage_source = event.response.usage.source.value

            self.ttft_ms = event.ttft_ms
            if self.ttft_ms is None:
                first_attempt = next(
                    (attempt for attempt in event.attempts if attempt.ttft_seconds is not None),
                    None,
                )
                if first_attempt is not None:
                    self.ttft_ms = round(first_attempt.ttft_seconds * 1000)  # type: ignore[operator]
            self.attempt_count = len(event.attempts)
            self.attempts_json = json.dumps(
                [attempt.to_dict() for attempt in event.attempts],
                ensure_ascii=False,
                default=_json_default,
            ) if event.attempts else None
            self.cost_snapshot_json = json.dumps(
                event.cost_snapshot.model_dump(mode="json"),
                ensure_ascii=False,
                default=_json_default,
            ) if event.cost_snapshot is not None else None
            
            # 记录响应内容
            if event.response:
                self.response = event.response.model_dump()
        
        elif isinstance(event, LLMRequestFailEvent):
            self.backend_name = event.backend_name
            self.response_time = datetime.fromtimestamp(event.end_time)
            self.duration = event.duration
            self.error = event.error
            self.status = "failed"
            self.ttft_ms = event.ttft_ms
            self.attempt_count = len(event.attempts)
            self.attempts_json = json.dumps(
                [attempt.to_dict() for attempt in event.attempts],
                ensure_ascii=False,
                default=_json_default,
            ) if event.attempts else None
    
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
            "cache_write_tokens": self.cache_write_tokens,
            "usage_source": self.usage_source,
            "ttft_ms": self.ttft_ms,
            "attempt_count": self.attempt_count,
            "attempts": self.attempts,
            "cost_snapshot": self.cost_snapshot,
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
            self.response_json = json.dumps(value, ensure_ascii=False, default=_json_default)

    @property
    def attempts(self) -> Optional[list[Dict[str, Any]]]:
        return json.loads(self.attempts_json) if self.attempts_json else None

    @property
    def cost_snapshot(self) -> Optional[Dict[str, Any]]:
        return json.loads(self.cost_snapshot_json) if self.cost_snapshot_json else None
