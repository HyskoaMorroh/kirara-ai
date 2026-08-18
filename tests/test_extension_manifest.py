from unittest.mock import AsyncMock, MagicMock

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.manager import MCPServerManager, ToolCacheEntry
from kirara_ai.mcp_module.models import MCPConnectionState
from kirara_ai.plugin_manager.models import (
    ExtensionCapabilities,
    ExtensionManifest,
    LifecycleHook,
)
from kirara_ai.plugin_manager.plugin_event_bus import PluginEventBus


def test_no_manifest_keeps_legacy_event_registration():
    event_bus = EventBus()
    plugin_bus = PluginEventBus(event_bus)
    received = []

    class LegacyEvent:
        pass

    plugin_bus.register(LegacyEvent, received.append)
    plugin_bus.post(LegacyEvent())
    assert len(received) == 1


def test_manifest_checks_lifecycle_and_capabilities_with_structured_audit():
    audit = []
    manifest = ExtensionManifest(
        name="example",
        version="1.0.0",
        capabilities=ExtensionCapabilities(lifecycle_hooks=True, network=True),
        hooks=[LifecycleHook(name="workflow_before", capability="lifecycle_hooks")],
    )
    plugin_bus = PluginEventBus(EventBus(), manifest=manifest, audit_sink=audit.append)

    listener = MagicMock()
    plugin_bus.register_lifecycle_hook("workflow_before", listener)
    plugin_bus.emit_lifecycle("workflow_before", {"workflow_id": "chat:plain_text"})
    listener.assert_called_once()

    with pytest.raises(PermissionError):
        plugin_bus.register_lifecycle_hook("workflow_after", listener)
    with pytest.raises(PermissionError):
        plugin_bus.require_capability("file")
    with pytest.raises(PermissionError):
        plugin_bus.require_capability("secret")

    assert any(record["outcome"] == "rejected" for record in audit)
    assert all("secret_value" not in record for record in audit)


def test_unknown_lifecycle_name_is_rejected():
    with pytest.raises(ValueError):
        LifecycleHook(name="unknown_event", capability="lifecycle_hooks")

    audit = []
    plugin_bus = PluginEventBus(
        EventBus(),
        manifest=ExtensionManifest(name="example", version="1.0.0"),
        audit_sink=audit.append,
    )
    with pytest.raises(ValueError):
        plugin_bus.register_lifecycle_hook("unknown_event", MagicMock())
    assert audit[-1]["outcome"] == "rejected"


def test_config_write_capability_accepts_manifest_spelling():
    capabilities = ExtensionCapabilities.model_validate({"config-write": True})

    assert capabilities.allows("config-write") is True
    assert capabilities.allows("config_write") is True


@pytest.mark.asyncio
async def test_mcp_operations_emit_redacted_audit_events():
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    audit = []
    manager = MCPServerManager(container, audit_sink=audit.append)

    server = MagicMock()
    server.state = MCPConnectionState.CONNECTED
    server.server_config.id = "local-tools"
    server.call_tool = AsyncMock(side_effect=RuntimeError("credential=private-value"))
    server.get_prompt = AsyncMock(return_value="prompt")
    server.read_resource = AsyncMock(return_value="resource")
    manager.servers["local-tools"] = server
    manager.tools_cache["search"] = ToolCacheEntry("local-tools", "search", MagicMock())

    assert await manager.call_tool("search", {"password": "private-value"}) is None
    assert await manager.get_prompt("local-tools", "draft", {"key": "private-value"}) == "prompt"
    assert await manager.get_resource("local-tools", "secret://private-value") == "resource"

    assert [record["operation"] for record in audit] == [
        "call_tool",
        "get_prompt",
        "get_resource",
    ]
    for record in audit:
        assert record["server"] == "local-tools"
        assert record["duration_ms"] >= 0
        assert record["outcome"] in {"success", "error"}
        serialized = repr(record)
        assert "private-value" not in serialized
        assert "password" not in serialized
