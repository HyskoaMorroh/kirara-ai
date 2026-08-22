import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from typing import Annotated, Any, Dict, List, Optional

from kirara_ai.im.adapter import IMAdapter
from kirara_ai.im.manager import IMManager
from kirara_ai.im.message import IMMessage, MessageElement, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.block import Block, Input, Output, ParamMeta


def im_adapter_options_provider(container: DependencyContainer, block: Block) -> List[str]:
    return [key for key, _ in container.resolve(IMManager).adapters.items()]

class GetIMMessage(Block):
    """获取 IM 消息"""

    name = "msg_input"
    description = "工作流的起点：取出触发本次执行的聊天消息，并同时给出发送者。"
    container: DependencyContainer
    outputs = {
        "msg": Output("msg", "IM 消息", IMMessage, "获取 IM 发送的最新一条的消息"),
        "sender": Output("sender", "发送者", ChatSender, "获取 IM 消息的发送者"),
    }

    def execute(self, **kwargs) -> Dict[str, Any]:
        msg = self.container.resolve(IMMessage)
        return {"msg": msg, "sender": msg.sender}


class SendIMMessage(Block):
    """发送 IM 消息"""

    # Blocks are executed in worker threads while adapters belong to the main
    # event loop.  A bounded wait keeps a broken IM connection from hanging a
    # workflow forever while still making the block's result reflect delivery.
    SEND_TIMEOUT_SECONDS = 120.0

    name = "msg_sender"
    description = "把消息发回聊天平台。发送对象留空时默认回复给触发消息的人。"
    inputs = {
        "msg": Input("msg", "IM 消息", IMMessage, "要从 IM 发送的消息"),
        "target": Input(
            "target",
            "发送对象",
            ChatSender,
            "要发送给谁，如果填空则默认发送给消息的发送者",
            nullable=True,
        ),
    }
    outputs = {}
    container: DependencyContainer

    def __init__(
        self, im_name: Annotated[Optional[str], ParamMeta(label="聊天平台适配器名称", description="指定用哪个聊天平台实例发送，留空则使用触发本次消息的平台", options_provider=im_adapter_options_provider)] = None
    ):
        self.im_name = im_name

    def execute(
        self, msg: IMMessage, target: Optional[ChatSender] = None
    ) -> Dict[str, Any]:
        src_msg = self.container.resolve(IMMessage)
        if not self.im_name:
            adapter = self.container.resolve(IMAdapter)
        else:
            adapter = self.container.resolve(
                IMManager).get_adapter(self.im_name)
        loop: asyncio.AbstractEventLoop = self.container.resolve(
            asyncio.AbstractEventLoop
        )

        if loop.is_running():
            try:
                running_loop = asyncio.get_running_loop()
            except RuntimeError:
                running_loop = None
            if running_loop is loop:
                raise RuntimeError(
                    "SendIMMessage must run outside its adapter event-loop thread"
                )

            future = asyncio.run_coroutine_threadsafe(
                adapter.send_message(msg, target or src_msg.sender), loop
            )
            try:
                future.result(timeout=self.SEND_TIMEOUT_SECONDS)
            except FutureTimeoutError as exc:
                if future.done():
                    raise
                future.cancel()
                raise TimeoutError(
                    f"IM message sending timed out after {self.SEND_TIMEOUT_SECONDS:g} seconds"
                ) from exc
        else:
            asyncio.run(
                asyncio.wait_for(
                    adapter.send_message(msg, target or src_msg.sender),
                    timeout=self.SEND_TIMEOUT_SECONDS,
                )
            )
        return {"ok": True}

# IMMessage 转纯文本


class IMMessageToText(Block):
    """IMMessage 转纯文本"""

    name = "im_message_to_text"
    description = "把聊天消息中的文字内容提取成纯文本字符串。"
    container: DependencyContainer
    inputs = {"msg": Input("msg", "IM 消息", IMMessage, "待提取文字的聊天消息")}
    outputs = {"text": Output("text", "纯文本", str, "消息中的文字内容")}

    def execute(self, msg: IMMessage) -> Dict[str, Any]:
        return {"text": msg.content}


# 纯文本转 IMMessage
class TextToIMMessage(Block):
    """纯文本转 IMMessage"""

    name = "text_to_im_message"
    description = "把一段文本包装成可发送的聊天消息，可按分段符拆成多条发送。"
    container: DependencyContainer
    inputs = {"text": Input("text", "纯文本", str, "要发送的文字内容")}
    outputs = {"msg": Output("msg", "IM 消息", IMMessage, "包装好的聊天消息")}

    def __init__(self, split_by: Annotated[Optional[str], ParamMeta(label="分段符", description="填写后按该符号把文本拆成多条消息发送，留空则作为一条发送")] = None):
        self.split_by = split_by

    def execute(self, text: str) -> Dict[str, Any]:
        if self.split_by:
            return {"msg": IMMessage(sender=ChatSender.get_bot_sender(), message_elements = [TextMessage(line.strip()) for line in text.split(self.split_by) if line.strip()])}
        else:
            return {"msg": IMMessage(sender=ChatSender.get_bot_sender(), message_elements=[TextMessage(text)])}

# 补充 IMMessage 消息
class AppendIMMessage(Block):
    """补充 IMMessage 消息"""

    name = "concat_im_message"
    description = "在已有聊天消息末尾追加一个消息片段（如图片、文字）。"
    container: DependencyContainer
    inputs = {
        "base_msg": Input("base_msg", "IM 消息", IMMessage, "作为基础的聊天消息"),
        "append_msg": Input("append_msg", "新消息片段", MessageElement, "要追加到末尾的消息片段"),
    }
    outputs = {"msg": Output("msg", "IM 消息", IMMessage, "追加后的完整消息")}

    def execute(self, base_msg: IMMessage, append_msg: MessageElement) -> Dict[str, Any]:
        return {"msg": IMMessage(sender=base_msg.sender, message_elements=base_msg.message_elements + [append_msg])}
