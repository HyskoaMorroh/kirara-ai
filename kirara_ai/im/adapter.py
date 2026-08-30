import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, Literal, Optional, Protocol

from pydantic import BaseModel, Field
from typing_extensions import runtime_checkable

from kirara_ai.im.message import IMMessage
from kirara_ai.im.qr_login import QRLoginSnapshot
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.llm_manager import LLMManager

from .profile import UserProfile


class BotStatus(BaseModel):
    """
    机器人状态
    """

    username: str
    avatar_url: str


class IMActionTimeoutError(asyncio.TimeoutError):
    """An IM platform action exceeded its adapter-level timeout."""


class MessageSendResult(BaseModel):
    """一次消息投递的可用结果。

    `send_message` 此前返回 ``None``。撤回接口（`recall_message` → `delete_msg`）
    一直可用，但调用方拿不到刚发出那条消息的 `message_id`，于是「发一条提示、
    30 秒后撤回」这种再普通不过的用法做不到：能撤，但不知道撤谁。
    上游在响应里明明给了 ID，我们收下、落库，然后丢掉。

    两个字段而不是一个，是因为长回复会分页发送：

    - ``message_ids`` 是**每一页**的 ID。只回第一页等于「后面几页撤不掉」，
      用户看到的是撤回一半的回复，比不撤更糟。
    - ``message_id`` 是第一页，作为「这条回复」的代表。

    上游没回 ID 时两者分别为空元组与 ``None``——绝不编一个（比如 0 或空串）：
    调用方会拿它去撤回，然后撤到别人的消息上，或者静默失败。
    """

    #: 每一页的上游消息 ID，按发送顺序。上游未回报时为空。
    message_ids: tuple[str, ...] = ()
    #: 本次投递的逻辑 ID，用来与日志、投递时间线对上。
    delivery_id: Optional[str] = None
    #: 实际发出的分页数量。空内容不发送时为 0。
    page_count: int = 0

    @property
    def message_id(self) -> Optional[str]:
        """第一页的 ID，即「这条回复」的代表。"""
        return self.message_ids[0] if self.message_ids else None


class AdapterHealthSnapshot(BaseModel):
    """Secret-free connection health exposed by capable IM adapters.

    ``status`` deliberately separates situations that a restart cycle makes look
    identical from the outside:

    - ``initializing`` — the process is up but the adapter has not finished
      ``start()``; nothing is wrong yet.
    - ``waiting`` — the adapter is listening and the upstream implementation has
      simply not dialed in yet.
    - ``connected`` — at least one account is live.
    - ``stale`` — an established link stopped sending heartbeats.
    - ``credential_rejected`` — the upstream reached us and was refused on its
      access token; retrying will not help until the token is fixed.
    - ``upstream_refused`` — the upstream reached us with a handshake we cannot
      accept (missing or unsupported role, missing account id).
    - ``storage_unavailable`` — the link is fine but the adapter's persistent
      data directory stopped accepting writes (read-only remount, full volume).
      需求 18.1 把「数据目录挂载错误」列为必须独立可见的一类，而它此前只在
      两个够不到的位置存在：启动期检查（失败时进程根本起不来，读不到任何
      接口）与 readiness 的 ``data_directories_writable``（只探测 DATA_PATH
      本身，且只在进程已起时）。真正会漏掉的是**运行期**：卷在启动之后被重新
      挂成只读时 WebSocket 仍然连着，适配器报 ``connected``，而每一条要落库的
      投递都在失败——面板上一切正常，消息在丢。
    - ``disconnected`` — the adapter is stopped.

    ``last_disconnect_reason`` is a stable, secret-free enum-like string (never a
    raw upstream message) so an operator can tell "not dialed in yet" apart from
    "dialed in and was rejected" without reading server logs.

    The three legacy statuses (``connected``, ``waiting``, ``disconnected``,
    ``stale``) keep their exact meaning, so an existing consumer that only knows
    those keeps working; new states are additive.
    """

    status: Literal[
        "connected",
        "waiting",
        "disconnected",
        "stale",
        "initializing",
        "credential_rejected",
        "upstream_refused",
        "storage_unavailable",
    ]
    connected_account_count: int = Field(default=0, ge=0)
    last_heartbeat_age_seconds: Optional[float] = Field(default=None, ge=0)
    adapter_started: Optional[bool] = None
    websocket_connected: Optional[bool] = None
    external_login_status: Optional[
        Literal[
            "unknown",
            "upstream_reported_online",
            "upstream_reported_offline",
        ]
    ] = None
    last_disconnect_reason: Optional[
        Literal[
            "access_token_missing",
            "access_token_mismatch",
            "invalid_client_role",
            "missing_self_id",
            "heartbeat_timeout",
            "upstream_lifecycle_disconnect",
            "adapter_stopped",
            # 持久化目录在运行期变得不可写（只读重挂、卷写满）。
            # 与握手类原因分开：处置是查挂载与磁盘，而不是查 Token。
            "data_directory_unwritable",
        ]
    ] = None
    outbox: Optional[Dict[str, int]] = None
    #: 上游实现自身的扫码登录生命周期，仅在能读到它的日志时给出。
    #:
    #: 这一层与 ``status`` 分属两个问题：``status`` 说的是「Kirara 与 OneBot
    #: 实现之间的连接」，``qr_login`` 说的是「OneBot 实现与 QQ 之间的登录」。
    #: 二维码过期要去重新扫码，凭据被拒要去改 Token，混成一个状态会把两种
    #: 完全不同的处置指向同一个错误方向。
    qr_login: Optional[QRLoginSnapshot] = None


@runtime_checkable
class AdapterHealthProvider(Protocol):
    """Optional capability for adapters with a distinct connection state."""

    def get_health_snapshot(self) -> AdapterHealthSnapshot:
        """Return connection health without account identifiers or credentials."""

@runtime_checkable
class EditStateAdapter(Protocol):
    """
    编辑状态适配器接口，定义了如何设置或取消对话的编辑状态
    """

    async def set_chat_editing_state(
        self, chat_sender: ChatSender, is_editing: bool = True
    ):
        """
        设置或取消对话的编辑状态
        :param chat_sender: 对话的发送者
        :param is_editing: True 表示正在编辑，False 表示取消编辑状态
        """


@runtime_checkable
class UserProfileAdapter(Protocol):
    """
    用户资料查询适配器接口，定义了如何获取用户资料
    """

    async def query_user_profile(self, chat_sender: ChatSender) -> UserProfile:
        """
        查询用户资料
        :param chat_sender: 用户的聊天发送者信息
        :return: 用户资料
        """

@runtime_checkable
class BotProfileAdapter(Protocol):
    """
    支持获取当前适配器对应的机器人资料
    """

    async def get_bot_profile(self) -> Optional[UserProfile]:
        """
        获取机器人资料
        :return: 机器人资料
        """

class IMAdapter(ABC):
    """
    通用的 IM 适配器接口，定义了如何将不同平台的原始消息转换为 Message 对象。
    """

    llm_manager: LLMManager

    is_running: bool

    @abstractmethod
    async def convert_to_message(self, raw_message: Any) -> IMMessage:
        """
        将平台的原始消息转换为 Message 对象。
        :param raw_message: 平台的原始消息对象。
        :return: 转换后的 Message 对象。
        """

    @abstractmethod
    async def send_message(self, message: IMMessage, recipient: Any):
        """
        发送消息到 IM 平台。
        :param message: 要发送的消息对象。
        :param recipient: 接收消息的目标对象，可以是用户ID、用户对象、群组ID等，具体由各平台实现决定。
        :return: 实现可以返回 :class:`MessageSendResult`（含上游 `message_id`，
                 供随后撤回或编辑使用）。返回 ``None`` 仍然合法——既有适配器与
                 第三方实现无需改动，调用方按「拿不到 ID」处理。
        """

    @abstractmethod
    async def start(self):
        pass

    @abstractmethod
    async def stop(self):
        pass
