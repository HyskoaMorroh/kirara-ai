"""The WebUI adapter for the shared IM and Agent runtime boundary."""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from kirara_ai.im.adapter import IMAdapter, IncrementalReplyHandle
from kirara_ai.im.message import IMMessage


class WebUIAdapter(IMAdapter):
    """Capture one request's runtime reply without sharing mutable state.

    实现 ``IncrementalDeliveryAdapter``（见 :mod:`kirara_ai.im.adapter`）。
    需求 4 要「流式和非流式输出」，而在此之前项目**自己的** WebUI 在线对话是四个
    入口里唯一一个连技术上的可能都没有的：一次性 ``POST /llm/chat``，后端没有任何
    聊天 SSE 路由。而浏览器恰恰是最容易做到逐步显示的地方——一条 SSE 事件就是一次
    改写，不需要平台提供 ``editMessageText`` 那样的接口。

    「往哪里推」由构造时传入的 ``stream_sink`` 决定：

    * 传 ``None``（既有的非流式路由）时整条增量链路是空操作，行为逐字节不变；
    * SSE 路由传一个协程回调，把每次改写变成一个 ``delta`` 事件。

    推送的是**新增的那一段**而不是「到目前为止的全文」。协议规定
    ``update_incremental_reply`` 收到的是全文（那是为了让 Telegram 直接改写整条
    消息），因此这里按已交付长度切出增量再送出去：向浏览器送全文会让每条事件都随
    回复变长，一段 8 KB 的回复要传 O(n²) 字节，而 SSE 是纯追加的，客户端本来就在
    自己拼接。
    """

    channel_type = "webui"
    adapter_type = "webui"
    adapter_instance = "webui"
    account_scope = "webui"
    is_running = True
    llm_manager = None

    def __init__(
        self,
        *,
        session_agent_id: str | None = None,
        stream_sink: Any = None,
    ) -> None:
        self.session_agent_id = session_agent_id
        self.reply: IMMessage | None = None
        #: 一个 ``async def sink(kind: str, text: str) -> None``；``None`` 表示不流式。
        self._stream_sink = stream_sink
        #: 已经通过 sink 交付出去的文本。
        #:
        #: 存**文本**而不是只存长度：判断「上游有没有改写已交付的前缀」需要拿新全文
        #: 与旧内容比，而只有长度时唯一能做的比较是「新全文的前 n 个字符是不是新全文
        #: 的前缀」——那是一个恒真式，检查等于没做。
        self._delivered = ""

    async def convert_to_message(self, raw_message: Any) -> IMMessage:
        if not isinstance(raw_message, IMMessage):
            raise TypeError("WebUIAdapter accepts normalized IMMessage values")
        return raw_message

    async def send_message(self, message: IMMessage, recipient: Any) -> None:
        self.reply = message

    async def start(self) -> None:
        self.is_running = True

    async def stop(self) -> None:
        self.is_running = False

    # ---- 增量投递协议（需求 4）----------------------------------------------

    async def begin_incremental_reply(
        self, recipient: Any
    ) -> Optional[IncrementalReplyHandle]:
        """开始一次增量回复。

        没有 sink 时返回 ``None``——协议规定调用方必须能接受 ``None`` 并退回整段
        投递，因此非流式路由上这一步就把整条链路关掉了，不会有任何多余开销。
        """
        if self._stream_sink is None:
            return None
        self._delivered = ""
        # message_id / chat_id 在这条路径上没有平台含义：浏览器端的「那条消息」
        # 就是这次请求的流本身。填固定值而不是伪造一个 ID，避免让人以为它可用于
        # 后续的撤回或引用。
        return IncrementalReplyHandle(message_id="webui-stream", chat_id="webui")

    async def update_incremental_reply(
        self, handle: IncrementalReplyHandle, text: str
    ) -> None:
        """把新增的那一段送进流。

        ``text`` 是到目前为止的全文（协议约定）。这里只送 ``text`` 相对已交付部分
        新增的尾巴。**上游重写了已交付的前缀时退回整段重发**：模型极少这样做，
        但真发生时静默按尾巴追加会让浏览器端拼出一段与服务端不同的文本，
        而两边都认为自己是对的。
        """
        if self._stream_sink is None:
            return
        if self._delivered and not text.startswith(self._delivered):
            await self._stream_sink("reset", text)
            self._delivered = text
            return
        delta = text[len(self._delivered) :]
        if not delta:
            return
        self._delivered = text
        await self._stream_sink("delta", delta)

    async def finish_incremental_reply(
        self, handle: IncrementalReplyHandle, text: str
    ) -> None:
        """收尾：补齐最后一段，让流里累积出的文本与最终回复逐字一致。

        节流或最后一个片段没触发推送时，浏览器端会停在半句话上而日志显示成功。
        """
        await self.update_incremental_reply(handle, text)

    @property
    def streamed_length(self) -> int:
        """已经通过流交付的字符数。调用方据此判断要不要补发完整文本。"""
        return len(self._delivered)


class WebUIStreamSink:
    """把增量投递变成一串 SSE 事件。

    做成独立的小类而不是一个闭包，是因为它有三件事必须成对：队列、结束哨兵、
    以及「生产者已经结束」这个事实。散在路由里时最容易漏掉的是最后一件——
    生产者异常退出而没有放哨兵，消费端会永远等在 ``queue.get()`` 上：
    浏览器停在「正在生成」，而后端已经没有人在干活了。
    """

    #: 放进队列表示「不会再有事件了」。用独立哨兵对象而不是 ``None``：
    #: ``None`` 是一个可能的合法负载，用它当哨兵会在某天变成一个静默截断。
    _DONE = object()

    def __init__(self) -> None:
        self._queue: asyncio.Queue = asyncio.Queue()

    async def emit(self, kind: str, text: str) -> None:
        await self._queue.put((kind, text))

    def close(self) -> None:
        self._queue.put_nowait(self._DONE)

    async def drain(self):
        """依次交出事件，直到生产者放下哨兵。"""
        while True:
            item = await self._queue.get()
            if item is self._DONE:
                return
            yield item
