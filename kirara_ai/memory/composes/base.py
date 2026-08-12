from abc import ABC, abstractmethod
from typing import List, Optional, Union

from kirara_ai.im.message import IMMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMChatMessage
from kirara_ai.llm.format.response import Message
from kirara_ai.memory.entry import MemoryEntry

# 可组合的消息类型
ComposableMessageType = Union[IMMessage, LLMChatMessage, Message, str]


class MemoryComposer(ABC):
    """记忆组合器抽象类"""
    
    container: DependencyContainer

    @abstractmethod
    def compose(
        self, sender: Optional[ChatSender], message: List[ComposableMessageType]
    ) -> MemoryEntry:
        """将消息转换为记忆条目"""


class MemoryDecomposer(ABC):
    """记忆解析器抽象类"""
    
    container: DependencyContainer

    @abstractmethod
    def decompose(self, entries: List[MemoryEntry]) -> List[ComposableMessageType]:
        """将记忆条目转换为消息"""

    @property
    def empty_message(self) -> ComposableMessageType:
        """空记忆消息"""
        return "<空记忆>"
