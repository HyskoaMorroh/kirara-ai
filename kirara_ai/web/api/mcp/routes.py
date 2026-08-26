#!/usr/bin/env python
# -*- coding: utf-8 -*-


from collections.abc import Mapping
import hashlib
import json
import secrets
import time
from typing import Any, Dict, Optional

from quart import Blueprint, g, jsonify, request

from kirara_ai.config.config_loader import CONFIG_FILE, ConfigLoader
from kirara_ai.config.global_config import GlobalConfig, MCPServerConfig, MCPTransportConfig
from kirara_ai.agent_runtime import AgentRegistry, resolve_mcp_tool_allowlist
from kirara_ai.logger import get_logger
from kirara_ai.mcp_module import MCPConnectionState, MCPServer, MCPServerManager
from kirara_ai.mcp_module.compat import normalize_mcp_server_entry

from ...auth.middleware import require_auth
from .models import (
    REDACTED_SECRET,
    MCPPromptInfo,
    MCPPromptSampleRequest,
    MCPAuditPage,
    MCPResourceInfo,
    MCPServerCreateRequest,
    MCPServerInfo,
    MCPServerList,
    MCPServerUpdateRequest,
    MCPStatistics,
    MCPToolCallRequest,
    MCPToolInfo,
)

# 创建蓝图
mcp_bp = Blueprint("mcp", __name__)
logger = get_logger("WebServer.MCP")


_TOOL_CONFIRMATION_TTL_SECONDS = 90

_SECRET_KEY_PARTS = (
    "key",
    "token",
    "secret",
    "password",
    "credential",
    "cookie",
    "authorization",
)


def _is_secret_key(key: object) -> bool:
    normalized = str(key).lower().replace("-", "_")
    return any(part in normalized for part in _SECRET_KEY_PARTS)


def _redact_value(value: Any, *, force: bool = False) -> Any:
    """Redact secret-shaped values while retaining useful metadata shape."""
    if force:
        return REDACTED_SECRET
    if isinstance(value, Mapping):
        return {
            str(key): _redact_value(item, force=_is_secret_key(key))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_value(item) for item in value]
    return value


def _redact_transport(transport: MCPTransportConfig) -> Dict[str, Any]:
    raw = _redact_value(
        transport.model_dump(mode="python", by_alias=True, exclude_none=False)
    )
    raw["env"] = {str(key): REDACTED_SECRET for key in (transport.env or {})}
    raw["headers"] = {str(key): REDACTED_SECRET for key in (transport.headers or {})}
    return raw


def _merge_secret_map(current: Mapping[str, str], incoming: Mapping[str, str]) -> Dict[str, str]:
    """Keep stored credentials when the client sends the public mask."""
    merged = dict(current)
    for key, value in incoming.items():
        if value == REDACTED_SECRET and key in merged:
            continue
        merged[str(key)] = str(value)
    return merged


def _public_error(message: str = "MCP operation failed") -> Dict[str, str]:
    """Keep transport credentials and remote exception details out of API errors."""
    return {"message": message}


def _invalid_request(message: str = "Invalid MCP request") -> Dict[str, str]:
    """Return a bounded input error without serializing validation payloads."""
    return {"message": message}


def _digest_json(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _tool_digest(entry: Any) -> str:
    info = getattr(entry, "tool_info", entry)
    if hasattr(info, "model_dump"):
        info = info.model_dump(mode="python", by_alias=True)
    return _digest_json(
        {
            "server_id": getattr(entry, "server_id", None),
            "original_name": getattr(entry, "original_name", None),
            "tool": info,
        }
    )


def _mcp_agent_policy(manager: MCPServerManager, agent_id: Optional[str], tool_name: str):
    """Resolve management-tool authority from the persisted Agent registry."""
    if not agent_id or not agent_id.strip():
        raise ValueError("agent_id is required")
    registry: AgentRegistry = manager.container.resolve(AgentRegistry)
    agent = registry.get(agent_id.strip())
    if not agent.enabled:
        raise PermissionError("Agent is disabled")
    bound_servers = frozenset(
        binding.resource_id.removeprefix("mcp.")
        for binding in agent.mcp_bindings
        if binding.enabled
    )
    allowed = resolve_mcp_tool_allowlist(
        agent_allowlist=agent.mcp_allowlist,
        tool_entries=manager.get_tools(),
        agent_mcp_server_ids=bound_servers,
    )
    if tool_name not in allowed:
        raise PermissionError("Tool is not allowed for this Agent")
    entry = manager.get_tools().get(tool_name)
    if entry is None:
        raise LookupError("Tool is unavailable")
    return agent, bound_servers, entry


def _issue_tool_confirmation(
    manager: MCPServerManager,
    *,
    agent_id: str,
    server_id: str,
    tool_name: str,
    params: dict[str, Any],
    entry: Any,
) -> str:
    token = secrets.token_urlsafe(32)
    manager.confirmation_store.issue(
        token,
        agent_id=agent_id,
        server_id=server_id,
        tool_name=tool_name,
        params_digest=_digest_json(params),
        tool_digest=_tool_digest(entry),
        expires_at=time.time() + _TOOL_CONFIRMATION_TTL_SECONDS,
    )
    return token


def _consume_tool_confirmation(
    manager: MCPServerManager,
    token: Optional[str],
    *,
    agent_id: str,
    server_id: str,
    tool_name: str,
    params: dict[str, Any],
    entry: Any,
) -> bool:
    if not token:
        return False
    return manager.confirmation_store.consume(
        token,
        agent_id=agent_id,
        server_id=server_id,
        tool_name=tool_name,
        params_digest=_digest_json(params),
        tool_digest=_tool_digest(entry),
    )


def _api_tool_name(manager: MCPServerManager, server_id: str, original_name: str) -> Optional[str]:
    """Resolve an API server-local tool name to the manager's display name."""
    for display_name, entry in manager.get_tools().items():
        if entry.server_id == server_id and entry.original_name == original_name:
            return display_name
    return None


def _convert_to_server_info(server: MCPServer) -> MCPServerInfo:
    """将服务器对象转换为MCPServerInfo响应对象"""
    config = server.server_config
    return MCPServerInfo(
        id=config.id,
        name=config.name,
        server=_redact_transport(config.server),
        apps=config.apps,
        description=config.description,
        tags=config.tags,
        homepage=config.homepage,
        docs=config.docs,
        metadata=_redact_value(config.metadata),
        connection_state=server.state.name.lower(),
    )


def _server_payload(server_info: MCPServerInfo) -> Dict[str, Any]:
    return server_info.model_dump(mode="json", by_alias=True)


def _convert_to_prompt_info(prompt) -> MCPPromptInfo:
    """将 MCP 原始 Prompt 转换为 WebUI 需要的响应对象

    MCP 协议里提示词的唯一标识是 name，WebUI 用 id 字段显示并回传，
    因此这里同时给出 id 与 name。
    """
    arguments = []
    for argument in getattr(prompt, "arguments", None) or []:
        arguments.append(
            argument.model_dump() if hasattr(argument, "model_dump") else dict(argument)
        )
    return MCPPromptInfo(
        id=prompt.name,
        name=prompt.name,
        description=getattr(prompt, "description", None),
        arguments=arguments,
    )


def _convert_to_resource_info(resource) -> MCPResourceInfo:
    """将 MCP 原始 Resource 转换为 WebUI 需要的响应对象

    资源的读取键是 uri（AnyUrl，需转成字符串才能 JSON 序列化）；
    WebUI 用 id 显示标题并拼进读取路径，这里取 name，缺失时回退到 uri。
    """
    uri = str(resource.uri)
    name = getattr(resource, "name", None) or uri
    return MCPResourceInfo(
        id=name,
        name=name,
        uri=uri,
        description=getattr(resource, "description", None),
        mime_type=getattr(resource, "mimeType", None),
        size=getattr(resource, "size", None),
    )


@mcp_bp.route("/servers", methods=["GET"])
@require_auth("mcp.read")
async def list_servers():
    """获取所有MCP服务器列表"""
    try:
        # 获取查询参数
        page = request.args.get('page', 1, type=int)
        page_size = request.args.get('page_size', 20, type=int)
        transport_type = request.args.get('type') or request.args.get('connection_type')
        status = request.args.get('status')
        query = request.args.get('query')

        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 获取所有服务器
        servers = manager.get_all_servers()

        # 转换为响应格式
        server_list = []
        for server_id, server in servers.items():
            # 过滤条件
            if transport_type and server.server_config.server.type != transport_type:
                continue

            server_state = server.state.name.lower()
            if status:
                if status == 'connected' and server_state != 'connected':
                    continue
                elif status == 'disconnected' and server_state != 'disconnected':
                    continue
                elif status == 'error' and server_state != 'error':
                    continue

            searchable = " ".join(
                value
                for value in (
                    server_id,
                    server.server_config.name,
                    server.server_config.description,
                    server.server_config.server.command,
                    server.server_config.server.url,
                    " ".join(server.server_config.tags),
                )
                if value
            ).lower()
            if query and query.lower() not in searchable:
                continue

            server_list.append(_convert_to_server_info(server))

        # 计算分页
        total = len(server_list)
        total_pages = (total + page_size - 1) // page_size
        start_idx = (page - 1) * page_size
        end_idx = min(start_idx + page_size, total)
        paginated_servers = server_list[start_idx:end_idx]

        # 返回响应
        return MCPServerList(
            items=[server for server in paginated_servers],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages
        ).model_dump(mode="json", by_alias=True)
    except Exception as e:
        logger.opt(exception=e).error("获取MCP服务器列表失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/statistics", methods=["GET"])
@require_auth("mcp.read")
async def get_statistics():
    """获取MCP服务器统计信息"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 获取统计信息
        stats = manager.get_statistics()
        
        # 获取工具总数
        tools = manager.get_tools()
        total_tools = len(tools)

        # 返回响应
        return MCPStatistics(
            total_servers=stats.get("total", 0),
            stdio_servers=stats.get("stdio", 0),
            http_servers=stats.get("http", 0),
            sse_servers=stats.get("sse", 0),
            connected_servers=stats.get("connected", 0),
            disconnected_servers=stats.get("disconnected", 0),
            error_servers=stats.get("error", 0),
            total_tools=total_tools
        ).model_dump()
    except Exception as e:
        logger.opt(exception=e).error("获取MCP统计信息失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/audit", methods=["GET"])
@require_auth("mcp.read")
async def list_audit():
    """List redacted MCP operation records from the current process."""
    try:
        offset = int(request.args.get("offset", 0))
        limit = int(request.args.get("limit", 50))
        manager: MCPServerManager = g.container.resolve(MCPServerManager)
        page = manager.list_audit(
            offset=offset,
            limit=limit,
            server_id=request.args.get("server_id"),
            operation=request.args.get("operation"),
            outcome=request.args.get("outcome"),
        )
        return jsonify(MCPAuditPage.model_validate(page).model_dump(mode="json"))
    except (TypeError, ValueError):
        return jsonify(_invalid_request("audit pagination or filter values are invalid")), 400
    except Exception as e:
        logger.opt(exception=e).error("获取MCP审计记录失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/<server_id>", methods=["GET"])
@require_auth("mcp.read")
async def get_server(server_id: str):
    """获取特定MCP服务器的详情"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 获取服务器
        server = manager.get_server(server_id)
        if not server:
            return jsonify({"message": f"服务器 {server_id} 不存在"}), 404

        # 转换为响应格式
        server_info = _convert_to_server_info(server)

        # 返回响应
        return _server_payload(server_info)
    except Exception as e:
        logger.opt(exception=e).error(f"获取MCP服务器 {server_id} 详情失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/<server_id>/tools", methods=["GET"])
@require_auth("mcp.read")
async def get_server_tools(server_id: str):
    """获取MCP服务器提供的工具列表"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 获取服务器
        server = manager.get_server(server_id)
        if not server:
            return jsonify({"message": f"服务器 {server_id} 不存在"}), 404

        # 如果服务器未连接，返回空列表
        if server.state != MCPConnectionState.CONNECTED:
            return []

        # 获取服务器工具
        tools = manager.get_tools()

        # 转换为响应格式
        tool_list = []
        for _, tool in tools.items():
            if tool.server_id == server_id:
                tool_list.append(MCPToolInfo(
                    name=tool.original_name,
                    description=tool.tool_info.description,
                    input_schema=tool.tool_info.inputSchema,
                    server_id=tool.server_id,
                ))

        # 返回响应
        return [tool.model_dump() for tool in tool_list]
    except Exception as e:
        logger.opt(exception=e).error(f"获取MCP服务器 {server_id} 工具列表失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/check/<server_id>", methods=["GET"])
@require_auth("mcp.read")
async def check_server_id(server_id: str):
    """检查服务器ID是否可用"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 检查ID是否可用
        is_available = manager.is_server_id_available(server_id)

        # 返回响应
        return jsonify({
            "is_available": is_available
        })
    except Exception as e:
        logger.opt(exception=e).error(f"检查服务器ID {server_id} 可用性失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers", methods=["POST"])
@require_auth("mcp.manage")
async def create_server():
    """创建新的MCP服务器"""
    try:
        # 获取请求数据
        data = await request.get_json()
        request_data = MCPServerCreateRequest.model_validate(data or {})

        # 从容器中获取全局配置和MCP服务器管理器
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 检查ID是否已存在
        if not manager.is_server_id_available(request_data.id):
            return jsonify({"message": f"服务器ID '{request_data.id}' 已存在或服务器正在运行"}), 409

        # Normalize once at the API boundary.  Runtime code only receives the
        # same canonical shape that CC Switch exports.
        new_server_config = normalize_mcp_server_entry(request_data.to_config())

        # 添加到全局配置中
        config.mcp.servers.append(new_server_config)

        # 保存配置
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

        # 让管理器加载新服务器
        server = manager.load_server(new_server_config)

        # 转换为响应格式
        server_info = _convert_to_server_info(server)

        # 返回响应
        return _server_payload(server_info)
    except (ValueError, TypeError):
        logger.warning("Rejected invalid MCP server payload")
        return jsonify(_invalid_request("Invalid MCP server configuration")), 400
    except Exception as e:
        logger.opt(exception=e).error("创建MCP服务器失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/<server_id>", methods=["PUT"])
@require_auth("mcp.manage")
async def update_server(server_id: str):
    """更新MCP服务器配置"""
    try:
        # 获取请求数据
        data = await request.get_json()
        request_data = MCPServerUpdateRequest.model_validate(data or {})

        # 从容器中获取全局配置和MCP服务器管理器
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 查找服务器配置
        server_index = -1
        for i, server in enumerate(config.mcp.servers):
            if server.id == server_id:
                server_index = i
                break

        if server_index == -1:
            return jsonify({"message": f"服务器 '{server_id}' 不存在"}), 404

        # 检查服务器状态
        current_server = manager.get_server(server_id)
        if current_server and current_server.state == MCPConnectionState.CONNECTED:
            return jsonify({"message": "无法更新正在运行的服务器，请先停止服务器"}), 409

        # Apply only explicitly supplied canonical fields.  Masked secrets
        # mean "leave the stored value unchanged".
        server_config = config.mcp.servers[server_index]
        updates = request_data.update_values()
        if "name" in updates:
            server_config.name = updates["name"]
        if "server" in updates and updates["server"] is not None:
            incoming = MCPTransportConfig.model_validate(updates["server"])
            incoming.env = _merge_secret_map(server_config.server.env, incoming.env)
            incoming.headers = _merge_secret_map(server_config.server.headers, incoming.headers)
            server_config.server = incoming
        for field in ("apps", "description", "tags", "homepage", "docs", "metadata"):
            if field in updates:
                setattr(server_config, field, updates[field])

        normalized = normalize_mcp_server_entry(server_config)
        config.mcp.servers[server_index] = normalized
        server_config = normalized

        # 保存配置
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

        # 停止服务器
        await manager.stop_server(server_id)

        # Reload only.  Connecting is an explicit lifecycle action, which
        # avoids an edit request unexpectedly launching a local process or
        # making a remote request.
        current_server = manager.load_server(server_config)

        # 转换为响应格式
        server_info = _convert_to_server_info(current_server)

        # 返回响应
        return _server_payload(server_info)
    except (ValueError, TypeError):
        logger.warning("Rejected invalid MCP server update")
        return jsonify(_invalid_request("Invalid MCP server configuration")), 400
    except Exception as e:
        logger.opt(exception=e).error(f"更新MCP服务器 {server_id} 失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/<server_id>", methods=["DELETE"])
@require_auth("mcp.manage")
async def delete_server(server_id: str):
    """删除MCP服务器"""
    try:
        # 从容器中获取全局配置和MCP服务器管理器
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 查找服务器配置
        server_index = -1
        for i, server in enumerate(config.mcp.servers):
            if server.id == server_id:
                server_index = i
                break

        if server_index == -1:
            return jsonify({"message": f"服务器 '{server_id}' 不存在"}), 404

        # 如果服务器正在运行，先停止它
        current_server = manager.get_server(server_id)
        if current_server and current_server.state == MCPConnectionState.CONNECTED:
            await manager.stop_server(server_id)

        # 从配置中删除服务器
        removed_server = config.mcp.servers.pop(server_index)

        # 保存配置
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

        await manager.stop_server(server_id)

        # 返回响应
        return jsonify({})
    except Exception as e:
        logger.opt(exception=e).error(f"删除MCP服务器 {server_id} 失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/<server_id>/start", methods=["POST"])
@require_auth("mcp.manage")
async def start_server(server_id: str):
    """连接 MCP 服务器"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 尝试连接服务器
        success = await manager.connect_server(server_id)

        if not success:
            return jsonify({"message": f"服务器 '{server_id}' 不存在或无法连接"}), 404

        # 返回响应
        return jsonify({})
    except Exception as e:
        logger.opt(exception=e).error(f"连接 MCP 服务器 {server_id} 失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/<server_id>/stop", methods=["POST"])
@require_auth("mcp.manage")
async def stop_server(server_id: str):
    """断开 MCP 服务器"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 尝试停止服务器
        success = await manager.stop_server(server_id)

        if not success:
            return jsonify({"message": f"服务器 '{server_id}' 不存在或未连接"}), 404

        # 返回响应
        return jsonify({})
    except Exception as e:
        logger.opt(exception=e).error(f"断开 MCP 服务器 {server_id} 失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/tools", methods=["GET"])
@require_auth("mcp.read")
async def get_all_tools():
    """获取所有可用工具"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 获取所有工具
        tools = manager.get_tools()

        # 转换为响应格式
        tool_list = []
        for name, tool_info in tools.items():
            tool_list.append(MCPToolInfo(
                name=name,
                description=tool_info.tool_info.description,
                input_schema=tool_info.tool_info.inputSchema,
                server_id=tool_info.server_id,
            ))

        # 返回响应
        return [tool.model_dump() for tool in tool_list]
    except Exception as e:
        logger.opt(exception=e).error("获取所有工具失败")
        return jsonify(_public_error()), 500

@mcp_bp.route("/servers/<server_id>/tools/call", methods=["POST"])
@require_auth("mcp.execute")
async def call_tool(server_id: str):
    """调用工具"""
    tool_name = "unknown"
    try:
        data = await request.get_json()
        request_data = MCPToolCallRequest.model_validate(data or {})
        tool_name = request_data.toolName

        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 获取服务器
        server: Optional[MCPServer] = manager.get_server(server_id)
        if not server:
            return jsonify({"message": f"服务器 '{server_id}' 不存在"}), 404

        manager_tool_name = _api_tool_name(manager, server_id, request_data.toolName)
        if manager_tool_name is None:
            return jsonify({"message": f"工具 '{request_data.toolName}' 不存在"}), 404

        try:
            agent, bound_servers, entry = _mcp_agent_policy(
                manager, request_data.agent_id, manager_tool_name
            )
        except LookupError:
            return jsonify({"message": "Agent 或工具不存在"}), 404
        except PermissionError:
            return jsonify({"message": "Agent 无权调用该工具"}), 403
        except ValueError:
            return jsonify(_invalid_request("agent_id is required")), 400

        if entry.server_id != server_id or server_id not in bound_servers:
            return jsonify({"message": "Agent 未绑定该 MCP 服务器"}), 403

        requires_confirmation = manager._tool_requires_confirmation(entry.tool_info)
        confirmed = False
        if requires_confirmation:
            if request_data.confirmation_id:
                confirmed = _consume_tool_confirmation(
                    manager,
                    request_data.confirmation_id,
                    agent_id=agent.agent_id,
                    server_id=server_id,
                    tool_name=manager_tool_name,
                    params=request_data.params,
                    entry=entry,
                )
                if not confirmed:
                    return jsonify({"message": "确认令牌无效、已过期或已使用"}), 403
            else:
                confirmation_id = _issue_tool_confirmation(
                    manager,
                    agent_id=agent.agent_id,
                    server_id=server_id,
                    tool_name=manager_tool_name,
                    params=request_data.params,
                    entry=entry,
                )
                return jsonify(
                    {
                        "message": "该工具需要明确确认",
                        "confirmation_id": confirmation_id,
                        "expires_in": _TOOL_CONFIRMATION_TTL_SECONDS,
                    }
                ), 409

        result = await manager.call_tool(
            manager_tool_name,
            request_data.params,
            agent_allowlist=frozenset(agent.mcp_allowlist),
            agent_mcp_server_ids=bound_servers,
            confirmed=confirmed,
        )
        if result is None:
            return jsonify({"message": "工具调用未获准或服务器未连接"}), 403

        return jsonify({"result": result.model_dump(mode="json")})
    except ValueError:
        logger.warning("Rejected invalid MCP tool call")
        return jsonify(_invalid_request("Invalid MCP tool request")), 400
    except Exception as e:
        logger.opt(exception=e).error(f"调用工具 {tool_name} 失败")
        return jsonify(_public_error()), 500

@mcp_bp.route("/servers/<server_id>/prompts", methods=["GET"])
@require_auth("mcp.read")
async def get_server_prompts(server_id: str):
    """获取MCP服务器提供的提示词列表"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)
        
        server = manager.get_server(server_id)
        if not server:
            return jsonify({"message": f"服务器 {server_id} 不存在"}), 404
        
        prompts = await manager.get_prompt_list(server_id)
        if prompts is None:
            return jsonify({"message": f"服务器 {server_id} 未连接"}), 404

        # MCP 原始对象含 AnyUrl 等非 JSON 原生类型，统一转换后再返回
        return jsonify([_convert_to_prompt_info(prompt).model_dump() for prompt in prompts])
    except Exception as e:
        logger.opt(exception=e).error(f"获取MCP服务器 {server_id} 提示词列表失败")
        return jsonify(_public_error()), 500

@mcp_bp.route("/servers/<server_id>/resources", methods=["GET"])
@require_auth("mcp.read")
async def get_server_resources(server_id: str):
    """获取MCP服务器提供的资源列表"""
    try:
        # 从容器中获取MCP服务器管理器
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        # 获取服务器
        server = manager.get_server(server_id)
        if not server:
            return jsonify({"message": f"服务器 {server_id} 不存在"}), 404

        resources = await manager.get_resource_list(server_id)
        if resources is None:
            return jsonify({"message": f"服务器 {server_id} 未连接"}), 404
        
        return jsonify([_convert_to_resource_info(resource).model_dump() for resource in resources])
    except Exception as e:
        logger.opt(exception=e).error(f"获取MCP服务器 {server_id} 资源列表失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/<server_id>/resources/<path:resource_id>", methods=["GET"])
@require_auth("mcp.read")
async def read_server_resource(server_id: str, resource_id: str):
    """读取MCP服务器上指定资源的内容

    resource_id 为资源列表里返回的 id（即 name，缺失时为 uri）。这里先在资源
    列表中按 name / uri 反查真实 uri，再交给 MCP 会话读取，避免直接把前端
    传入的字符串当作 uri 使用。
    """
    try:
        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        server = manager.get_server(server_id)
        if not server:
            return jsonify({"message": f"服务器 {server_id} 不存在"}), 404

        resources = await manager.get_resource_list(server_id)
        if resources is None:
            return jsonify({"message": f"服务器 {server_id} 未连接"}), 404

        target_uri: Optional[str] = None
        for resource in resources:
            uri = str(resource.uri)
            name = getattr(resource, "name", None) or uri
            if resource_id in (name, uri):
                target_uri = uri
                break

        if target_uri is None:
            return jsonify({"message": f"资源 {resource_id} 不存在"}), 404

        result = await manager.get_resource(server_id, target_uri)
        if result is None:
            return jsonify({"message": f"服务器 {server_id} 未连接"}), 404

        return jsonify(result.model_dump(mode="json"))
    except Exception as e:
        logger.opt(exception=e).error(f"读取MCP服务器 {server_id} 资源 {resource_id} 失败")
        return jsonify(_public_error()), 500


@mcp_bp.route("/servers/<server_id>/prompts/sample", methods=["POST"])
@require_auth("mcp.execute")
async def sample_server_prompt(server_id: str):
    """按给定参数取回MCP服务器上的提示词内容

    WebUI 传入 promptId 和按 prompts/list 声明生成的 arguments。旧版 text 与
    temperature 字段仍兼容；所有参数在校验 required 声明后原样传给 prompts/get。
    """
    try:
        data = await request.get_json()
        request_data = MCPPromptSampleRequest(**data)

        manager: MCPServerManager = g.container.resolve(MCPServerManager)

        server = manager.get_server(server_id)
        if not server:
            return jsonify({"message": f"服务器 {server_id} 不存在"}), 404

        prompts = await manager.get_prompt_list(server_id)
        if prompts is None:
            return jsonify({"message": f"服务器 {server_id} 未连接"}), 404
        prompt = next(
            (item for item in prompts if item.name == request_data.promptId), None
        )
        if prompt is None:
            return jsonify({"message": "提示词不存在"}), 404

        declarations = {
            argument.name: argument
            for argument in (getattr(prompt, "arguments", None) or [])
        }
        prompt_args = dict(request_data.arguments)
        if request_data.text:
            prompt_args.setdefault("text", request_data.text)
        if request_data.temperature is not None:
            prompt_args.setdefault("temperature", str(request_data.temperature))

        missing = sorted(
            name
            for name, argument in declarations.items()
            if bool(getattr(argument, "required", False))
            and not prompt_args.get(name, "").strip()
        )
        if missing:
            return jsonify(
                _invalid_request(
                    f"缺少必填提示词参数: {', '.join(name[:64] for name in missing[:16])}"
                )
            ), 400

        result = await manager.get_prompt(
            server_id, request_data.promptId, prompt_args or None
        )
        if result is None:
            return jsonify({"message": f"服务器 {server_id} 未连接"}), 404

        payload = result.model_dump(mode="json")
        # 前端优先读 text 字段，没有 text 时才回退到展示整个 JSON
        texts = [
            message.content.text
            for message in result.messages
            if getattr(message.content, "type", None) == "text"
        ]
        if texts:
            payload["text"] = "\n".join(texts)
        return jsonify(payload)
    except (ValueError, TypeError):
        logger.warning(f"拒绝无效的 MCP 提示词采样请求: {server_id}")
        return jsonify(_invalid_request("Invalid MCP prompt request")), 400
    except Exception as e:
        logger.opt(exception=e).error(f"采样MCP服务器 {server_id} 提示词失败")
        return jsonify(_public_error()), 500
