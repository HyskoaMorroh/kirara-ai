import asyncio
from abc import ABC, abstractmethod
from typing import Any, Dict, Literal, Optional, Protocol

from pydantic import BaseModel, Field
from typing_extensions import runtime_checkable

from kirara_ai.im.message import IMMessage
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
        ]
    ] = None
    outbox: Optional[Dict[str, int]] = None


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
        """

    @abstractmethod
    async def start(self):
        pass

    @abstractmethod
    async def stop(self):
        pass
