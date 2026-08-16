from typing import Annotated, Any, Dict

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.memory.memory_manager import MemoryManager
from kirara_ai.memory.registry import ScopeRegistry
from kirara_ai.workflow.core.block import Block, Input, Output, ParamMeta


class ClearMemory(Block):
    """Block for clearing conversation memory"""

    name = "clear_memory"
    description = "清空指定聊天对象在该级别下的全部记忆，并返回一条提示消息。"
    inputs = {
        "chat_sender": Input("chat_sender", "消息发送者", ChatSender, "要清空记忆的聊天对象")
    }
    outputs = {"response": Output("response", "响应", IMMessage, "清空完成的提示消息")}
    container: DependencyContainer

    def __init__(
        self,
        scope_type: Annotated[
            str, ParamMeta(label="级别", description="要清空记忆的级别，需与写入记忆时使用的级别一致")
        ] = "member",
    ):
        self.scope_type = scope_type

    def execute(self, chat_sender: ChatSender) -> Dict[str, Any]:
        self.memory_manager = self.container.resolve(MemoryManager)

        # Get scope instance
        scope_registry = self.container.resolve(ScopeRegistry)
        self.scope = scope_registry.get_scope(self.scope_type)
        # Clear memory using the manager's method
        self.memory_manager.clear_memory(self.scope, chat_sender)
        return {
            "response": IMMessage(
                sender=chat_sender,
                message_elements=[TextMessage("已清空当前对话的记忆。")],
            )
        }
