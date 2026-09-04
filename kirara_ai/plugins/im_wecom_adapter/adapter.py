import asyncio
import base64
import hashlib
import json
import os
import time
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import Any, List, Optional

import aiohttp
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.routing import Route
from wechatpy.client import BaseWeChatClient
from wechatpy.exceptions import InvalidSignatureException
from wechatpy.messages import BaseMessage
from wechatpy.replies import create_reply

from kirara_ai.config import DATA_PATH
from kirara_ai.database import DatabaseManager
from kirara_ai.im.adapter import AdapterHealthProvider, AdapterHealthSnapshot, IMAdapter
from kirara_ai.im.dispatch_failure import describe_dispatch_failure
from kirara_ai.im.message import (FileElement, ImageMessage, IMMessage, MessageElement, TextMessage, VideoElement,
                                  VoiceMessage)
from kirara_ai.im.sender import ChatSender
from kirara_ai.logger import HypercornLoggerWrapper, get_logger
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher

from .delegates import CorpWechatApiDelegate, PublicWechatApiDelegate, WechatApiDelegate, markdown_to_plain_text, split_long_message
from .outbox import WecomDeliveryResult, WecomOutboxService

# 媒体临时目录必须挂在 DATA_PATH 下：此前用 os.getcwd() 拼接，
# 一旦容器工作目录与数据卷挂载点不同，临时文件就会落在卷外，
# 既进不了备份，也会在每次重建容器时丢失。
WECOM_TEMP_DIR = os.path.join(DATA_PATH, 'temp', 'wecom')

WEBHOOK_URL_PREFIX = "/im/webhook/wechat"

def make_webhook_url():
    return f"{WEBHOOK_URL_PREFIX}/{str(uuid.uuid4())[:8]}"


def auto_generate_webhook_url(s: dict):
    s["readOnly"] = True
    s["default"] = make_webhook_url()
    s["textType"] = True


class WecomConfig(BaseModel):
    """企业微信配置
    文档： https://work.weixin.qq.com/api/doc/90000/90136/91770
    """

    app_id: str = Field(title="应用ID", description="见微信侧显示")
    secret: str = Field(title="应用Secret", description="见微信侧显示")
    token: str = Field(title="Token", description="与微信侧填写保持一致")
    encoding_aes_key: str = Field(
        title="EncodingAESKey", description="请通过微信侧随机生成")
    corp_id: Optional[str] = Field(
        title="企业ID", description="企业微信后台显示的企业ID，微信公众号等场景无需填写。", default=None)
    webhook_url: str = Field(
        title="微信端回调地址",
        description="供微信端请求的 Webhook URL，填写在微信端，由系统自动生成，无法修改。",
        default_factory=make_webhook_url,
        json_schema_extra=auto_generate_webhook_url
    )

    host: Optional[str] = Field(title="HTTP 服务地址", description="已过时，请删除并使用 webhook_url 代替。",
                                default=None, json_schema_extra={"hidden_unset": True})
    port: Optional[int] = Field(title="HTTP 服务端口", description="已过时，请删除并使用 webhook_url 代替。",
                                default=None, json_schema_extra={"hidden_unset": True})
    send_timeout_seconds: float = Field(
        default=30.0,
        gt=0,
        le=120,
        title="企业微信 API 操作超时",
        description="等待企业微信主动发送或媒体上传返回的最长秒数；结果未知时不自动重发。",
    )

    model_config = ConfigDict(extra="allow")

    def __init__(self, **kwargs: Any):
        # 如果 agent_id 存在，则自动使用 agent_id 作为 app_id
        if "agent_id" in kwargs:
            kwargs["app_id"] = str(kwargs["agent_id"])
        super().__init__(**kwargs)


class WeComUtils:
    """企业微信相关的工具类"""

    def __init__(self, client: BaseWeChatClient):
        self.client = client
        self.logger = get_logger("WeComUtils")

    @property
    def access_token(self) -> Optional[str]:
        return self.client.access_token

    async def download_and_save_media(self, media_id: str, file_name: str) -> Optional[str]:
        """下载并保存媒体文件到本地"""
        file_path = os.path.join(WECOM_TEMP_DIR, file_name)
        try:
            media_data = await self.download_media(media_id)
            if media_data:
                os.makedirs(os.path.dirname(file_path), exist_ok=True)
                with open(file_path, "wb") as f:
                    f.write(media_data)
                return file_path
        except Exception as e:
            self.logger.error(f"Failed to save media: {str(e)}")
        return None

    async def download_media(self, media_id: str) -> Optional[bytes]:
        """下载企业微信的媒体文件"""
        url = f"https://qyapi.weixin.qq.com/cgi-bin/media/get?access_token={self.access_token}&media_id={media_id}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    self.logger.error(
                        f"Failed to download media: {response.status}")
        except Exception as e:
            self.logger.error(f"Failed to download media: {str(e)}")
        return None


@dataclass(frozen=True)
class _WecomSendUnit:
    action: str
    params: dict[str, Any]


def truncated_passive_reply(first_page: str, *, dropped_pages: int) -> str:
    """被动回复只能发一条时，在那条消息末尾说明还有几页发不出去。

    企业微信未开通主动回复能力时上游返回 `48001`，只能走被动回复 API，
    而它**只能回一条消息**。此前的处理是把第 1 页交出去、记一条 `send_succeeded`
    就结束——用户收到「第 1 页 / 共 4 页」然后什么都没有，而看板上这一轮是成功。
    19.4 要求「保证顺序稳定、**全部发送**、失败可记录」，这一条正好三项全丢。

    能发多少是平台的限制，如实说出来是我们的责任。因此追加一句说明，
    并给出用户自己能做的事（问得更窄、或分次问）。

    **刻意不把剩余页塞回这一条。** 被动回复的长度上限与主动发送一致
    （`split_long_message` 已按它切过），硬拼回去只会让整条被上游拒收——
    那时用户连第 1 页都收不到，比现在更糟。
    """
    if dropped_pages <= 0:
        return first_page
    return (
        f"{first_page}\n\n"
        f"（本次仅能回复 1 条消息，还有 {dropped_pages} 页未能发出："
        "企业微信未开通主动回复能力时只能被动回一条。"
        "请缩小提问范围或分次获取剩余部分。）"
    )


class WecomAdapter(IMAdapter, AdapterHealthProvider):
    """企业微信适配器"""

    #: 统一关系模型里的渠道类型（需求 10）。显式声明的理由见
    #: `im_onebot_adapter/adapter.py` 上同名属性的注释。
    channel_type = "wecom"

    dispatcher: WorkflowDispatcher
    web_server: WebServer
    database_manager: DatabaseManager

    def __init__(self, config: WecomConfig):
        self.wecom_utils = None
        self.api_delegate: Optional[WechatApiDelegate] = None
        self.config = config
        if self.config.host:
            self.app = FastAPI()
        else:
            self.app = self.web_server.app

        self.logger = get_logger("Wecom-Adapter")
        self.is_running = False
        if not self.config.host:
            self.config.host = None
            self.config.port = None
        elif not self.config.port:
            self.config.port = 15650
        if not self.config.webhook_url:
            self.config.webhook_url = make_webhook_url()

        self.reply_tasks: dict[str, asyncio.Future[Any]] = {}
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._outbox: Optional[WecomOutboxService] = None
        self._ever_started = False
        self._last_disconnect_reason: Optional[str] = None

    def get_health_snapshot(self) -> AdapterHealthSnapshot:
        """Report WeCom readiness instead of letting readiness assume health.

        没有这个方法时 `readiness.py` 会走「不实现该协议就按 connected 计数」
        的兜底分支——一个凭据无效、`access_token` 根本换不出来的适配器在就绪检查
        里显示为健康。面板给出错误的安心，比不给状态更糟。

        企业微信是**回调**模型：我们提供 webhook 地址，由企业微信在有消息时才
        推过来。因此没有「链路已连通」这种可持续观测的信号，能确认的只有
        「路由已挂载且 API 凭据可用」。这里把这一点显式表达出来，而不是假装
        它等价于 OneBot 的 `connected`：
        - `connected`：已启动且 API 代理就绪（能换取 access_token）；
        - `waiting`：已启动但 API 代理还没就绪，通常是凭据错误或网络不通；
        - `initializing` / `disconnected`：从未启动 / 曾启动后停止。
        """
        started = bool(getattr(self, "is_running", False))
        if started:
            self._ever_started = True
        api_ready = getattr(self, "api_delegate", None) is not None

        if not started:
            status = "disconnected" if self._ever_started else "initializing"
        elif api_ready:
            status = "connected"
        else:
            status = "waiting"

        return AdapterHealthSnapshot(
            status=status,
            connected_account_count=1 if status == "connected" else 0,
            adapter_started=started,
            last_disconnect_reason=(
                None if status == "connected" else self._last_disconnect_reason
            ),
            outbox=self._outbox_counts(),
        )

    def _outbox_counts(self) -> Optional[dict[str, int]]:
        """Pending/terminal delivery counts, or ``None`` when no outbox is wired."""
        outbox = getattr(self, "_outbox", None)
        if outbox is None:
            return None
        counts = getattr(outbox, "status_counts", None)
        if not callable(counts):
            return None
        try:
            return counts()
        except Exception:  # noqa: BLE001 - 观测失败不得影响健康快照
            return None

        # 根据配置选择合适的API代理
        self.setup_wechat_api()

    def setup_wechat_api(self):
        """根据配置设置微信API代理"""
        if self.config.corp_id:
            self.api_delegate = CorpWechatApiDelegate()
        else:
            self.api_delegate = PublicWechatApiDelegate()

        self.api_delegate.setup_api(self.config)

        # 设置工具类
        self.wecom_utils = WeComUtils(self.api_delegate.client)

    def _ensure_outbox(self) -> WecomOutboxService:
        outbox = getattr(self, "_outbox", None)
        if outbox is not None:
            return outbox
        database = getattr(self, "database_manager", None)
        if database is None:
            raise RuntimeError("WeCom persistent outbox requires DatabaseManager")
        self._outbox = WecomOutboxService(database, self._send_outbox_payload)
        return self._outbox

    def _outbox_counts(self) -> Optional[dict[str, int]]:
        if getattr(self, "database_manager", None) is None:
            return None
        try:
            return self._ensure_outbox().status_counts()
        except Exception as exc:
            self.logger.warning(f"WeCom 投递队列状态读取失败：{exc}")
            return None

    @staticmethod
    def _unit_delivery_id(logical_delivery_id: str, unit_index: int) -> str:
        return hashlib.sha256(
            f"{logical_delivery_id}:{unit_index}".encode("utf-8")
        ).hexdigest()

    def _recipient_key(self, recipient: ChatSender) -> str:
        return f"{getattr(self, 'adapter_instance', 'wecom')}:{recipient.user_id}"

    def _implicit_delivery_id(
        self, recipient_key: str, units: list[_WecomSendUnit]
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

    async def _send_outbox_payload(self, params: dict[str, Any]) -> Any:
        payload = dict(params)
        action = str(payload.pop("_action"))
        try:
            if action == "text":
                return await asyncio.wait_for(
                    self._send_text(str(payload["user_id"]), str(payload["text"])),
                    timeout=self.config.send_timeout_seconds,
                )
            if action == "media":
                return await asyncio.wait_for(
                    self._send_media(
                        str(payload["user_id"]),
                        str(payload["media_data"]),
                        str(payload["media_type"]),
                    ),
                    timeout=self.config.send_timeout_seconds,
                )
        except asyncio.TimeoutError:
            raise
        raise ValueError(f"不支持的企业微信投递动作：{action}")

    async def _render_send_units(
        self, message: IMMessage, recipient: ChatSender
    ) -> tuple[str, list[_WecomSendUnit], Optional[str]]:
        recipient_key = self._recipient_key(recipient)
        units: list[_WecomSendUnit] = []
        text_reply: Optional[str] = None
        for element in message.message_elements:
            if isinstance(element, TextMessage) and element.text:
                plain_text = markdown_to_plain_text(element.text)
                chunks = split_long_message(plain_text)
                if text_reply is None and chunks:
                    text_reply = chunks[0]
                units.extend(
                    _WecomSendUnit(
                        "text",
                        {
                            "_action": "text",
                            "user_id": str(recipient.user_id),
                            "text": chunk,
                        },
                    )
                    for chunk in chunks
                )
                continue

            if isinstance(element, (ImageMessage, VoiceMessage, VideoElement, FileElement)):
                media_type = (
                    "image"
                    if isinstance(element, ImageMessage)
                    else "voice"
                    if isinstance(element, VoiceMessage)
                    else "video"
                    if isinstance(element, VideoElement)
                    else "file"
                )
                data = await element.get_data()
                units.append(
                    _WecomSendUnit(
                        "media",
                        {
                            "_action": "media",
                            "user_id": str(recipient.user_id),
                            "media_type": media_type,
                            "media_data": base64.b64encode(data).decode("ascii"),
                        },
                    )
                )
        return recipient_key, units, text_reply

    @staticmethod
    def _delivery_error(result: WecomDeliveryResult) -> BaseException:
        if result.error is not None:
            return result.error
        message = result.error_message or f"WeCom delivery {result.status}"
        if result.status == "ambiguous":
            return asyncio.TimeoutError(message)
        return RuntimeError(message)

    def _track_background(self, coroutine: Any) -> asyncio.Task[Any]:
        task = asyncio.create_task(coroutine)
        tasks = getattr(self, "_background_tasks", None)
        if tasks is None:
            tasks = self._background_tasks = set()
        tasks.add(task)

        def discard(done: asyncio.Task[Any]) -> None:
            tasks.discard(done)

        task.add_done_callback(discard)
        return task

    async def _resume_outbox(self) -> None:
        results = await self._ensure_outbox().resume_pending()
        failed = sum(result.status != "accepted" for result in results)
        if failed:
            self.logger.warning(
                f"WeCom 恢复投递完成，{failed} 个发送单元仍需人工核对"
            )

    def setup_routes(self):
        if self.config.host:
            webhook_url = '/wechat'
        else:
            webhook_url = self.config.webhook_url
        # unregister old route if exists
        for route in self.app.routes:
            if isinstance(route, Route) and route.path == webhook_url:
                self.app.routes.remove(route)

        @self.app.get(webhook_url)
        async def handle_check_request(request: Request):
            """处理 GET 请求"""
            if not self.is_running:
                self.logger.warning("Wecom-Adapter is not running, skipping check request.")
                raise HTTPException(status_code=404)
            
            assert self.api_delegate is not None

            signature = request.query_params.get("msg_signature", "")
            if not signature:
                signature = request.query_params.get("signature", "")
            timestamp = request.query_params.get("timestamp", "")
            nonce = request.query_params.get("nonce", "")
            echo_str = request.query_params.get("echostr", "")

            try:
                echo_str = self.api_delegate.check_signature(
                    signature, timestamp, nonce, echo_str
                )
                return Response(content=echo_str, media_type="text/plain")
            except InvalidSignatureException:
                self.logger.error("failed to check signature, please check your settings.")
                raise HTTPException(status_code=403)

        @self.app.post(webhook_url)
        async def handle_message(request: Request):
            """处理 POST 请求"""
            if not self.is_running:
                self.logger.warning("Wecom-Adapter is not running, skipping message request.")
                raise HTTPException(status_code=404)
            
            assert self.api_delegate is not None
            assert self.wecom_utils is not None
            
            signature = request.query_params.get("msg_signature", "")
            if not signature:
                signature = request.query_params.get("signature", "")
            timestamp = request.query_params.get("timestamp", "")
            nonce = request.query_params.get("nonce", "")
            try:
                msg_str = self.api_delegate.decrypt_message(
                    await request.body(), signature, timestamp, nonce
                )
            except InvalidSignatureException:
                self.logger.error("failed to check signature, please check your settings.")
                raise HTTPException(status_code=403)
            msg: BaseMessage = self.api_delegate.parse_message(msg_str)
            # 跳过事件消息（如关注/菜单点击等），它们没有 MsgId
            if not msg.id or str(msg.id) == "0":
                self.logger.debug(
                    f"skip processing due to invalid msgid (event message): type={msg.type}"
                )
                return Response(content="", media_type="text/plain")
            msg_id = str(msg.id)

            if msg_id in self.reply_tasks:
                self.logger.debug(f"skip processing due to duplicate msgid: {msg.id}")
                existing_reply_task = self.reply_tasks[msg_id]

                try:
                    # 重复回调等待正在执行的同一个任务，但不重复分发消息
                    reply = await asyncio.wait_for(
                        asyncio.shield(existing_reply_task),
                        timeout=4.5,
                    )
                except asyncio.TimeoutError:
                    # 返回企业微信要求的 XML 格式空响应，避免客户端显示空白消息
                    xml_content = (
                        '<xml>'
                        '<ToUserName><![CDATA[' + msg.source + ']]></ToUserName>'
                        '<FromUserName><![CDATA[' + self.config.app_id + ']]></FromUserName>'
                        '<CreateTime>' + str(int(time.time())) + '</CreateTime>'
                        '<MsgType><![CDATA[text]]></MsgType>'
                        '<Content><![CDATA[]]></Content>'
                        '</xml>'
                    )
                    return Response(content=xml_content, media_type="application/xml")
                except Exception as e:
                    self.logger.error(
                        f"Failed to wait for duplicate msgid {msg.id}: {e}"
                    )
                    return Response(content="", media_type="text/plain")

                return Response(
                    content=create_reply(reply, msg, render=True),
                    media_type="text/xml",
                )

            # Claim the callback before dispatching it.  The durable claim is
            # required because WeCom can retry a callback after this process
            # has restarted and an in-memory Future is no longer available.
            if getattr(self, "database_manager", None) is not None:
                if not self._ensure_outbox().claim_inbound(msg_id, str(msg.source)):
                    return Response(content="", media_type="text/plain")

            loop = asyncio.get_running_loop()
            reply_task: asyncio.Future[Any] = loop.create_future()
            self.reply_tasks[msg_id] = reply_task

            # 已处理 MsgId 保留 5 分钟，用于拦截企业微信重复回调
            def cleanup_reply_task():
                if self.reply_tasks.get(msg_id) is reply_task:
                    self.reply_tasks.pop(msg_id, None)

            loop.call_later(300, cleanup_reply_task)

            try:
                # 预处理媒体消息
                media_path = None
                if msg.type in ["voice", "video", "file"]:
                    media_id = msg.media_id
                    file_name = f"temp_{msg.type}_{media_id}.{msg.type}"
                    media_path = await self.wecom_utils.download_and_save_media(media_id, file_name)

                # 转换消息
                message = await self.convert_to_message(msg, media_path)
            except Exception:
                cleanup_reply_task()
                if getattr(self, "database_manager", None) is not None:
                    self._ensure_outbox().retry_inbound(
                        msg_id, "failed to normalize WeCom callback"
                    )
                raise

            # raw_metadata 中只保存可 JSON 序列化的 MsgId，不再保存 Future
            message.sender.raw_metadata["reply"] = msg_id

            async def dispatch_message():
                try:
                    # 分发消息
                    await self.dispatcher.dispatch(self, message, require_agent=True)
                    if getattr(self, "database_manager", None) is not None:
                        self._ensure_outbox().complete_inbound(msg_id, None)
                except asyncio.CancelledError:
                    if getattr(self, "database_manager", None) is not None:
                        self._ensure_outbox().retry_inbound(
                            msg_id, "WeCom callback processing was cancelled"
                        )
                    raise
                except Exception as e:
                    self.logger.error(f"Failed to dispatch message: {e}")

                    if not reply_task.done():
                        # 根据异常内容给出可读的失败原因，便于用户判断问题
                        # 失败描述统一由 `kirara_ai/im/dispatch_failure.py` 给出。
                        # 这套分类原来只在企业微信这一处有，而 Telegram 是一句英文、
                        # OneBot 与 QQ 官方机器人只记日志——同一个上游故障在三个渠道上
                        # 呈现成三种样子，而用户往往同时接了两个渠道。
                        error_reply = describe_dispatch_failure(e)

                        try:
                            # 优先尝试企业微信主动发送
                            await self._send_text(
                                message.sender.user_id,
                                error_reply,
                            )
                            if not reply_task.done():
                                reply_task.set_result(None)
                            if getattr(self, "database_manager", None) is not None:
                                self._ensure_outbox().complete_inbound(msg_id, None)
                        except Exception as send_error:
                            self.logger.error(
                                f"Failed to send error reply: {send_error}"
                            )
                            if not reply_task.done():
                                # 主动发送不可用时，尝试通过当前回调被动回复
                                reply_task.set_result(error_reply)
                            if getattr(self, "database_manager", None) is not None:
                                self._ensure_outbox().complete_inbound(msg_id, error_reply)
                finally:
                    message.sender.raw_metadata.pop("reply", None)

            self._track_background(dispatch_message())

            try:
                # 企业微信通常会在约 5 秒后重试，因此最多等待 4.5 秒
                reply = await asyncio.wait_for(
                    asyncio.shield(reply_task),
                    timeout=4.5,
                )
            except asyncio.TimeoutError:
                self.logger.debug(
                    f"reply timeout for msgid: {msg.id}, "
                    "response acknowledged and processing continues"
                )
                # 返回企业微信要求的 XML 格式空响应，避免客户端显示空白消息
                # 同时后台继续处理，完成后通过 _send_text 主动发送（工作台消息）
                xml_content = (
                    '<xml>'
                    '<ToUserName><![CDATA[' + msg.source + ']]></ToUserName>'
                    '<FromUserName><![CDATA[' + self.config.app_id + ']]></FromUserName>'
                    '<CreateTime>' + str(int(time.time())) + '</CreateTime>'
                    '<MsgType><![CDATA[text]]></MsgType>'
                    '<Content><![CDATA[]]></Content>'
                    '</xml>'
                )
                return Response(content=xml_content, media_type="application/xml")

            return Response(
                content=create_reply(reply, msg, render=True),
                media_type="text/xml",
            )

    async def convert_to_message(self, raw_message: Any, media_path: Optional[str] = None) -> IMMessage:
        """将企业微信消息转换为统一消息格式"""
        # 企业微信应用似乎没有群聊的概念，所以这里只能用单聊
        sender = ChatSender.from_c2c_chat(
            raw_message.source, raw_message.source)

        message_elements: List[MessageElement] = []
        raw_message_dict = raw_message.__dict__

        if raw_message.type == "text":
            message_elements.append(TextMessage(text=raw_message.content))
        elif raw_message.type == "image":
            message_elements.append(ImageMessage(url=raw_message.image))
        elif raw_message.type == "voice" and media_path:
            message_elements.append(VoiceMessage(url=media_path))
        elif raw_message.type == "video" and media_path:
            message_elements.append(VideoElement(path=media_path))
        elif raw_message.type == "file" and media_path:
            message_elements.append(FileElement(path=media_path))
        elif raw_message.type == "location":
            location_text = f"[Location] {raw_message.label} (X: {raw_message.location_x}, Y: {raw_message.location_y})"
            message_elements.append(TextMessage(text=location_text))
        elif raw_message.type == "link":
            link_text = f"[Link] {raw_message.title}: {raw_message.description} ({raw_message.url})"
            message_elements.append(TextMessage(text=link_text))
        else:
            message_elements.append(TextMessage(
                text=f"Unsupported message type: {raw_message.type}"))

        return IMMessage(
            sender=sender,
            message_elements=message_elements,
            raw_message=raw_message_dict,
        )

    async def _send_text(self, user_id: str, text: str):
        """发送文本消息"""
        assert self.api_delegate is not None
        try:
            app_id: Any = (
                int(self.config.app_id)
                if self.config.corp_id
                else self.config.app_id
            )
            return await self.api_delegate.send_text(app_id, user_id, text)
        except Exception as e:
            self.logger.error(f"Failed to send text message: {e}")
            raise e

    async def _send_media(self, user_id: str, media_data: str, media_type: str):
        """发送媒体消息的通用方法"""
        assert self.api_delegate is not None
        try:
            app_id: Any = (
                int(self.config.app_id)
                if self.config.corp_id
                else self.config.app_id
            )
            media_bytes = BytesIO(base64.b64decode(media_data))
            return await self.api_delegate.send_media(app_id, user_id, media_type, media_bytes)
        except Exception as e:
            self.logger.error(f"Failed to send {media_type} message: {e}")
            raise e

    def _get_reply_task(
        self,
        recipient: ChatSender,
    ) -> Optional[asyncio.Future[Any]]:
        """获取当前企业微信回调对应的 Future"""
        if not recipient.raw_metadata:
            return None

        reply_id = recipient.raw_metadata.get("reply")
        if reply_id is None:
            return None

        # 兼容旧逻辑中 raw_metadata 直接保存 Future 的情况
        if isinstance(reply_id, asyncio.Future):
            return reply_id

        return self.reply_tasks.get(str(reply_id))

    async def send_message(
        self,
        message: IMMessage,
        recipient: ChatSender,
        delivery_id: Optional[str] = None,
    ):
        """Render and persist every WeCom send unit before network I/O."""
        message.record_delivery_stage("formatting_started", adapter="wecom")
        recipient_key, units, text_reply = await self._render_send_units(message, recipient)
        message.record_delivery_stage(
            "formatting_completed",
            adapter="wecom",
            segment_count=len(units),
        )
        if not units:
            reply_task = self._get_reply_task(recipient)
            if reply_task is not None and not reply_task.done():
                reply_task.set_result(text_reply)
            return

        reply_task = self._get_reply_task(recipient)
        if delivery_id is None:
            delivery_id = getattr(message, "_wecom_delivery_id", None)
        if delivery_id is None:
            reply_id = (
                recipient.raw_metadata.get("reply")
                if recipient.raw_metadata
                else None
            )
            if reply_id is not None:
                delivery_id = f"{getattr(self, 'adapter_instance', 'wecom')}:inbound:{reply_id}"
            else:
                delivery_id = self._implicit_delivery_id(recipient_key, units)
            setattr(message, "_wecom_delivery_id", delivery_id)
        if len(delivery_id) > 64:
            delivery_id = hashlib.sha256(delivery_id.encode("utf-8")).hexdigest()

        outbox = self._ensure_outbox()
        unit_ids: list[str] = []
        for index, unit in enumerate(units):
            unit_id = self._unit_delivery_id(delivery_id, index)
            unit_ids.append(unit_id)
            outbox.enqueue(unit_id, recipient_key, unit.action, unit.params)

        message.record_delivery_stage("send_started", adapter="wecom")
        results: list[WecomDeliveryResult] = []
        try:
            for unit_id in unit_ids:
                result = await outbox.deliver(unit_id)
                results.append(result)
                if result.status != "accepted":
                    raise self._delivery_error(result)
            if reply_task is not None and not reply_task.done():
                reply_task.set_result(None)
            if recipient.raw_metadata:
                reply_id = recipient.raw_metadata.get("reply")
                if reply_id is not None:
                    outbox.complete_inbound(str(reply_id), None)
        except Exception as exc:
            if "Error code: 48001" in str(exc):
                if reply_task is not None and not reply_task.done():
                    # 被动回复只能发一条。丢掉的页数必须让用户知道，也必须留在
                    # 时间线上——否则「上周二那批回复用户说看不全」事后无从查证。
                    text_units = sum(1 for unit in units if unit.action == "text")
                    dropped_pages = max(0, text_units - 1)
                    self.logger.warning(
                        "未开通主动回复能力，将采用被动回复消息 API，"
                        f"此模式下只能回复一条消息；本次有 {dropped_pages} 页未能发出。"
                    )
                    reply_task.set_result(
                        truncated_passive_reply(
                            text_reply or "", dropped_pages=dropped_pages
                        )
                        if text_reply is not None
                        else text_reply
                    )
                    message.record_delivery_stage(
                        "send_succeeded",
                        adapter="wecom",
                        retry_count=sum(
                            max(0, item.attempt_count - 1) for item in results
                        ),
                        delivery_mode="passive_reply",
                        # 不是完全成功：内容确实少发了，阶段里要留下证据。
                        dropped_pages=dropped_pages,
                    )
                    return
                self.logger.warning("未开通主动回复能力，且不在上下文中，无法发送消息。")
                message.record_delivery_stage(
                    "send_failed",
                    adapter="wecom",
                    error_type=type(exc).__name__,
                    retry_count=sum(
                        max(0, item.attempt_count - 1) for item in results
                    ),
                )
                raise
            self.logger.error(f"Failed to send message: {exc}")
            message.record_delivery_stage(
                "send_failed",
                adapter="wecom",
                error_type=type(exc).__name__,
                retry_count=sum(max(0, item.attempt_count - 1) for item in results),
            )
            raise
        message.record_delivery_stage(
            "send_succeeded",
            adapter="wecom",
            retry_count=sum(max(0, item.attempt_count - 1) for item in results),
            delivery_mode="active",
        )

    async def _start_standalone_server(self):
        """启动服务"""
        from hypercorn.asyncio import serve
        from hypercorn.config import Config
        from hypercorn.logging import Logger

        config = Config()
        config.bind = [f"{self.config.host}:{self.config.port}"]
        # config._log = get_logger("Wecom-API")
        # hypercorn 的 logger 需要做转换
        config._log = Logger(config)
        config._log.access_logger = HypercornLoggerWrapper(self.logger) # type: ignore
        config._log.error_logger = HypercornLoggerWrapper(self.logger) # type: ignore

        self.server_task = asyncio.create_task(serve(self.app, config)) # type: ignore

    async def _stop_standalone_server(self):
        """停止服务"""
        if hasattr(self, "server_task"):
            self.server_task.cancel()
            try:
                await self.server_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                self.logger.error(f"Error during server shutdown: {e}")

    async def start(self):
        self._background_tasks = getattr(self, "_background_tasks", set())
        if getattr(self, "database_manager", None) is not None:
            outbox = self._ensure_outbox()
            outbox.recover_on_startup()
            recovered_inbound = outbox.recover_inbound()
            if recovered_inbound:
                self.logger.warning(
                    f"WeCom 恢复了 {recovered_inbound} 条未完成入站回调，允许重试"
                )
        self.setup_wechat_api()
        if self.config.host:
            self.logger.warning("正在使用过时的启动模式，请尽快更新为 Webhook 模式。")
            await self._start_standalone_server()
        self.setup_routes()
        self.is_running = True
        self._ever_started = True
        self._last_disconnect_reason = None
        if getattr(self, "database_manager", None) is not None:
            self._track_background(self._resume_outbox())
        self.logger.info("Wecom-Adapter 启动成功")

    async def stop(self):
        tasks = list(getattr(self, "_background_tasks", set()))
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        getattr(self, "_background_tasks", set()).clear()
        if self.config.host:
            await self._stop_standalone_server()
        self.is_running = False
        self._last_disconnect_reason = "adapter_stopped"
        self.logger.info("Wecom-Adapter 停止成功")
