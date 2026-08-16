from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.implementations.blocks.im.messages import GetIMMessage
from kirara_ai.workflow.implementations.blocks.memory.chat_memory import ChatMemoryStore


class MemoryWorkflowFactory:
    """记忆相关工作流工厂

    这里的工作流原先只以 `data/workflows/chat/memory_store.yaml` 形式存在，
    而 data 目录不随 wheel 分发，导致 pip 安装后兜底规则引用的
    `chat:memory_store` 找不到对应工作流。补成代码预设后，纯 pip 环境也可用。
    """

    @staticmethod
    def create_memory_store_workflow() -> WorkflowBuilder:
        """创建「只记录不回复」的工作流

        用作兜底规则的目标：把没有触发任何功能的普通聊天记录进记忆，
        后续对话通过「记忆: 查询记忆」就能读到这些上下文，但本身不回复消息。
        """
        builder = (
            WorkflowBuilder("记录聊天内容")
            .use(GetIMMessage, name="get_message")
            .chain(ChatMemoryStore, name="store_memory", scope_type="group")
        )
        builder.description = "默默记下大家的聊天内容，可以使用查询记忆模块读取出来。"
        return builder
