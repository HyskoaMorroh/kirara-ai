from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from kirara_ai.llm.format.message import LLMChatMessage
from kirara_ai.llm.format.tool import Function, ModelTypes, ToolCall

# 3.2.0 及更早版本在本模块内定义了 Function / ToolCall / ModelTypes，
# 现已统一到 kirara_ai.llm.format.tool，这里保留再导出以兼容按旧路径导入的插件。
__all__ = [
    "Function",
    "ToolCall",
    "ModelTypes",
    "Message",
    "Usage",
    "UsageSource",
    "LLMChatResponse",
]


class Message(LLMChatMessage):
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None

class UsageSource(str, Enum):
    PROVIDER = "provider"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class Usage(BaseModel):
    completion_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    source: UsageSource = UsageSource.UNKNOWN

class LLMChatResponse(BaseModel):
    model: Optional[str] = None
    usage: Optional[Usage] = None
    message: Message
