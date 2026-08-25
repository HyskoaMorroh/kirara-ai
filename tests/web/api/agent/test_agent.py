from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.agent_runtime import AgentRegistry
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_sources import ResourceSourceService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService
from kirara_ai.workflow.core.block.registry import BlockRegistry


class WorkflowRegistry:
    def get_workflow(self, workflow_id, container):
        return object() if workflow_id == "chat:normal" else None


@pytest.fixture
def agent_api(tmp_path: Path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(GlobalConfig, GlobalConfig())
    container.register(AuthService, MockAuthService())
    container.register(EventBus, EventBus())
    container.register(BlockRegistry, BlockRegistry())

    lifecycle = ResourceLifecycleService(
        tmp_path / "data",
        workflow_registry=WorkflowRegistry(),
        container=container,
    )
    source_service = ResourceSourceService(lifecycle)
    catalog = ResourceCatalogService(lifecycle, source_service)
    catalog.ensure_builtins()
    for resource_id in (
        "prompt.office-research",
        "mcp.context7",
        "hook.ai-debug",
    ):
        lifecycle.enable(resource_id, confirmed=True)

    container.register(ResourceLifecycleService, lifecycle)
    container.register(ResourceSourceService, source_service)
    container.register(ResourceCatalogService, catalog)
    container.register(MCPServerManager, MCPServerManager(container))
    registry = AgentRegistry(tmp_path / "data")
    container.register(AgentRegistry, registry)

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), lifecycle, registry


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _binding(resource_id: str, **metadata):
    return {"resource_id": resource_id, **metadata}


@pytest.mark.asyncio
async def test_create_agent_defaults_bindings_to_current_and_uses_server_metadata(agent_api):
    client, lifecycle, _ = agent_api

    response = await client.post(
        "/api/agents",
        headers=_headers(),
        json={
            "agent_id": "office-agent",
            "model_priority": ["primary-model", "backup-model"],
            "prompt_bindings": [
                _binding(
                    "prompt.office-research",
                    version="9.9.9",
                    content_sha256="f" * 64,
                    source="client-controlled-source",
                    permissions=["admin"],
                )
            ],
            "mcp_bindings": [_binding("mcp.context7")],
            "hook_bindings": [_binding("hook.ai-debug")],
            "mcp_allowlist": ["context7"],
        },
    )

    assert response.status_code == 201
    payload = await response.get_json()
    assert payload["model_priority"] == ["primary-model", "backup-model"]
    prompt = payload["prompt_bindings"][0]
    assert prompt["version"] == "1.0.0"
    assert prompt["version_policy"] == "current"
    assert prompt["source"] == "catalog://kirara/prompt/office-research"
    assert prompt["permissions"] == ["workflow.read"]
    assert prompt["content_sha256"] == lifecycle.get_resource(
        "prompt.office-research"
    )["versions"][0]["content_sha256"]
    assert prompt["content_sha256"] != "f" * 64
    assert payload["mcp_bindings"][0]["resource_id"] == "mcp.context7"
    assert payload["hook_bindings"][0]["resource_id"] == "hook.ai-debug"


@pytest.mark.asyncio
async def test_agent_api_preserves_explicit_fixed_version_policy(agent_api):
    client, _, _ = agent_api

    response = await client.post(
        "/api/agents",
        headers=_headers(),
        json={
            "agent_id": "pinned-agent",
            "model_priority": ["model-a"],
            "prompt_bindings": [
                _binding(
                    "prompt.office-research",
                    version="1.0.0",
                    version_policy="fixed",
                )
            ],
        },
    )

    assert response.status_code == 201
    binding = (await response.get_json())["prompt_bindings"][0]
    assert binding["version"] == "1.0.0"
    assert binding["version_policy"] == "fixed"


@pytest.mark.asyncio
async def test_agent_api_rejects_unknown_version_policy(agent_api):
    client, _, _ = agent_api

    response = await client.post(
        "/api/agents",
        headers=_headers(),
        json={
            "agent_id": "invalid-agent",
            "model_priority": ["model-a"],
            "prompt_bindings": [
                _binding(
                    "prompt.office-research",
                    version_policy="floating",
                )
            ],
        },
    )

    assert response.status_code == 400
    assert "fixed or current" in (await response.get_json())["error"]


@pytest.mark.asyncio
async def test_agent_api_requires_authentication(agent_api):
    client, _, _ = agent_api

    response = await client.post(
        "/api/agents",
        json={"agent_id": "unauthenticated", "model_priority": ["model-a"]},
    )

    assert response.status_code == 401


def _configuration_payload(**overrides):
    payload = {
        "agent_id": "office-agent",
        "display_name": "Office research",
        "enabled": True,
        "model_priority": ["primary-model", "backup-model"],
        "provider_allowlist": ["provider-a"],
        "capabilities": ["chat", "tools"],
        "prompt_bindings": [_binding("prompt.office-research")],
        "skill_bindings": [],
        "memory_bindings": [],
        "mcp_bindings": [_binding("mcp.context7")],
        "hook_bindings": [_binding("hook.ai-debug")],
        "mcp_allowlist": ["context7.resolve-library-id", "context7.query-docs"],
        "allow_tools": True,
        "max_tool_iterations": 6,
        "relations": {
            "is_default": True,
            "channels": ["webui", "onebot", "qqbot", "telegram", "wecom"],
            "accounts": [
                {
                    "channel_type": "telegram",
                    "adapter_instance": "telegram-main",
                    "account_scope": "research-bot",
                }
            ],
            "sessions": [
                "webui/webui/webui/c2c:research-user/research-user",
            ],
        },
    }
    payload.update(overrides)
    return payload


@pytest.mark.asyncio
async def test_create_complete_agent_configuration_commits_definition_and_relations_once(agent_api):
    client, lifecycle, registry = agent_api

    response = await client.post(
        "/api/agents/configuration",
        headers=_headers(),
        json=_configuration_payload(),
    )

    assert response.status_code == 201
    payload = await response.get_json()
    assert payload["relations"] == {
        "channels": ["onebot", "qqbot", "telegram", "webui", "wecom"],
        "accounts": [
            {
                "channel_type": "telegram",
                "adapter_instance": "telegram-main",
                "account_scope": "research-bot",
            }
        ],
        "sessions": ["webui/webui/webui/c2c:research-user/research-user"],
        "is_default": True,
    }
    assert payload["prompt_bindings"][0]["content_sha256"] == lifecycle.get_resource(
        "prompt.office-research"
    )["versions"][0]["content_sha256"]
    assert registry.default_agent_id == "office-agent"
    assert registry.get("office-agent").model_priority == (
        "primary-model",
        "backup-model",
    )


@pytest.mark.asyncio
async def test_update_complete_configuration_replaces_only_that_agents_relations(agent_api):
    client, _, registry = agent_api
    created = await client.post(
        "/api/agents/configuration",
        headers=_headers(),
        json=_configuration_payload(),
    )
    assert created.status_code == 201

    updated_payload = _configuration_payload(
        display_name="Updated office research",
        model_priority=["replacement-model"],
        relations={
            "is_default": False,
            "channels": ["telegram"],
            "accounts": [],
            "sessions": [],
        },
    )
    response = await client.put(
        "/api/agents/office-agent/configuration",
        headers=_headers(),
        json=updated_payload,
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["display_name"] == "Updated office research"
    assert payload["model_priority"] == ["replacement-model"]
    assert payload["relations"] == {
        "channels": ["telegram"],
        "accounts": [],
        "sessions": [],
        "is_default": False,
    }
    assert registry.default_agent_id is None


@pytest.mark.asyncio
async def test_invalid_complete_configuration_leaves_registry_unchanged(agent_api):
    client, _, registry = agent_api
    created = await client.post(
        "/api/agents/configuration",
        headers=_headers(),
        json=_configuration_payload(),
    )
    assert created.status_code == 201
    before = registry.to_dict()

    invalid = _configuration_payload(
        display_name="Must not persist",
        model_priority=["must-not-persist"],
        relations={
            "is_default": True,
            "channels": ["unsupported-channel"],
            "accounts": [],
            "sessions": [],
        },
    )
    response = await client.put(
        "/api/agents/office-agent/configuration",
        headers=_headers(),
        json=invalid,
    )

    assert response.status_code == 400
    assert "unsupported" in (await response.get_json())["error"].lower()
    assert registry.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("relation_kind", ["default", "channel", "account", "session"])
async def test_disabled_agent_cannot_become_a_routing_target(agent_api, relation_kind):
    client, _, registry = agent_api
    relations = {
        "is_default": relation_kind == "default",
        "channels": ["webui"] if relation_kind == "channel" else [],
        "accounts": (
            [
                {
                    "channel_type": "telegram",
                    "adapter_instance": "main",
                    "account_scope": "bot",
                }
            ]
            if relation_kind == "account"
            else []
        ),
        "sessions": ["webui/webui/webui/c2c:user/user"] if relation_kind == "session" else [],
    }

    response = await client.post(
        "/api/agents/configuration",
        headers=_headers(),
        json=_configuration_payload(enabled=False, relations=relations),
    )

    assert response.status_code == 400
    assert "disabled" in (await response.get_json())["error"].lower()
    assert registry.list() == []
