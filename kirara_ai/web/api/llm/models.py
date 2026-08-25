from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kirara_ai.config.global_config import LLMBackendConfig, ModelConfig


class LLMBackendInfo(LLMBackendConfig):
    """LLM后端信息"""



class LLMBackendList(BaseModel):
    """LLM后端列表"""

    backends: List[LLMBackendInfo]


class LLMBackendResponse(BaseModel):
    """LLM后端响应"""

    error: Optional[str] = None
    data: Optional[LLMBackendInfo] = None


class LLMBackendListResponse(BaseModel):
    """LLM后端列表响应"""

    error: Optional[str] = None
    data: Optional[LLMBackendList] = None


class LLMBackendCreateRequest(LLMBackendConfig):
    """创建LLM后端请求"""



class LLMBackendUpdateRequest(LLMBackendConfig):
    """更新LLM后端请求"""



class LLMAdapterTypes(BaseModel):
    """可用的LLM适配器类型列表"""

    types: List[str]


class LLMAdapterConfigSchema(BaseModel):
    """LLM适配器配置模式"""

    error: Optional[str] = None
    configSchema: Optional[Dict[str, Any]] = None


class ModelConfigListResponse(BaseModel):
    """模型配置列表响应"""

    error: Optional[str] = None
    models: List[ModelConfig] = []


class WebUIChatRequest(BaseModel):
    """Validated input for the WebUI's normalized IM entry point."""

    model_config = ConfigDict(extra="forbid")

    message: str = Field(min_length=1)
    session_id: str = Field(min_length=1)
    username: str = Field(default="WebUI user", min_length=1)
    chat_type: Literal["c2c", "group"] = "c2c"
    group_id: Optional[str] = Field(default=None, min_length=1)
    agent_id: Optional[str] = Field(default=None, min_length=1)

    @field_validator("message", "session_id", "username", "group_id", "agent_id")
    @classmethod
    def reject_blank_strings(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("must not be blank")
        return value.strip() if value is not None else value

    @model_validator(mode="after")
    def validate_scope(self) -> "WebUIChatRequest":
        if self.chat_type == "group" and self.group_id is None:
            raise ValueError("group_id is required for group chat")
        if self.chat_type == "c2c" and self.group_id is not None:
            raise ValueError("group_id is only valid for group chat")
        return self
