from __future__ import annotations

from typing import Any

from quart import Blueprint, g, jsonify, request

from kirara_ai.agent_runtime import AgentDefinition, AgentRegistry, ResourceBinding
from kirara_ai.logger import get_logger
from kirara_ai.plugin_manager.resource_lifecycle import (
    ResourceLifecycleService,
    ResourceStateError,
    ResourceValidationError,
)
from kirara_ai.web.auth.middleware import require_auth


agent_bp = Blueprint("agent", __name__)
logger = get_logger("Web.Agent")


def _registry() -> AgentRegistry:
    return g.container.resolve(AgentRegistry)


def _resources() -> ResourceLifecycleService:
    return g.container.resolve(ResourceLifecycleService)


def _session_store():
    """Resolve the session store, or ``None`` when the runtime is not wired.

    A deployment without the Agent runtime (or a unit test using a bare
    container) must get a clear 503 rather than a 500 from an unresolved
    dependency.
    """
    from kirara_ai.agent_runtime import SessionStore

    container = g.container
    if not container.has(SessionStore):
        return None
    return container.resolve(SessionStore)


def _hook_runtime():
    """Resolve the hook runtime from the Agent executor, or ``None``."""
    from kirara_ai.agent_runtime import AgentRuntimeExecutor

    container = g.container
    if not container.has(AgentRuntimeExecutor):
        return None
    return getattr(container.resolve(AgentRuntimeExecutor), "hook_runtime", None)


def _error(message: str, status_code: int):
    return jsonify({"error": message}), status_code


def _binding_payload(binding: ResourceBinding) -> dict[str, Any]:
    return {
        "resource_id": binding.resource_id,
        "resource_type": binding.resource_type,
        "version": binding.version,
        "version_policy": binding.version_policy,
        "content_sha256": binding.content_sha256,
        "enabled": binding.enabled,
        "permissions": list(binding.permissions),
        "source": binding.source,
    }


def _agent_payload(agent: AgentDefinition, registry: AgentRegistry) -> dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "display_name": agent.display_name,
        "enabled": agent.enabled,
        "workflow_id": agent.workflow_id,
        "model_priority": list(agent.model_priority),
        "provider_allowlist": sorted(agent.provider_allowlist),
        "capabilities": sorted(agent.capabilities),
        "prompt_bindings": [_binding_payload(item) for item in agent.prompt_bindings],
        "skill_bindings": [_binding_payload(item) for item in agent.skill_bindings],
        "memory_bindings": [_binding_payload(item) for item in agent.memory_bindings],
        "mcp_bindings": [_binding_payload(item) for item in agent.mcp_bindings],
        "hook_bindings": [_binding_payload(item) for item in agent.hook_bindings],
        "mcp_allowlist": sorted(agent.mcp_allowlist),
        "allow_tools": agent.allow_tools,
        "max_tool_iterations": agent.max_tool_iterations,
        "relations": registry.relation_summary(agent.agent_id),
    }


def _string_list(payload: dict[str, Any], name: str, *, default: Any = None) -> tuple[str, ...]:
    value = payload.get(name, default)
    if value is None:
        return ()
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"{name} must be a list of non-empty strings")
    return tuple(item.strip() for item in value)


def _resolve_bindings(payload: Any, expected_type: str) -> tuple[ResourceBinding, ...]:
    if payload is None:
        return ()
    if not isinstance(payload, list):
        raise ValueError(f"{expected_type}_bindings must be a list")
    result = []
    for item in payload:
        if not isinstance(item, dict):
            raise ValueError("resource binding must be an object")
        resource_id = item.get("resource_id")
        if not isinstance(resource_id, str) or not resource_id.strip():
            raise ValueError("resource_id is required")
        binding_type = item.get("resource_type", expected_type)
        if binding_type != expected_type:
            raise ValueError("resource binding type does not match its field")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise ValueError("resource binding enabled must be boolean")
        version_policy = item.get("version_policy", "current")
        if version_policy not in {"fixed", "current"}:
            raise ValueError("resource binding version_policy must be fixed or current")
        version = item.get("version")
        if version is not None and (not isinstance(version, str) or not version.strip()):
            raise ValueError("resource binding version must be a non-empty string")
        result.append(
            _resources().resolve_binding(
                resource_id.strip(),
                expected_type,
                version=(version.strip() if version_policy == "fixed" and version else None),
                enabled=enabled,
                version_policy=version_policy,
            )
        )
    return tuple(result)


def _agent_from_payload(payload: dict[str, Any], existing: AgentDefinition | None = None) -> AgentDefinition:
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    base = {
        "agent_id": existing.agent_id if existing else payload.get("agent_id"),
        "display_name": existing.display_name if existing else None,
        "enabled": existing.enabled if existing else True,
        "workflow_id": existing.workflow_id if existing else None,
        "model_priority": existing.model_priority if existing else (),
        "provider_allowlist": existing.provider_allowlist if existing else frozenset(),
        "capabilities": existing.capabilities if existing else frozenset(),
        "prompt_bindings": existing.prompt_bindings if existing else (),
        "skill_bindings": existing.skill_bindings if existing else (),
        "memory_bindings": existing.memory_bindings if existing else (),
        "mcp_bindings": existing.mcp_bindings if existing else (),
        "hook_bindings": existing.hook_bindings if existing else (),
        "mcp_allowlist": existing.mcp_allowlist if existing else frozenset(),
        "allow_tools": existing.allow_tools if existing else True,
        "max_tool_iterations": existing.max_tool_iterations if existing else 8,
    }
    for name in (
        "display_name",
        "enabled",
        "workflow_id",
        "allow_tools",
        "max_tool_iterations",
    ):
        if name in payload:
            base[name] = payload[name]
    for name in ("model_priority", "provider_allowlist", "capabilities", "mcp_allowlist"):
        if name in payload:
            base[name] = _string_list(payload, name)
    for name, resource_type in (
        ("prompt_bindings", "prompt"),
        ("skill_bindings", "skill"),
        ("memory_bindings", "memory"),
        ("mcp_bindings", "mcp"),
        ("hook_bindings", "hook"),
    ):
        if name in payload:
            base[name] = _resolve_bindings(payload[name], resource_type)
    if not isinstance(base["enabled"], bool) or not isinstance(base["allow_tools"], bool):
        raise ValueError("enabled and allow_tools must be boolean")
    return AgentDefinition(**base)


def _relations_from_payload(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("relations must be an object")

    channels = _string_list(payload, "channels", default=[])
    sessions = _string_list(payload, "sessions", default=[])
    is_default = payload.get("is_default", False)
    if not isinstance(is_default, bool):
        raise ValueError("relations.is_default must be boolean")

    raw_accounts = payload.get("accounts", [])
    if not isinstance(raw_accounts, list):
        raise ValueError("relations.accounts must be a list")
    accounts = []
    for item in raw_accounts:
        if not isinstance(item, dict):
            raise ValueError("account relation must be an object")
        values = tuple(
            item.get(name)
            for name in ("channel_type", "adapter_instance", "account_scope")
        )
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError(
                "channel_type, adapter_instance and account_scope are required"
            )
        accounts.append(tuple(value.strip() for value in values))
    return {
        "channels": channels,
        "accounts": tuple(accounts),
        "sessions": sessions,
        "is_default": is_default,
    }


def _handle_error(error: Exception):
    if isinstance(error, (ValueError, KeyError, ResourceValidationError)):
        return _error(str(error), 400)
    if isinstance(error, ResourceStateError):
        return _error(str(error), 409)
    logger.opt(exception=error).error("Agent API request failed")
    return _error("Agent operation failed", 500)


@agent_bp.route("", methods=["GET"])
@require_auth
async def list_agents():
    registry = _registry()
    return jsonify([_agent_payload(agent, registry) for agent in registry.list()])


@agent_bp.route("", methods=["POST"])
@require_auth
async def create_agent():
    try:
        payload = await request.get_json(silent=True) or {}
        agent = _agent_from_payload(payload)
        _registry().register(agent)
        return jsonify(_agent_payload(agent, _registry())), 201
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/configuration", methods=["POST"])
@require_auth
async def create_agent_configuration():
    try:
        payload = await request.get_json(silent=True) or {}
        agent = _agent_from_payload(payload)
        relations = _relations_from_payload(payload.get("relations", {}))
        registry = _registry()
        registry.configure(agent, create=True, **relations)
        return jsonify(_agent_payload(registry.get(agent.agent_id), registry)), 201
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>/configuration", methods=["PUT"])
@require_auth
async def update_agent_configuration(agent_id: str):
    try:
        payload = await request.get_json(silent=True) or {}
        if "agent_id" in payload and payload["agent_id"] != agent_id:
            raise ValueError("agent_id cannot change")
        registry = _registry()
        agent = _agent_from_payload(payload, registry.get(agent_id))
        relations = _relations_from_payload(payload.get("relations", {}))
        registry.configure(agent, create=False, **relations)
        return jsonify(_agent_payload(registry.get(agent_id), registry))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>", methods=["GET"])
@require_auth
async def get_agent(agent_id: str):
    try:
        registry = _registry()
        return jsonify(_agent_payload(registry.get(agent_id), registry))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>", methods=["PUT"])
@require_auth
async def update_agent(agent_id: str):
    try:
        registry = _registry()
        current = registry.get(agent_id)
        payload = await request.get_json(silent=True) or {}
        if "agent_id" in payload and payload["agent_id"] != agent_id:
            raise ValueError("agent_id cannot change")
        updated = _agent_from_payload(payload, current)
        registry.update(updated)
        return jsonify(_agent_payload(updated, registry))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>", methods=["DELETE"])
@require_auth
async def delete_agent(agent_id: str):
    try:
        _registry().remove(agent_id)
        return jsonify({})
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/default", methods=["POST"])
@require_auth
async def set_default_agent():
    try:
        payload = await request.get_json(silent=True) or {}
        agent_id = payload.get("agent_id")
        _registry().set_default(agent_id)
        return jsonify(_agent_payload(_registry().get(agent_id), _registry()))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>/enable", methods=["POST"])
@require_auth
async def enable_agent(agent_id: str):
    try:
        agent = _registry().set_enabled(agent_id, True)
        return jsonify(_agent_payload(agent, _registry()))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>/disable", methods=["POST"])
@require_auth
async def disable_agent(agent_id: str):
    try:
        agent = _registry().set_enabled(agent_id, False)
        return jsonify(_agent_payload(agent, _registry()))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>/channels", methods=["POST", "DELETE"])
@require_auth
async def bind_channel(agent_id: str):
    try:
        payload = await request.get_json(silent=True) or {}
        channel_type = payload.get("channel_type")
        if request.method == "POST":
            _registry().bind_channel(channel_type, agent_id)
        else:
            _registry().unbind_channel(channel_type)
        return jsonify(_agent_payload(_registry().get(agent_id), _registry()))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>/accounts", methods=["POST", "DELETE"])
@require_auth
async def bind_account(agent_id: str):
    try:
        payload = await request.get_json(silent=True) or {}
        values = [payload.get(name) for name in ("channel_type", "adapter_instance", "account_scope")]
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise ValueError("channel_type, adapter_instance and account_scope are required")
        if request.method == "POST":
            _registry().bind_account(*values, agent_id)
        else:
            _registry().unbind_account(*values)
        return jsonify(_agent_payload(_registry().get(agent_id), _registry()))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>/sessions", methods=["POST", "DELETE"])
@require_auth
async def bind_session(agent_id: str):
    try:
        payload = await request.get_json(silent=True) or {}
        session_key = payload.get("session_key")
        if not isinstance(session_key, str) or not session_key.strip():
            raise ValueError("session_key is required")
        if request.method == "POST":
            _registry().bind_session(session_key, agent_id)
        else:
            _registry().unbind_session(session_key)
        return jsonify(_agent_payload(_registry().get(agent_id), _registry()))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/<agent_id>/resources", methods=["POST"])
@require_auth
async def add_agent_resources(agent_id: str):
    try:
        registry = _registry()
        current = registry.get(agent_id)
        payload = await request.get_json(silent=True) or {}
        updated = _agent_from_payload(payload, current)
        registry.update_resource_bindings(
            agent_id,
            prompt_bindings=updated.prompt_bindings,
            skill_bindings=updated.skill_bindings,
            memory_bindings=updated.memory_bindings,
            mcp_bindings=updated.mcp_bindings,
            hook_bindings=updated.hook_bindings,
            mcp_allowlist=updated.mcp_allowlist,
        )
        return jsonify(_agent_payload(registry.get(agent_id), registry))
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/sessions", methods=["GET"])
@require_auth
async def list_sessions():
    """List persisted sessions with counts only, never conversation text.

    会话此前只有「绑定」接口，没有列表：既看不到有哪些会话、历史多长、
    是否卡在待确认，也无从清理。这里只返回元数据，正文不出这个边界。
    """
    store = _session_store()
    if store is None:
        return _error("session store is not configured", 503)
    try:
        limit = int(request.args.get("limit", 200))
    except (TypeError, ValueError):
        return _error("limit must be an integer", 400)
    try:
        return jsonify({"items": store.list_sessions(limit=limit)})
    except ValueError as error:
        return _error(str(error), 400)
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/sessions/<session_id>", methods=["DELETE"])
@require_auth
async def delete_session(session_id: str):
    """Delete one session's stored history."""
    store = _session_store()
    if store is None:
        return _error("session store is not configured", 503)
    try:
        if not store.delete_session(session_id):
            return _error("session not found", 404)
        return jsonify({"deleted": True})
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/sessions/<session_id>/history", methods=["DELETE"])
@require_auth
async def clear_session_history(session_id: str):
    """Empty one session's history while keeping its binding intact."""
    store = _session_store()
    if store is None:
        return _error("session store is not configured", 503)
    try:
        if not store.clear_history(session_id):
            return _error("session not found", 404)
        return jsonify({"cleared": True})
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/hooks", methods=["GET"])
@require_auth
async def list_hook_declarations():
    """Describe every installed hook's declared events without executing them.

    Hook 此前只能「装上再看它会不会跑」：事件名写错、matcher 写成非法正则、
    把 command 写进不该写的位置，都只能在真实请求里暴露——而那时它已经在
    生产路径上了。这里是只读描述，不执行任何 handler、不启动任何进程。
    """
    runtime = _hook_runtime()
    if runtime is None:
        return _error("agent hook runtime is not configured", 503)
    try:
        service = _resources()
        items = []
        for resource in service.list_resources("hook"):
            resource_id = resource.get("resource_id")
            if not resource_id:
                continue
            binding = service.resolve_binding(
                resource_id,
                "hook",
                version=resource.get("current_version"),
                enabled=bool(resource.get("enabled")),
                version_policy="fixed",
            )
            items.append(runtime.describe_binding(binding))
        return jsonify({"items": items})
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/hooks/<resource_id>/preview", methods=["POST"])
@require_auth
async def preview_hook_event(resource_id: str):
    """Answer "would this hook run for this tool?" without running it.

    这是 dry-run 的核心问题：用户能在把 Hook 放到生产路径之前验证
    matcher 是否按预期收窄，而不是靠一次真实的工具调用去试。
    """
    runtime = _hook_runtime()
    if runtime is None:
        return _error("agent hook runtime is not configured", 503)
    payload = await request.get_json(silent=True) or {}
    event = payload.get("event")
    if not isinstance(event, str) or not event.strip():
        return _error("event is required", 400)
    tool_name = payload.get("tool_name")
    if tool_name is not None and not isinstance(tool_name, str):
        return _error("tool_name must be a string", 400)
    try:
        service = _resources()
        resource = service.get_resource(resource_id)
        binding = service.resolve_binding(
            resource_id,
            "hook",
            version=resource.get("current_version"),
            enabled=bool(resource.get("enabled")),
            version_policy="fixed",
        )
        return jsonify(
            runtime.preview_event(binding, event.strip(), tool_name=tool_name)
        )
    except Exception as error:
        return _handle_error(error)


@agent_bp.route("/confirmations", methods=["GET"])
@require_auth
async def list_pending_confirmations():
    """List confirmations still waiting on a human decision.

    确认记录一直是持久化的，但没有任何查询出口：一个卡住的待确认操作
    在界面上完全不可见，只能去翻 data/sessions/pending.json。
    这里只返回状态与时间戳等元数据，不含工具参数。
    """
    store = _session_store()
    if store is None:
        return _error("session store is not configured", 503)
    try:
        items = [
            {
                "confirmation_id": record.get("confirmation_id"),
                "agent_id": record.get("agent_id"),
                "status": record.get("status"),
                "created_at": record.get("created_at"),
                "updated_at": record.get("updated_at"),
                "expires_at": record.get("expires_at"),
                "correlation_id": record.get("correlation_id"),
                "tool_name": record.get("tool_name"),
            }
            for record in store.load_pending()
        ]
        return jsonify({"items": items})
    except Exception as error:
        return _handle_error(error)
