"""OneBot V11 adapter configuration."""

from __future__ import annotations

import uuid
from typing import Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field


WEBSOCKET_URL_PREFIX = "/im/websocket/onebot"


def make_websocket_url() -> str:
    return f"{WEBSOCKET_URL_PREFIX}/{uuid.uuid4().hex[:8]}/ws"


def auto_generate_websocket_url(schema: dict[str, Any]) -> None:
    schema["readOnly"] = True
    schema["default"] = make_websocket_url()
    schema["textType"] = True
    schema["apiEndpoint"] = True


class OneBotConfig(BaseModel):
    """OneBot V11 反向 WebSocket 配置。"""

    websocket_url: str = Field(
        title="反向 WebSocket 地址",
        description="填写到 OneBot 实现中的反向 WebSocket 地址。",
        default_factory=make_websocket_url,
        json_schema_extra=auto_generate_websocket_url,
    )
    access_token: Optional[str] = Field(
        default=None,
        title="访问 Token",
        description="可选；需要与 OneBot 实现中的 Token 保持一致。",
    )
    heartbeat_interval: int = Field(
        default=15,
        ge=1,
        title="心跳检查间隔",
        description="用于显示连接健康状态的检查间隔，单位为秒。",
    )
    heartbeat_timeout_seconds: int = Field(
        default=90,
        ge=1,
        title="心跳超时",
        description="超过该时间未收到 OneBot 心跳时标记连接异常，单位为秒。",
    )
    action_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        lt=120,
        title="OneBot 操作超时",
        description="等待 OneBot API 返回的最长秒数；发送超时不会自动重试。",
    )
    outbox_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        title="明确失败重试上限",
        description="仅在确认上游未处理操作时有限重试；超时或断线等结果未知情况不会重发。",
    )
    outbox_retry_delay_seconds: float = Field(
        default=1.0,
        ge=0,
        le=60,
        title="投递重试基础间隔",
        description="明确瞬态失败的指数退避基础秒数。",
    )
    isolate_code_messages: bool = Field(
        default=True,
        title="代码单独成条",
        description=(
            "QQ 没有可用的复制按钮；开启后代码块会单独发一条消息，"
            "整条内容即代码本体，长按全选即可复制。关闭则与正文混排。"
        ),
    )
    inbound_media_timeout_seconds: float = Field(
        default=15.0,
        gt=0,
        le=60,
        title="入站媒体下载超时",
        description="下载 QQ 消息中公网媒体的最长秒数。",
    )
    inbound_media_max_bytes: int = Field(
        default=20 * 1024 * 1024,
        gt=0,
        le=100 * 1024 * 1024,
        title="入站媒体大小上限",
        description="QQ 消息中单个图片、语音、视频或文件的最大字节数。",
    )
    host: Optional[str] = Field(
        default=None,
        title="旧版监听地址",
        description="兼容旧配置；设置后使用独立 HTTP 服务。建议迁移到 websocket_url。",
        json_schema_extra={"hidden_unset": True},
    )
    port: Optional[int] = Field(
        default=None,
        title="旧版监听端口",
        description="兼容旧配置；设置后使用独立 HTTP 服务。建议迁移到 websocket_url。",
        json_schema_extra={"hidden_unset": True},
    )

    model_config = ConfigDict(extra="allow")

    @property
    def websocket_path(self) -> str:
        """Return the mounted application path without the final ``/ws`` route."""
        path = urlparse(self.websocket_url).path.rstrip("/")
        if path.endswith("/ws"):
            path = path[:-3].rstrip("/")
        if not path:
            raise ValueError("websocket_url 必须包含 OneBot WebSocket 路径")
        return path if path.startswith("/") else f"/{path}"
