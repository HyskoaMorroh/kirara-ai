import asyncio
import base64
import functools
import uuid
from typing import List, Optional

import ymbotpy as botpy
import ymbotpy.message
from pydantic import BaseModel, ConfigDict, Field
from ymbotpy.http import Route as BotpyRoute
from ymbotpy.types.message import Media as BotpyMedia

from kirara_ai.im.adapter import BotProfileAdapter, IMAdapter
from kirara_ai.im.message import (ImageMessage, IMMessage, MentionElement, MessageElement, TextMessage, VideoElement,
                                  VoiceMessage)
from kirara_ai.im.profile import UserProfile
from kirara_ai.im.sender import ChatSender, ChatType
from kirara_ai.logger import get_logger
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.dispatch import WorkflowDispatcher

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
    app_id: str = Field(description="机器人的 App ID。")
    app_secret: str = Field(title="App Secret", description="机器人的 App Secret。")
    token: str = Field(
        title="Token", description="机器人令牌，用于调用 QQ 机器人的 OpenAPI。")
    sandbox: bool = Field(
        title="沙盒环境", description="是否为沙盒环境，通常只有正式发布的机器人才会关闭此选项。", default=False)
    webhook_url: str = Field(
        title="Webhook 回调 URL", description="供 QQ 机器人回调的 URL，由系统自动生成，无法修改。",
        default_factory=make_webhook_url,
        json_schema_extra=auto_generate_webhook_url
    )
    model_config = ConfigDict(extra="allow")


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


class QQBotAdapter(botpy.WebHookClient, IMAdapter, BotProfileAdapter):
    """
    QQBot Adapter，包含 QQBot Bot 的所有逻辑。
    """

    dispatcher: WorkflowDispatcher
    web_server: WebServer
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

    async def send_message(self, message: IMMessage, recipient: ChatSender):
        """
        发送消息
        :param message: 要发送的消息对象。
        :param recipient: 接收消息的目标对象。
        """
        if recipient.raw_metadata is None or recipient.raw_metadata.get('message_id') is None:
            raise ValueError("Unable to retreive send_message info from metadata")
        
        msg_id = recipient.raw_metadata['message_id']
        
        if recipient.chat_type == ChatType.C2C:
            assert recipient.user_id is not None
            post_message_func = functools.partial(
                self.api.post_c2c_message, openid=recipient.user_id, msg_id=msg_id)
            upload_func = functools.partial(
                patched_post_file, self.api, openid=recipient.user_id)
            
        elif recipient.chat_type == ChatType.GROUP:
            assert recipient.group_id is not None
            post_message_func = functools.partial(
                self.api.post_group_message, group_openid=recipient.group_id, msg_id=msg_id)
            upload_func = functools.partial(
                patched_post_file, self.api, group_openid=recipient.group_id)
        else:
            raise ValueError(f"不支持的消息类型: {recipient.chat_type}")

        # 文本缓冲区
        current_text = ""
        msg_seq = 0
        url_replaced = False  # 标记是否替换过 URL

        def replace_url_dots(text: str) -> str:
            """
            检查文本是否包含 URL，如果包含则替换 URL 中的句点为句号。
            :param text: 要检查的文本。
            :return: 替换后的文本。
            """
            nonlocal url_replaced
            def replace_dots(match):
                nonlocal url_replaced
                url_replaced = True
                return match.group(0).replace('.', '。')

            return URL_PATTERN.sub(replace_dots, text)

        async def send_text_message(text: str):
            """
            发送文本消息。
            :param text: 要发送的文本内容。
            """
            await post_message_func(content=text, msg_seq=msg_seq) # type: ignore

        # 单次循环处理所有元素
        for element in message.message_elements:
            if isinstance(element, TextMessage):
                # 如果有文本，直接添加到当前缓冲区
                current_text += element.text
                # 立即发送当前文本缓冲区内容
                if current_text:
                    modified_text = replace_url_dots(current_text)
                    await send_text_message(modified_text)
                    msg_seq += 1
                    current_text = ""
            elif isinstance(element, MentionElement):
                # 添加提及标记到当前文本缓冲区
                current_text += f'<qqbot-at-user id="{element.target.user_id}" />'
            elif isinstance(element, ImageMessage) or isinstance(element, VoiceMessage) or isinstance(element, VideoElement):
                # 如果有累积的文本，先发送文本
                if current_text:
                    modified_text = replace_url_dots(current_text)
                    await send_text_message(modified_text)
                    msg_seq += 1
                    current_text = ""

                # 然后发送媒体
                if isinstance(element, ImageMessage):
                    file_type = 1
                elif isinstance(element, VoiceMessage):
                    file_type = 3
                elif isinstance(element, VideoElement):
                    file_type = 2
                media = await upload_func(file_type=file_type, file_data=await element.get_data())
                await post_message_func(media=media, msg_seq=msg_seq, msg_type=7) # type: ignore
                msg_seq += 1
        # 补充解释性文本
        if url_replaced:
            current_text = current_text + "（URL 中的句点已替换为句号以避免屏蔽）"
            
        # 发送循环结束后可能剩余的文本
        if current_text:
            modified_text = replace_url_dots(current_text)
            await send_text_message(modified_text)
            msg_seq += 1

    async def on_c2c_message_create(self, message: ymbotpy.message.C2CMessage):
        """
        处理接收到的消息。
        :param message: 接收到的消息对象。
        """
        self.logger.debug(f"收到 C2C 消息: {message}")
        im_message = await self.convert_to_message(message)
        await self.dispatcher.dispatch(self, im_message)

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
        await self.dispatcher.dispatch(self, im_message)

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

        token = botpy.Token(self.config.app_id, self.config.app_secret)
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
        self.web_server.mount_app(
            self.config.webhook_url.removesuffix('/'), app)

    async def stop(self):
        """停止 Bot"""
