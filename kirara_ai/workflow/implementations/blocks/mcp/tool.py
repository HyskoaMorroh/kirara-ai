from base64 import b64decode
from typing import Annotated, Any, Dict, List, Sequence

from mcp import types

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format import tool
from kirara_ai.llm.format.message import LLMToolResultContent
from kirara_ai.llm.format.tool import CallableWrapper, Tool, ToolCall
from kirara_ai.logger import get_logger
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.media.manager import MediaManager
from kirara_ai.media.types.media_type import MediaType
from kirara_ai.workflow.core.block import Block, Output
from kirara_ai.workflow.core.block.param import ParamMeta


def get_enabled_mcp_tools(container: DependencyContainer, block: Block) -> List[str]:
    mcp_manager = container.resolve(MCPServerManager)
    return list(mcp_manager.get_tools().keys())


class MCPToolProvider(Block):
    """
    提供MCP工具调用工具

    """
    name = "mcp_tool_provider"
    description = "输出选中的 MCP 工具列表，连到「LLM: 执行对话并调用工具」即可让模型使用这些工具。"
    outputs = {
        "tools": Output("tools", "工具列表", List[Tool], "可供模型调用的工具列表")
    }
    container: DependencyContainer

    def __init__(self, enabled_tools: Annotated[List[str], ParamMeta(label="启用工具列表", description="勾选允许模型调用的 MCP 工具，需先在 MCP 页面连接服务器", options_provider=get_enabled_mcp_tools)]):
        self.logger = get_logger("MCPCallTool")
        self.enabled_tools = enabled_tools

    async def _call_tool(self, tool_call: ToolCall) -> LLMToolResultContent:
        """提供MCP工具调用执行回调"""
        mcp_manager = self.container.resolve(MCPServerManager)

        function = tool_call.function
        if function is None or not function.name:
            raise ValueError("MCP工具调用缺少工具名称")
        if not tool_call.id:
            raise ValueError("MCP工具调用缺少调用 ID")
        tool_name = function.name

        arguments = function.arguments or {}
        # The manager is the single execution boundary for policy, connection
        # state, confirmation and audit.  Do not call the server instance here.
        result = await mcp_manager.call_tool(
            tool_name,
            arguments,
            agent_allowlist=frozenset(self.enabled_tools),
            session_allowlist=frozenset(self.enabled_tools),
            workflow_allowlist=frozenset(self.enabled_tools),
            confirmed=False,
        )
        if result is None:
            return LLMToolResultContent(
                id=tool_call.id,
                name=tool_name,
                content=[tool.TextContent(text="工具调用未执行：工具不可用、权限不足或需要确认。")],
                isError=True,
            )
        
        tool_result = await self._create_tool_result(
            tool_call.id, tool_name, result.content
        )

        tool_result.isError = result.isError
        self.logger.info(f"工具调用结果: {tool_result}")
        return tool_result

    def execute(self) -> Dict[str, Any]:
        """
        提供MCP工具列表

        Returns:
            包含工具列表的字典
        """
        mcp_manager = self.container.resolve(MCPServerManager)
        mcp_tools = mcp_manager.get_tools()
        built_tools = []
        for tool_name, tool_info in mcp_tools.items():
            if tool_name in self.enabled_tools:
                built_tools.append(
                    Tool(
                        name=tool_name,
                        parameters=tool_info.tool_info.inputSchema,
                        description=tool_info.tool_info.description or "",
                        invokeFunc=CallableWrapper(self._call_tool)
                    )
                )
        return {
            "tools": built_tools
        }

    async def _create_tool_result(
        self, tool_id: str, tool_name: str, content: Sequence[object]
    ) -> LLMToolResultContent:
        """创建工具调用结果"""
        converted_content: List[tool.TextContent | tool.MediaContent] = []
        for item in content:
            if isinstance(item, types.TextContent):
                converted_content.append(tool.TextContent(
                    text=item.text
                ))
            elif isinstance(item, types.ImageContent):
                data = b64decode(item.data)
                media_type = MediaType.from_mime(item.mimeType)
                format = item.mimeType.split("/")[1]
                media_id = await self.container.resolve(MediaManager).register_from_data(data, format=format, media_type=media_type)
                converted_content.append(tool.MediaContent(
                    media_id=media_id,
                    mime_type=item.mimeType,
                    data=data
                ))
        return LLMToolResultContent(
            id=tool_id,
            name=tool_name,
            content=converted_content
        )
