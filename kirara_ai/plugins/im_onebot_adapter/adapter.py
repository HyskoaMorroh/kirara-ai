"""OneBot V11 IM adapter using aiocqhttp's public ASGI interface."""

import asyncio
import hashlib
import os
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from aiocqhttp import CQHttp, Event, MessageSegment
from hypercorn.asyncio import worker_serve
from hypercorn.config import Config
from hypercorn.utils import wrap_app

from kirara_ai.im.adapter import (
    AdapterHealthProvider,
    AdapterHealthSnapshot,
    BotProfileAdapter,
    EditStateAdapter,
    IMActionTimeoutError,
    IMAdapter,
    UserProfileAdapter,
)
from kirara_ai.im.message import (
    AtElement,
    EmojiMessage,
    FileMessage,
    ImageMessage,
    IMMessage,
    JsonMessage,
    MentionElement,
    ReplyElement,
    TextMessage,
    VideoMessage,
    VoiceMessage,
)
from kirara_ai.im.profile import Gender, UserProfile
from kirara_ai.im.sender import ChatSender, ChatType
from kirara_ai.database import DatabaseManager
from kirara_ai.logger import HypercornLoggerWrapper, get_logger
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher

from .config import OneBotConfig
from .render import paginate_onebot_text, render_onebot_text
from .outbox import OneBotDeliveryResult, OneBotOutboxService
from .utils.media import (
    decode_inline_media,
    download_public_media,
    validate_public_media_url,
)
from .utils.message import create_message_element


class OneBotActionTimeoutError(IMActionTimeoutError):
    """A OneBot action timed out and was not retried."""


@dataclass
class _RecipientLockState:
    lock: asyncio.Lock
    users: int = 0


class OneBotAdapter(
    IMAdapter,
    UserProfileAdapter,
    BotProfileAdapter,
    EditStateAdapter,
    AdapterHealthProvider,
):
    """OneBot V11 adapter with reverse WebSocket and paginated QQ replies."""

    dispatcher: WorkflowDispatcher
    web_server: WebServer
    database_manager: DatabaseManager

    def __init__(self, config: OneBotConfig):
        self.config = config
        self.bot = CQHttp(access_token=config.access_token)
        self.logger = get_logger("OneBot")
        self.is_running = False
        self.self_id: Optional[str] = None
        self.connections: dict[str, dict[str, Any]] = {}
        self._recipient_locks: dict[tuple[str, str, str], _RecipientLockState] = {}
        self._server_task: Optional[asyncio.Task[Any]] = None
        self._server_shutdown_event: Optional[asyncio.Event] = None
        self._server_ready_event: Optional[asyncio.Event] = None
        self._server_sockets: Any = None
        self._standalone_port: Optional[int] = None
        self._heartbeat_task: Optional[asyncio.Task[Any]] = None
        self._outbox_resume_task: Optional[asyncio.Task[Any]] = None
        self._outbox: Optional[OneBotOutboxService] = None
        self._mount_path: Optional[str] = None
        self._mounted_route: Any = None
        self._started = False
        self._connection_status = "waiting"
        self._external_login_status = "unknown"
        self._last_heartbeat_at: Optional[float] = None
        self._profile_cache: dict[str, UserProfile] = {}
        self._profile_cache_time: dict[str, float] = {}
        self._cache_ttl = 3600

        self.bot.on_meta_event(self._handle_meta)
        self.bot.on_message(self._handle_message)
        self.bot.on_notice(self._handle_notice)

    @staticmethod
    def _event_value(event: Any, key: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    async def _handle_meta(self, event: Event) -> None:
        self_id = self._event_value(event, "self_id")
        if self_id is None:
            return
        self_id = str(self_id)
        self.self_id = self_id
        meta_type = self._event_value(event, "meta_event_type")
        sub_type = self._event_value(event, "sub_type")
        if meta_type == "lifecycle" and sub_type == "disconnect":
            self.connections.pop(self_id, None)
            self._connection_status = (
                "connected" if self.connections else "disconnected"
            )
            if not self.connections:
                self._external_login_status = "upstream_reported_offline"
            self.logger.warning("OneBot 连接已断开")
            return
        if meta_type in {"lifecycle", "heartbeat"}:
            now = time.monotonic()
            self.connections[self_id] = {"last_heartbeat": now}
            self._last_heartbeat_at = now
            self._connection_status = "connected"
            self._external_login_status = "upstream_reported_online"
            if meta_type == "lifecycle" and sub_type == "connect":
                self.logger.info("OneBot 连接已建立")
            self._schedule_outbox_resume()

    async def _handle_notice(self, event: Event) -> None:
        return None

    def _prune_stale_connections(self, now: Optional[float] = None) -> None:
        """Remove connections that exceeded the configured heartbeat timeout."""
        if now is None:
            now = time.monotonic()
        timeout = self.config.heartbeat_timeout_seconds
        stale_ids = [
            self_id
            for self_id, state in self.connections.items()
            if now - float(state.get("last_heartbeat", 0)) > timeout
        ]
        for self_id in stale_ids:
            heartbeat = float(self.connections[self_id].get("last_heartbeat", 0))
            if heartbeat > 0:
                self._last_heartbeat_at = heartbeat
            self.connections.pop(self_id, None)
            self.logger.warning("OneBot 连接心跳超时")
        if stale_ids:
            self._connection_status = "connected" if self.connections else "stale"

    def get_health_snapshot(
        self, now: Optional[float] = None
    ) -> AdapterHealthSnapshot:
        now = time.monotonic() if now is None else now
        if self.connections:
            self._prune_stale_connections(now)

        if not self._started:
            status = "disconnected"
        elif self.connections:
            status = "connected"
        elif self._connection_status in {"stale", "disconnected"}:
            status = self._connection_status
        else:
            status = "waiting"

        heartbeat_times = [
            float(state.get("last_heartbeat", 0))
            for state in self.connections.values()
            if float(state.get("last_heartbeat", 0)) > 0
        ]
        last_heartbeat = max(heartbeat_times, default=self._last_heartbeat_at)
        heartbeat_age = (
            max(0.0, now - last_heartbeat) if last_heartbeat is not None else None
        )
        return AdapterHealthSnapshot(
            status=status,
            connected_account_count=len(self.connections),
            last_heartbeat_age_seconds=heartbeat_age,
            adapter_started=self._started,
            websocket_connected=bool(self.connections),
            external_login_status=self._external_login_status,
            outbox=self._outbox_counts(),
        )

    def _ensure_outbox(self) -> OneBotOutboxService:
        if self._outbox is not None:
            return self._outbox
        database = getattr(self, "database_manager", None)
        if database is None:
            raise RuntimeError("OneBot persistent outbox requires DatabaseManager")
        self._outbox = OneBotOutboxService(
            database,
            self._call_action,
            max_attempts=self.config.outbox_max_attempts,
            retry_delay_seconds=self.config.outbox_retry_delay_seconds,
        )
        return self._outbox

    def _outbox_counts(self) -> Optional[dict[str, int]]:
        database = getattr(self, "database_manager", None)
        if database is None:
            return None
        try:
            return self._ensure_outbox().status_counts()
        except Exception as exc:
            self.logger.warning(f"OneBot 投递队列状态读取失败：{exc}")
            return None

    def _schedule_outbox_resume(self) -> None:
        if getattr(self, "database_manager", None) is None:
            return
        if self._outbox_resume_task is not None and not self._outbox_resume_task.done():
            return
        self._outbox_resume_task = asyncio.create_task(self._resume_outbox())

    async def _resume_outbox(self) -> None:
        results = await self._ensure_outbox().resume_pending()
        failed = sum(result.status != "accepted" for result in results)
        if failed:
            self.logger.warning(
                f"OneBot 恢复投递完成，{failed} 个发送单元仍需人工核对"
            )

    async def _call_action(self, action: str, **params: Any) -> dict[str, Any]:
        self_id = params.pop("self_id", None)
        if self_id is not None:
            params["self_id"] = str(self_id)
        timeout = getattr(
            getattr(self, "config", None), "action_timeout_seconds", 30.0
        )
        try:
            return await asyncio.wait_for(
                self.bot.call_action(action, **params), timeout=timeout
            )
        except asyncio.TimeoutError as exc:
            self.logger.error(
                f"OneBot 操作 {action} 在 {timeout:g} 秒后超时；未自动重试"
            )
            raise OneBotActionTimeoutError(
                f"OneBot action {action} timed out after {timeout:g} seconds"
            ) from exc

    async def _monitor_heartbeats(self) -> None:
        try:
            while True:
                await asyncio.sleep(self.config.heartbeat_interval)
                self._prune_stale_connections()
        except asyncio.CancelledError:
            raise

    async def _handle_message(self, event: Event) -> None:
        message = await self.convert_to_message(event)
        await self.dispatcher.dispatch(self, message)

    async def convert_to_message(self, event: Event) -> IMMessage:
        event_self_id = self._event_value(event, "self_id")
        event_self_id = str(event_self_id) if event_self_id is not None else None
        user_id = str(self._event_value(event, "user_id", ""))
        group_id = self._event_value(event, "group_id")
        sender_info = self._event_value(event, "sender", {}) or {}
        sender_metadata = dict(sender_info)
        if event_self_id is not None:
            sender_metadata["onebot_self_id"] = event_self_id
        if group_id is not None:
            sender = ChatSender.from_group_chat(
                user_id=user_id,
                group_id=str(group_id),
                display_name=str(sender_info.get("card") or sender_info.get("nickname") or user_id),
                metadata=sender_metadata,
            )
        else:
            sender = ChatSender.from_c2c_chat(
                user_id=user_id,
                display_name=str(sender_info.get("nickname") or user_id),
                metadata=sender_metadata,
            )

        elements = []
        for segment in self._event_value(event, "message", []) or []:
            msg_type = self._event_value(segment, "type")
            data = self._event_value(segment, "data", {}) or {}
            if msg_type in {"image", "file", "record", "video"}:
                try:
                    media_data = await self._load_inbound_media(data)
                    if media_data is None:
                        continue
                    element = await self._create_inbound_media_element(
                        str(msg_type), media_data, data
                    )
                except Exception as exc:
                    self.logger.warning(
                        f"OneBot 入站媒体处理失败，已跳过该媒体段 type={msg_type}: {exc}"
                    )
                    continue
            else:
                element = create_message_element(
                    str(msg_type), data, self.logger, self_id=event_self_id
                )
            if element is not None:
                elements.append(element)
        raw_message = dict(event) if isinstance(event, dict) else None
        return IMMessage(sender=sender, message_elements=elements, raw_message=raw_message)

    async def _load_inbound_media(self, data: dict[str, Any]) -> Optional[bytes]:
        source: Any = data.get("url") or data.get("path")
        if source is None:
            file_value = data.get("file")
            if isinstance(file_value, str) and file_value.startswith(
                ("http://", "https://", "data:", "base64://")
            ):
                source = file_value
        if source is None:
            source = data.get("data")
        if source is None:
            return None
        if isinstance(source, bytes):
            if len(source) > self.config.inbound_media_max_bytes:
                raise ValueError(
                    "OneBot 入站媒体超过大小上限"
                )
            return source
        if not isinstance(source, str):
            raise ValueError("OneBot 入站媒体来源格式无效")
        source = source.strip()
        if source.startswith(("data:", "base64://")):
            return decode_inline_media(
                source, max_bytes=self.config.inbound_media_max_bytes
            )
        validate_public_media_url(source)
        return await self._download_inbound_media(source)

    async def _download_inbound_media(self, url: str) -> bytes:
        return await download_public_media(
            url,
            max_bytes=self.config.inbound_media_max_bytes,
            timeout_seconds=self.config.inbound_media_timeout_seconds,
        )

    async def _create_inbound_media_element(
        self, msg_type: str, media_data: bytes, original_data: dict[str, Any]
    ) -> Any:
        # MediaMessage registers bytes synchronously in its constructor.
        return await asyncio.to_thread(
            create_message_element,
            msg_type,
            original_data,
            self.logger,
            media_data=media_data,
        )

    async def _media_url(self, element: Any) -> str:
        return await element.get_url()

    async def _to_segment(self, element: Any) -> Optional[MessageSegment]:
        if isinstance(element, AtElement):
            return MessageSegment.at(str(element.user_id))
        if isinstance(element, MentionElement):
            return MessageSegment.at(str(element.target.user_id))
        if isinstance(element, ReplyElement):
            return MessageSegment.reply(str(element.message_id))
        if isinstance(element, EmojiMessage):
            return MessageSegment.face(str(element.face_id))
        if isinstance(element, JsonMessage):
            return MessageSegment.json(element.data)
        if isinstance(element, ImageMessage):
            return MessageSegment.image(await self._media_url(element))
        if isinstance(element, VoiceMessage):
            return MessageSegment.record(await self._media_url(element))
        if isinstance(element, VideoMessage):
            return MessageSegment.video(await self._media_url(element))
        if isinstance(element, FileMessage):
            url = await self._media_url(element)
            source = element.path or url
            filename = os.path.basename(source.rstrip("/")) or element.format or "附件"
            return MessageSegment.text(f"文件：{filename}\n链接：{url}")
        return None

    def _segment_action(
        self, recipient: ChatSender, segments: list[MessageSegment]
    ) -> tuple[str, dict[str, Any]]:
        self_id = self._action_self_id(recipient)
        if recipient.chat_type == ChatType.GROUP:
            if recipient.group_id is None:
                raise ValueError("群聊发送缺少 group_id")
            return (
                "send_group_msg",
                {
                    "group_id": int(recipient.group_id),
                    "message": segments,
                    "self_id": self_id,
                },
            )
        if recipient.chat_type == ChatType.C2C:
            return (
                "send_private_msg",
                {
                    "user_id": int(recipient.user_id),
                    "message": segments,
                    "self_id": self_id,
                },
            )
        raise ValueError(f"不支持的 OneBot 聊天类型：{recipient.chat_type}")

    async def _send_segments(self, recipient: ChatSender, segments: list[MessageSegment]) -> dict[str, Any]:
        action, params = self._segment_action(recipient, segments)
        return await self._call_action(action, **params)

    def _action_self_id(self, recipient: Optional[ChatSender] = None) -> Optional[str]:
        if recipient is not None:
            value = recipient.raw_metadata.get("onebot_self_id")
            if value is not None:
                return str(value)
        if len(getattr(self, "connections", {})) > 1:
            raise ValueError("OneBot 多账号场景缺少目标 self_id，拒绝将操作路由到错误账号")
        return None

    def _recipient_key(self, recipient: ChatSender) -> tuple[str, str, str]:
        self_id = self._action_self_id(recipient) or ""
        if recipient.chat_type == ChatType.GROUP:
            if recipient.group_id is None:
                raise ValueError("群聊发送缺少 group_id")
            return (self_id, "group", str(recipient.group_id))
        if recipient.chat_type == ChatType.C2C:
            return (self_id, "c2c", str(recipient.user_id))
        raise ValueError(f"不支持的 OneBot 聊天类型：{recipient.chat_type}")

    async def _render_message_batches(
        self, message: IMMessage
    ) -> list[list[MessageSegment]]:
        """Render ordered OneBot action payloads without sending them."""
        batches: list[list[MessageSegment]] = []
        pending_special: list[MessageSegment] = []
        for element in message.message_elements:
            if isinstance(element, TextMessage):
                text = render_onebot_text(element.text)
                if not text:
                    continue
                for page in paginate_onebot_text(text):
                    segments = [*pending_special, MessageSegment.text(page)]
                    batches.append(segments)
                    pending_special = []
                continue

            try:
                segment = await self._to_segment(element)
            except Exception as exc:
                self.logger.warning(f"OneBot 出站媒体转换失败，已跳过该媒体段：{exc}")
                continue
            if segment is not None:
                if isinstance(element, (ImageMessage, VoiceMessage, VideoMessage, FileMessage)):
                    if pending_special:
                        batches.append(pending_special)
                        pending_special = []
                    batches.append([segment])
                else:
                    pending_special.append(segment)

        if pending_special:
            batches.append(pending_special)
        return batches

    async def _send_message_unlocked(
        self, message: IMMessage, recipient: ChatSender
    ) -> None:
        """Send message elements in order; API failures intentionally propagate."""
        for segments in await self._render_message_batches(message):
            await self._send_segments(recipient, segments)

    @staticmethod
    def _page_delivery_id(logical_delivery_id: str, page_index: int) -> str:
        value = f"{logical_delivery_id}:{page_index}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    @staticmethod
    def _delivery_error(result: OneBotDeliveryResult) -> BaseException:
        if result.error is not None:
            return result.error
        message = result.error_message or f"OneBot delivery {result.status}"
        if result.status == "ambiguous":
            return OneBotActionTimeoutError(message)
        return RuntimeError(message)

    async def _send_via_outbox(
        self,
        message: IMMessage,
        recipient: ChatSender,
        delivery_id: Optional[str],
    ) -> None:
        batches = await self._render_message_batches(message)
        if not batches:
            return
        if delivery_id is None:
            delivery_id = getattr(message, "_onebot_delivery_id", None)
        if delivery_id is None:
            delivery_id = uuid.uuid4().hex
            setattr(message, "_onebot_delivery_id", delivery_id)
        if len(delivery_id) > 64:
            raise ValueError("OneBot delivery_id 不能超过 64 个字符")

        outbox = self._ensure_outbox()
        recipient_key = ":".join(self._recipient_key(recipient))
        page_ids: list[str] = []
        for page_index, segments in enumerate(batches):
            action, params = self._segment_action(recipient, segments)
            page_id = self._page_delivery_id(delivery_id, page_index)
            page_ids.append(page_id)
            outbox.enqueue(
                page_id,
                recipient_key,
                action,
                params,
                logical_delivery_id=delivery_id,
                page_index=page_index,
                page_count=len(batches),
            )

        for page_id in page_ids:
            result = await outbox.deliver(page_id)
            if result.status != "accepted":
                raise self._delivery_error(result)

    async def send_message(
        self,
        message: IMMessage,
        recipient: ChatSender,
        delivery_id: Optional[str] = None,
    ) -> None:
        """Serialize pages for one recipient while keeping other chats concurrent."""
        if getattr(self, "database_manager", None) is not None:
            await self._send_via_outbox(message, recipient, delivery_id)
            return
        locks = getattr(self, "_recipient_locks", None)
        if locks is None:
            locks = self._recipient_locks = {}
        key = self._recipient_key(recipient)
        state = locks.get(key)
        if state is None:
            state = locks[key] = _RecipientLockState(lock=asyncio.Lock())
        state.users += 1
        try:
            async with state.lock:
                await self._send_message_unlocked(message, recipient)
        finally:
            state.users -= 1
            if state.users == 0 and not state.lock.locked():
                locks.pop(key, None)

    async def recall_message(
        self, message_id: int | str, delay: float = 0, self_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Recall a OneBot message, optionally after a caller-requested delay."""
        if delay < 0:
            raise ValueError("撤回延迟不能为负数")
        if delay:
            await asyncio.sleep(delay)
        if self_id is None:
            self._action_self_id()
        return await self._call_action("delete_msg", message_id=int(message_id), self_id=self_id)

    async def mute_user(
        self, group_id: int | str, user_id: int | str, duration: int
        , self_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Mute a group member for the OneBot duration in seconds."""
        if duration < 0:
            raise ValueError("禁言时长不能为负数")
        if self_id is None:
            self._action_self_id()
        return await self._call_action(
            "set_group_ban",
            group_id=int(group_id),
            user_id=int(user_id),
            duration=int(duration),
            self_id=self_id,
        )

    async def unmute_user(
        self,
        group_id: int | str,
        user_id: int | str,
        self_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Remove a group member mute."""
        return await self.mute_user(group_id, user_id, 0, self_id=self_id)

    async def kick_user(
        self, group_id: int | str, user_id: int | str, self_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Remove a member from a group without blocking future requests."""
        if self_id is None:
            self._action_self_id()
        return await self._call_action(
            "set_group_kick",
            group_id=int(group_id),
            user_id=int(user_id),
            reject_add_request=False,
            self_id=self_id,
        )

    async def asgi(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Validate OneBot reverse WebSocket metadata before aiocqhttp handles it."""
        if scope["type"] == "websocket":
            headers = {
                key.decode("latin-1").casefold(): value.decode("latin-1").strip()
                for key, value in scope.get("headers", [])
            }
            role = headers.get("x-client-role")
            if role not in {"event", "api", "universal"} or (
                role in {"api", "universal"} and not headers.get("x-self-id")
            ):
                await send(
                    {
                        "type": "websocket.close",
                        "code": 4400,
                        "reason": "Invalid OneBot reverse WebSocket headers",
                    }
                )
                return
        await self.bot.asgi(scope, receive, send)

    async def _start_standalone_server(self) -> None:
        config = Config()
        config.bind = [f"{self.config.host}:{self.config.port}"]
        config._log = config.logger_class(config)
        ready_event = asyncio.Event()

        class ReadyLoggerWrapper(HypercornLoggerWrapper):
            def info(self, message: str, *args: Any, **kwargs: Any) -> None:
                super().info(message, *args, **kwargs)
                if "Running on http://" in message or "Running on https://" in message:
                    ready_event.set()

        config._log.access_logger = ReadyLoggerWrapper(self.logger)
        config._log.error_logger = ReadyLoggerWrapper(self.logger)
        sockets = config.create_sockets()
        self._server_sockets = sockets
        self._server_shutdown_event = asyncio.Event()
        self._server_ready_event = ready_event
        self._standalone_port = next(
            (
                sock.getsockname()[1]
                for sock in [*sockets.insecure_sockets, *sockets.secure_sockets]
                if hasattr(sock.getsockname(), "__len__") and len(sock.getsockname()) > 1
            ),
            self.config.port,
        )
        self._server_task = asyncio.create_task(
            worker_serve(
                wrap_app(self.asgi, config.wsgi_max_body_size, mode="asgi"),
                config,
                sockets=sockets,
                shutdown_trigger=self._server_shutdown_event.wait,
            )
        )
        ready_wait_task = asyncio.create_task(ready_event.wait())
        try:
            done, _ = await asyncio.wait(
                {ready_wait_task, self._server_task},
                timeout=config.startup_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError(
                    f"OneBot 独立服务启动超时：{self.config.host}:{self.config.port}"
                )
            if self._server_task in done:
                await self._server_task
                raise RuntimeError("OneBot 独立服务在完成启动前意外退出")
            await ready_wait_task
            if self._server_task.done():
                await self._server_task
        except BaseException:
            if self._server_shutdown_event is not None:
                self._server_shutdown_event.set()
            if self._server_task is not None:
                if not self._server_task.done():
                    try:
                        await asyncio.wait_for(self._server_task, timeout=5.0)
                    except BaseException:
                        self._server_task.cancel()
                await asyncio.gather(self._server_task, return_exceptions=True)
            self._close_server_sockets()
            self._server_task = None
            self._server_sockets = None
            self._server_shutdown_event = None
            self._server_ready_event = None
            self._standalone_port = None
            raise
        finally:
            if not ready_wait_task.done():
                ready_wait_task.cancel()
                await asyncio.gather(ready_wait_task, return_exceptions=True)
        self.logger.info(f"OneBot 独立服务已启动：{self.config.host}:{self._standalone_port}")

    def _close_server_sockets(self) -> None:
        if self._server_sockets is None:
            return
        sockets = [
            *self._server_sockets.insecure_sockets,
            *self._server_sockets.secure_sockets,
            *self._server_sockets.quic_sockets,
        ]
        for sock in sockets:
            try:
                sock.close()
            except OSError:
                pass

    async def start(self) -> None:
        if self._started:
            return
        if self.config.host is not None and self.config.port is not None:
            self.logger.warning("OneBot 正在使用旧版独立服务模式，建议迁移到 websocket_url")
            await self._start_standalone_server()
        else:
            self._mount_path = self.config.websocket_path
            routes = self.web_server.app.routes
            if any(getattr(route, "path", None) == self._mount_path for route in routes):
                raise RuntimeError(
                    f"OneBot WebSocket 路径已被占用：{self._mount_path}；"
                    "请为每个 OneBot 实例配置不同的 websocket_url"
                )
            self.web_server.mount_app(self._mount_path, self.asgi)
            self._mounted_route = next(
                (
                    route
                    for route in reversed(routes)
                    if getattr(route, "path", None) == self._mount_path
                ),
                None,
            )
            if self._mounted_route is None:
                raise RuntimeError(
                    f"OneBot WebSocket 路径挂载失败：{self._mount_path}"
                )
            self.logger.info(f"OneBot WebSocket 已挂载：{self._mount_path}/ws")
        self._heartbeat_task = asyncio.create_task(self._monitor_heartbeats())
        if getattr(self, "database_manager", None) is not None:
            self._ensure_outbox().recover_on_startup()
        self._started = True
        self._connection_status = "waiting"

    async def stop(self) -> None:
        if self._outbox_resume_task and not self._outbox_resume_task.done():
            self._outbox_resume_task.cancel()
            await asyncio.gather(self._outbox_resume_task, return_exceptions=True)
        self._outbox_resume_task = None
        if self._heartbeat_task and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
        self._heartbeat_task = None
        if self._server_task is not None:
            if self._server_shutdown_event is not None:
                self._server_shutdown_event.set()
            try:
                if self._server_task.done():
                    await self._server_task
                else:
                    await asyncio.wait_for(self._server_task, timeout=5.0)
            except asyncio.TimeoutError:
                self._server_task.cancel()
                await asyncio.gather(self._server_task, return_exceptions=True)
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                self.logger.error(f"OneBot 独立服务停止失败：{exc}")
        self._close_server_sockets()
        self._server_task = None
        self._server_sockets = None
        self._server_shutdown_event = None
        self._server_ready_event = None
        self._standalone_port = None
        if self._mounted_route is not None:
            routes = self.web_server.app.routes
            if self._mounted_route in routes:
                routes.remove(self._mounted_route)
        self._mounted_route = None
        self._mount_path = None
        self.connections.clear()
        self._started = False
        self._connection_status = "disconnected"
        self._external_login_status = "unknown"
        self._last_heartbeat_at = None

    async def query_user_profile(self, chat_sender: ChatSender) -> UserProfile:
        user_id = str(chat_sender.user_id)
        group_id = chat_sender.group_id if chat_sender.chat_type == ChatType.GROUP else None
        self_id = self._action_self_id(chat_sender)
        cache_key = f"{self_id or ''}:{group_id}:{user_id}" if group_id else f"{self_id or ''}:{user_id}"
        now = asyncio.get_running_loop().time()
        if cache_key in self._profile_cache and now - self._profile_cache_time[cache_key] < self._cache_ttl:
            return self._profile_cache[cache_key]
        try:
            if group_id:
                info = await self._call_action(
                    "get_group_member_info", group_id=int(group_id), user_id=int(user_id), no_cache=True,
                    self_id=self_id,
                )
                profile = self._profile_from_info(info, user_id)
            else:
                info = await self._call_action(
                    "get_stranger_info", user_id=int(user_id), no_cache=True, self_id=self_id
                )
                profile = self._profile_from_info(info, user_id)
        except Exception as exc:
            self.logger.warning(f"查询 OneBot 用户资料失败 {chat_sender}: {exc}")
            profile = UserProfile(user_id=user_id, username=user_id, display_name=chat_sender.display_name)
        self._profile_cache[cache_key] = profile
        self._profile_cache_time[cache_key] = now
        return profile

    @staticmethod
    def _profile_from_info(info: dict[str, Any], fallback_id: str) -> UserProfile:
        sex = info.get("sex")
        gender = Gender.MALE if sex == "male" else Gender.FEMALE if sex == "female" else Gender.UNKNOWN
        nickname = str(info.get("card") or info.get("nickname") or fallback_id)
        return UserProfile(
            user_id=str(info.get("user_id", fallback_id)),
            username=nickname,
            display_name=nickname,
            full_name=str(info.get("nickname", nickname)),
            gender=gender,
            age=info.get("age"),
            level=info.get("level"),
            avatar_url=info.get("avatar"),
        )

    async def get_bot_profile(self, self_id: Optional[str] = None) -> Optional[UserProfile]:
        try:
            if self_id is None:
                self._action_self_id()
            info = await self._call_action("get_login_info", self_id=self_id)
        except Exception:
            return UserProfile(user_id="unknown", username="未连接", display_name="未连接")
        self.self_id = str(info.get("user_id", self.self_id or "unknown"))
        nickname = str(info.get("nickname", self.self_id))
        return UserProfile(
            user_id=self.self_id,
            username=nickname,
            display_name=nickname,
            avatar_url=f"https://q1.qlogo.cn/g?b=qq&nk={self.self_id}&s=640",
        )

    async def set_chat_editing_state(self, chat_sender: ChatSender, is_editing: bool = True) -> None:
        self.logger.debug(f"OneBot 不支持输入状态：{chat_sender}, editing={is_editing}")
