from typing import List, Literal, Optional

from pydantic import BaseModel

from kirara_ai.llm.format.message import LLMChatMessage
from kirara_ai.llm.format.tool import Function, ToolCall

# 3.2.0 及更早版本在本模块内定义了 Function / ToolCall / ModelTypes，
# 现已统一到 kirara_ai.llm.format.tool，这里保留再导出以兼容按旧路径导入的插件。
ModelTypes = Literal["openai", "gemini", "claude", "ollama"]

__all__ = ["Function", "ToolCall", "ModelTypes", "Message", "Usage", "LLMChatResponse"]


class Message(LLMChatMessage):
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None

class Usage(BaseModel):
    completion_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None

class LLMChatResponse(BaseModel):
    model: Optional[str] = None
    usage: Optional[Usage] = None
    message: Message
