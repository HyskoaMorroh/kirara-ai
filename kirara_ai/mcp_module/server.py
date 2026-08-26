import asyncio
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Optional
from urllib.parse import unquote, urlparse

import anyio
import anyio.lowlevel
from mcp import ClientSession, StdioServerParameters, stdio_client, types
from mcp.client.session import MessageHandlerFnT
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.session import RequestResponder
from pydantic import AnyUrl

from kirara_ai.config.global_config import MCPServerConfig
from kirara_ai.logger import get_logger
from .models import MCPConnectionState

logger = get_logger("MCP.Server")

class MCPServer:
    """
    MCP (Model Control Protocol) 服务器客户端类
    
    用于与 MCP 服务器进行通信，支持 stdio、SSE 和 Streamable HTTP 三种连接模式。
    提供工具调用、补全、资源管理等功能。
    
    本类为 mcp.ClientSession 的代理，
    使其适应 Kirara AI 的生命周期。
    """
    session: Optional[ClientSession] = None
    state: MCPConnectionState = MCPConnectionState.DISCONNECTED
    message_handler: Optional[MessageHandlerFnT] = None
    def __init__(self, server_config: MCPServerConfig):
        """
        初始化 MCP 服务器客户端
        
        Args:
            server_config: MCP 服务器配置
        """
        self.server_config = server_config
        self.session = None
        self.state = MCPConnectionState.DISCONNECTED
        self._lifecycle_task = None
        self._shutdown_event = asyncio.Event()
        self._connected_event = asyncio.Event()
        self._client = None
        self.message_handler = None

    async def connect(self):
        """
        连接到 MCP 服务器
        
        根据配置连接到 MCP 服务器，并初始化会话
        
        Returns:
            bool: 连接是否成功
        """
        if self.state != MCPConnectionState.DISCONNECTED and self.state != MCPConnectionState.ERROR:
            return False
            
        try:
            self.state = MCPConnectionState.CONNECTING
            
            # 重置事件
            self._shutdown_event.clear()
            self._connected_event.clear()
            
            # 创建并启动生命周期任务
            if self._lifecycle_task is None or self._lifecycle_task.done():
                self._lifecycle_task = asyncio.create_task(self._lifecycle_manager())
            
            # 等待连接完成或超时
            try:
                await asyncio.wait_for(
                    self._connected_event.wait(),
                    timeout=self.server_config.server.startup_timeout_ms / 1000.0,
                )
                if self.state != MCPConnectionState.CONNECTED:
                    # 连接失败
                    return False
                return True
            except asyncio.TimeoutError:
                logger.error(f"连接到 MCP 服务器 {self.server_config.id} 超时")
                await self.disconnect()  # 超时时断开连接
                return False
                
        except Exception as e:
            self.state = MCPConnectionState.ERROR
            logger.opt(exception=e).error(f"连接 MCP 服务器 {self.server_config.id} 时发生错误")
            return False
    
    async def disconnect(self):
        """
        断开与 MCP 服务器的连接
        
        Returns:
            bool: 断开连接是否成功
        """
        if self.state == MCPConnectionState.DISCONNECTED:
            return True
            
        try:
            self.state = MCPConnectionState.DISCONNECTING
            
            # 发送关闭信号
            self._shutdown_event.set()
            
            # 等待生命周期任务完成
            if self._lifecycle_task and not self._lifecycle_task.done():
                try:
                    await asyncio.wait_for(self._lifecycle_task, timeout=10.0)
                except asyncio.TimeoutError:
                    # 如果任务没有及时完成，取消它
                    self._lifecycle_task.cancel()
                    try:
                        await self._lifecycle_task
                    except (asyncio.CancelledError, Exception):
                        pass
            
            self.state = MCPConnectionState.DISCONNECTED
            return True
        except Exception as e:
            self.state = MCPConnectionState.ERROR
            logger.opt(exception=e).error(f"断开 MCP 服务器 {self.server_config.id} 连接时发生错误")
            return False
        
    async def _lifecycle_manager(self):
        """
        服务器生命周期管理任务
        
        负责服务器的连接、运行和断开连接的完整生命周期
        """
        exit_stack = AsyncExitStack()
        
        try:
            # 初始化连接
            transport = self.server_config.server
            if transport.type == "stdio":
                if transport.command is None:
                    raise ValueError("stdio 连接类型需要提供命令")
                self._client = stdio_client(StdioServerParameters(
                    command=transport.command,
                    args=transport.args,
                    env=transport.env or None,
                    cwd=transport.cwd,
                ))
            elif transport.type == "sse":
                if transport.url is None:
                    raise ValueError("sse 连接类型需要提供 url")
                self._client = sse_client(transport.url, headers=transport.headers or None)
            elif transport.type == "http":
                if transport.url is None:
                    raise ValueError("http 连接类型需要提供 url")
                self._client = streamablehttp_client(
                    transport.url,
                    headers=transport.headers or None,
                )
            else:
                raise ValueError(f"不支持的服务器连接类型: {transport.type}")
            
            # 使用 exit_stack 管理资源
            client_streams = await exit_stack.enter_async_context(self._client)
            # Streamable HTTP returns a third session-id callback; ClientSession
            # consumes the same read/write pair as stdio and SSE.
            read, write = client_streams[:2]
            self.session = await exit_stack.enter_async_context(
                ClientSession(
                    read,
                    write,
                    list_roots_callback=self.list_client_roots_callback,
                    message_handler=self.message_handler_callback,
                )
            )
            
            # 初始化会话
            await self.session.initialize()
            
            # 更新状态并通知连接完成
            self.state = MCPConnectionState.CONNECTED
            self._connected_event.set()
            
            # 等待关闭信号
            await self._shutdown_event.wait()
            
        except Exception as e:
            # 连接失败
            self.state = MCPConnectionState.ERROR
            self._connected_event.set()  # 通知连接过程已完成（虽然是失败的）
            logger.opt(exception=e).error(f"MCP server {self.server_config.id} lifecycle task error")
        finally:
            # 清理资源
            self.session = None
            self._client = None
            try:
                # 关闭所有资源
                await exit_stack.aclose()
            except Exception as e:
                logger.opt(exception=e).error(f"error occured during shutting down handle of: {self.server_config.id}")
            
            # 如果状态仍然是 DISCONNECTING，则更新为 DISCONNECTED
            if self.state == MCPConnectionState.DISCONNECTING:
                self.state = MCPConnectionState.DISCONNECTED

    # 工具相关方法
    
    async def get_tools(self) -> types.ListToolsResult:
        """获取可用工具列表"""
        assert self.session is not None 
        return await self.session.list_tools()
    
    async def call_tool(self, tool_name: str, tool_args: Optional[dict] = None) -> types.CallToolResult:
        """
        调用指定工具
        
        Args:
            tool_name: 工具名称
            tool_args: 工具参数
            
        Returns:
            工具调用结果
        """
        assert self.session is not None
        return await self.session.call_tool(tool_name, tool_args)
    
    async def complete(self, prompt: str, tool_args: dict):
        """
        使用模型进行补全
        
        Args:
            prompt: 提示文本
            tool_args: 补全参数
            
        Returns:
            补全结果
        """
        assert self.session is not None
        return await self.session.complete(types.PromptReference(name=prompt, type="ref/prompt"), tool_args)
    
    # 提示词相关方法
    
    async def get_prompt(self, prompt_name: str, prompt_args: dict[str, str] | None = None) -> types.GetPromptResult:
        """
        获取指定提示词
        
        Args:
            prompt_name: 提示词名称
            prompt_args: 提示词参数
            
        Returns:
            提示词内容
        """
        assert self.session is not None
        return await self.session.get_prompt(prompt_name, prompt_args)
    
    async def list_prompts(self) -> types.ListPromptsResult:
        """获取可用提示词列表"""
        assert self.session is not None
        return await self.session.list_prompts()
    
    # 资源相关方法
    
    async def list_resources(self) -> types.ListResourcesResult:
        """获取可用资源列表"""
        assert self.session is not None
        return await self.session.list_resources()
    
    async def list_resource_templates(self) -> types.ListResourceTemplatesResult:
        """获取可用资源模板列表"""
        assert self.session is not None
        return await self.session.list_resource_templates()
    
    async def read_resource(self, uri: str) -> types.ReadResourceResult:
        """
        读取指定资源
        
        Args:
            uri: 资源名称
            
        Returns:
            资源内容
        """
        assert self.session is not None
        return await self.session.read_resource(AnyUrl(uri))
    
    async def subscribe_resource(self, uri: str) -> types.EmptyResult:
        """
        订阅指定资源
        
        Args:
            uri: 资源名称
            
        Returns:
            订阅结果
        """
        assert self.session is not None
        return await self.session.subscribe_resource(AnyUrl(uri))
    
    async def unsubscribe_resource(self, uri: str) -> types.EmptyResult:
        """
        取消订阅指定资源
        
        Args:
            uri: 资源名称
            
        Returns:
            取消订阅结果
        """
        assert self.session is not None
        return await self.session.unsubscribe_resource(AnyUrl(uri))
    
    async def message_handler_callback(
            self,
            message: RequestResponder[types.ServerRequest, types.ClientResult]
            | types.ServerNotification
            | Exception,
        ) -> None:
        """
        消息处理回调函数
        Args:
            message: 请求响应器或通知或异常
        """
        if self.message_handler is None:
            logger.warning(f"MCP客户端接收到服务器{self.server_config.id}的通知，但未对其进行处理: {message}")
            await anyio.lowlevel.checkpoint()
            return
        await self.message_handler(message)

    async def list_client_roots_callback(self, ctx) -> types.ListRootsResult | types.ErrorData:
        """
        列出客户端允许的资源根目录

        Args:

        Returns:
            types.ListRootsResult: 资源根目录列表
        """
        roots: list[types.Root] = []
        for configured_root in self.server_config.server.roots:
            try:
                path = self._root_path(configured_root)
                if path.is_symlink() or not path.exists():
                    logger.warning(
                        "Skipping unavailable or symlinked MCP root for %s",
                        self.server_config.id,
                    )
                    continue
                roots.append(types.Root(uri=path.as_uri(), name=path.name or str(path)))
            except (OSError, ValueError, TypeError):
                logger.warning(
                    "Skipping invalid MCP root for %s",
                    self.server_config.id,
                )
        return types.ListRootsResult(roots=roots)

    @staticmethod
    def _root_path(value: str) -> Path:
        """Resolve one explicitly configured root without accepting remote URIs."""

        if not isinstance(value, str) or not value.strip():
            raise ValueError("MCP root must be a non-empty string")
        candidate = value.strip()
        parsed = urlparse(candidate)
        is_windows_drive_path = (
            len(candidate) >= 2
            and candidate[0].isalpha()
            and candidate[1] == ":"
        )
        if parsed.scheme and parsed.scheme != "file" and not is_windows_drive_path:
            raise ValueError("MCP roots must use local paths or file URLs")
        if parsed.scheme == "file":
            if parsed.netloc not in ("", "localhost"):
                raise ValueError("MCP file root host is not supported")
            candidate = unquote(parsed.path)
            if len(candidate) >= 3 and candidate[0] == "/" and candidate[2] == ":":
                candidate = candidate[1:]
        path = Path(candidate).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        # ``resolve()`` follows links, which would turn a forbidden link into
        # an apparently safe target before the caller can inspect it.
        return Path(path.absolute())

    async def send_ping(self) -> None:
        assert self.session is not None
        await self.session.send_ping()

    async def send_notification(self, notification: types.ClientNotification) -> None:
        """
        给服务器发消息，例如资源根更改
        不使用 ClientSession 的 send_roots_list_changed，因为它只支持发送 RootsListChangedNotification。
        这里使用其父对象 BaseSession 的 send_notification，其支持发送所有 ClientNotification。

        Args:
            notification: 客户端通知
        """
        assert self.session is not None
        await self.session.send_notification(notification)

    async def sampling_callback(self):
        """
        采样回调函数
        """
    async def logging_callback(self):
        """
        日志回调函数
        """
