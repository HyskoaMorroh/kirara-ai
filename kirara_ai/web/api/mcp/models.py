from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MCPServerInfo(BaseModel):
    """MCP服务器信息"""
    id: str
    description: Optional[str] = None
    connection_type: str
    command: Optional[str] = None
    args: str = Field(default="")
    url: Optional[str] = None
    connection_state: str

class MCPToolInfo(BaseModel):
    """MCP工具信息"""
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any] = Field(default_factory=dict)


class MCPPromptInfo(BaseModel):
    """MCP提示词信息

    WebUI 的提示词卡片按 `id` 显示标题、按 `description` 显示说明，并把 `id`
    回传给采样接口，因此这里在 MCP 原始字段之外额外暴露 `id`（取 name）。
    """
    id: str
    name: str
    description: Optional[str] = None
    arguments: List[Dict[str, Any]] = Field(default_factory=list)


class MCPResourceInfo(BaseModel):
    """MCP资源信息

    WebUI 的资源卡片按 `id` 显示标题，并把 `id` 拼进资源读取接口的路径，
    因此这里在 MCP 原始字段之外额外暴露 `id`（取 name，缺失时回退到 uri）。
    """
    id: str
    name: str
    uri: str
    description: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None


class MCPPromptSampleRequest(BaseModel):
    """提示词采样请求"""
    promptId: str
    text: str = Field(default="")
    temperature: Optional[float] = None

class MCPServerList(BaseModel):
    """MCP服务器列表"""
    items: List[MCPServerInfo]
    total: int
    page: int
    page_size: int
    total_pages: int


class MCPStatistics(BaseModel):
    """MCP统计信息"""
    total_servers: int
    stdio_servers: int
    sse_servers: int
    connected_servers: int
    disconnected_servers: int
    error_servers: int
    total_tools: int


class MCPServerCreateRequest(BaseModel):
    """创建MCP服务器请求"""
    id: str
    description: Optional[str] = None
    command: str
    args: str
    connection_type: str


class MCPServerUpdateRequest(BaseModel):
    """更新MCP服务器请求"""
    description: Optional[str] = None
    command: Optional[str] = None
    args: str = Field(default="")
    connection_type: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[Dict[str, str]] = None
    env: Optional[Dict[str, str]] = None

