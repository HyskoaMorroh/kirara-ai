
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.implementations.blocks.im.messages import GetIMMessage, SendIMMessage
from kirara_ai.workflow.implementations.blocks.im.states import ToggleEditState
from kirara_ai.workflow.implementations.blocks.llm.chat import (ChatCompletion, ChatMessageConstructor,
                                                                ChatResponseConverter)
from kirara_ai.workflow.implementations.blocks.memory.chat_memory import ChatMemoryQuery, ChatMemoryStore
from kirara_ai.workflow.implementations.blocks.system.basic import TextBlock

# 人设提示词的唯一来源见 factories/persona.py。这段文本原先同时写死在本文件、
# presets/chat/talk_break.yaml 和 data/workflows/chat/normal.yaml 中，
# 三份副本很容易改一处漏两处；代码侧现在统一引用该常量。
from kirara_ai.workflow.implementations.factories.persona import (DEFAULT_PERSONA_SYSTEM_PROMPT,
                                                                  DEFAULT_USER_PROMPT_FORMAT)


class DefaultWorkflowFactory:
    """
    构建默认的聊天工作流，提供基本的聊天 bot 能力。
    """

    @staticmethod
    def create_default_workflow() -> WorkflowBuilder:
        """使用 DSL 创建默认工作流"""
        # 提示词正文统一维护在 factories/persona.py，避免多处副本互相漂移
        system_prompt = DEFAULT_PERSONA_SYSTEM_PROMPT

        user_prompt = DEFAULT_USER_PROMPT_FORMAT

        builder = (
            WorkflowBuilder("聊天 - 角色扮演")
            .use(GetIMMessage, name="get_message")
            .parallel(
                [
                    (ToggleEditState, {"is_editing": True}),
                    (ChatMemoryQuery, "query_memory", {"scope_type": "group"}),
                ]
            )
            .chain(TextBlock, name="system_prompt", text=system_prompt)
            .chain(TextBlock, name="user_prompt", text=user_prompt)
            .chain(
                ChatMessageConstructor,
                wire_from=[
                    "get_message",
                    "user_prompt",
                    "query_memory",
                    "get_message",
                    "system_prompt",
                ],
            )
            .chain(ChatCompletion, name="llm_chat")
            .chain(ChatResponseConverter)
            .parallel(
                [
                    SendIMMessage,
                    (
                        ChatMemoryStore,
                        {"scope_type": "group"},
                        ["get_message", "llm_chat"],
                    ),
                ]
            )
        )
        builder.description = "标准的文本对话功能，扮演刘思思的角色和大家聊天~"
        return builder
