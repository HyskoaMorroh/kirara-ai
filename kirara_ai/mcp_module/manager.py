#!/usr/bin/env python
# -*- coding: utf-8 -*-

import asyncio
import json
from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
import time
from typing import Callable, Dict, Iterable, NamedTuple, Optional, Tuple

from mcp import McpError, types
from mcp.shared.session import RequestResponder

from kirara_ai.config.global_config import GlobalConfig, MCPServerConfig
from kirara_ai.config import DATA_PATH
from kirara_ai.agent_runtime.core import (
    principal_can_control_agent,
    resolve_mcp_tool_allowlist,
)
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.extension_host import ExtensionLifecycleHost
from .models import MCPConnectionState
from .server import MCPServer
from .audit_store import MCPAuditStore
from .confirmation_store import MCPConfirmationStore

logger = get_logger("MCP")

class ToolCacheEntry(NamedTuple):
    """工具缓存条目"""
    server_id: str          # 服务器ID
    original_name: str      # 原始工具名称
    tool_info: types.Tool  # 工具信息

class MCPServerManager:
    """MCP服务器管理器，负责管理和控制MCP服务器进程"""

    def __init__(
        self,
        container: DependencyContainer,
        audit_sink: Optional[Callable[[dict], None]] = None,
        confirmation_store: MCPConfirmationStore | None = None,
        audit_store: MCPAuditStore | None = None,
    ):
        """初始化MCP服务器管理器"""
        self.container = container
        self.config = container.resolve(GlobalConfig)
        self.confirmation_store = confirmation_store or MCPConfirmationStore(
            Path(DATA_PATH) / "mcp" / "confirmations.db"
        )
        self.servers: Dict[str, MCPServer] = {}
        self.tools_cache: Dict[str, ToolCacheEntry] = {}
        self.prompts_cache: Dict[str, list[types.Prompt]] = {}
        self.resources_cache: Dict[str, list[types.Resource]] = {}
        # Runtime state is intentionally ephemeral.  Lifecycle configuration
        # remains owned by ResourceLifecycleService and is never polluted by
        # connection attempts or remote error details.
        self.runtime_status: Dict[str, dict[str, object]] = {}
        self.audit_records: deque[dict[str, object]] = deque(maxlen=1000)
        self._audit_sink = audit_sink
        try:
            self.audit_store = audit_store or MCPAuditStore(
                Path(DATA_PATH) / "mcp" / "audit.db",
                retention_limit=self.audit_records.maxlen or 1000,
            )
        except Exception:
            self.audit_store = None
            logger.warning("Persistent MCP audit storage is unavailable; using memory only")

    @staticmethod
    def _runtime_timestamp() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _runtime_error(error: BaseException | str | None) -> str | None:
        if error is None:
            return None
        if isinstance(error, str):
            return error
        # Keep the diagnostic useful while excluding transport URLs, command
        # lines, credentials and arbitrary remote exception text.
        return f"{type(error).__name__}: MCP operation failed"

    def _set_runtime_status(
        self,
        server_id: str,
        status: str,
        *,
        error: BaseException | str | None = None,
    ) -> None:
        if status not in {"stopped", "running", "failed"}:
            raise ValueError("MCP runtime status is invalid")
        self.runtime_status[server_id] = {
            "status": status,
            "running": status == "running",
            "failed": status == "failed",
            "last_error": self._runtime_error(error),
            "last_checked_at": self._runtime_timestamp(),
        }

    def get_runtime_status(self, server_id: str) -> dict[str, object]:
        """Return a stable, non-persistent runtime projection for one server."""

        record = self.runtime_status.get(server_id)
        server = self.servers.get(server_id)
        state = getattr(server, "state", None) if server is not None else None

        if state == MCPConnectionState.ERROR:
            if record is None or record.get("status") != "failed":
                self._set_runtime_status(server_id, "failed", error="MCP server reported an error")
        elif state == MCPConnectionState.CONNECTED:
            # A successful reconnect supersedes a previous failed attempt.
            self._set_runtime_status(server_id, "running")
        elif server is not None and (record is None or record.get("status") != "failed"):
            self._set_runtime_status(server_id, "stopped")
        elif record is None:
            self._set_runtime_status(server_id, "stopped")

        return deepcopy(self.runtime_status[server_id])

    def _audit_operation(
        self,
        server: str,
        operation: str,
        started_at: float,
        outcome: str,
        error: Optional[BaseException] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        record = {
            "component": "mcp",
            "timestamp": self._runtime_timestamp(),
            "server": server,
            "resource_id": f"mcp.{server}",
            "operation": operation,
            "duration_ms": round((time.monotonic() - started_at) * 1000, 3),
            "outcome": outcome,
            "correlation_id": correlation_id,
            "error": (
                {"type": type(error).__name__, "message": "operation failed"}
                if error is not None
                else None
            ),
        }
        self.audit_records.append(record)
        if self.audit_store is not None:
            try:
                self.audit_store.append(record)
            except Exception:
                self.audit_store = None
                logger.warning("Persistent MCP audit write failed; using memory only")
        if self.container.has(ExtensionLifecycleHost):
            self.container.resolve(ExtensionLifecycleHost).emit(
                "mcp_operation",
                {
                    "server_id": server,
                    "operation": operation,
                    "duration_ms": record["duration_ms"],
                    "outcome": outcome,
                },
            )
        if self._audit_sink is not None:
            try:
                self._audit_sink(record)
            except Exception:
                logger.warning("MCP audit sink rejected an event")

    def list_audit(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        server_id: str | None = None,
        operation: str | None = None,
        outcome: str | None = None,
    ) -> dict[str, object]:
        """Return persisted records, falling back to this process when needed."""
        if offset < 0:
            raise ValueError("audit offset cannot be negative")
        if limit < 1 or limit > 200:
            raise ValueError("audit limit is outside the allowed range")

        normalized_server = server_id.strip() if server_id else None
        normalized_operation = operation.strip() if operation else None
        normalized_outcome = outcome.strip() if outcome else None

        if self.audit_store is not None:
            try:
                return self.audit_store.list(
                    offset=offset,
                    limit=limit,
                    server_id=normalized_server,
                    operation=normalized_operation,
                    outcome=normalized_outcome,
                )
            except Exception:
                self.audit_store = None
                logger.warning("Persistent MCP audit query failed; using memory only")

        records = list(self.audit_records)
        filtered = [
            deepcopy(record)
            for record in records
            if (normalized_server is None or record.get("server") == normalized_server)
            and (
                normalized_operation is None
                or record.get("operation") == normalized_operation
            )
            and (
                normalized_outcome is None or record.get("outcome") == normalized_outcome
            )
        ]
        filtered.reverse()
        items = filtered[offset : offset + limit]
        return {
            "items": items,
            "total": len(filtered),
            "offset": offset,
            "limit": limit,
            "has_more": offset + len(items) < len(filtered),
            "persistent": False,
            "retention_limit": self.audit_records.maxlen or len(records),
        }
            
    def load_servers(self):
        """从配置加载所有MCP服务器"""
        for server_config in self._configured_servers():
            try:
                self.load_server(server_config)
            except Exception as e:
                logger.opt(exception=e).error(f"Failed to load MCP server {server_config.id}")
        logger.info(f"MCP server manager initialized, loaded {len(self.servers)} servers")

    def _configured_servers(self) -> list[MCPServerConfig]:
        """Merge legacy config with enabled server-managed MCP resources."""

        configured: dict[str, MCPServerConfig] = {
            item.id: item for item in self.config.mcp.servers
        }
        try:
            from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService

            lifecycle = self.container.resolve(ResourceLifecycleService)
            for resource in lifecycle.list_resources("mcp"):
                if not resource.get("enabled"):
                    continue
                try:
                    payload = json.loads(
                        lifecycle.read_entry(resource["resource_id"], resource["current_version"])
                    )
                    server_config = MCPServerConfig.model_validate(payload)
                    expected_id = resource["resource_id"].removeprefix("mcp.")
                    if server_config.id != expected_id:
                        raise ValueError("managed MCP server ID does not match its resource ID")
                    server_config.metadata["resource_id"] = resource["resource_id"]
                    configured[server_config.id] = server_config
                except Exception as error:
                    logger.warning(
                        f"Skipping invalid enabled MCP resource {resource.get('resource_id')}: {type(error).__name__}"
                    )
        except (KeyError, ImportError):
            pass
        return list(configured.values())

    async def refresh_managed_servers(self, *, connect: bool = True) -> None:
        """Reconcile enabled MCP resources after a WebUI lifecycle change."""

        desired = {item.id: item for item in self._configured_servers()}
        for server_id in list(self.servers):
            if server_id not in desired:
                await self.stop_server(server_id)
                self.servers.pop(server_id, None)
        for server_id, server_config in desired.items():
            current = self.servers.get(server_id)
            if current is None:
                self.load_server(server_config)
                current = self.servers[server_id]
            elif current.server_config.model_dump(mode="json") != server_config.model_dump(mode="json"):
                await self.stop_server(server_id)
                self.servers.pop(server_id, None)
                self.load_server(server_config)
                current = self.servers[server_id]
            if connect and current.state != MCPConnectionState.CONNECTED:
                await self.connect_server(server_id)
        
    def load_server(self, server_config: MCPServerConfig) -> MCPServer:
        """从配置加载MCP服务器"""
        server = MCPServer(server_config)
        logger.info(f"Initializing MCP server {server_config.id}")
        self.servers[server_config.id] = server
        return server
    
    def get_all_servers(self) -> Dict[str, MCPServer]:
        """获取所有MCP服务器列表"""
        return self.servers
    
    def get_server(self, server_id: str) -> Optional[MCPServer]:
        """获取指定ID的MCP服务器"""
        return self.servers.get(server_id)
    
    def is_server_id_available(self, server_id: str) -> bool:
        """
        检查服务器ID是否可用

        ``server_id`` 是持久化配置和 Agent MCP 绑定的稳定身份。只要该
        身份已经存在，就不能被创建操作复用；断开或错误状态只表示可以
        对已有实例执行重新连接，不表示可以覆盖已有配置。
        """
        return server_id not in self.servers
    
    def get_statistics(self) -> Dict[str, int]:
        """获取MCP服务器统计信息"""
        total = len(self.servers)
        stdio = sum(bool(s.server_config.server.type == "stdio") for s in self.servers.values())
        http = sum(bool(s.server_config.server.type == "http") for s in self.servers.values())
        sse = sum(bool(s.server_config.server.type == "sse") for s in self.servers.values())
        connected = sum(bool(s.state == MCPConnectionState.CONNECTED) for s in self.servers.values())
        disconnected = sum(bool(s.state == MCPConnectionState.DISCONNECTED) for s in self.servers.values())
        error = sum(bool(s.state == MCPConnectionState.ERROR) for s in self.servers.values())
        
        return {
            "total": total,
            "stdio": stdio,
            "http": http,
            "sse": sse,
            "connected": connected,
            "disconnected": disconnected,
            "error": error
        }
    def connect_all_servers(self, loop: asyncio.AbstractEventLoop):
        """连接所有MCP服务器"""
        async def _connect_server_safe(server_id):
            try:
                await self.connect_server(server_id)
            except Exception as e:
                logger.opt(exception=e).error(f"Exception occurred when connecting MCP server {server_id}")
        
        tasks = []
        for server_id in self.servers.keys():
            task = loop.create_task(_connect_server_safe(server_id))
            tasks.append(task)
            
        if tasks:
            loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
            
    async def connect_server(self, server_id: str) -> bool:
        """连接MCP服务器"""
        started_at = time.monotonic()
        server = self.servers.get(server_id)
        if not server:
            self._clear_server_caches(server_id)
            self._set_runtime_status(server_id, "failed", error="MCP server was not found")
            self._audit_operation(server_id, "connect", started_at, "not_found")
            logger.error(f"Cannot connect to non-existent MCP server: {server_id}")
            return False

        if server.state == MCPConnectionState.CONNECTED:
            self._set_runtime_status(server_id, "running")
            self._audit_operation(server_id, "connect", started_at, "already_connected")
            logger.warning(f"MCP server {server_id} is already connected")
            return True

        self._clear_server_caches(server_id)
        
        try:
            logger.info(f"Connecting to MCP server {server_id}")
            
            server.message_handler = partial(self._handle_server_message, server_id)
            
            # 连接到服务器
            success = await server.connect()
            
            if not success:
                self._clear_server_caches(server_id)
                self._set_runtime_status(server_id, "failed", error="connection attempt failed")
                self._audit_operation(server_id, "connect", started_at, "failure")
                logger.error(f"Failed to connect to MCP server {server_id}")
                return False
            
            # 连接成功后，更新缓存
            cache_results = await asyncio.gather(
                self._update_tools_cache(server_id),
                self._update_prompts_cache(server_id),
                self._update_resources_cache(server_id),
            )
            if not all(cache_results):
                self._clear_server_caches(server_id)
                self._set_runtime_status(server_id, "failed", error="capability loading failed")
                self._audit_operation(server_id, "connect", started_at, "capability_load_failed")
                logger.error(f"Failed to load MCP capabilities for server {server_id}")
                return False
            
            self._set_runtime_status(server_id, "running")
            self._audit_operation(server_id, "connect", started_at, "success")
            logger.info(f"Successfully connected to MCP server {server_id}")
            return True
            
        except Exception as e:
            self._clear_server_caches(server_id)
            self._set_runtime_status(server_id, "failed", error=e)
            self._audit_operation(server_id, "connect", started_at, "error", e)
            logger.opt(exception=e).error(f"Error occurred when connecting to MCP server {server_id}")
            return False
    
    def disconnect_all_servers(self, loop: asyncio.AbstractEventLoop):
        """断开所有MCP服务器连接"""
        disconnect_tasks = []
        for server_id, server in self.servers.items():
            if server.state == MCPConnectionState.CONNECTED:
                disconnect_tasks.append(loop.create_task(self.stop_server(server_id)))
        
        if disconnect_tasks:
            loop.run_until_complete(asyncio.gather(*disconnect_tasks, return_exceptions=True))
            
        self.tools_cache.clear()
        self.prompts_cache.clear()
        self.resources_cache.clear()
        for server_id in self.servers:
            self._set_runtime_status(server_id, "stopped")
        
        logger.info("All MCP servers have been disconnected")
            
    async def stop_server(self, server_id: str) -> bool:
        """断开MCP服务器连接"""
        started_at = time.monotonic()
        server = self.servers.get(server_id)
        
        if not server:
            self._clear_server_caches(server_id)
            self._set_runtime_status(server_id, "stopped")
            self._audit_operation(server_id, "disconnect", started_at, "not_found")
            logger.error(f"Cannot disconnect from non-existent MCP server: {server_id}")
            return False
        
        if server.state != MCPConnectionState.CONNECTED:
            self._clear_server_caches(server_id)
            self._set_runtime_status(server_id, "stopped")
            self._audit_operation(server_id, "disconnect", started_at, "already_disconnected")
            logger.warning(f"MCP server {server_id} is not connected")
            return True
        
        try:
            logger.info(f"Disconnecting from MCP server {server_id}")
            
            # 断开服务器连接
            success = await server.disconnect()
            
            if not success:
                self._set_runtime_status(server_id, "failed", error="disconnect attempt failed")
                self._audit_operation(server_id, "disconnect", started_at, "failure")
                logger.error(f"Failed to disconnect from MCP server {server_id}")
                return False

            self._set_runtime_status(server_id, "stopped")
            self._audit_operation(server_id, "disconnect", started_at, "success")
            logger.info(f"Successfully disconnected from MCP server {server_id}")
            return True

        except Exception as e:
            self._set_runtime_status(server_id, "failed", error=e)
            self._audit_operation(server_id, "disconnect", started_at, "error", e)
            logger.opt(exception=e).error(f"Error occurred when disconnecting from MCP server {server_id}")
            return False
        finally:
            self._clear_server_caches(server_id)
    
    async def _update_tools_cache(self, server_id: str) -> bool:
        """
        更新指定服务器的工具缓存
        
        Args:
            server_id: 服务器ID
            
        Returns:
            bool: 更新是否成功
        """
        server = self.servers.get(server_id)
        if not server or server.state != MCPConnectionState.CONNECTED:
            return False
        
        try:
            # 获取服务器工具列表
            tools = await server.get_tools()
            
            # 先移除该服务器的旧工具
            self._remove_server_tools_from_cache(server_id)
            # 添加新工具到缓存
            for tool in tools.tools:
                original_name = tool.name
                if not original_name:
                    continue
                
                # 检查工具名称是否已存在
                if original_name in self.tools_cache:
                    # 名称冲突，使用 server_id.tool_name 作为新名称
                    display_name = f"{server.server_config.id}.{original_name}"
                    logger.warning(f"工具名称冲突: {original_name}，重命名为 {display_name}")
                else:
                    display_name = original_name
                
                # 存储工具信息
                self.tools_cache[display_name] = ToolCacheEntry(
                    server_id=server_id,
                    original_name=original_name,
                    tool_info=tool
                )
            
            return True
        except McpError as e:
            if e.error == "Method not found":
                logger.warning(f"Server {server_id} does not support tools")
                return True
        except Exception as e:
            logger.opt(exception=e).error(f"更新服务器 {server_id} 工具缓存时发生错误")
        return False
    
    def _remove_server_tools_from_cache(self, server_id: str):
        """
        从工具缓存中移除指定服务器的所有工具
        
        Args:
            server_id: 服务器ID
        """
        # 找出属于该服务器的所有工具名称
        tool_names_to_remove = [
            name for name, entry in self.tools_cache.items() if entry.server_id == server_id
        ]
        
        # 从缓存中移除这些工具
        for name in tool_names_to_remove:
            self.tools_cache.pop(name, None)

    def _clear_server_caches(self, server_id: str) -> None:
        """Remove every capability advertised by one server instance."""

        self._remove_server_tools_from_cache(server_id)
        self.prompts_cache.pop(server_id, None)
        self.resources_cache.pop(server_id, None)
    
    def get_tools(self) -> Dict[str, ToolCacheEntry]:
        """
        获取所有可用工具
        """
        # 返回工具信息
        return self.tools_cache
    
    def get_tool_server(self, tool_name: str) -> Optional[Tuple[MCPServer, str]]:
        """
        根据工具名称获取对应的服务器实例和原始工具名称
        
        Args:
            tool_name: 工具显示名称
            
        Returns:
            Optional[Tuple[MCPServer, str]]: (服务器实例, 原始工具名称)，如果工具不存在则返回None
        """
        if tool_name not in self.tools_cache:
            return None
        
        entry = self.tools_cache[tool_name]
        server = self.servers.get(entry.server_id)
        if not server:
            return None
        
        return (server, entry.original_name)
    
    async def call_tool(
        self,
        tool_name: str,
        tool_args: dict,
        *,
        agent_allowlist: Optional[set[str] | frozenset[str]] = None,
        agent_mcp_server_ids: Optional[Iterable[str]] = None,
        agent_owner_subject: Optional[str] = None,
        session_allowlist: Optional[set[str] | frozenset[str]] = None,
        workflow_allowlist: Optional[set[str] | frozenset[str]] = None,
        confirmed: bool = False,
        correlation_id: Optional[str] = None,
    ) -> Optional[types.CallToolResult]:
        """
        调用指定工具
        
        Args:
            tool_name: 工具显示名称
            tool_args: 工具参数
            
        Returns:
            Optional[dict]: 工具调用结果，如果调用失败则返回None
        """
        started_at = time.monotonic()
        entry = self.tools_cache.get(tool_name)
        server_id = entry.server_id if entry is not None else "unavailable"
        if not principal_can_control_agent(agent_owner_subject):
            logger.warning("Rejected unauthorized host MCP tool execution")
            self._audit_operation(
                server_id,
                "call_tool",
                started_at,
                "host_unauthorized",
                correlation_id=correlation_id,
            )
            return None
        result = self.get_tool_server(tool_name)
        if not result:
            logger.error(f"Tool {tool_name} not found or server not available")
            self._audit_operation(
                server_id,
                "call_tool",
                started_at,
                "not_found",
                correlation_id=correlation_id,
            )
            return None

        if agent_mcp_server_ids is not None:
            bound_servers = {
                str(value).strip()
                for value in agent_mcp_server_ids
                if str(value).strip()
            }
            if server_id not in bound_servers:
                logger.warning("Rejected MCP tool from an unbound server")
                self._audit_operation(
                    server_id,
                    "call_tool",
                    started_at,
                    "server_not_bound",
                    correlation_id=correlation_id,
                )
                return None

        if agent_allowlist is not None:
            try:
                allowed_tools = resolve_mcp_tool_allowlist(
                    agent_allowlist=agent_allowlist,
                    tool_entries=self.tools_cache,
                    agent_mcp_server_ids=agent_mcp_server_ids,
                    session_allowlist=session_allowlist,
                    workflow_allowlist=workflow_allowlist,
                )
            except ValueError as error:
                logger.warning("Rejected MCP tool policy expansion")
                self._audit_operation(
                    server_id,
                    "call_tool",
                    started_at,
                    "denied",
                    error,
                    correlation_id=correlation_id,
                )
                return None
            if tool_name not in allowed_tools:
                logger.warning("Rejected MCP tool outside runtime allowlist")
                self._audit_operation(
                    server_id,
                    "call_tool",
                    started_at,
                    "denied",
                    correlation_id=correlation_id,
                )
                return None

        if self._tool_requires_confirmation(entry.tool_info) and not confirmed:
            logger.warning("Rejected MCP tool call pending user confirmation")
            self._audit_operation(
                server_id,
                "call_tool",
                started_at,
                "confirmation_required",
                correlation_id=correlation_id,
            )
            return None
        
        server, original_name = result
        
        if server.state != MCPConnectionState.CONNECTED:
            logger.error(f"Server for tool {tool_name} is not connected")
            self._audit_operation(
                server_id,
                "call_tool",
                started_at,
                "disconnected",
                correlation_id=correlation_id,
            )
            return None
        
        try:
            # 使用原始工具名称调用
            call_tool_result = await server.call_tool(original_name, tool_args)
            self._audit_operation(
                server_id,
                "call_tool",
                started_at,
                "success",
                correlation_id=correlation_id,
            )
            return call_tool_result
        except Exception as e:
            logger.error(f"Error occurred when calling tool {tool_name}")
            self._audit_operation(
                server_id,
                "call_tool",
                started_at,
                "error",
                e,
                correlation_id=correlation_id,
            )
            return None

    @staticmethod
    def tool_requires_confirmation(tool_info: types.Tool) -> bool:
        """Public predicate for callers that must decide before invoking a tool.

        The HTTP route needs this to issue a confirmation token, and the runtime
        needs it to refuse an unconfirmed call. It was reachable only as a private
        method, which made the boundary look accidental; the behavior is
        unchanged and the old private name is kept as an alias.
        """
        annotations = getattr(tool_info, "annotations", None)
        if annotations is not None and getattr(annotations, "destructiveHint", False) is True:
            return True
        metadata = getattr(tool_info, "_meta", None) or getattr(tool_info, "metadata", None) or {}
        if not isinstance(metadata, dict):
            return False
        return metadata.get("requires_confirmation") is True

    # 兼容既有调用方（含外部插件）：保留原私有名，行为完全一致。
    _tool_requires_confirmation = tool_requires_confirmation
        
    async def _update_prompts_cache(self, server_id: str) -> bool:
        """
        更新指定server的prompts 索引缓存
        # notification ! 
        这个函数存的缓存是一个prompts的索引，请调用get_prompts获取具体的prompts信息

        Args:
            server_id: 服务器ID

        Returns:
            bool: 更新是否成功
        """
        server = self.servers.get(server_id)
        if not server or server.state != MCPConnectionState.CONNECTED:
            return False
        
        try:
            # 获取服务器prompts 索引
            prompts = await server.list_prompts()

            # 移除旧缓存
            self.prompts_cache.pop(server_id, None)
            # 添加新索引到缓存
            self.prompts_cache[server_id] = prompts.prompts
            return True
        except McpError as e:
            if e.error == "Method not found":
                self.prompts_cache[server_id] = []
                logger.warning(f"Server {server_id} does not support prompts")
                return True
        except Exception as e:
            logger.opt(exception=e).error(f"更新服务器 {server_id} prompts 索引缓存时发生错误")
        return False

    async def get_prompt_list(self, server_id: str) -> Optional[list[types.Prompt]]:
        """
        获取指定服务器的prompts

        Args:
            server_id: 服务器ID
        Returns:
            types.GetPromptResult: prompts
        """

        server = self.servers.get(server_id)
        if not server or server.state != MCPConnectionState.CONNECTED:
            return None
        
        return self.prompts_cache.get(server_id, [])

    async def get_prompt(self, server_id: str, prompt_name: str, prompt_args: dict[str, str] | None = None) -> Optional[types.GetPromptResult]:
        """
        获取指定服务器的prompt
        """
        started_at = time.monotonic()
        server = self.servers.get(server_id)
        if not server or server.state != MCPConnectionState.CONNECTED:
            outcome = "not_found" if server is None else "disconnected"
            self._audit_operation(server_id, "get_prompt", started_at, outcome)
            return None
        try:
            result = await server.get_prompt(prompt_name, prompt_args)
            self._audit_operation(server_id, "get_prompt", started_at, "success")
            return result
        except Exception as error:
            self._audit_operation(server_id, "get_prompt", started_at, "error", error)
            raise
    
    async def _update_resources_cache(self, server_id: str) -> bool:
        """
        更新指定server的resources 缓存
        # notification ! 
        这个函数存的缓存是一个resources的索引，请调用get_resources获取具体的resources信息

        Args:
            server_id: 服务器ID

        Returns:
            bool: 更新是否成功
        """
        server = self.servers.get(server_id)
        if not server or server.state != MCPConnectionState.CONNECTED:
            return False
        
        try:
            # 获取服务器resources 索引
            resources = await server.list_resources()

            # 移除旧缓存
            self.resources_cache.pop(server_id, None)

            # 存储新索引到缓存
            self.resources_cache[server_id] = resources.resources
            return True
        except McpError as e:
            if e.error == "Method not found":
                self.resources_cache[server_id] = []
                logger.warning(f"Server {server_id} does not support resources")
                return True
        except Exception as e:
            logger.opt(exception=e).error(f"更新服务器 {server_id} resources 缓存时发生错误")
        return False
    async def get_resource_list(self, server_id: str) -> Optional[list[types.Resource]]:
        """获取指定服务器的资源列表

        Args:
            server_id (str): 服务器ID

        Returns:
            Optional[types.Resource]: 资源列表
        """
        server = self.servers.get(server_id)
        if not server or server.state != MCPConnectionState.CONNECTED:
            return None
        
        return self.resources_cache.get(server_id, [])
    
    async def get_resource(self, server_id: str, uri: str) -> Optional[types.ReadResourceResult]:
        """
        获取指定服务器的resources

        Args:
            server_id: 服务器ID
            uri: 资源URI
        Returns:
            types.ReadResourceResult: resource
        """

        started_at = time.monotonic()
        server = self.servers.get(server_id)
        if not server or server.state != MCPConnectionState.CONNECTED:
            outcome = "not_found" if server is None else "disconnected"
            self._audit_operation(server_id, "get_resource", started_at, outcome)
            return None
        try:
            result = await server.read_resource(uri)
            self._audit_operation(server_id, "get_resource", started_at, "success")
            return result
        except Exception as error:
            self._audit_operation(server_id, "get_resource", started_at, "error", error)
            raise
    
    async def _handle_server_message(self, server_id: str, message: RequestResponder[types.ServerRequest, types.ClientResult]
            | types.ServerNotification
            | Exception):
        """
        处理服务器通知
        """
        if isinstance(message, types.ToolListChangedNotification):
            await self._update_tools_cache(server_id)
        elif isinstance(message, types.PromptListChangedNotification):
            await self._update_prompts_cache(server_id)
        elif isinstance(message, types.ResourceListChangedNotification):
            await self._update_resources_cache(server_id)
        else:
            logger.warning(f"Unknown notification from server {server_id}: {message}")
