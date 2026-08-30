"""OneBot V11 adapter configuration."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:  # pragma: no cover - 仅供类型检查
    # 运行时不导入：节流模块不该成为配置模块的启动期依赖，
    # `build_send_pacing` 里的局部导入已经够用。
    from kirara_ai.plugins.im_onebot_adapter.pacing import SendPacing


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
    send_pacing_enabled: bool = Field(
        default=True,
        title="发送节流",
        description=(
            "多页回复的页与页之间主动等待，避免被 QQ 判定为刷屏。"
            "命中风控的表现与「发送失败」完全不同：接口全部返回成功，"
            "消息却到不了对方，且要等很久才恢复。"
            "默认开启——关掉等于让每个部署自己踩一次风控才知道要开。"
            "本地自建或压测可以关闭。"
        ),
    )
    send_pacing_per_character_seconds: float = Field(
        default=0.1,
        ge=0,
        le=1,
        title="每字符附加等待",
        description="按文本长度增加等待：短文本连发才是风控最敏感的形态。",
    )
    send_pacing_minimum_seconds: float = Field(
        default=1.0,
        ge=0,
        le=30,
        title="页间最小等待",
        description="即便内容很短也至少等这么久。",
    )
    send_pacing_jitter_seconds: float = Field(
        default=1.0,
        ge=0,
        le=30,
        title="随机抖动上限",
        description="固定间隔本身就是一种可识别的机器特征，因此加入随机量。",
    )
    send_pacing_maximum_seconds: float = Field(
        default=8.0,
        ge=0,
        le=120,
        title="页间最大等待",
        description=(
            "上界。风控看的是频率而不是「等得够不够久」，"
            "超过某个点再等只是在惩罚用户。"
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
    expand_forward_messages: bool = Field(
        default=False,
        title="展开合并转发",
        description=(
            "是否调用 get_forward_msg 把合并转发的内容取回来。"
            "关闭时只给出 `[合并转发：<id>]` 占位（默认，与旧行为一致）。"
            "打开后用户转发一段对话过来提问时，模型能看到真正的内容而不是一个 ID；"
            "代价是每段转发多一次上游调用。"
        ),
    )
    forward_max_depth: int = Field(
        default=2,
        ge=1,
        le=5,
        title="合并转发展开深度",
        description=(
            "嵌套转发的展开层数上限。合并转发可以包含另一段转发，"
            "无界递归会把一次消息转换变成一串上游调用。"
        ),
    )
    forward_max_nodes: int = Field(
        default=20,
        ge=1,
        le=200,
        title="合并转发展开条数",
        description=(
            "单段转发最多展开多少条。一段转发可能有几百条，"
            "全部展开会让提示词爆掉，随后排版层又要把它切成几十页。"
            "超出部分会明确标注被省略，而不是静默截断。"
        ),
    )
    qr_login_log_path: Optional[str] = Field(
        default=None,
        title="OneBot 实现日志路径",
        description=(
            "可选。填写 OneBot 实现（LLOneBot / PMHQ）的日志文件路径后，"
            "连接状态里会附带扫码登录生命周期：有效期、生成时间、当前状态、"
            "刷新次数、失败原因与最新二维码路径。留空则不读取任何文件。"
        ),
    )
    qr_login_log_tail_bytes: int = Field(
        default=256 * 1024,
        gt=0,
        le=8 * 1024 * 1024,
        title="日志读取尾部字节数",
        description=(
            "每次只读日志尾部这么多字节。登录事件总在最近，"
            "整文件读取会在长期运行的部署上把一个诊断接口变成慢查询。"
        ),
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

    def build_send_pacing(self) -> "SendPacing":
        """把配置映射成运行时那份 `SendPacing`。

        由这里统一转换，而不是让适配器各自读五个字段：参数名在两处各写一份时，
        迟早出现「界面调了但运行时没变」，而那种不一致没有任何症状——
        只表现为账号又被限制发言。
        """
        from kirara_ai.plugins.im_onebot_adapter.pacing import SendPacing

        return SendPacing(
            enabled=self.send_pacing_enabled,
            per_character_seconds=self.send_pacing_per_character_seconds,
            minimum_seconds=self.send_pacing_minimum_seconds,
            jitter_seconds=self.send_pacing_jitter_seconds,
            maximum_seconds=max(
                self.send_pacing_maximum_seconds, self.send_pacing_minimum_seconds
            ),
        )

    @property
    def websocket_path(self) -> str:
        """Return the mounted application path without the final ``/ws`` route."""
        path = urlparse(self.websocket_url).path.rstrip("/")
        if path.endswith("/ws"):
            path = path[:-3].rstrip("/")
        if not path:
            raise ValueError("websocket_url 必须包含 OneBot WebSocket 路径")
        return path if path.startswith("/") else f"/{path}"
