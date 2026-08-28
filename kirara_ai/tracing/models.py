import json
import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, Optional

from sqlalchemy import Column, DateTime, Float, Index, Integer, String, Text
from sqlalchemy.orm import validates

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


_SENSITIVE_KEYS = frozenset({
    "password",
    "passwd",
    "token",
    "secret",
    "cookie",
    "set_cookie",
    "authorization",
    "proxy_authorization",
    "credential",
    "credentials",
    "api_key",
    "x_api_key",
    "private_key",
    "access_key",
    "secret_key",
    "access_token",
    "refresh_token",
    "id_token",
    "api_token",
    "auth_token",
    "bearer_token",
    "session_token",
    "csrf_token",
    "client_secret",
    "api_secret",
    "webhook_secret",
    "signing_secret",
})
_SENSITIVE_KEY_SUFFIXES = (
    "_password",
    "_passwd",
    "_token",
    "_secret",
    "_cookie",
    "_credential",
    "_credentials",
    "_api_key",
    "_private_key",
    "_access_key",
    "_secret_key",
)
_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|x-api-key|api[-_ ]?key|token|cookie|password|secret|credential)"
    r"\s*[:=]\s*(?:(?:bearer|basic)\s+)?[^,;\s]+"
)
_AUTH_SCHEME_PATTERN = re.compile(
    r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+"
)


def _redact_trace_value(value: Any, key: str = "") -> Any:
    """Recursively remove credentials before trace data reaches storage or APIs."""
    snake_key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key)
    normalized_key = re.sub(r"[^a-zA-Z0-9]+", "_", snake_key).strip("_").lower()
    if normalized_key in _SENSITIVE_KEYS or normalized_key.endswith(
        _SENSITIVE_KEY_SUFFIXES
    ):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item): _redact_trace_value(child, str(item))
            for item, child in value.items()
        }
    if isinstance(value, list):
        return [_redact_trace_value(item, key) for item in value]
    if isinstance(value, tuple):
        return [_redact_trace_value(item, key) for item in value]
    if isinstance(value, str):
        value = _INLINE_SECRET_PATTERN.sub(r"\1=[redacted]", value)
        return _AUTH_SCHEME_PATTERN.sub(r"\1 [redacted]", value)
    return value


class LLMRequestTrace(TraceRecord):
    """LLM请求跟踪记录"""
    
    __tablename__ = "llm_request_traces"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    trace_id = Column(String(64), nullable=False, index=True, unique=True)
    correlation_id = Column(String(64), nullable=True, index=True)
    model_id = Column(String(64), nullable=False, index=True)
    backend_name = Column(String(64), nullable=False, index=True)
    provider = Column(String(64), nullable=True)
    
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
    error_category = Column(String(32), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    
    # 创建索引
    __table_args__ = (
        Index('idx_request_model', 'model_id', 'request_time'),
        Index('idx_backend_time', 'backend_name', 'request_time'),
        Index('idx_status_time', 'status', 'request_time'),
        Index('idx_provider_time', 'provider', 'request_time'),
        Index('idx_error_category_time', 'error_category', 'request_time'),
        Index('idx_usage_source_time', 'usage_source', 'request_time'),
    )
    
    def __repr__(self):
        return f"<LLMRequestTrace id={self.id} trace_id={self.trace_id}>"
    
    def update_from_event(self, event: TraceEvent) -> None:
        """从事件更新记录"""
        if isinstance(event, LLMRequestStartEvent):
            self.trace_id = event.trace_id
            self.correlation_id = event.correlation_id
            self.model_id = event.model_id
            self.backend_name = event.backend_name
            self.provider = event.backend_name
            self.request_time = datetime.fromtimestamp(event.start_time)
            self.status = "pending"
            self.usage_source = "unknown"
            self.error_category = None
            if event.request:
                self.request = event.request.model_dump()
        
        elif isinstance(event, LLMRequestCompleteEvent):
            self.backend_name = event.backend_name
            self.response_time = datetime.fromtimestamp(event.end_time)
            self.duration = event.duration
            self.status = "success"
            successful_attempt = next(
                (attempt for attempt in reversed(event.attempts) if attempt.success),
                None,
            )
            if successful_attempt is not None:
                self.provider = successful_attempt.provider
            elif event.cost_snapshot is not None:
                self.provider = event.cost_snapshot.provider
            else:
                self.provider = event.backend_name
            self.error_category = None

            # 记录令牌使用情况
            if event.response and event.response.usage:
                self.prompt_tokens = event.response.usage.prompt_tokens
                self.completion_tokens = event.response.usage.completion_tokens
                self.total_tokens = event.response.usage.total_tokens
                self.cached_tokens = event.response.usage.cached_tokens
                self.cache_write_tokens = event.response.usage.cache_write_tokens
                self.usage_source = event.response.usage.source.value
            else:
                self.usage_source = "unknown"

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
                _redact_trace_value([attempt.to_dict() for attempt in event.attempts]),
                ensure_ascii=False,
                default=_json_default,
            ) if event.attempts else None
            self.cost_snapshot_json = json.dumps(
                _redact_trace_value(event.cost_snapshot.model_dump(mode="json")),
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
            self.usage_source = self.usage_source or "unknown"
            last_attempt = event.attempts[-1] if event.attempts else None
            self.provider = last_attempt.provider if last_attempt else event.backend_name
            classified_attempt = next(
                (attempt for attempt in reversed(event.attempts) if attempt.error_category),
                None,
            )
            self.error_category = (
                classified_attempt.error_category if classified_attempt else "unknown"
            )
            self.ttft_ms = event.ttft_ms
            self.attempt_count = len(event.attempts)
            self.attempts_json = json.dumps(
                _redact_trace_value([attempt.to_dict() for attempt in event.attempts]),
                ensure_ascii=False,
                default=_json_default,
            ) if event.attempts else None

    @validates("error")
    def _redact_error(self, _key: str, value: Any) -> Optional[str]:
        if value is None:
            return None
        return str(_redact_trace_value(str(value)))
    
    def to_dict(self) -> Dict[str, Any]:
        """将记录转换为基本字典，用于JSON序列化"""
        return {
            "id": self.id,
            "trace_id": self.trace_id,
            "correlation_id": self.correlation_id,
            "model_id": self.model_id,
            "backend_name": self.backend_name,
            "provider": self.provider,
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
            "error": _redact_trace_value(self.error),
            "error_category": self.error_category,
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
        return _redact_trace_value(json.loads(self.request_json)) if self.request_json else None  # type: ignore
    
    @request.setter
    def request(self, value: Any):
        """设置请求内容"""
        if value:
            self.request_json = json.dumps(
                _redact_trace_value(value), ensure_ascii=False, default=_json_default
            )
    
    @property
    def response(self) -> Optional[Dict[str, Any]]:
        """获取响应内容"""
        return _redact_trace_value(json.loads(self.response_json)) if self.response_json else None  # type: ignore
    
    @response.setter
    def response(self, value: Any):
        """设置响应内容"""
        if value:
            self.response_json = json.dumps(
                _redact_trace_value(value), ensure_ascii=False, default=_json_default
            )

    @property
    def attempts(self) -> Optional[list[Dict[str, Any]]]:
        return _redact_trace_value(json.loads(self.attempts_json)) if self.attempts_json else None

    @property
    def cost_snapshot(self) -> Optional[Dict[str, Any]]:
        return _redact_trace_value(json.loads(self.cost_snapshot_json)) if self.cost_snapshot_json else None
