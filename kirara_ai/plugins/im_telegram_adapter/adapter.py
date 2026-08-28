import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field
from telegram import Bot, ChatFullInfo, Update, User
from telegram.constants import MessageEntityType
from telegram.error import NetworkError, RetryAfter, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from telegramify_markdown import markdownify

from kirara_ai.database import DatabaseManager
from kirara_ai.im.adapter import BotProfileAdapter, EditStateAdapter, IMAdapter, UserProfileAdapter
from kirara_ai.im.message import (FileElement, ImageMessage, IMMessage, MentionElement, MessageElement, TextMessage,
                                  VideoMessage, VoiceMessage)
from kirara_ai.im.profile import UserProfile
from kirara_ai.im.sender import ChatSender, ChatType
from kirara_ai.im.text_render import convert_markdown_tables, split_structured_text
from kirara_ai.logger import get_logger
from kirara_ai.workflow.core.dispatch import WorkflowDispatcher

from .outbox import (
    TelegramDeliveryResult,
    TelegramOutboxService,
    TelegramRetryableError,
)


def get_display_name(user: User | ChatFullInfo):
    if user.first_name or user.last_name:
        return f"{user.first_name or ''} {user.last_name or ''}".strip()
    elif user.username:
        return user.username
    else:
        return str(user.id)


# Telegram 单条消息上限 4096 字符，留出安全余量
TELEGRAM_MESSAGE_LIMIT = 3900
TELEGRAM_MESSAGE_FILTER = (
    filters.TEXT
    | filters.VOICE
    | filters.PHOTO
    | filters.VIDEO
    | filters.Document.ALL
)


def _split_telegram_message_legacy(text: str, max_length: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    """
    将 MarkdownV2 文本按结构安全分段，避免超过 Telegram 4096 字符上限导致发送失败。

    分段规则：
    1. 围栏代码块（```）视为整体，超长时按行拆分并为每段补齐围栏，保持缩进与语法高亮；
    2. 普通文本优先按空行分段，再按行分段，避免把 MarkdownV2 实体截断在中间。
    """
    if len(text) <= max_length:
        return [text]

    # 以围栏为界切分，奇数段即代码块内容
    segments = text.split("```")
    chunks: List[str] = []
    buffer = ""

    def flush_buffer():
        nonlocal buffer
        if buffer.strip():
            chunks.append(buffer.rstrip("\n"))
        buffer = ""

    def append_plain(content: str):
        nonlocal buffer
        for paragraph in content.split("\n\n"):
            block = paragraph if not buffer else "\n\n" + paragraph
            if len(buffer) + len(block) <= max_length:
                buffer += block
                continue
            flush_buffer()
            if len(paragraph) <= max_length:
                buffer = paragraph
                continue
            # 段落自身超长，逐行拆
            for line in paragraph.split("\n"):
                candidate = line if not buffer else buffer + "\n" + line
                if len(candidate) <= max_length:
                    buffer = candidate
                else:
                    flush_buffer()
                    # 单行仍超长时按字符硬切，保证不超限
                    while len(line) > max_length:
                        chunks.append(line[:max_length])
                        line = line[max_length:]
                    buffer = line

    for index, segment in enumerate(segments):
        if index % 2 == 0:
            append_plain(segment)
            continue

        # 代码块：首行可能是语言标识
        lines = segment.split("\n")
        lang = lines[0].strip() if lines and lines[0].strip() and " " not in lines[0].strip() else ""
        body_lines = lines[1:] if lang else lines
        fence_open = f"```{lang}\n" if lang else "```\n"
        fence_cost = len(fence_open) + len("\n```")

        current: List[str] = []
        current_len = fence_cost
        code_chunks: List[str] = []
        for line in body_lines:
            line_len = len(line) + 1
            if current and current_len + line_len > max_length:
                code_chunks.append(fence_open + "\n".join(current) + "\n```")
                current = [line]
                current_len = fence_cost + line_len
            else:
                current.append(line)
                current_len += line_len
        if current:
            code_chunks.append(fence_open + "\n".join(current) + "\n```")

        for code_chunk in code_chunks:
            if buffer and len(buffer) + len(code_chunk) + 1 <= max_length:
                buffer += "\n" + code_chunk
            else:
                flush_buffer()
                buffer = code_chunk
        flush_buffer()

    flush_buffer()
    return [chunk for chunk in chunks if chunk.strip()]


def split_telegram_message(text: str, max_length: int = TELEGRAM_MESSAGE_LIMIT) -> List[str]:
    """Split MarkdownV2 by Unicode character count with complete structures."""
    return split_structured_text(
        text,
        max_length=max_length,
        max_total_bytes=None,
    )


class TelegramConfig(BaseModel):
    """
    Telegram 配置文件模型。
    """

    token: str = Field(
        description="Telegram 机器人的 Token，从 @BotFather 获取。",
        repr=False,
    )
    drop_pending_updates: bool = Field(
        default=False,
        description="启动轮询时是否丢弃 Telegram 服务端尚未处理的消息。",
    )
    send_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        description="等待 Telegram API 返回的最长秒数；结果未知时不会自动重发。",
    )
    outbox_max_attempts: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Telegram 明确拒绝请求时的最大投递尝试次数。",
    )
    outbox_retry_delay_seconds: float = Field(
        default=1.0,
        ge=0,
        le=60,
        description="Telegram 明确瞬态失败后的基础重试间隔。",
    )
    model_config = ConfigDict(extra="allow")

    def __repr__(self):
        return (
            "TelegramConfig(token=<redacted>, "
            f"drop_pending_updates={self.drop_pending_updates})"
        )

    def __str__(self):
        return repr(self)


@dataclass(frozen=True)
class _TelegramSendUnit:
    action: str
    params: dict[str, Any]


class TelegramAdapter(IMAdapter, UserProfileAdapter, EditStateAdapter, BotProfileAdapter):
    """
    Telegram Adapter，包含 Telegram Bot 的所有逻辑。
    """

    dispatcher: WorkflowDispatcher
    database_manager: DatabaseManager

    def __init__(self, config: TelegramConfig):
        self.me = None
        self.config = config
        self.application = Application.builder().token(config.token).build()
        self.bot = Bot(token=config.token)
        # 注册命令处理器和消息处理器
        self.application.add_handler(
            CommandHandler("start", self.command_start))
        self.application.add_handler(
            MessageHandler(
                TELEGRAM_MESSAGE_FILTER, self.handle_message
            )
        )
        self.logger = get_logger("Telegram-Adapter")
        self._outbox: Optional[TelegramOutboxService] = None
        self._recovery_task: Optional[asyncio.Task[Any]] = None

    def _ensure_outbox(self) -> TelegramOutboxService:
        outbox = getattr(self, "_outbox", None)
        if outbox is not None:
            return outbox
        database = getattr(self, "database_manager", None)
        if database is None:
            raise RuntimeError("Telegram persistent outbox requires DatabaseManager")
        self._outbox = TelegramOutboxService(
            database,
            self._send_outbox_payload,
            adapter_instance=str(getattr(self, "adapter_instance", "telegram")),
            max_attempts=self.config.outbox_max_attempts,
            retry_delay_seconds=self.config.outbox_retry_delay_seconds,
        )
        return self._outbox

    @staticmethod
    def _update_payload(update: Update) -> Optional[dict[str, Any]]:
        to_dict = getattr(update, "to_dict", None)
        if not callable(to_dict):
            return None
        payload = to_dict()
        return payload if isinstance(payload, dict) else None

    def _chat_key(self, update: Update) -> str:
        message = update.message
        chat_id = getattr(message, "chat_id", "unknown") if message else "unknown"
        return f"{getattr(self, 'adapter_instance', 'telegram')}:chat:{chat_id}"

    async def _process_update(self, update: Update) -> None:
        if not update.message:
            return
        update_id = getattr(update, "update_id", None)
        outbox: Optional[TelegramOutboxService] = None
        if getattr(self, "database_manager", None) is not None and update_id is not None:
            outbox = self._ensure_outbox()
            if not outbox.claim_inbound(
                update_id,
                self._chat_key(update),
                payload=self._update_payload(update),
            ):
                return
        try:
            message = await self.convert_to_message(update)
            await self.dispatcher.dispatch(self, message, require_agent=True)
        except asyncio.CancelledError:
            if outbox is not None:
                outbox.retry_inbound(update_id)
            raise
        except Exception as exc:
            if outbox is not None:
                outbox.retry_inbound(update_id)
            try:
                await update.message.reply_text(
                    "Workflow execution failed, please try again later: "
                    f"{str(exc)}"
                )
            except Exception:
                self.logger.opt(exception=True).error(
                    "Failed to send Telegram workflow error reply"
                )
            return
        if outbox is not None:
            outbox.complete_inbound(update_id)

    async def command_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理 /start 命令"""
        await self._process_update(update)

    async def handle_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """处理接收到的消息"""
        await self._process_update(update)

    async def convert_to_message(self, raw_message: Update) -> IMMessage:
        """
        将 Telegram 的 Update 对象转换为 Message 对象。
        :param raw_message: Telegram 的 Update 对象。
        :return: 转换后的 Message 对象。
        """
        assert raw_message.message
        assert raw_message.message.from_user

        if (
            raw_message.message.chat.type == "group"
            or raw_message.message.chat.type == "supergroup"
        ):
            sender = ChatSender.from_group_chat(
                user_id=str(raw_message.message.from_user.id),
                group_id=str(raw_message.message.chat_id),
                display_name=get_display_name(raw_message.message.from_user),
            )
        else:
            sender = ChatSender.from_c2c_chat(
                user_id=str(raw_message.message.chat_id),
                display_name=get_display_name(raw_message.message.from_user),
            )

        message_elements: List[MessageElement] = []
        raw_message_dict = raw_message.message.to_dict()
        # 处理文本消息
        if raw_message.message.text is not None or raw_message.message.caption is not None:
            text: str = raw_message.message.text or raw_message.message.caption # type: ignore
            offset = 0
            for entity in raw_message.message.entities or raw_message.message.caption_entities or []:
                if entity.type in (MessageEntityType.MENTION, MessageEntityType.TEXT_MENTION):
                    # Extract mention text
                    mention_text = text[entity.offset:entity.offset + entity.length]

                    # Add preceding text as TextMessage
                    if entity.offset > offset:
                        message_elements.append(TextMessage(
                            text=text[offset:entity.offset]))

                    # Create ChatSender for MentionElement
                    if entity.type == "text_mention" and entity.user:
                        if entity.user.id == self.me.id:  # type: ignore
                            mention_element = MentionElement(
                                target=ChatSender.get_bot_sender())
                        else:
                            mention_element = MentionElement(target=ChatSender.from_c2c_chat(
                                user_id=str(entity.user.id), display_name=mention_text))
                    elif entity.type == "mention":
                        # 这里需要从 adapter 实例中获取 bot 的 username
                        if mention_text == f'@{self.me.username}':  # type: ignore
                            mention_element = MentionElement(
                                target=ChatSender.get_bot_sender())
                        else:
                            mention_element = MentionElement(target=ChatSender.from_c2c_chat(
                                user_id=f'unknown_id:{mention_text}', display_name=mention_text))
                    else:
                        # Fallback in case of unknown entity type
                        mention_element = TextMessage(  # type: ignore
                            text=mention_text)  # Or handle as needed
                    message_elements.append(mention_element)

                    offset = entity.offset + entity.length

            # Add remaining text as TextMessage
            if offset < len(text):
                message_elements.append(TextMessage(text=text[offset:]))

        # 处理语音消息
        if raw_message.message.voice:
            voice_file = await raw_message.message.voice.get_file()
            data = await voice_file.download_as_bytearray()
            voice_element = VoiceMessage(data=bytes(data))
            message_elements.append(voice_element)

        # 处理图片消息
        if raw_message.message.photo:
            # 获取最高分辨率的图片
            photo = raw_message.message.photo[-1]
            photo_file = await photo.get_file()
            data = await photo_file.download_as_bytearray()
            photo_element = ImageMessage(data=bytes(data))
            message_elements.append(photo_element)
            
        if raw_message.message.video:
            video_file = await raw_message.message.video.get_file()
            data = await video_file.download_as_bytearray()
            video_element = VideoMessage(data=bytes(data))
            message_elements.append(video_element)
            
        if raw_message.message.document:
            document_file = await raw_message.message.document.get_file()
            data = await document_file.download_as_bytearray()
            document_element = FileElement(data=bytes(data))
            message_elements.append(document_element)

        # 创建 Message 对象
        message = IMMessage(
            sender=sender,
            message_elements=message_elements,
            raw_message=raw_message_dict,
        )
        return message

    def _recipient(self, recipient: ChatSender) -> tuple[str, str]:
        if recipient.chat_type == ChatType.C2C:
            return (
                f"{getattr(self, 'adapter_instance', 'telegram')}:c2c:{recipient.user_id}",
                str(recipient.user_id),
            )
        if recipient.chat_type == ChatType.GROUP:
            if recipient.group_id is None:
                raise ValueError("Telegram group recipient is missing group_id")
            return (
                f"{getattr(self, 'adapter_instance', 'telegram')}:group:{recipient.group_id}",
                str(recipient.group_id),
            )
        raise ValueError(f"Unsupported chat type: {recipient.chat_type}")

    async def _render_send_units(
        self,
        message: IMMessage,
        recipient: ChatSender,
    ) -> tuple[str, list[_TelegramSendUnit]]:
        recipient_key, chat_id = self._recipient(recipient)
        units: list[_TelegramSendUnit] = []
        for element in message.message_elements:
            if isinstance(element, TextMessage):
                text = markdownify(convert_markdown_tables(element.text, fenced=True))
                for chunk in split_telegram_message(text):
                    units.append(
                        _TelegramSendUnit(
                            "text",
                            {
                                "_action": "text",
                                "chat_id": chat_id,
                                "text": chunk,
                                "parse_mode": "MarkdownV2",
                            },
                        )
                    )
                continue
            media_action = (
                "photo"
                if isinstance(element, ImageMessage)
                else "voice"
                if isinstance(element, VoiceMessage)
                else "video"
                if isinstance(element, VideoMessage)
                else "document"
                if isinstance(element, FileElement)
                else None
            )
            if media_action is not None:
                units.append(
                    _TelegramSendUnit(
                        media_action,
                        {
                            "_action": media_action,
                            "chat_id": chat_id,
                            "media_data": base64.b64encode(
                                await element.get_data()
                            ).decode("ascii"),
                        },
                    )
                )
        return recipient_key, units

    async def _send_outbox_payload(self, params: dict[str, Any]) -> Any:
        payload = dict(params)
        action = str(payload.pop("_action"))
        method_name = {
            "text": "send_message",
            "photo": "send_photo",
            "voice": "send_voice",
            "video": "send_video",
            "document": "send_document",
        }.get(action)
        if method_name is None:
            raise ValueError(f"Unsupported Telegram delivery action: {action}")
        if action != "text":
            payload[action] = base64.b64decode(str(payload.pop("media_data")))
        method = getattr(self.application.bot, method_name)
        try:
            return await asyncio.wait_for(
                method(**payload),
                timeout=self.config.send_timeout_seconds,
            )
        except TimedOut as exc:
            raise asyncio.TimeoutError(str(exc)) from exc
        except RetryAfter as exc:
            raise TelegramRetryableError(str(exc)) from exc
        except NetworkError as exc:
            raise ConnectionError(str(exc)) from exc

    @staticmethod
    def _unit_delivery_id(logical_delivery_id: str, unit_index: int) -> str:
        return hashlib.sha256(
            f"{logical_delivery_id}:{unit_index}".encode("utf-8")
        ).hexdigest()

    def _implicit_delivery_id(
        self,
        recipient_key: str,
        units: list[_TelegramSendUnit],
    ) -> str:
        encoded = json.dumps(
            {
                "adapter_instance": str(getattr(self, "adapter_instance", "")),
                "recipient_key": recipient_key,
                "units": [
                    {"action": unit.action, "params": unit.params}
                    for unit in units
                ],
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _delivery_error(result: TelegramDeliveryResult) -> BaseException:
        if result.error is not None:
            return result.error
        message = result.error_message or f"Telegram delivery {result.status}"
        if result.status == "ambiguous":
            return asyncio.TimeoutError(message)
        return RuntimeError(message)

    async def send_message(
        self,
        message: IMMessage,
        recipient: ChatSender,
        delivery_id: Optional[str] = None,
    ) -> None:
        """Persist every Telegram page before making any message API call."""
        message.record_delivery_stage("formatting_started", adapter="telegram")
        recipient_key, units = await self._render_send_units(message, recipient)
        message.record_delivery_stage(
            "formatting_completed",
            adapter="telegram",
            segment_count=len(units),
        )
        if not units:
            return
        if delivery_id is None:
            delivery_id = getattr(message, "_telegram_delivery_id", None)
        if delivery_id is None:
            delivery_id = self._implicit_delivery_id(recipient_key, units)
            setattr(message, "_telegram_delivery_id", delivery_id)
        if len(delivery_id) > 64:
            raise ValueError("Telegram delivery_id cannot exceed 64 characters")

        message.record_delivery_stage("send_started", adapter="telegram")
        if getattr(self, "database_manager", None) is None:
            try:
                for unit in units:
                    await self._send_outbox_payload(unit.params)
            except Exception as exc:
                message.record_delivery_stage(
                    "send_failed",
                    adapter="telegram",
                    error_type=type(exc).__name__,
                    retry_count=0,
                )
                raise
            message.record_delivery_stage(
                "send_succeeded",
                adapter="telegram",
                retry_count=0,
            )
            return

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
                page_index=unit_index,
                page_count=len(units),
                logical_delivery_id=delivery_id,
            )

        results: list[TelegramDeliveryResult] = []
        try:
            for unit_id in unit_ids:
                result = await outbox.deliver(unit_id)
                results.append(result)
                if result.status != "accepted":
                    raise self._delivery_error(result)
        except Exception as exc:
            message.record_delivery_stage(
                "send_failed",
                adapter="telegram",
                error_type=type(exc).__name__,
                retry_count=sum(max(0, item.attempt_count - 1) for item in results),
            )
            raise
        message.record_delivery_stage(
            "send_succeeded",
            adapter="telegram",
            retry_count=sum(max(0, item.attempt_count - 1) for item in results),
        )

    def _deserialize_update(self, payload: dict[str, Any]) -> Update:
        return Update.de_json(payload, self.application.bot)

    async def _recover_persisted_work(self) -> None:
        outbox = self._ensure_outbox()
        for update_id, payload in outbox.pending_inbound():
            try:
                update = self._deserialize_update(payload)
                await self._process_update(update)
            except asyncio.CancelledError:
                raise
            except Exception:
                outbox.retry_inbound(update_id)
                self.logger.opt(exception=True).error(
                    f"Failed to recover Telegram update {update_id}"
                )
        results = await outbox.resume_pending()
        failed = sum(result.status != "accepted" for result in results)
        if failed:
            self.logger.warning(
                f"Telegram recovery left {failed} delivery units for manual review"
            )

    async def start(self):
        """启动 Bot"""
        await self.application.initialize()
        await self.application.start()
        self.me = await self.bot.get_me()

        if getattr(self, "database_manager", None) is not None:
            outbox = self._ensure_outbox()
            outbox.recover_on_startup()
            outbox.recover_inbound()

        assert self.application.updater

        await self.application.updater.start_polling(
            drop_pending_updates=self.config.drop_pending_updates
        )
        if getattr(self, "database_manager", None) is not None:
            self._recovery_task = asyncio.create_task(
                self._recover_persisted_work()
            )

    async def stop(self):
        """停止 Bot"""
        recovery_task = getattr(self, "_recovery_task", None)
        if recovery_task is not None and not recovery_task.done():
            recovery_task.cancel()
            await asyncio.gather(recovery_task, return_exceptions=True)
        self._recovery_task = None

        assert self.application.updater
        if self.application.updater.running:
            try:
                await self.application.updater.stop()
            except asyncio.CancelledError:
                raise
            except RuntimeError:
                # python-telegram-bot can observe a concurrent stop between
                # the state check and the operation. Only that state race is benign.
                if self.application.updater.running:
                    self.logger.opt(exception=True).error(
                        "Failed to stop Telegram updater"
                    )
                    raise
            except Exception:
                self.logger.opt(exception=True).error(
                    "Failed to stop Telegram updater"
                )
                raise

        if self.application.running:
            try:
                await self.application.stop()
            except asyncio.CancelledError:
                raise
            except RuntimeError:
                if self.application.running:
                    self.logger.opt(exception=True).error(
                        "Failed to stop Telegram application"
                    )
                    raise
            except Exception:
                self.logger.opt(exception=True).error(
                    "Failed to stop Telegram application"
                )
                raise

        try:
            await self.application.shutdown()
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.opt(exception=True).error(
                "Failed to shut down Telegram application"
            )
            raise

    async def set_chat_editing_state(
        self, chat_sender: ChatSender, is_editing: bool = True
    ):
        """
        设置或取消对话的编辑状态
        :param chat_sender: 对话的发送者
        :param is_editing: True 表示正在编辑，False 表示取消编辑状态
        """
        action = "typing" if is_editing else "cancel"
        chat_id = (
            chat_sender.user_id
            if chat_sender.chat_type == ChatType.C2C
            else chat_sender.group_id
        )
        if not chat_id:
            raise ValueError("Unable to get chat_id")

        try:
            self.logger.debug(
                f"Setting chat editing state to {is_editing} for chat_id {chat_id}"
            )
            if is_editing:
                await self.application.bot.send_chat_action(
                    chat_id=chat_id, action=action
                )
            else:
                # 取消编辑状态时发送一个空操作
                await self.application.bot.send_chat_action(
                    chat_id=chat_id, action=action
                )
        except Exception as e:
            self.logger.warning(f"Failed to set chat editing state: {str(e)}")

    @lru_cache(maxsize=10)
    async def _cached_get_chat(self, user_id):
        """
        带缓存的获取用户信息方法
        :param user_id: 用户ID
        :return: 用户对象
        """
        return await self.application.bot.get_chat(user_id)

    async def query_user_profile(self, chat_sender: ChatSender) -> UserProfile:
        """
        查询 Telegram 用户资料
        :param chat_sender: 用户的聊天发送者信息
        :return: 用户资料
        """
        try:
            # 获取用户 ID
            user_id = chat_sender.user_id
            # 获取用户对象（使用缓存）
            user = await self._cached_get_chat(user_id)

            # 构建用户资料
            profile = UserProfile(  # type: ignore
                user_id=str(user_id),
                username=user.username,
                display_name=get_display_name(user),
                full_name=f"{user.first_name or ''} {user.last_name or ''}".strip(),
                avatar_url=None,  # Telegram 需要额外处理获取头像
            )

            return profile

        except Exception as e:
            self.logger.warning(f"Failed to query user profile: {str(e)}")
            # 返回部分信息
            return UserProfile(  # type: ignore
                user_id=str(chat_sender.user_id), display_name=chat_sender.display_name
            )

    async def get_bot_profile(self) -> Optional[UserProfile]:
        """
        获取机器人资料
        :return: 机器人资料
        """
        if not self.me or not self.is_running:
            return None
        profile_photos = await self.me.get_profile_photos()
        if profile_photos and profile_photos.photos:
            file_id = profile_photos.photos[0][-1].file_id
            file = await self.bot.get_file(file_id)
            photo_url = file.file_path
        else:
            photo_url = None

        return UserProfile(
            user_id=str(self.me.id),
            username=self.me.username,
            display_name=get_display_name(self.me),
            full_name=f"{self.me.first_name or ''} {self.me.last_name or ''}".strip(),
            avatar_url=photo_url,
        )
