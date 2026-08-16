from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.implementations.blocks.im.basic import ExtractChatSender
from kirara_ai.workflow.implementations.blocks.llm.basic import LLMResponseToText
from kirara_ai.workflow.implementations.blocks.llm.image import SimpleStableDiffusionWebUI
from kirara_ai.workflow.implementations.blocks.mcp.tool import MCPToolProvider
from kirara_ai.workflow.implementations.blocks.memory.clear_memory import ClearMemory
from kirara_ai.workflow.implementations.blocks.system.basic import (CodeBlock, CurrentTimeBlock, TextBlock,
                                                                    TextConcatBlock, TextExtractByRegexBlock,
                                                                    TextReplaceBlock, TextStripMarkdownBlock)

from .game.dice import DiceRoll
from .game.gacha import GachaSimulator
from .im.messages import AppendIMMessage, GetIMMessage, IMMessageToText, SendIMMessage, TextToIMMessage
from .im.states import ToggleEditState
from .llm.chat import ChatCompletion, ChatCompletionWithTools, ChatMessageConstructor, ChatResponseConverter, FunctionCalling
from .memory.chat_memory import ChatMemoryQuery, ChatMemoryStore
from .system.help import GenerateHelp


def register_system_blocks(registry: BlockRegistry):
    """注册系统自带的 block

    第四个参数是展示在 WebUI 节点列表与画布上的中文名，
    第五个参数是节点标题栏的配色，用于在画布上按功能域区分节点：
    文本处理 / IM 消息 / LLM 调用 / 记忆 / 画图 / 游戏 / 系统 / MCP。
    """
    # 各功能域配色。同域节点同色，便于在大型工作流里快速定位。
    COLOR_TEXT = "#6b7a99"      # 基础文本处理：中性蓝灰
    COLOR_IM = "#4a9d7f"        # IM 消息收发：绿
    COLOR_LLM = "#5b8dff"       # LLM 调用：蓝
    COLOR_MEMORY = "#8b7cd8"    # 记忆读写：紫
    COLOR_IMAGE = "#c86fa8"     # 画图：品红
    COLOR_GAME = "#e0954a"      # 游戏娱乐：橙
    COLOR_SYSTEM = "#7d8794"    # 系统功能：灰
    COLOR_MCP = "#3fa89a"       # MCP 工具：青

    # 基础 blocks
    registry.register("text_block", "internal", TextBlock, "基础：文本", COLOR_TEXT)
    registry.register("text_concat_block", "internal", TextConcatBlock, "基础：拼接文本", COLOR_TEXT)
    registry.register("text_replace_block", "internal", TextReplaceBlock, "基础：替换文本", COLOR_TEXT)
    registry.register("text_extract_by_regex_block", "internal", TextExtractByRegexBlock, "基础：正则表达式提取文本", COLOR_TEXT)
    registry.register("text_strip_markdown_block", "internal", TextStripMarkdownBlock, "基础：清除 Markdown 标记", COLOR_TEXT)
    registry.register("current_time_block", "internal", CurrentTimeBlock, "基础：当前时间", COLOR_TEXT)
    registry.register("code", "internal", CodeBlock, "基础：代码", COLOR_SYSTEM)

    # IM 相关 blocks
    registry.register("get_message", "internal", GetIMMessage, "IM: 获取最新消息", COLOR_IM)
    registry.register("send_message", "internal", SendIMMessage, "IM: 发送消息", COLOR_IM)
    registry.register(
        "toggle_edit_state", "internal", ToggleEditState, "IM: 切换编辑状态", COLOR_IM
    )
    registry.register(
        "extract_chat_sender", "internal", ExtractChatSender, "IM: 提取消息发送者", COLOR_IM
    )
    registry.register("append_im_message", "internal", AppendIMMessage, "IM: 补充消息", COLOR_IM)
    registry.register("im_message_to_text", "internal", IMMessageToText, "IM: 消息转文本", COLOR_IM)
    registry.register("text_to_im_message", "internal", TextToIMMessage, "IM: 文本转消息", COLOR_IM)

    # 记忆相关 blocks
    registry.register("chat_memory_query", "internal", ChatMemoryQuery, "记忆: 查询记忆", COLOR_MEMORY)
    registry.register("chat_memory_store", "internal", ChatMemoryStore, "记忆: 存储记忆", COLOR_MEMORY)

    # LLM 相关 blocks
    registry.register(
        "chat_message_constructor",
        "internal",
        ChatMessageConstructor,
        "LLM: 构造对话记录",
        COLOR_LLM,
    )
    registry.register("chat_completion", "internal", ChatCompletion, "LLM: 执行对话", COLOR_LLM)
    registry.register("chat_function_calling", "internal", FunctionCalling, "LLM: 函数调用", COLOR_LLM)
    registry.register("chat_completion_with_tools", "internal", ChatCompletionWithTools, "LLM: 执行对话并调用工具", COLOR_LLM)
    registry.register(
        "chat_response_converter",
        "internal",
        ChatResponseConverter,
        "LLM->IM: 转换消息",
        COLOR_LLM,
    )
    registry.register("llm_response_to_text", "internal", LLMResponseToText, "LLM: 响应转文本", COLOR_LLM)

    # 画图相关 blocks
    registry.register(
        "simple_stable_diffusion_webui",
        "internal",
        SimpleStableDiffusionWebUI,
        "画图: 简单 Stable Diffusion WebUI",
        COLOR_IMAGE,
    )

    # 游戏相关 blocks
    registry.register("dice_roll", "game", DiceRoll, "游戏: 掷骰子", COLOR_GAME)
    registry.register("gacha_simulator", "game", GachaSimulator, "游戏: 抽卡模拟", COLOR_GAME)

    # 系统相关 blocks
    registry.register("generate_help", "system", GenerateHelp, "系统: 生成帮助", COLOR_SYSTEM)
    registry.register("clear_memory", "system", ClearMemory, "系统: 清空记忆", COLOR_MEMORY)

    # MCP 相关 blocks
    registry.register("mcp_tool_provider", "mcp", MCPToolProvider, "MCP: 提供工具", COLOR_MCP)
