import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from mcp import types

from kirara_ai.config.global_config import MCPServerConfig
from kirara_ai.mcp_module.models import MCPConnectionState
from kirara_ai.mcp_module.server import MCPServer


# 测试配置
@pytest.fixture
def stdio_config():
    return MCPServerConfig(
        id="test-stdio",
        connection_type="stdio",
        command="python",
        args=["-m", "mcp.server"]
    )

@pytest.fixture
def sse_config():
    return MCPServerConfig(
        id="test-sse",
        connection_type="sse",
        url="http://localhost:8000/sse"
    )

@pytest.fixture
def http_config():
    return MCPServerConfig(
        id="test-http",
        server={
            "type": "http",
            "url": "https://mcp.example.test/mcp",
            "headers": {"Authorization": "Bearer test"},
        },
    )

@pytest.fixture
def invalid_config():
    return MCPServerConfig(
        id="test-invalid",
        connection_type="invalid"
    )

# 模拟 MCP 客户端会话
class MockClientSession:
    def __init__(self):
        self.initialize = AsyncMock()
        self.list_tools = AsyncMock(return_value=types.ListToolsResult(tools=[]))
        self.call_tool = AsyncMock(return_value=types.CallToolResult(content=[types.TextContent(text="114514", type="text")], isError=False))
        self.complete = AsyncMock(return_value={})
        self.get_prompt = AsyncMock(return_value="测试提示词")
        self.list_prompts = AsyncMock(return_value=[])
        self.list_resources = AsyncMock(return_value=[])
        self.list_resource_templates = AsyncMock(return_value=[])
        self.read_resource = AsyncMock(return_value="资源内容")
        self.subscribe_resource = AsyncMock(return_value={})
        self.unsubscribe_resource = AsyncMock(return_value={})

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

# 测试基本初始化
def test_init(stdio_config):
    server = MCPServer(stdio_config)
    assert server.server_config == stdio_config
    assert server.session is None
    assert server.state == MCPConnectionState.DISCONNECTED
    assert server._lifecycle_task is None
    assert not server._shutdown_event.is_set()
    assert not server._connected_event.is_set()


def test_transport_startup_timeout_uses_interoperable_default(stdio_config):
    assert stdio_config.server.startup_timeout_ms == 120_000


def test_transport_startup_timeout_is_configurable():
    config = MCPServerConfig(
        id="custom-timeout",
        server={
            "type": "stdio",
            "command": "python",
            "args": [],
            "startup_timeout_ms": 7_500,
        },
    )

    assert config.server.startup_timeout_ms == 7_500


@pytest.mark.asyncio
async def test_list_client_roots_returns_only_explicit_local_roots(stdio_config, tmp_path: Path):
    allowed = tmp_path / "workspace"
    allowed.mkdir()
    symlink_target = tmp_path / "outside"
    symlink_target.mkdir()
    symlink = tmp_path / "linked"
    try:
        symlink.symlink_to(symlink_target, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symbolic links are unavailable in this environment")

    stdio_config.server.roots = [
        str(allowed),
        symlink.as_uri(),
        "https://example.invalid/remote-root",
        str(tmp_path / "missing"),
    ]
    result = await MCPServer(stdio_config).list_client_roots_callback(None)

    assert [str(root.uri) for root in result.roots] == [allowed.resolve().as_uri()]
    assert result.roots[0].name == "workspace"


@pytest.mark.asyncio
async def test_connect_declares_roots_callback(stdio_config):
    with patch("kirara_ai.mcp_module.server.stdio_client") as mock_stdio_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()) as mock_session:
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_stdio_client.return_value = mock_client

        server = MCPServer(stdio_config)
        assert await server.connect() is True
        assert mock_session.call_args.kwargs["list_roots_callback"] == server.list_client_roots_callback
        assert await server.disconnect() is True

# 测试连接和断开连接
@pytest.mark.asyncio
async def test_connect_disconnect_stdio(stdio_config):
    with patch("kirara_ai.mcp_module.server.stdio_client") as mock_stdio_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()):
        
        # 设置模拟返回值
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_stdio_client.return_value = mock_client
        
        server = MCPServer(stdio_config)
        
        # 测试连接
        connect_result = await server.connect()
        assert connect_result is True
        assert server.state == MCPConnectionState.CONNECTED
        
        # 测试断开连接
        disconnect_result = await server.disconnect()
        assert disconnect_result is True
        assert server.state == MCPConnectionState.DISCONNECTED


@pytest.mark.asyncio
async def test_connect_waits_using_configured_startup_timeout(stdio_config):
    stdio_config.server.startup_timeout_ms = 7_500
    wait_timeouts = []

    with patch("kirara_ai.mcp_module.server.stdio_client") as mock_stdio_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_stdio_client.return_value = mock_client

        original_wait_for = asyncio.wait_for

        async def record_wait_for(awaitable, timeout):
            wait_timeouts.append(timeout)
            return await original_wait_for(awaitable, timeout)

        server = MCPServer(stdio_config)
        with patch.object(asyncio, "wait_for", new=record_wait_for):
            assert await server.connect() is True

        assert wait_timeouts[0] == pytest.approx(7.5)
        assert await server.disconnect() is True

@pytest.mark.asyncio
async def test_connect_disconnect_sse(sse_config):
    with patch("kirara_ai.mcp_module.server.sse_client") as mock_sse_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()):
        
        # 设置模拟返回值
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_sse_client.return_value = mock_client
        
        server = MCPServer(sse_config)
        
        # 测试连接
        connect_result = await server.connect()
        assert connect_result is True
        assert server.state == MCPConnectionState.CONNECTED
        
        # 测试断开连接
        disconnect_result = await server.disconnect()
        assert disconnect_result is True
        assert server.state == MCPConnectionState.DISCONNECTED

@pytest.mark.asyncio
async def test_connect_disconnect_http_uses_streamable_http_transport(http_config):
    with patch("kirara_ai.mcp_module.server.streamablehttp_client") as mock_http_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()):
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(
            return_value=(AsyncMock(), AsyncMock(), lambda: "session-id")
        )
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_http_client.return_value = mock_client

        server = MCPServer(http_config)

        assert await server.connect() is True
        mock_http_client.assert_called_once_with(
            "https://mcp.example.test/mcp",
            headers={"Authorization": "Bearer test"},
        )
        assert await server.disconnect() is True

@pytest.mark.asyncio
async def test_connect_invalid_config(invalid_config):
    server = MCPServer(invalid_config)
    connect_result = await server.connect()
    assert connect_result is False
    assert server.state == MCPConnectionState.ERROR

# 测试连接超时
@pytest.mark.asyncio
async def test_connect_timeout(stdio_config):
    with patch("kirara_ai.mcp_module.server.stdio_client") as mock_stdio_client, \
         patch("kirara_ai.mcp_module.server.ClientSession") as mock_session:
        
        # 设置模拟返回值，但不设置连接完成事件
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(side_effect=lambda: asyncio.sleep(60))  # 模拟长时间操作
        mock_stdio_client.return_value = mock_client
        
        server = MCPServer(stdio_config)
        
        # 修改超时时间以加快测试
        async def timeout_wait_for(awaitable, timeout):
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise asyncio.TimeoutError

        with patch.object(asyncio, "wait_for", side_effect=timeout_wait_for):
            connect_result = await server.connect()
            assert connect_result is False

# 测试工具相关方法
@pytest.mark.asyncio
async def test_tool_methods(stdio_config):
    with patch("kirara_ai.mcp_module.server.stdio_client") as mock_stdio_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()):
        
        # 设置模拟返回值
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_stdio_client.return_value = mock_client
        
        server = MCPServer(stdio_config)
        await server.connect()
        
        # 测试获取工具列表
        tools = await server.get_tools()
        assert isinstance(tools, types.ListToolsResult)
        
        # 测试调用工具
        result = await server.call_tool("test_tool", {"arg": "value"})
        assert isinstance(result, types.CallToolResult)
        await server.disconnect()

# 测试补全方法
@pytest.mark.asyncio
async def test_complete(stdio_config):
    with patch("kirara_ai.mcp_module.server.stdio_client") as mock_stdio_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()):
        
        # 设置模拟返回值
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_stdio_client.return_value = mock_client
        
        server = MCPServer(stdio_config)
        await server.connect()
        
        result = await server.complete("test_prompt", {"temperature": 0.7})
        assert result == {}
        await server.disconnect()

# 测试提示词相关方法
@pytest.mark.asyncio
async def test_prompt_methods(stdio_config):
    with patch("kirara_ai.mcp_module.server.stdio_client") as mock_stdio_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()):
        
        # 设置模拟返回值
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_stdio_client.return_value = mock_client
        
        server = MCPServer(stdio_config)
        await server.connect()
        
        # 测试获取提示词
        prompt = await server.get_prompt("test_prompt", {})
        assert prompt == "测试提示词"
        
        # 测试获取提示词列表
        prompts = await server.list_prompts()
        assert prompts == []
        await server.disconnect()

# 测试资源相关方法
@pytest.mark.asyncio
async def test_resource_methods(stdio_config):
    with patch("kirara_ai.mcp_module.server.stdio_client") as mock_stdio_client, \
         patch("kirara_ai.mcp_module.server.ClientSession", return_value=MockClientSession()):
        
        # 设置模拟返回值
        mock_client = MagicMock()
        mock_client.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_stdio_client.return_value = mock_client
        
        server = MCPServer(stdio_config)
        await server.connect()
        
        # 测试获取资源列表
        resources = await server.list_resources()
        assert resources == []
        
        # 测试获取资源模板列表
        templates = await server.list_resource_templates()
        assert templates == []
        
        # 测试读取资源
        content = await server.read_resource("http://localhost/test-resource")
        assert content == "资源内容"
        
        # 测试订阅资源
        sub_result = await server.subscribe_resource("http://localhost/test-resource")
        assert sub_result == {}
        
        # 测试取消订阅资源
        unsub_result = await server.unsubscribe_resource("http://localhost/test-resource")
        assert unsub_result == {} 
        
        await server.disconnect()
