from unittest.mock import AsyncMock, MagicMock

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.events.plugin import PluginLoaded, PluginStarted, PluginStopped
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.mcp_module.manager import MCPServerManager, ToolCacheEntry
from kirara_ai.mcp_module.models import MCPConnectionState
from kirara_ai.plugin_manager.models import (
    ExtensionCapabilities,
    ExtensionManifest,
    LifecycleHook,
)
from kirara_ai.plugin_manager.plugin_event_bus import PluginEventBus
from kirara_ai.plugin_manager.plugin import Plugin
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.entry import notify_extension_lifecycle


def test_no_manifest_keeps_legacy_event_registration():
    event_bus = EventBus()
    plugin_bus = PluginEventBus(event_bus)
    received = []

    class LegacyEvent:
        pass

    plugin_bus.register(LegacyEvent, received.append)
    plugin_bus.post(LegacyEvent())
    assert len(received) == 1


def _loader(tmp_path):
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    container.register(EventBus, EventBus())
    return PluginLoader(container, str(tmp_path))


def test_manifest_capabilities_are_enforced_through_real_loader_injection(tmp_path):
    class ManifestedPlugin(Plugin):
        event_bus: EventBus
        manifest = ExtensionManifest(
            name="manifested", version="1", capabilities=ExtensionCapabilities()
        )

        def __init__(self, event_bus: EventBus):
            event_bus.register(object, lambda event: None)

        def on_load(self):
            pass

        def on_start(self):
            pass

        def on_stop(self):
            pass

    with pytest.raises(PermissionError, match="events"):
        _loader(tmp_path).register_plugin(ManifestedPlugin)


def test_manifested_loader_preserves_existing_dependency_injection(tmp_path):
    class ExistingDependency:
        pass

    dependency = ExistingDependency()

    class ManifestedPlugin(Plugin):
        event_bus: EventBus
        manifest = ExtensionManifest(name="manifested", version="1")

        def __init__(
            self, existing_dependency: ExistingDependency, event_bus: EventBus
        ):
            self.existing_dependency = existing_dependency
            self.injected_event_bus = event_bus

        def on_load(self):
            pass

        def on_start(self):
            pass

        def on_stop(self):
            pass

    loader = _loader(tmp_path)
    loader.container.register(ExistingDependency, dependency)

    plugin = loader.register_plugin(ManifestedPlugin)

    assert plugin.existing_dependency is dependency
    assert isinstance(plugin.injected_event_bus, PluginEventBus)


def test_manifestless_loader_path_preserves_event_registration(tmp_path):
    received = []

    class LegacyPlugin(Plugin):
        event_bus: EventBus

        def __init__(self, event_bus: EventBus):
            event_type = type("LegacyEvent", (), {})
            event_bus.register(event_type, received.append)

        def on_load(self):
            pass

        def on_start(self):
            pass

        def on_stop(self):
            pass

    loader = _loader(tmp_path)
    plugin = loader.register_plugin(LegacyPlugin)
    event_type = next(iter(plugin.event_bus._event_bus._listeners))
    plugin.event_bus.post(event_type())
    assert len(received) == 1


def test_manifested_host_operations_are_checked_at_loader_boundary(tmp_path):
    class ManifestedPlugin(Plugin):
        event_bus: EventBus
        manifest = ExtensionManifest(
            name="manifested", version="1", capabilities=ExtensionCapabilities()
        )

        def on_load(self):
            pass

        def on_start(self):
            pass

        def on_stop(self):
            pass

    plugin = _loader(tmp_path).register_plugin(ManifestedPlugin)
    with pytest.raises(PermissionError):
        plugin.event_bus.read_file(tmp_path / "config.yaml")
    with pytest.raises(PermissionError):
        plugin.event_bus.request("GET", "https://example.invalid")
    with pytest.raises(PermissionError):
        plugin.event_bus.run_process(["python", "-c", "pass"])
    with pytest.raises(PermissionError):
        plugin.event_bus.write_config(tmp_path / "config.json", {})
    with pytest.raises(PermissionError):
        plugin.event_bus.get_secret("example")


def test_declared_lifecycle_registered_by_loader_is_produced_by_application(tmp_path):
    received = []

    class ManifestedPlugin(Plugin):
        event_bus: EventBus
        manifest = ExtensionManifest(
            name="manifested",
            version="1",
            capabilities=ExtensionCapabilities(
                lifecycle_hooks=True, events=True
            ),
            hooks=[
                LifecycleHook(name="startup_completed"),
                LifecycleHook(name="shutdown_requested"),
            ],
        )

        def on_load(self):
            self.event_bus.register_lifecycle_hook(
                "startup_completed", lambda payload: received.append(("start", payload))
            )
            self.event_bus.register_lifecycle_hook(
                "shutdown_requested", lambda payload: received.append(("stop", payload))
            )

        def on_start(self):
            pass

        def on_stop(self):
            pass

    loader = _loader(tmp_path)
    legacy_events = []
    for event_type in (PluginLoaded, PluginStarted, PluginStopped):
        loader.event_bus.register(event_type, legacy_events.append)
    loader.register_plugin(ManifestedPlugin)
    loader.load_plugins()
    loader.start_plugins()
    notify_extension_lifecycle(loader.container, "startup_completed")
    notify_extension_lifecycle(loader.container, "shutdown_requested")
    loader.stop_plugins()

    assert [kind for kind, _ in received] == ["start", "stop"]
    assert received[0][1]["component"] == "application"
    assert [type(event) for event in legacy_events] == [
        PluginLoaded,
        PluginStarted,
        PluginStopped,
    ]


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
    lifecycle = []
    from kirara_ai.plugin_manager.extension_host import ExtensionLifecycleHost

    host = ExtensionLifecycleHost()
    container.register(ExtensionLifecycleHost, host)
    lifecycle_bus = PluginEventBus(
        EventBus(),
        manifest=ExtensionManifest(
            name="observer",
            version="1",
            capabilities=ExtensionCapabilities(lifecycle_hooks=True),
            hooks=[LifecycleHook(name="mcp_operation")],
        ),
    )
    lifecycle_bus.register_lifecycle_hook("mcp_operation", lifecycle.append)
    host.register(lifecycle_bus)
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
    assert [item["operation"] for item in lifecycle] == [
        "call_tool",
        "get_prompt",
        "get_resource",
    ]


@pytest.mark.asyncio
async def test_mcp_stop_clears_all_server_caches():
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    manager = MCPServerManager(container)

    server = MagicMock()
    server.state = MCPConnectionState.CONNECTED

    async def disconnect():
        server.state = MCPConnectionState.DISCONNECTED
        return True

    server.disconnect = AsyncMock(side_effect=disconnect)
    manager.servers["local-tools"] = server
    manager.tools_cache["search"] = ToolCacheEntry("local-tools", "search", MagicMock())
    manager.prompts_cache["local-tools"] = [MagicMock()]
    manager.resources_cache["local-tools"] = [MagicMock()]

    assert await manager.stop_server("local-tools") is True
    assert not manager.tools_cache
    assert "local-tools" not in manager.prompts_cache
    assert "local-tools" not in manager.resources_cache


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "state",
    [MCPConnectionState.DISCONNECTED, MCPConnectionState.ERROR],
)
async def test_mcp_stop_clears_all_server_caches_when_server_is_not_connected(state):
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    manager = MCPServerManager(container)

    server = MagicMock()
    server.state = state
    manager.servers["local-tools"] = server
    manager.tools_cache["search"] = ToolCacheEntry("local-tools", "search", MagicMock())
    manager.prompts_cache["local-tools"] = [MagicMock()]
    manager.resources_cache["local-tools"] = [MagicMock()]

    assert await manager.stop_server("local-tools") is True
    assert not manager.tools_cache
    assert "local-tools" not in manager.prompts_cache
    assert "local-tools" not in manager.resources_cache


@pytest.mark.asyncio
async def test_mcp_stop_clears_caches_even_when_disconnect_fails():
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    manager = MCPServerManager(container)

    server = MagicMock()
    server.state = MCPConnectionState.CONNECTED
    server.disconnect = AsyncMock(return_value=False)
    manager.servers["local-tools"] = server
    manager.tools_cache["search"] = ToolCacheEntry("local-tools", "search", MagicMock())
    manager.prompts_cache["local-tools"] = [MagicMock()]
    manager.resources_cache["local-tools"] = [MagicMock()]

    assert await manager.stop_server("local-tools") is False
    assert not manager.tools_cache
    assert "local-tools" not in manager.prompts_cache
    assert "local-tools" not in manager.resources_cache


@pytest.mark.asyncio
async def test_mcp_connect_failure_does_not_leave_stale_server_caches():
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    manager = MCPServerManager(container)

    server = MagicMock()
    server.state = MCPConnectionState.DISCONNECTED
    server.connect = AsyncMock(return_value=False)
    manager.servers["failed"] = server
    manager.tools_cache["old"] = ToolCacheEntry("failed", "old", MagicMock())
    manager.prompts_cache["failed"] = [MagicMock()]
    manager.resources_cache["failed"] = [MagicMock()]

    assert await manager.connect_server("failed") is False
    assert not manager.tools_cache
    assert "failed" not in manager.prompts_cache
    assert "failed" not in manager.resources_cache


@pytest.mark.asyncio
async def test_mcp_runtime_status_records_safe_connection_failure():
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    manager = MCPServerManager(container)

    server = MagicMock()
    server.state = MCPConnectionState.DISCONNECTED
    server.connect = AsyncMock(return_value=False)
    manager.servers["failed"] = server

    assert await manager.connect_server("failed") is False

    runtime = manager.get_runtime_status("failed")
    assert runtime["status"] == "failed"
    assert runtime["running"] is False
    assert runtime["failed"] is True
    assert runtime["last_error"]
    assert runtime["last_checked_at"]
    assert "private" not in repr(runtime)


def test_mcp_runtime_status_maps_connection_states():
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    manager = MCPServerManager(container)
    server = MagicMock()
    manager.servers["docs"] = server

    server.state = MCPConnectionState.DISCONNECTED
    assert manager.get_runtime_status("docs")["status"] == "stopped"

    server.state = MCPConnectionState.CONNECTED
    assert manager.get_runtime_status("docs")["status"] == "running"

    server.state = MCPConnectionState.ERROR
    runtime = manager.get_runtime_status("docs")
    assert runtime["status"] == "failed"
    assert runtime["failed"] is True
    assert runtime["last_error"]


@pytest.mark.asyncio
async def test_mcp_tool_call_requires_runtime_allowlist_intersection():
    container = DependencyContainer()
    container.register(GlobalConfig, GlobalConfig())
    manager = MCPServerManager(container)

    server = MagicMock()
    server.state = MCPConnectionState.CONNECTED
    server.server_config.id = "local-tools"
    server.call_tool = AsyncMock()
    manager.servers["local-tools"] = server
    manager.tools_cache["search"] = ToolCacheEntry("local-tools", "search", MagicMock())

    assert await manager.call_tool(
        "search",
        {},
        agent_allowlist={"search"},
        session_allowlist={"other"},
    ) is None
    server.call_tool.assert_not_awaited()
