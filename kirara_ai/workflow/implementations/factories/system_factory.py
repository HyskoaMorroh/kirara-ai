from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.implementations.blocks.im.messages import GetIMMessage, SendIMMessage
from kirara_ai.workflow.implementations.blocks.memory.clear_memory import ClearMemory
from kirara_ai.workflow.implementations.blocks.system.help import GenerateHelp


class SystemWorkflowFactory:
    """系统相关工作流工厂"""

    @staticmethod
    def create_help_workflow() -> WorkflowBuilder:
        """创建帮助信息工作流"""
        builder = WorkflowBuilder("帮助信息").use(GenerateHelp).chain(SendIMMessage)
        builder.description = "根据当前已启用的调度规则自动汇总一份命令帮助并发送。"
        return builder

    @staticmethod
    def create_clear_memory_workflow() -> WorkflowBuilder:
        """创建清空记忆工作流

        群聊与私聊两个级别的记忆需要分别清理，这里用 wire_from 把两个
        ClearMemory 都显式接到消息来源上——builder 的 parallel() 之后
        current 只会停在第一个分支，若依赖默认自动连线，第二个分支会成为
        无人引用的孤立节点，其输出也不会被 SendIMMessage 使用。
        """
        builder = (
            WorkflowBuilder("清空记忆")
            .use(GetIMMessage, name="get_message")
            .parallel(
                [
                    (ClearMemory, "clear_group_memory", {"scope_type": "group"}, ["get_message"]),
                    (ClearMemory, "clear_member_memory", {"scope_type": "member"}, ["get_message"]),
                ]
            )
            .chain(SendIMMessage, wire_from=["clear_group_memory"])
        )
        builder.description = "清空当前会话的群聊与私聊记忆，并回复一条确认消息。"
        return builder
