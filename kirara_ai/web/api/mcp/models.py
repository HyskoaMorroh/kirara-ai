from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kirara_ai.config.global_config import MCPAppsConfig, MCPServerConfig, MCPTransportConfig


REDACTED_SECRET = "********"


class MCPTransportInfo(BaseModel):
    """Public interoperable transport shape with secret values redacted."""

    type: str
    command: Optional[str] = None
    args: List[str] = Field(default_factory=list)
    env: Dict[str, str] = Field(default_factory=dict)
    cwd: Optional[str] = None
    url: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class MCPServerInfo(BaseModel):
    """Canonical MCP entry returned by the API."""

    id: str
    name: str
    server: MCPTransportInfo
    apps: MCPAppsConfig
    description: str = ""
    tags: List[str] = Field(default_factory=list)
    homepage: Optional[str] = None
    docs: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    connection_state: str

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class MCPToolInfo(BaseModel):
    name: str
    description: Optional[str] = None
    input_schema: Dict[str, Any] = Field(default_factory=dict)
    server_id: Optional[str] = None


class MCPPromptInfo(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    arguments: List[Dict[str, Any]] = Field(default_factory=list)


class MCPResourceInfo(BaseModel):
    id: str
    name: str
    uri: str
    description: Optional[str] = None
    mime_type: Optional[str] = None
    size: Optional[int] = None


class MCPPromptSampleRequest(BaseModel):
    promptId: str
    arguments: Dict[str, str] = Field(default_factory=dict)
    text: str = Field(default="")
    temperature: Optional[float] = None


class MCPServerList(BaseModel):
    items: List[MCPServerInfo]
    total: int
    page: int
    page_size: int
    total_pages: int


class MCPStatistics(BaseModel):
    total_servers: int
    stdio_servers: int
    http_servers: int
    sse_servers: int
    connected_servers: int
    disconnected_servers: int
    error_servers: int
    total_tools: int


class MCPAuditRecord(BaseModel):
    component: str = "mcp"
    timestamp: Optional[str] = None
    server: str
    operation: str
    duration_ms: float
    outcome: str
    correlation_id: Optional[str] = None
    error: Optional[Dict[str, str]] = None


class MCPAuditPage(BaseModel):
    items: List[MCPAuditRecord]
    total: int
    offset: int
    limit: int
    has_more: bool
    persistent: bool = False
    retention_limit: int


class _CanonicalServerPayload(BaseModel):
    """Shared request boundary; legacy fields are accepted only for migration."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    id: str
    name: Optional[str] = None
    server: Optional[MCPTransportConfig] = None
    apps: MCPAppsConfig = Field(default_factory=MCPAppsConfig)
    description: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    homepage: Optional[str] = None
    docs: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return MCPServerConfig.model_validate(data).model_dump(
            mode="python", by_alias=True, exclude_none=False
        )

    def to_config(self, *, server_id: Optional[str] = None) -> MCPServerConfig:
        data = self.model_dump(mode="python", by_alias=True, exclude_none=False)
        if server_id is not None:
            data["id"] = server_id
        return MCPServerConfig.model_validate(data)


class MCPServerCreateRequest(_CanonicalServerPayload):
    pass


class MCPServerUpdateRequest(BaseModel):
    """Partial canonical update; old top-level fields remain migration-only."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    name: Optional[str] = None
    server: Optional[MCPTransportConfig] = None
    apps: Optional[MCPAppsConfig] = None
    description: Optional[str] = None
    tags: Optional[List[str]] = None
    homepage: Optional[str] = None
    docs: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = None

    @model_validator(mode="before")
    @classmethod
    def migrate_payload(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        values = dict(data)
        legacy_keys = {"connection_type", "command", "args", "url", "headers", "env", "cwd", "type"}
        if "server" not in values and any(key in values for key in legacy_keys):
            transport = {
                key: values.pop(key)
                for key in ("type", "command", "args", "url", "headers", "env", "cwd")
                if key in values
            }
            if "connection_type" in values:
                transport["type"] = values.pop("connection_type")
            values["server"] = MCPTransportConfig.model_validate(transport)
        return values

    def update_values(self) -> Dict[str, Any]:
        """Return only fields explicitly supplied by the caller.

        This keeps a partial update from replacing optional canonical fields
        with Pydantic defaults.  Legacy fields are consumed by the validator
        and are never returned to the route layer.
        """
        return self.model_dump(mode="python", by_alias=True, exclude_unset=True)


class MCPToolCallRequest(BaseModel):
    toolName: str
    params: Dict[str, Any] = Field(default_factory=dict)
    agent_id: Optional[str] = None
    confirmation_id: Optional[str] = None
