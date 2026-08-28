import asyncio
import base64
import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any, List, Optional

import ymbotpy as botpy
import ymbotpy.message
from pydantic import BaseModel, ConfigDict, Field
from ymbotpy.errors import ServerError
from ymbotpy.http import Route as BotpyRoute
from ymbotpy.types.message import Media as BotpyMedia

from kirara_ai.database import DatabaseManager
from kirara_ai.im.adapter import (
    AdapterHealthSnapshot,
    BotProfileAdapter,
    IMActionTimeoutError,
    IMAdapter,
)
from kirara_ai.im.message import (FileMessage, ImageMessage, IMMessage, MentionElement, MessageElement, TextMessage,
                                  VideoElement, VoiceMessage)
from kirara_ai.im.profile import UserProfile
from kirara_ai.im.inbound_receipts import InboundReceiptService
from kirara_ai.im.sender import ChatSender, ChatType
from kirara_ai.im.text_render import render_plain_text, split_structured_text
from kirara_ai.logger import get_logger
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.dispatch import WorkflowDispatcher

from .outbox import (
    QQBotDeliveryResult,
    QQBotOutboxService,
    QQBotRetryableError,
)
from .utils import URL_PATTERN

WEBHOOK_URL_PREFIX = "/im/webhook/qqbot"


def make_webhook_url():
    return f"{WEBHOOK_URL_PREFIX}/{str(uuid.uuid4())[:8]}/"


def auto_generate_webhook_url(s: dict):
    s["readOnly"] = True
    s["default"] = make_webhook_url()
    s["textType"] = True


class QQBotConfig(BaseModel):
    """
    QQBot 配置文件模型。
    """
    app_id: str = Field(description="机器人的 App ID。", repr=False)
    app_secret: str = Field(
        title="App Secret",
        description="机器人的 App Secret。",
        repr=False,
    )
    token: str = Field(
        title="Token",
        description="机器人令牌，用于调用 QQ 机器人的 OpenAPI。",
        repr=False,
    )
    sandbox: bool = Field(
        title="沙盒环境", description="是否为沙盒环境，通常只有正式发布的机器人才会关闭此选项。", default=False)
    webhook_url: str = Field(
        title="Webhook 回调 URL", description="供 QQ 机器人回调的 URL，由系统自动生成，无法修改。",
        default_factory=make_webhook_url,
        json_schema_extra=auto_generate_webhook_url
    )
    send_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        title="QQ API 操作超时",
        description="等待 QQ 消息发送或媒体上传返回的最长秒数；消息结果未知时不会自动重发。",
    )
    outbox_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        title="明确失败重试上限",
        description="仅在 QQ 明确返回可重试的服务端拒绝时有限重试。",
    )
    outbox_retry_delay_seconds: float = Field(
        default=1.0,
        ge=0,
        le=60,
        title="投递重试基础间隔",
        description="明确瞬态失败的指数退避基础秒数。",
    )
    model_config = ConfigDict(extra="allow")

    def __repr__(self):
        return (
            "QQBotConfig(app_id=<redacted>, app_secret=<redacted>, "
            f"token=<redacted>, sandbox={self.sandbox}, webhook_url=<redacted>)"
        )

    def __str__(self):
        return repr(self)


async def patched_post_file(
    self,
    file_type: int,
    file_data: bytes,
    openid: Optional[str] = None,
    group_openid: Optional[str] = None
) -> BotpyMedia:
    """
    重写 post_file 方法，添加文件类型参数。
    """
    payload = {
        "file_type": file_type,
        "file_data": base64.b64encode(file_data).decode('utf-8'),
        "srv_send_msg": False
    }
    if openid:
        route = BotpyRoute("POST", "/v2/users/{openid}/files", openid=openid)
    elif group_openid:
        route = BotpyRoute(
            "POST", "/v2/groups/{group_openid}/files", group_openid=group_openid)
    else:
        raise ValueError("openid 和 group_openid 不能同时为空")
    return await self._http.request(route, json=payload)


class QQBotDeliveryAmbiguousError(IMActionTimeoutError):
    """A QQ message may have been accepted, so automatic replay is unsafe."""


@dataclass(frozen=True)
class _QQBotSendUnit:
    action: str
    params: dict[str, Any]
    media_file_type: Optional[int] = None
    media_data: Optional[bytes] = None


class QQBotAdapter(botpy.WebHookClient, IMAdapter, BotProfileAdapter):
    """
    QQBot Adapter，包含 QQBot Bot 的所有逻辑。
    """

    dispatcher: WorkflowDispatcher
    web_server: WebServer
    database_manager: DatabaseManager
    _loop: asyncio.AbstractEventLoop

    def __init__(self, config: QQBotConfig):
        self.config = config
        self.is_sandbox = config.sandbox
        self.logger = get_logger("QQBot-Adapter")
        super().__init__(
            timeout=5,
            is_sandbox=self.is_sandbox,
            bot_log=True,
            ext_handlers=True,
        )
        self.loop = self._loop
        self.user = None
        self.robot = None
        self._mount_path = None
        self._mounted_route = None
        self._started = False
        self._http_open = False
        self._outbox: Optional[QQBotOutboxService] = None
        self._inbound_receipts: Optional[InboundReceiptService] = None
        self._outbox_resume_task: Optional[asyncio.Task[Any]] = None

    def get_health_snapshot(self) -> AdapterHealthSnapshot:
        connected = self._started and self.user is not None
        return AdapterHealthSnapshot(
            status=("connected" if connected else "waiting" if self._started else "disconnected"),
            connected_account_count=1 if connected else 0,
            adapter_started=self._started,
            outbox=self._outbox_counts(),
        )

    def _ensure_outbox(self) -> QQBotOutboxService:
        outbox = getattr(self, "_outbox", None)
        if outbox is not None:
            return outbox
        database = getattr(self, "database_manager", None)
        if database is None:
            raise RuntimeError("QQBot persistent outbox requires DatabaseManager")
        self._outbox = QQBotOutboxService(
            database,
            self._send_outbox_message,
            media_uploader=self._upload_outbox_media,
            max_attempts=self.config.outbox_max_attempts,
            retry_delay_seconds=self.config.outbox_retry_delay_seconds,
        )
        return self._outbox

    def _outbox_counts(self) -> Optional[dict[str, int]]:
        if getattr(self, "database_manager", None) is None:
            return None
        try:
            return self._ensure_outbox().status_counts()
        except Exception as exc:
            self.logger.warning(f"QQBot 投递队列状态读取失败：{exc}")
            return None

    def _schedule_outbox_resume(self) -> None:
        if getattr(self, "database_manager", None) is None:
            return
        task = getattr(self, "_outbox_resume_task", None)
        if task is not None and not task.done():
            return
        self._outbox_resume_task = asyncio.create_task(self._resume_outbox())

    async def _resume_outbox(self) -> None:
        results = await self._ensure_outbox().resume_pending()
        failed = sum(result.status != "accepted" for result in results)
        if failed:
            self.logger.warning(
                f"QQBot 恢复投递完成，{failed} 个发送单元仍需人工核对"
            )

    async def _send_outbox_message(self, params: dict[str, Any]) -> Any:
        payload = dict(params)
        action = str(payload.pop("_action"))
        method = getattr(self.api, action)
        try:
            return await asyncio.wait_for(
                method(**payload),
                timeout=self.config.send_timeout_seconds,
            )
        except ServerError as exc:
            raise QQBotRetryableError(str(exc)) from exc

    async def _upload_outbox_media(self, params: dict[str, Any]) -> Any:
        payload = dict(params)
        payload.pop("_action", None)
        upload_args: dict[str, Any] = {
            "file_type": int(payload["file_type"]),
            "file_data": payload["file_data"],
        }
        if "openid" in payload:
            upload_args["openid"] = payload["openid"]
        elif "group_openid" in payload:
            upload_args["group_openid"] = payload["group_openid"]
        else:
            raise ValueError("QQBot 媒体上传缺少接收方")
        try:
            return await asyncio.wait_for(
                patched_post_file(self.api, **upload_args),
                timeout=self.config.send_timeout_seconds,
            )
        except ServerError as exc:
            raise QQBotRetryableError(str(exc)) from exc

    async def convert_to_message(self, raw_message: ymbotpy.message.BaseMessage) -> IMMessage:
        if isinstance(raw_message, ymbotpy.message.GroupMessage):
            assert raw_message.author.member_openid is not None
            assert raw_message.group_openid is not None
            sender = ChatSender.from_group_chat(
                raw_message.author.member_openid, raw_message.group_openid, 'QQ 用户')
        elif isinstance(raw_message, ymbotpy.message.C2CMessage):
            sender = ChatSender.from_c2c_chat(
                raw_message.author.user_openid, 'QQ 用户')
        else:
            raise ValueError(f"不支持的消息类型: {type(raw_message)}")

        raw_dict = {items: str(getattr(raw_message, items))
                    for items in raw_message.__slots__ if not items.startswith("_")}
        sender.raw_metadata = {
            "message_id": raw_message.id,
            "message_seq": raw_message.msg_seq,
            "timestamp": raw_message.timestamp,
        }
        elements: List[MessageElement] = []
        if raw_message.content.strip():
            elements.append(TextMessage(text=raw_message.content.lstrip()))
        for attachment in raw_message.attachments:
            if attachment.content_type.startswith('image/'):
                elements.append(
                    ImageMessage(
                        url=attachment.url,
                        format=attachment.content_type.removeprefix('image/')
                    )
                )
            elif attachment.content_type.startswith('audio'):
                elements.append(
                    VoiceMessage(
                        url=attachment.url,
                        format=attachment.filename.split('.')[-1]
                    )
                )
        return IMMessage(sender=sender, message_elements=elements, raw_message=raw_dict)

    def _recipient_params(self, recipient: ChatSender) -> tuple[str, dict[str, str]]:
        if recipient.chat_type == ChatType.C2C:
            return (
                f"c2c:{recipient.user_id}",
                {"_action": "post_c2c_message", "openid": recipient.user_id},
            )
        if recipient.chat_type == ChatType.GROUP:
            if recipient.group_id is None:
                raise ValueError("QQBot 群聊发送缺少 group_id")
            return (
                f"group:{recipient.group_id}",
                {
                    "_action": "post_group_message",
                    "group_openid": recipient.group_id,
                },
            )
        raise ValueError(f"不支持的消息类型: {recipient.chat_type}")

    async def _render_send_units(
        self,
        message: IMMessage,
        recipient: ChatSender,
    ) -> tuple[str, list[_QQBotSendUnit]]:
        if not recipient.raw_metadata or not recipient.raw_metadata.get("message_id"):
            raise ValueError("Unable to retrieve QQBot reply message_id from metadata")
        msg_id = str(recipient.raw_metadata["message_id"])
        recipient_key, base_params = self._recipient_params(recipient)
        units: list[_QQBotSendUnit] = []
        current_text = ""
        url_replaced = False

        def replace_url_dots(text: str) -> str:
            nonlocal url_replaced

            def replace_dots(match):
                nonlocal url_replaced
                url_replaced = True
                return match.group(0).replace(".", "。")

            return URL_PATTERN.sub(replace_dots, text)

        def append_text(text: str) -> None:
            if not text:
                return
            rendered = replace_url_dots(render_plain_text(text))
            for page in split_structured_text(rendered, max_bytes=3800):
                params: dict[str, Any] = {
                    **base_params,
                    "msg_id": msg_id,
                    "content": page,
                }
                units.append(_QQBotSendUnit(str(base_params["_action"]), params))

        for element in message.message_elements:
            if isinstance(element, TextMessage):
                current_text += element.text
                append_text(current_text)
                current_text = ""
                continue
            if isinstance(element, MentionElement):
                current_text += (
                    f'<qqbot-at-user id="{element.target.user_id}" />'
                )
                continue
            if isinstance(element, (ImageMessage, VoiceMessage, VideoElement, FileMessage)):
                append_text(current_text)
                current_text = ""
                file_type = (
                    1
                    if isinstance(element, ImageMessage)
                    else 3
                    if isinstance(element, VoiceMessage)
                    else 2
                    if isinstance(element, VideoElement)
                    else 4
                )
                params = {
                    **base_params,
                    "msg_id": msg_id,
                    "msg_type": 7,
                }
                units.append(
                    _QQBotSendUnit(
                        str(base_params["_action"]),
                        params,
                        media_file_type=file_type,
                        media_data=await element.get_data(),
                    )
                )

        append_text(current_text)
        if url_replaced:
            append_text("（URL 中的句点已替换为句号以避免屏蔽）")
        for msg_seq, unit in enumerate(units, start=1):
            unit.params["msg_seq"] = msg_seq
        return recipient_key, units

    @staticmethod
    def _unit_delivery_id(logical_delivery_id: str, unit_index: int) -> str:
        value = f"{logical_delivery_id}:{unit_index}".encode("utf-8")
        return hashlib.sha256(value).hexdigest()

    def _implicit_delivery_id(
        self,
        recipient_key: str,
        units: list[_QQBotSendUnit],
    ) -> str:
        fingerprints = []
        for unit in units:
            fingerprints.append(
                {
                    "action": unit.action,
                    "params": unit.params,
                    "media_file_type": unit.media_file_type,
                    "media_sha256": (
                        hashlib.sha256(unit.media_data).hexdigest()
                        if unit.media_data is not None
                        else None
                    ),
                }
            )
        payload = {
            "adapter_instance": str(getattr(self, "adapter_instance", "")),
            "recipient_key": recipient_key,
            "units": fingerprints,
        }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _delivery_error(result: QQBotDeliveryResult) -> BaseException:
        if result.error is not None:
            return result.error
        message = result.error_message or f"QQBot delivery {result.status}"
        if result.status == "ambiguous":
            return QQBotDeliveryAmbiguousError(message)
        return RuntimeError(message)

    async def send_message(
        self,
        message: IMMessage,
        recipient: ChatSender,
        delivery_id: Optional[str] = None,
    ) -> None:
        """Persist every ordered QQ send unit before making network calls."""
        message.record_delivery_stage("formatting_started", adapter="qqbot")
        recipient_key, units = await self._render_send_units(message, recipient)
        message.record_delivery_stage(
            "formatting_completed",
            adapter="qqbot",
            segment_count=len(units),
        )
        if not units:
            return
        if delivery_id is None:
            delivery_id = getattr(message, "_qqbot_delivery_id", None)
        if delivery_id is None:
            delivery_id = self._implicit_delivery_id(recipient_key, units)
            setattr(message, "_qqbot_delivery_id", delivery_id)
        if len(delivery_id) > 64:
            raise ValueError("QQBot delivery_id 不能超过 64 个字符")

        outbox = self._ensure_outbox()
        unit_ids: list[str] = []
        for unit_index, unit in enumerate(units):
            unit_id = self._unit_delivery_id(delivery_id, unit_index)
            unit_ids.append(unit_id)
            outbox.enqueue(
                unit_id,
                recipient_key,
                unit.action,
                unit.params,
                logical_delivery_id=delivery_id,
                media_file_type=unit.media_file_type,
                media_data=unit.media_data,
            )

        message.record_delivery_stage("send_started", adapter="qqbot")
        results: list[QQBotDeliveryResult] = []
        try:
            for unit_id in unit_ids:
                result = await outbox.deliver(unit_id)
                results.append(result)
                if result.status != "accepted":
                    raise self._delivery_error(result)
        except Exception as exc:
            message.record_delivery_stage(
                "send_failed",
                adapter="qqbot",
                error_type=type(exc).__name__,
                retry_count=sum(
                    max(0, item.attempt_count - 1)
                    + max(0, item.upload_attempt_count - 1)
                    for item in results
                ),
            )
            raise
        message.record_delivery_stage(
            "send_succeeded",
            adapter="qqbot",
            retry_count=sum(
                max(0, item.attempt_count - 1)
                + max(0, item.upload_attempt_count - 1)
                for item in results
            ),
        )

    def _ensure_inbound_receipts(self) -> Optional[InboundReceiptService]:
        """Return the inbound dedup service, or ``None`` without a database."""
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
            channel="qqbot",
            adapter_instance=str(instance),
        )
        self._inbound_receipts = service
        return service

    @staticmethod
    def _inbound_event_key(message: Any) -> Optional[str]:
        """Identify one inbound QQ message.

        QQ's webhook payload carries a per-message ``id``. Returning ``None``
        means the event cannot be identified; the caller then processes it
        without dedup, because losing a message is worse than a rare duplicate.
        """
        message_id = getattr(message, "id", None)
        if message_id:
            return str(message_id)[:128]
        return None

    async def _dispatch_inbound(
        self,
        message: Any,
        im_message: IMMessage,
        chat_key: str,
    ) -> None:
        """Dispatch one inbound QQ message exactly once.

        QQ 的 webhook 在网关重连或我们回 5xx 时会重投同一条消息；没有收据的话
        整条工作流会再跑一遍（重复计费 + 重复回复）。去重必须由本侧完成。
        """
        receipts = self._ensure_inbound_receipts()
        event_key = self._inbound_event_key(message) if receipts is not None else None
        if receipts is not None and event_key is not None:
            if not receipts.claim(event_key, chat_key):
                self.logger.debug("QQ 重复事件已忽略")
                return
        try:
            await self.dispatcher.dispatch(self, im_message, require_agent=True)
        except BaseException:
            if receipts is not None and event_key is not None:
                receipts.retry(event_key)
            raise
        if receipts is not None and event_key is not None:
            receipts.complete(event_key)

    async def on_c2c_message_create(self, message: ymbotpy.message.C2CMessage):
        """
        处理接收到的消息。
        :param message: 接收到的消息对象。
        """
        self.logger.debug(f"收到 C2C 消息: {message}")
        im_message = await self.convert_to_message(message)
        await self._dispatch_inbound(
            message,
            im_message,
            f"c2c:{getattr(getattr(message, 'author', None), 'user_openid', '')}",
        )

    async def on_group_at_message_create(self, message: ymbotpy.message.GroupMessage):
        """
        处理接收到的群消息。
        :param message: 接收到的消息对象。
        """
        self.logger.debug(f"收到群消息: {message}")
        im_message = await self.convert_to_message(message)
        # 这个逆天的 Webhook 居然不包含 mention 字段，这里要手动补上
        im_message.message_elements.append(
            MentionElement(target=ChatSender.get_bot_sender()))
        await self._dispatch_inbound(
            message,
            im_message,
            f"group:{getattr(message, 'group_openid', '')}",
        )

    async def get_bot_profile(self) -> Optional[UserProfile]:
        """
        获取机器人资料
        :return: 机器人资料
        """
        if self.user is None:
            return None
        return UserProfile(
            user_id=self.user['id'],
            username=self.user['username'],
            display_name=self.user['username'],
            avatar_url=self.user['avatar']
        )

    async def start(self):
        """启动 Bot"""

        if self._started:
            return

        if getattr(self, "database_manager", None) is not None:
            self._ensure_outbox().recover_on_startup()
            receipts = self._ensure_inbound_receipts()
            if receipts is not None:
                # 上次中断时留在 processing 的事件重新开放认领：不丢事件，
                # 上游若重投也只会被处理一次。
                reopened = receipts.recover_on_startup()
                if reopened:
                    self.logger.info(f"QQ 重新开放 {reopened} 个未完成的入站事件")

        mount_path = self.config.webhook_url.removesuffix('/') or '/'
        routes = self.web_server.app.routes
        if any(getattr(route, "path", None) == mount_path for route in routes):
            raise RuntimeError(
                f"QQBot Webhook 路径已被占用：{mount_path}；"
                "请为每个 QQBot 实例配置不同的 webhook_url"
            )

        token = botpy.Token(self.config.app_id, self.config.app_secret)
        self._http_open = True
        try:
            self.user = await self.http.login(token)
            self.robot = botpy.Robot(self.user)

            bot_webhook = botpy.BotWebHook(
                self.config.app_id,
                self.config.app_secret,
                hook_route='/',
                client=self,
                system_log=True,
                botapi=self.api,
                loop=self.loop
            )

            app = await bot_webhook.init_fastapi()
            app.user_middleware.clear()
            if any(getattr(route, "path", None) == mount_path for route in routes):
                raise RuntimeError(
                    f"QQBot Webhook 路径已被占用：{mount_path}；"
                    "请为每个 QQBot 实例配置不同的 webhook_url"
                )
            self.web_server.mount_app(mount_path, app)
            self._mounted_route = next(
                (
                    route
                    for route in reversed(routes)
                    if getattr(route, "path", None) == mount_path
                ),
                None,
            )
            if self._mounted_route is None:
                raise RuntimeError(f"QQBot Webhook 路径挂载失败：{mount_path}")
            self._mount_path = mount_path
            self._started = True
            self._schedule_outbox_resume()
        except BaseException:
            if self._mounted_route is not None and self._mounted_route in routes:
                routes.remove(self._mounted_route)
            self._mounted_route = None
            self._mount_path = None
            self.user = None
            self.robot = None
            try:
                await self.http.close()
                self._http_open = False
            except BaseException:
                self.logger.opt(exception=True).error(
                    "Failed to close QQBot HTTP session after startup failure"
                )
            raise

    async def stop(self):
        """停止 Bot"""
        outbox_task = getattr(self, "_outbox_resume_task", None)
        if outbox_task is not None and not outbox_task.done():
            outbox_task.cancel()
            await asyncio.gather(outbox_task, return_exceptions=True)
        self._outbox_resume_task = None

        if self._mounted_route is not None:
            routes = self.web_server.app.routes
            if self._mounted_route in routes:
                routes.remove(self._mounted_route)
        self._mounted_route = None
        self._mount_path = None
        self._started = False
        self.user = None
        self.robot = None

        if not self._http_open:
            return
        try:
            await self.http.close()
            self._http_open = False
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.opt(exception=True).error(
                "Failed to close QQBot HTTP session"
            )
            raise
