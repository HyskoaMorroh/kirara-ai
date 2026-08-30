"""OneBot V11 IM adapter using aiocqhttp's public ASGI interface."""

import asyncio
import hashlib
import os
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any, Optional
from urllib.parse import parse_qs

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
    MessageSendResult,
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
from kirara_ai.im.qr_login import QRLoginSnapshot, parse_qr_login_log
from kirara_ai.im.sender import ChatSender, ChatType
from kirara_ai.database import DatabaseManager
from kirara_ai.logger import HypercornLoggerWrapper, get_logger
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher

from .config import OneBotConfig
from kirara_ai.im.inbound_receipts import InboundReceiptService
from kirara_ai.im.text_render import CODE_COPY_HINT, split_for_copyable_code

from .render import paginate_onebot_text_or_truncate, render_onebot_text
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
        self._inbound_receipts: Optional[InboundReceiptService] = None
        self._mount_path: Optional[str] = None
        self._mounted_route: Any = None
        self._started = False
        self._ever_started = False
        self._connection_status = "waiting"
        self._external_login_status = "unknown"
        self._last_disconnect_reason: Optional[str] = None
        #: 持久化目录在运行期是否已不可写。由 `_outbox_counts` 每次读队列时刷新。
        self._storage_unavailable = False
        self._last_heartbeat_at: Optional[float] = None
        self._profile_cache: dict[str, UserProfile] = {}
        self._profile_cache_time: dict[str, float] = {}
        self._cache_ttl = 3600

        self.bot.on_meta_event(self._handle_meta)
        self.bot.on_message(self._handle_message)
        self.bot.on_notice(self._handle_notice)
        # 请求事件（好友申请、入群邀请）此前完全没有订阅：管理员在 QQ 里看到一个
        # 悬而未决的申请，服务端日志里一个字都没有。只记录、不自动同意。
        self.bot.on_request(self._handle_request)

    @staticmethod
    def _event_value(event: Any, key: str, default: Any = None) -> Any:
        if isinstance(event, dict):
            return event.get(key, default)
        return getattr(event, key, default)

    def _record_connection_failure(
        self, reason: str, *, status: Optional[str] = None
    ) -> None:
        """Remember why the last inbound connection attempt did not succeed.

        Only a fixed reason code is stored; upstream messages, tokens and account
        identifiers never reach this field, because it is served over the health
        API and rendered in the panel.
        """
        self._last_disconnect_reason = reason
        if status is not None and not self.connections:
            self._connection_status = status

    def _clear_connection_failure(self) -> None:
        self._last_disconnect_reason = None

    def _classify_access_token(
        self,
        headers: dict[str, str],
        query_string: bytes | str = b"",
    ) -> Optional[str]:
        """Return why the presented access token is unacceptable, or ``None``.

        ``aiocqhttp`` performs the authoritative check and answers 401/403 on its
        own; it does not report the outcome back to the adapter, so the handshake
        state model would otherwise never learn that a credential was rejected.
        This mirrors the same two header forms aiocqhttp accepts
        (``Bearer <token>`` and ``Token <token>``) plus the ``access_token``
        query parameter, and never logs or stores the token itself.

        查询参数形式必须一起读：LLOneBot 与 NapCat 都允许用
        ``?access_token=...`` 认证。只看请求头时这类连接会被记成
        ``access_token_missing``，而 aiocqhttp 实际放行了——健康面板给出的
        原因码与真实情况相反，比不给原因更糟。
        """
        expected = getattr(getattr(self, "config", None), "access_token", None)
        if not expected:
            return None
        authorization = headers.get("authorization", "")
        presented = ""
        if authorization:
            parts = authorization.split(None, 1)
            if len(parts) == 2 and parts[0].casefold() in {"bearer", "token"}:
                presented = parts[1].strip()
        if not presented:
            presented = self._query_access_token(query_string)
        if not presented:
            return "access_token_missing"
        if not secrets.compare_digest(presented, str(expected)):
            return "access_token_mismatch"
        return None

    @staticmethod
    def _query_access_token(query_string: bytes | str) -> str:
        """Extract ``access_token`` from an ASGI query string; never log it."""
        if not query_string:
            return ""
        if isinstance(query_string, bytes):
            try:
                query_string = query_string.decode("latin-1")
            except Exception:  # noqa: BLE001 - 畸形查询串按「未提供」处理
                return ""
        try:
            values = parse_qs(query_string, keep_blank_values=True)
        except Exception:  # noqa: BLE001 - 同上
            return ""
        candidates = values.get("access_token") or []
        return candidates[0].strip() if candidates else ""

    def _with_authorization_from_query(
        self, scope: dict[str, Any], headers: dict[str, str]
    ) -> dict[str, Any]:
        """Promote an already-validated query token into an ``Authorization`` header.

        只在三个条件同时成立时改写 scope：配置了令牌、请求头里没有
        ``Authorization``、查询串里带了令牌（其正确性由调用方先用
        ``_classify_access_token`` 校验过）。任一不成立就原样返回同一个对象——
        没必要为绝大多数正常连接白拷一份 scope。
        """
        expected = getattr(getattr(self, "config", None), "access_token", None)
        if not expected or headers.get("authorization"):
            return scope
        token = self._query_access_token(scope.get("query_string", b""))
        if not token:
            return scope
        promoted = dict(scope)
        promoted["headers"] = [
            *scope.get("headers", []),
            (b"authorization", f"Bearer {token}".encode("latin-1")),
        ]
        return promoted

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
                self._last_disconnect_reason = "upstream_lifecycle_disconnect"
            self.logger.warning("OneBot 连接已断开")
            return
        if meta_type in {"lifecycle", "heartbeat"}:
            now = time.monotonic()
            self.connections[self_id] = {"last_heartbeat": now}
            self._last_heartbeat_at = now
            self._connection_status = "connected"
            self._external_login_status = "upstream_reported_online"
            self._clear_connection_failure()
            if meta_type == "lifecycle" and sub_type == "connect":
                self.logger.info("OneBot 连接已建立")
            self._schedule_outbox_resume()

    #: 会被记录的通知类型 → 可读说明。
    #:
    #: 这些事件不进工作流（它们不是消息，没有回复语义），但完全丢掉是另一个极端：
    #: 「机器人被踢出群之后就再也不回话了」在日志里应当留下痕迹，否则排查时看到的
    #: 只是「消息发不出去」，找不到原因。
    _NOTICE_DESCRIPTIONS = {
        "group_recall": "群消息被撤回",
        "friend_recall": "私聊消息被撤回",
        "group_increase": "群成员增加",
        "group_decrease": "群成员减少",
        "group_ban": "群禁言状态变化",
        "group_admin": "群管理员变化",
        "group_upload": "群文件上传",
        "friend_add": "好友已添加",
        "notify": "群内提醒事件",
        "group_card": "群名片变更",
        "essence": "精华消息变化",
        "offline_file": "离线文件",
        "client_status": "其他客户端状态变化",
    }

    async def _handle_notice(self, event: Event) -> None:
        """Log notice events at a level an operator can actually find.

        通知事件不派发进工作流：它们不是消息，没有「回复」这个语义，硬塞进去会让
        每一次群成员变动都跑一遍模型。但此前这里是 ``return None``——被踢出群、
        被禁言这类会直接导致「机器人不回话」的事件完全无声，排查时只能看到
        发送失败，看不到原因。

        自身被移出群或被禁言按 warning 记（它会立刻改变可用性），其余按 info。
        不记录消息正文，只记录事件类型与作用域。
        """
        notice_type = str(self._event_value(event, "notice_type") or "unknown")
        sub_type = self._event_value(event, "sub_type")
        description = self._NOTICE_DESCRIPTIONS.get(notice_type, "未分类通知")
        group_id = self._event_value(event, "group_id")
        scope = f"group={group_id}" if group_id is not None else "c2c"

        self_id = self._event_value(event, "self_id")
        target_id = self._event_value(event, "user_id")
        affects_self = (
            self_id is not None and target_id is not None and str(self_id) == str(target_id)
        )
        detail = f"{description}（{notice_type}"
        if sub_type:
            detail += f"/{sub_type}"
        detail += f"，{scope}）"

        if affects_self and notice_type in {"group_decrease", "group_ban"}:
            # 这两类直接决定「还能不能在这个群说话」，必须比 info 更醒目。
            self.logger.warning(f"OneBot 通知影响本账号可用性：{detail}")
        else:
            self.logger.info(f"OneBot 通知：{detail}")

    async def _handle_request(self, event: Event) -> None:
        """Log friend/group invitations instead of dropping them silently.

        请求事件此前**根本没有订阅**：好友申请与入群邀请到达时没有任何记录，
        管理员在 QQ 里看到一个悬而未决的申请，而服务端日志里一个字都没有。
        这里只记录，不自动同意——自动接受入群邀请是一个安全决定，不该由框架
        代替部署者做。

        日志必须带上 `flag`：处置动作（`respond_to_friend_request` /
        `respond_to_group_request`）需要它，而运维唯一能看到事件的地方就是日志。
        只记「有一条好友申请」等于把处置能力锁死在日志里。
        """
        request_type = str(self._event_value(event, "request_type") or "unknown")
        sub_type = self._event_value(event, "sub_type")
        group_id = self._event_value(event, "group_id")
        scope = f"group={group_id}" if group_id is not None else "c2c"
        label = {"friend": "好友申请", "group": "群相关申请"}.get(request_type, "未分类申请")
        suffix = f"/{sub_type}" if sub_type else ""
        flag = self._event_value(event, "flag")
        flag_note = f"，flag={flag}" if flag else ""
        self.logger.info(
            f"OneBot {label}（{request_type}{suffix}，{scope}{flag_note}）待人工处理；"
            "框架不会自动同意。可用适配器的 approve/reject 方法处置，"
            "或在 QQ 客户端与上游 WebUI 处理。"
        )

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
            if not self.connections:
                self._last_disconnect_reason = "heartbeat_timeout"

    def get_health_snapshot(
        self, now: Optional[float] = None
    ) -> AdapterHealthSnapshot:
        now = time.monotonic() if now is None else now
        if self._started:
            # Latch the fact that the adapter has been up at least once, so a
            # later stop reports "disconnected" rather than "initializing".
            self._ever_started = True
        if self.connections:
            self._prune_stale_connections(now)

        if not self._started:
            # Distinguish "the container just came up" from "we stopped on
            # purpose": before the first successful start there is nothing to be
            # disconnected from, and reporting a failure state there is what made
            # a normal restart look broken.
            status = "disconnected" if self._ever_started else "initializing"
        elif self.connections:
            status = "connected"
        elif self._connection_status in {
            "stale",
            "disconnected",
            "credential_rejected",
            "upstream_refused",
        }:
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
        # 队列状态必须在决定 status 之前读：它同时是「存储还活着吗」的探针。
        outbox_counts = self._outbox_counts()
        disconnect_reason = None if self.connections else self._last_disconnect_reason
        if self._storage_unavailable and status in {"connected", "waiting"}:
            # 只覆盖「看起来没问题」的两个状态。凭据被拒 / 握手被拒 / 心跳超时
            # 都是用户能直接动手修的原因，不该被存储故障盖掉——那会让操作者
            # 去查磁盘，而真正要改的是 Token。
            status = "storage_unavailable"
            disconnect_reason = "data_directory_unwritable"
        return AdapterHealthSnapshot(
            status=status,
            connected_account_count=len(self.connections),
            last_heartbeat_age_seconds=heartbeat_age,
            adapter_started=self._started,
            websocket_connected=bool(self.connections),
            external_login_status=self._external_login_status,
            # A live connection means the previous failure no longer describes
            # the current state, so it must not keep showing in the panel.
            last_disconnect_reason=disconnect_reason,
            outbox=outbox_counts,
            qr_login=self._read_qr_login_snapshot(),
        )

    def _read_qr_login_snapshot(self) -> Optional[QRLoginSnapshot]:
        """Fold the upstream implementation's log into a QR login snapshot.

        二维码由 LLOneBot / PMHQ 在自己的容器里生成，Kirara 不参与生成也不代理。
        但只要那份日志挂到了本容器可读的位置，就能把「这张码还能扫吗」变成一个
        可回答的问题——否则操作者只能翻 scrollback 猜哪一行是最新的，
        而扫到旧图正是「二维码总是过期」这个报障的根因。

        未配置路径时返回 ``None``（不是空快照）：「没开这个功能」和
        「开了但读不到任何事件」是两件事。读取失败同样返回 ``None``——
        观测不能成为新的失败点。
        """
        path = getattr(getattr(self, "config", None), "qr_login_log_path", None)
        if not path:
            return None
        tail_bytes = int(
            getattr(self.config, "qr_login_log_tail_bytes", 256 * 1024) or 256 * 1024
        )
        try:
            with open(path, "rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - tail_bytes))
                raw = handle.read()
        except OSError as exc:
            self.logger.debug(f"OneBot 实现日志不可读，跳过扫码状态：{exc}")
            return None
        try:
            text = raw.decode("utf-8", errors="replace")
            # 首行可能被 seek 截断成半行，丢掉它而不是拿它去匹配。
            lines = text.split("\n")
            if size > tail_bytes and lines:
                lines = lines[1:]
            return parse_qr_login_log(lines)
        except Exception as exc:  # noqa: BLE001 - 观测失败不得影响健康快照
            self.logger.debug(f"扫码状态解析失败，已跳过：{exc}")
            return None

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
            self._storage_unavailable = False
            return None
        try:
            counts = self._ensure_outbox().status_counts()
        except Exception as exc:
            # 队列读不出来意味着持久化目录本身出了问题（只读重挂、卷写满、
            # 数据库文件被删）。此前这里只记一条 warning 就返回 None，
            # 于是健康快照照旧报 `connected`——链路确实连着，但每一条要落库的
            # 投递都在失败。需求 18.1 要求「数据目录挂载错误」是一个独立可见的
            # 状态，这里就是它在运行期唯一能被观测到的位置。
            self._storage_unavailable = True
            self.logger.warning(f"OneBot 投递队列状态读取失败：{exc}")
            return None
        self._storage_unavailable = False
        return counts

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

    def _inbound_event_key(self, event: Event) -> Optional[str]:
        """Build a stable per-event identity for inbound dedup.

        OneBot V11 events carry ``message_id`` for messages; combined with
        ``self_id`` that is unique per account. Falling back to
        ``(self_id, user_id, time)`` covers implementations that omit
        ``message_id``. Returning ``None`` means "cannot identify this event",
        and the caller then processes it without dedup rather than dropping it —
        losing a message is worse than a rare duplicate.
        """
        self_id = self._event_value(event, "self_id")
        message_id = self._event_value(event, "message_id")
        if message_id is not None:
            return f"{self_id or '-'}:{message_id}"[:128]
        user_id = self._event_value(event, "user_id")
        timestamp = self._event_value(event, "time")
        if user_id is not None and timestamp is not None:
            return f"{self_id or '-'}:{user_id}:{timestamp}"[:128]
        return None

    def _ensure_inbound_receipts(self) -> Optional[InboundReceiptService]:
        """Return the dedup service, or ``None`` when no database is wired."""
        existing = getattr(self, "_inbound_receipts", None)
        if existing is not None:
            return existing
        database = getattr(self, "database_manager", None)
        if database is None:
            return None
        instance = getattr(self, "adapter_instance", None) or getattr(
            self, "name", None
        ) or "default"
        service = InboundReceiptService(
            database,
            channel="onebot",
            adapter_instance=str(instance),
        )
        self._inbound_receipts = service
        return service

    def _is_self_originated(self, event: Event) -> bool:
        """Whether this event is the bot's own outgoing message echoed back.

        某些 OneBot 实现（LLOneBot、NapCat 的 ``reportSelfMessage``）会把机器人
        自己发出的消息也作为事件推回来。不过滤的话机器人会回复自己，并且很容易
        形成自问自答的循环。入站去重收据挡不住这一类：回声的 ``message_id``
        与入站消息不同，在去重表看来是一条全新事件。

        两条判据：``post_type == "message_sent"`` 是 V11 对自身消息的专用类型；
        ``user_id == self_id`` 覆盖只改了 ``user_id`` 而仍用 ``message`` 类型的实现。
        """
        post_type = self._event_value(event, "post_type")
        if str(post_type or "") == "message_sent":
            return True
        self_id = self._event_value(event, "self_id")
        user_id = self._event_value(event, "user_id")
        if self_id is None or user_id is None:
            return False
        return str(self_id) == str(user_id)

    async def _handle_message(self, event: Event) -> None:
        # 自身消息回声必须在去重之前丢掉：它的 message_id 与入站消息不同，
        # 去重收据看不出这是回声，机器人会开始回复自己。
        if self._is_self_originated(event):
            self.logger.debug("OneBot 自身消息回声已忽略")
            return
        # 入站去重：反向 WebSocket 在投递中途断开时，上游无法知道我们是否已处理，
        # 因此重投是它唯一安全的选择——去重必须由本侧完成。缺少这一层时，
        # 重连后同一事件会把整条工作流再跑一遍（重复计费 + 重复回复）。
        receipts = self._ensure_inbound_receipts()
        event_key = self._inbound_event_key(event) if receipts is not None else None
        if receipts is not None and event_key is not None:
            if not receipts.claim(event_key, self._inbound_chat_key(event)):
                self.logger.debug("OneBot 重复事件已忽略")
                return
        try:
            message = await self.convert_to_message(event)
            await self.dispatcher.dispatch(self, message, require_agent=True)
        except BaseException:
            # 处理失败时把收据放回可重领状态，让上游重投仍能被处理一次。
            if receipts is not None and event_key is not None:
                receipts.retry(event_key)
            raise
        if receipts is not None and event_key is not None:
            receipts.complete(event_key)

    def _inbound_chat_key(self, event: Event) -> str:
        group_id = self._event_value(event, "group_id")
        if group_id is not None:
            return f"group:{group_id}"
        return f"c2c:{self._event_value(event, 'user_id', '')}"

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
            # `mface`（市场表情）也带 url，但它可以只有 summary 而没有可下载资源；
            # 因此下载失败时必须回落到文本占位，而不是像纯媒体段那样整段跳过。
            is_media = msg_type in {"image", "file", "record", "video"}
            is_optional_media = msg_type == "mface"
            if is_media or is_optional_media:
                element = None
                try:
                    media_data = await self._load_inbound_media(data)
                    if media_data is not None:
                        element = await self._create_inbound_media_element(
                            str(msg_type), media_data, data
                        )
                except Exception as exc:
                    self.logger.warning(
                        f"OneBot 入站媒体处理失败，已跳过该媒体段 type={msg_type}: {exc}"
                    )
                    if is_media:
                        continue
                if element is None:
                    if is_media:
                        continue
                    element = create_message_element(
                        str(msg_type), data, self.logger, self_id=event_self_id
                    )
            else:
                element = create_message_element(
                    str(msg_type), data, self.logger, self_id=event_self_id
                )
                if msg_type == "forward":
                    element = await self._expand_forward_element(
                        data, element, self_id=event_self_id
                    )
            if element is not None:
                elements.append(element)
        raw_message = dict(event) if isinstance(event, dict) else None
        return IMMessage(sender=sender, message_elements=elements, raw_message=raw_message)

    async def _expand_forward_element(
        self,
        data: dict[str, Any],
        placeholder: Any,
        *,
        self_id: Optional[str],
    ) -> Any:
        """Replace a forward placeholder with its real content when enabled.

        占位（`[合并转发：<id>]`）在「不静默丢消息」这一层是对的，但它把内容
        也一起丢了：用户转发一段对话过来问「这里说的对吗」，模型收到的只有一个 ID。

        默认关闭——展开有真实的上游调用成本。失败时**退回占位**：展开是增强
        而不是前提，`get_forward_msg` 权限不足、ID 过期或上游未实现时，
        绝不能让整条消息失败。
        """
        if not getattr(self.config, "expand_forward_messages", False):
            return placeholder
        forward_id = data.get("id")
        if forward_id is None:
            return placeholder
        try:
            rendered = await self._render_forward(
                str(forward_id),
                depth=1,
                seen={str(forward_id)},
                self_id=self_id,
            )
        except Exception as exc:  # noqa: BLE001 - 展开失败只损失内容，不损失消息
            self.logger.warning(
                f"OneBot 合并转发展开失败，已退回占位 id={forward_id}: {exc}"
            )
            return placeholder
        if not rendered:
            return placeholder
        return TextMessage(rendered)

    async def _render_forward(
        self,
        forward_id: str,
        *,
        depth: int,
        seen: set[str],
        self_id: Optional[str],
    ) -> str:
        """Fetch one forward and render it as readable indented text."""
        response = await self._call_action(
            "get_forward_msg", id=forward_id, self_id=self_id
        )
        nodes = self._forward_nodes(response)
        max_nodes = int(getattr(self.config, "forward_max_nodes", 20) or 20)
        max_depth = int(getattr(self.config, "forward_max_depth", 2) or 2)

        lines: list[str] = [f"[合并转发：{forward_id}]"]
        for node in nodes[:max_nodes]:
            node_data = node.get("data") if isinstance(node, dict) else None
            if isinstance(node, dict) and node.get("type") == "forward":
                nested_id = str((node_data or {}).get("id") or "")
                # 深度与重复都要挡：自引用是最短的无限递归，
                # 而每一层都是一次真实的上游调用。
                if not nested_id or depth >= max_depth or nested_id in seen:
                    lines.append(f"  [合并转发：{nested_id or '未知'}]（未展开）")
                    continue
                seen.add(nested_id)
                nested = await self._render_forward(
                    nested_id, depth=depth + 1, seen=seen, self_id=self_id
                )
                lines.extend(f"  {line}" for line in nested.splitlines())
                continue
            if not isinstance(node_data, dict):
                continue
            nickname = str(node_data.get("nickname") or "").strip()
            text = self._forward_node_text(node_data.get("content"))
            if not text:
                continue
            lines.append(f"  {nickname}：{text}" if nickname else f"  {text}")

        omitted = max(0, len(nodes) - max_nodes)
        if omitted:
            # 静默截断会让人以为转发里只有这么几条。
            lines.append(f"  …（已省略 {omitted} 条）")
        return "\n".join(lines)

    @staticmethod
    def _forward_nodes(response: Any) -> list[Any]:
        """Return the node list from a `get_forward_msg` response.

        OneBot 实现之间放的位置不一致：有的顶层 `messages`，有的裹在 `data` 里，
        字段名也可能是 `message`。三处都看。
        """
        for source in (response, (response or {}).get("data") if isinstance(response, dict) else None):
            if not isinstance(source, dict):
                continue
            for key in ("messages", "message", "nodes"):
                value = source.get(key)
                if isinstance(value, list):
                    return value
        return []

    @staticmethod
    def _forward_node_text(content: Any) -> str:
        """Flatten one node's segments into readable text.

        转发里的媒体**不下载**——那会把一次消息转换变成一串下载。
        给出可读标记即可：模型至少知道「这里有一张图」。
        """
        if isinstance(content, str):
            return content.strip()
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        markers = {
            "image": "[图片]",
            "record": "[语音]",
            "video": "[视频]",
            "file": "[文件]",
            "face": "[表情]",
            "mface": "[表情]",
        }
        for segment in content:
            if not isinstance(segment, dict):
                continue
            segment_type = str(segment.get("type") or "")
            segment_data = segment.get("data") or {}
            if segment_type == "text":
                text = str(segment_data.get("text") or "").strip()
                if text:
                    parts.append(text)
            elif segment_type in markers:
                parts.append(markers[segment_type])
        return " ".join(parts).strip()

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
                for page, is_code in self._text_pages(text):
                    segments = [*pending_special, MessageSegment.text(page)]
                    batches.append(segments)
                    pending_special = []
                    if is_code:
                        # 代码已单独成条；紧随其后给出一句复制指引，
                        # 而不是画一个 QQ 根本点不动的「复制」按钮。
                        batches.append([MessageSegment.text(CODE_COPY_HINT)])
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

    def _text_pages(self, text: str) -> list[tuple[str, bool]]:
        """Paginate one text element, optionally isolating code into its own page.

        返回 ``(页面文本, 是否为代码页)``。代码单独成条后，用户长按整条消息
        即可拿到干净代码；关闭 ``isolate_code_messages`` 时退回原有的混排分页，
        既有部署的观感完全不变。

        超出页数或总字节预算时**截断并提示**，而不是让 ``ValueError`` 穿出
        ``send_message``——后者的结果是用户什么都收不到，比收到被截断的内容更糟。
        """
        # 兼容以 object.__new__ 构造的轻量适配器实例（既有测试与部分插件如此使用）：
        # 缺少 config 时按默认行为处理，而不是抛 AttributeError。
        if not getattr(getattr(self, "config", None), "isolate_code_messages", True):
            pages, _ = paginate_onebot_text_or_truncate(text)
            return [(page, False) for page in pages]

        pages: list[tuple[str, bool]] = []
        for part in split_for_copyable_code(text):
            part_pages, truncated = paginate_onebot_text_or_truncate(part.text)
            for page in part_pages:
                pages.append((page, part.is_code))
            if truncated:
                self.logger.warning(
                    "OneBot 回复超过分页预算，已截断后发送；请检查上游回复长度。"
                )
        if not pages:
            # split_for_copyable_code 会丢弃空代码块；若整段都被丢弃，
            # 仍按原路径分页，避免把一条本该发出的消息静默吞掉。
            fallback, _ = paginate_onebot_text_or_truncate(text)
            return [(page, False) for page in fallback]
        return pages


    def _send_pacing(self):
        """本适配器的节流参数；配置缺失时回落到默认（开启）。

        缺失时**不是**关闭：嵌入式用法或旧配置同样面对 QQ 风控，
        默认关掉等于让它们踩一次才知道。
        """
        # `getattr(self, "config", None)`：适配器在部分单测里用
        # `object.__new__` 构造，没有 `config` 属性。直接 `self.config`
        # 会把「没配置」变成 AttributeError，而节流是可观测性/防风控功能，
        # 它绝不该让一次发送失败。
        builder = getattr(getattr(self, "config", None), "build_send_pacing", None)
        if callable(builder):
            return builder()
        from kirara_ai.plugins.im_onebot_adapter.pacing import SendPacing

        return SendPacing()

    @staticmethod
    def _page_text_length(segments: list[MessageSegment]) -> int:
        """一页里的可见文本长度，用于按长度计算节流等待。

        只数文本：图片与语音本身不构成「刷屏文字」，把它们的 base64
        长度算进去会得到几十秒的荒谬等待。
        """
        total = 0
        for segment in segments:
            data = getattr(segment, "data", None)
            if isinstance(data, dict):
                text = data.get("text")
                if isinstance(text, str):
                    total += len(text)
        return total

    async def _send_message_unlocked(
        self,
        batches: list[list[MessageSegment]],
        recipient: ChatSender,
    ) -> list[str]:
        """Send message elements in order; API failures intentionally propagate.

        返回每一页的上游 `message_id`（上游未回报的页不产生条目）：
        撤回接口一直可用，缺的一直是「撤谁」。
        """
        message_ids: list[str] = []
        pacing = self._send_pacing()
        for page_index, segments in enumerate(batches):
            # 页与页之间主动等待，避免被判定为刷屏（需求 11）。
            # 与失败重试退避是两件事：那个是「发失败了再试」，这个是
            # 「发成功了也要等」，方向相反、不能互相替代。
            await pacing.wait_before_page(
                page_index, text_length=self._page_text_length(segments)
            )
            response = await self._send_segments(recipient, segments)
            message_id = self._response_message_id(response)
            if message_id is not None:
                message_ids.append(message_id)
        return message_ids

    @staticmethod
    def _response_message_id(response: Any) -> Optional[str]:
        """Extract the upstream message id from one action response.

        OneBot 实现之间有差异：有的把 `message_id` 放在顶层，有的裹在 `data` 里。
        两处都看；取不到就返回 ``None``——绝不编一个（0 或空串会让调用方拿它去
        撤回，然后撤到别人的消息上，或者静默失败）。
        """
        if not isinstance(response, dict):
            return None
        for source in (response, response.get("data")):
            if not isinstance(source, dict):
                continue
            value = source.get("message_id")
            if value is None or isinstance(value, bool):
                continue
            text = str(value).strip()
            if text:
                return text
        return None

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
        batches: list[list[MessageSegment]],
        recipient: ChatSender,
        delivery_id: Optional[str],
        results: list[OneBotDeliveryResult],
    ) -> int:
        if delivery_id is None:
            delivery_id = uuid.uuid4().hex
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

        pacing = self._send_pacing()
        for page_index, page_id in enumerate(page_ids):
            # 走 outbox 时同样要节流：风控与投递路径无关，而部署有没有配数据库
            # 决定走哪条——只修一条等于同一个账号换个部署形态又会被限制发言。
            await pacing.wait_before_page(
                page_index, text_length=self._page_text_length(batches[page_index])
            )
            result = await outbox.deliver(page_id)
            results.append(result)
            if result.status != "accepted":
                raise self._delivery_error(result)
        return sum(max(0, item.attempt_count - 1) for item in results)

    async def send_message(
        self,
        message: IMMessage,
        recipient: ChatSender,
        delivery_id: Optional[str] = None,
    ) -> MessageSendResult:
        """Serialize pages for one recipient while keeping other chats concurrent.

        返回 :class:`MessageSendResult`：其中的 `message_ids` 让调用方能撤回或
        引用刚发出的消息。此前这里返回 ``None``，于是「发一条提示、30 秒后撤回」
        做不到——撤回接口有，可没人知道要撤谁。
        """
        message.record_delivery_stage("formatting_started", adapter="onebot")
        batches = await self._render_message_batches(message)
        message.record_delivery_stage(
            "formatting_completed",
            adapter="onebot",
            segment_count=len(batches),
        )
        if not batches:
            # 空内容不发送，也就没有任何 ID。返回空结果而不是 None：
            # 调用方无需为「发了但没内容」单独分支。
            return MessageSendResult(delivery_id=delivery_id, page_count=0)

        if delivery_id is None:
            delivery_id = getattr(message, "_onebot_delivery_id", None)
        if delivery_id is None:
            delivery_id = uuid.uuid4().hex
            setattr(message, "_onebot_delivery_id", delivery_id)

        message.record_delivery_stage("send_started", adapter="onebot")
        results: list[OneBotDeliveryResult] = []
        retry_count = 0
        message_ids: list[str] = []
        try:
            if getattr(self, "database_manager", None) is not None:
                retry_count = await self._send_via_outbox(
                    batches,
                    recipient,
                    delivery_id,
                    results,
                )
                message_ids = [
                    message_id
                    for message_id in (
                        self._response_message_id(item.response) for item in results
                    )
                    if message_id is not None
                ]
            else:
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
                        message_ids = await self._send_message_unlocked(
                            batches, recipient
                        )
                finally:
                    state.users -= 1
                    if state.users == 0 and not state.lock.locked():
                        locks.pop(key, None)
        except Exception as exc:
            retry_count = sum(max(0, item.attempt_count - 1) for item in results)
            message.record_delivery_stage(
                "send_failed",
                adapter="onebot",
                error_type=type(exc).__name__,
                retry_count=retry_count,
            )
            raise
        message.record_delivery_stage(
            "send_succeeded",
            adapter="onebot",
            retry_count=retry_count,
        )
        return MessageSendResult(
            message_ids=tuple(message_ids),
            delivery_id=delivery_id,
            page_count=len(batches),
        )

    async def recall_message(
        self, message_id: int | str, delay: float = 0, self_id: Optional[str] = None
    ) -> dict[str, Any]:
        """Recall a OneBot message, optionally after a caller-requested delay."""
        if delay < 0:
            raise ValueError("撤回延迟不能为负数")
        if delay:
            await asyncio.sleep(delay)
        if self_id is None:
            # 结果必须接住。当前 `_action_self_id()` 在无 recipient 时只有两种结局
            # （多账号抛错、其余返回 None），所以赋值与丢弃等价；但把返回值丢掉会让
            # 这行读起来像「解析了目标账号」而实际没有，一旦该函数将来学会解析
            # 单账号，丢弃就变成静默路由到错误账号。
            self_id = self._action_self_id()
        return await self._call_action("delete_msg", message_id=int(message_id), self_id=self_id)

    async def mute_user(
        self,
        group_id: int | str,
        user_id: int | str,
        duration: int,
        self_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Mute a group member for the OneBot duration in seconds."""
        if duration < 0:
            raise ValueError("禁言时长不能为负数")
        if self_id is None:
            self_id = self._action_self_id()
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
            self_id = self._action_self_id()
        return await self._call_action(
            "set_group_kick",
            group_id=int(group_id),
            user_id=int(user_id),
            reject_add_request=False,
            self_id=self_id,
        )

    #: `set_group_add_request` 认得的两种子类型。
    #:
    #: `add` 是「别人申请加入我在的群」，`invite` 是「别人邀请我进群」。
    #: 传错不会报错——上游匹配不到那条请求，返回成功但什么都没做，
    #: 那是最难排查的一类失败。因此这里不设默认值，且发出前先校验。
    _GROUP_REQUEST_SUB_TYPES = frozenset({"add", "invite"})

    @staticmethod
    def _request_flag(flag: str) -> str:
        text = str(flag).strip()
        if not text:
            # 空 flag 匹配不到任何申请，发出去只是浪费一次往返。
            raise ValueError("处理申请需要非空的 flag")
        return text

    def _request_self_id(self, self_id: Optional[str]) -> Optional[str]:
        if self_id is not None:
            return str(self_id)
        # 同意一个入群邀请是有副作用的动作：用错账号同意等于让另一个机器人进了群。
        # `_action_self_id` 在多账号且未指定时会抛错，这正是想要的行为。
        return self._action_self_id()

    async def respond_to_friend_request(
        self,
        flag: str,
        *,
        approve: bool,
        remark: str = "",
        self_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Approve or reject one friend request.

        `_handle_request` 只记录、不自动同意——那条边界不变：自动接受申请是一个
        安全决定，不该由框架代替部署者做。这里补的是「部署者可以决定」的能力：
        协议本来就有 `set_friend_add_request`，此前本项目没有任何调用点，
        于是处置只能回到手机上做。
        """
        return await self._call_action(
            "set_friend_add_request",
            flag=self._request_flag(flag),
            approve=bool(approve),
            remark=str(remark or ""),
            self_id=self._request_self_id(self_id),
        )

    async def approve_friend_request(
        self, flag: str, *, remark: str = "", self_id: Optional[str] = None
    ) -> dict[str, Any]:
        return await self.respond_to_friend_request(
            flag, approve=True, remark=remark, self_id=self_id
        )

    async def reject_friend_request(
        self, flag: str, *, self_id: Optional[str] = None
    ) -> dict[str, Any]:
        return await self.respond_to_friend_request(
            flag, approve=False, self_id=self_id
        )

    async def respond_to_group_request(
        self,
        flag: str,
        *,
        sub_type: str,
        approve: bool,
        reason: str = "",
        self_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Approve or reject one group join request or invitation."""
        normalized = str(sub_type).strip().lower()
        if normalized not in self._GROUP_REQUEST_SUB_TYPES:
            raise ValueError(
                f"未知的群申请 sub_type: {sub_type!r}；"
                f"只接受 {sorted(self._GROUP_REQUEST_SUB_TYPES)}"
            )
        return await self._call_action(
            "set_group_add_request",
            flag=self._request_flag(flag),
            sub_type=normalized,
            approve=bool(approve),
            reason=str(reason or ""),
            self_id=self._request_self_id(self_id),
        )

    async def approve_group_request(
        self, flag: str, *, sub_type: str, self_id: Optional[str] = None
    ) -> dict[str, Any]:
        return await self.respond_to_group_request(
            flag, sub_type=sub_type, approve=True, self_id=self_id
        )

    async def reject_group_request(
        self,
        flag: str,
        *,
        sub_type: str,
        reason: str = "",
        self_id: Optional[str] = None,
    ) -> dict[str, Any]:
        return await self.respond_to_group_request(
            flag, sub_type=sub_type, approve=False, reason=reason, self_id=self_id
        )

    async def asgi(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        """Validate OneBot reverse WebSocket metadata before aiocqhttp handles it."""
        if scope["type"] != "websocket":
            await self.bot.asgi(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").casefold(): value.decode("latin-1").strip()
            for key, value in scope.get("headers", [])
        }
        # 头值也要 casefold：LLOneBot / LuckyLilliaBot 发的是 'Universal'
        # （首字母大写），而被包装的 aiocqhttp 自己是 .lower() 之后再比。
        # 只 casefold 头名会让这道预检比被包装者更严格，把最常见的 OneBot
        # 实现以 4400 拒掉；对方每 3 秒重连一次，形成死循环。
        role = headers.get("x-client-role", "").casefold()
        if role not in {"event", "api", "universal"}:
            # The upstream did reach us; recording why it was refused is the
            # difference between "QQ never dialed in" and "QQ dialed in with a
            # handshake we reject".
            self._record_connection_failure(
                "invalid_client_role", status="upstream_refused"
            )
            self.logger.warning("OneBot 反向 WebSocket 握手缺少或使用了不支持的客户端角色")
            await send(
                {
                    "type": "websocket.close",
                    "code": 4400,
                    "reason": "Invalid OneBot reverse WebSocket headers",
                }
            )
            return
        if role in {"api", "universal"} and not headers.get("x-self-id"):
            self._record_connection_failure("missing_self_id", status="upstream_refused")
            self.logger.warning("OneBot 反向 WebSocket 握手缺少账号标识")
            await send(
                {
                    "type": "websocket.close",
                    "code": 4400,
                    "reason": "Invalid OneBot reverse WebSocket headers",
                }
            )
            return

        # aiocqhttp validates the access token itself and answers 401/403 without
        # telling the adapter, so classify it here from the same header it reads.
        token_reason = self._classify_access_token(
            headers, scope.get("query_string", b"")
        )
        if token_reason is not None:
            self._record_connection_failure(token_reason, status="credential_rejected")
            self.logger.warning("OneBot 反向 WebSocket 凭据校验未通过")
        else:
            # 查询串认证要真的能连上，而不只是「分类器认为没问题」。
            #
            # 被包装的 aiocqhttp 只读 `Authorization` 头
            # （`aiocqhttp/__init__.py`：匹配 `Token|Bearer <token>`，匹配不上直接
            # 401），所以用 `?access_token=...` 的实现会被它拒掉，而上面的分类器
            # 认为凭据没问题、不记录任何原因码——面板上既不是「已连接」也没有失败
            # 原因，比不给原因更糟。LLOneBot 与 NapCat 都允许这种配置。
            #
            # 因此在令牌**已校验通过**且请求头**确实缺失**时，把它补成一个标准
            # Authorization 头。已有头时绝不覆盖：「头里是错的、查询串里是对的」
            # 属于矛盾配置，必须以头为准而被拒绝，否则一个配错的部署会意外可用，
            # 换个客户端又突然不可用。补进去的值不写日志。
            scope = self._with_authorization_from_query(scope, headers)

        disconnected = False

        async def receive_with_disconnect_state() -> Any:
            nonlocal disconnected
            try:
                message = await receive()
            except asyncio.CancelledError as cancellation:
                # Starlette queues websocket.disconnect before cancelling the
                # ASGI task. Give that queued close message a bounded chance
                # to drain, while preserving unrelated cancellation.
                try:
                    message = await asyncio.wait_for(receive(), timeout=0.1)
                except asyncio.CancelledError:
                    raise cancellation
                except Exception:
                    raise cancellation
                if message.get("type") != "websocket.disconnect":
                    raise cancellation
            if message.get("type") == "websocket.disconnect":
                disconnected = True
            return message

        try:
            await self.bot.asgi(scope, receive_with_disconnect_state, send)
        except asyncio.CancelledError:
            if disconnected:
                return
            raise

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
            receipts = self._ensure_inbound_receipts()
            if receipts is not None:
                # 上次进程中断时留在 processing 的事件重新开放认领，
                # 既不丢事件，也不会因此产生第二条回复。
                reopened = receipts.recover_on_startup()
                if reopened:
                    self.logger.info(
                        f"OneBot 重新开放 {reopened} 个未完成的入站事件"
                    )
        self._started = True
        self._ever_started = True
        self._connection_status = "waiting"
        self._clear_connection_failure()

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
        """Best-effort typing indicator via the OneBot extension action.

        ``set_input_status`` 不在 OneBot V11 标准里，但 LLOneBot 与 NapCat 都实现了它。
        此前这里直接记一条「OneBot 不支持输入状态」的日志——对这两个最常用的实现
        来说那句话是错的，而且白白丢掉了一个能让长回复期间界面不显得卡死的提示。

        标准里没有的动作必须容错：不支持的实现会回 ``ApiNotAvailable`` 或
        ``ActionFailed``，那只应让这一个提示消失，绝不能影响这条消息的发送。
        私聊之外没有对应语义，直接跳过。
        """
        if chat_sender.chat_type is not ChatType.C2C:
            return
        try:
            await self._call_action(
                "set_input_status",
                user_id=int(chat_sender.user_id),
                # 1 = 正在输入，0 = 取消。取值取自 LLOneBot / NapCat 的实现。
                event_type=1 if is_editing else 0,
            )
        except (ValueError, TypeError):
            # 非数字 user_id：这是调用方的数据问题，但同样不该中断发送。
            self.logger.debug(f"OneBot 输入状态跳过：user_id 非数字 {chat_sender.user_id}")
        except Exception as exc:
            self.logger.debug(
                f"OneBot 实现不支持 set_input_status，已跳过输入状态：{exc}"
            )
