from __future__ import annotations

from pathlib import Path
import json
from unittest.mock import MagicMock

import pytest

import kirara_ai.entry as entry
from kirara_ai.agent_runtime import AgentDefinition, AgentRegistry, AgentRuntimeExecutor, SessionStore
from kirara_ai.config.global_config import AgentRuntimeConfig, GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.pricing import PriceCatalog
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.media import MediaManager
from kirara_ai.memory.memory_manager import MemoryManager
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService


def test_config_path_is_inside_configured_data_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(entry, "DATA_PATH", str(tmp_path / "vps-data"))

    assert entry._config_path() == tmp_path / "vps-data" / "config.yaml"


def test_init_pricing_system_uses_the_durable_data_path(tmp_path: Path, monkeypatch):
    data_path = tmp_path / "vps-data"
    monkeypatch.setattr(entry, "DATA_PATH", str(data_path))
    container = DependencyContainer()

    catalog = entry.init_pricing_system(container)

    assert isinstance(catalog, PriceCatalog)
    assert catalog.path == (data_path / "pricing" / "catalog.json").resolve()
    assert container.resolve(PriceCatalog) is catalog
    assert catalog.path.is_file()


def test_init_storage_keeps_database_and_media_below_configured_data_path(
    tmp_path: Path, monkeypatch
):
    data_path = tmp_path / "vps-data"
    database = MagicMock(spec=DatabaseManager)
    media = MagicMock(spec=MediaManager)
    media.metadata_cache = {}
    database_factory = MagicMock(return_value=database)
    media_factory = MagicMock(return_value=media)
    monkeypatch.setattr(entry, "DATA_PATH", str(data_path))
    monkeypatch.setattr(entry, "DatabaseManager", database_factory)
    monkeypatch.setattr(entry, "MediaManager", media_factory)
    container = DependencyContainer()

    initialized_database, initialized_media = entry.init_storage(container)

    database_factory.assert_called_once_with(container, data_dir=data_path.resolve() / "db")
    media_factory.assert_called_once_with(media_dir=data_path.resolve() / "media")
    database.initialize.assert_called_once_with()
    assert initialized_database is database
    assert initialized_media is media


def test_init_agent_runtime_registers_shared_registry_and_versioned_loader(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(entry, "DATA_PATH", str(tmp_path / "vps-data"))
    container = DependencyContainer()
    resource_service = MagicMock(spec=ResourceLifecycleService)
    llm_manager = MagicMock(spec=LLMManager)
    mcp_manager = MagicMock(spec=MCPServerManager)
    memory_manager = MagicMock(spec=MemoryManager)
    container.register(ResourceLifecycleService, resource_service)
    container.register(LLMManager, llm_manager)
    container.register(MCPServerManager, mcp_manager)
    container.register(MemoryManager, memory_manager)

    runtime = entry.init_agent_runtime(container)

    assert isinstance(container.resolve(AgentRegistry), AgentRegistry)
    assert isinstance(container.resolve(SessionStore), SessionStore)
    assert container.resolve(AgentRuntimeExecutor) is runtime
    assert runtime.agent_registry is container.resolve(AgentRegistry)
    assert runtime.llm_manager is llm_manager
    assert runtime.mcp_manager is mcp_manager
    assert runtime.memory_manager is memory_manager
    assert runtime.session_store is container.resolve(SessionStore)
    assert runtime.session_store.root == tmp_path / "vps-data" / "sessions"
    assert {
        "audit.agent_start",
        "audit.user_prompt",
        "audit.pre_tool",
        "audit.permission_request",
        "audit.post_tool",
        "audit.pre_compact",
        "audit.post_compact",
        "audit.stop",
    }.issubset(runtime.hook_runtime.handlers)
    assert runtime._load_resource("skill-a", "1.0.0") is resource_service.read_entry.return_value
    resource_service.read_entry.assert_called_once_with("skill-a", "1.0.0")


def test_init_agent_runtime_applies_configured_compaction_and_debug_hook_policy(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(entry, "DATA_PATH", str(tmp_path / "vps-data"))
    container = DependencyContainer()
    resource_service = MagicMock(spec=ResourceLifecycleService)
    container.register(ResourceLifecycleService, resource_service)
    container.register(LLMManager, MagicMock(spec=LLMManager))
    container.register(MCPServerManager, MagicMock(spec=MCPServerManager))
    container.register(
        GlobalConfig,
        GlobalConfig(
            agent_runtime=AgentRuntimeConfig(
                context_char_threshold=1200,
                debug_hooks_enabled=True,
            )
        ),
    )

    runtime = entry.init_agent_runtime(container)

    assert runtime.context_char_threshold == 1200
    assert "agent.debug" in runtime.hook_runtime.handlers


def test_init_agent_runtime_can_disable_debug_hook_registration(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(entry, "DATA_PATH", str(tmp_path / "vps-data"))
    container = DependencyContainer()
    container.register(ResourceLifecycleService, MagicMock(spec=ResourceLifecycleService))
    container.register(LLMManager, MagicMock(spec=LLMManager))
    container.register(MCPServerManager, MagicMock(spec=MCPServerManager))
    container.register(
        GlobalConfig,
        GlobalConfig(agent_runtime=AgentRuntimeConfig(debug_hooks_enabled=False)),
    )

    runtime = entry.init_agent_runtime(container)

    assert "agent.debug" not in runtime.hook_runtime.handlers


def test_executor_uses_resource_service_when_no_legacy_loader_is_supplied(tmp_path: Path):
    lifecycle = ResourceLifecycleService(tmp_path / "vps-data")
    catalog = ResourceCatalogService(lifecycle)
    catalog.ensure_builtins()
    lifecycle.enable("prompt.office-research", confirmed=True)
    current_version = lifecycle.get_resource("prompt.office-research")["current_version"]
    binding = lifecycle.resolve_binding(
        "prompt.office-research", "prompt", version=current_version, enabled=True
    )

    agent = AgentRegistry()
    agent.register(
        AgentDefinition(
            agent_id="office",
            model_priority=("model",),
            prompt_bindings=(binding,),
        )
    )
    agent.set_default("office")
    runtime = AgentRuntimeExecutor(
        agent_registry=agent,
        llm_manager=MagicMock(spec=LLMManager),
        mcp_manager=MagicMock(spec=MCPServerManager),
        resource_service=lifecycle,
    )

    messages = runtime._build_messages(
        agent.get("office"),
        IMMessage(
            ChatSender.from_c2c_chat("user", "User"),
            [TextMessage("hello")],
        ),
        None,
    )

    assert "我是上班族" in messages[0].content[0].text


def test_agent_runtime_audit_sink_does_not_log_prompt_or_credentials(monkeypatch):
    records = []
    monkeypatch.setattr(entry.logger, "info", lambda _message, value: records.append(value))

    entry._agent_runtime_audit_sink(
        {
            "operation": "run_event",
            "prompt": "private prompt text",
            "api_token": "credential-value",
            "nested": {"authorization": "Bearer credential-value"},
        }
    )

    serialized = repr(records)
    assert "private prompt text" not in serialized
    assert "credential-value" not in serialized
    assert "[redacted]" in serialized


def test_production_agent_audit_sink_persists_redacted_hook_events_for_unified_query(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(entry, "DATA_PATH", str(tmp_path / "vps-data"))
    lifecycle = ResourceLifecycleService(tmp_path / "vps-data")
    container = DependencyContainer()
    container.register(ResourceLifecycleService, lifecycle)
    container.register(LLMManager, MagicMock(spec=LLMManager))
    container.register(MCPServerManager, MagicMock(spec=MCPServerManager))

    runtime = entry.init_agent_runtime(container)
    runtime.audit_sink(
        {
            "component": "agent_hook",
            "operation": "run_event",
            "event": "PreToolUse",
            "resource_id": "hook.ai-debug",
            "resource_version": "1.1.0",
            "resource_sha256": "a" * 64,
            "outcome": "success",
            "correlation_id": "audit-correlation-123",
            "prompt": "private prompt must not be persisted",
            "api_token": "credential-value",
            "session": {"channel_type": "webui"},
        }
    )

    reloaded = ResourceLifecycleService(tmp_path / "vps-data")
    page = reloaded.list_audit(
        correlation_id="audit-correlation-123",
        component="agent_hook",
        event="PreToolUse",
    )

    assert page["total"] == 1
    record = page["items"][0]
    assert record["resource_id"] == "hook.ai-debug"
    assert record["resource_version"] == "1.1.0"
    assert record["resource_sha256"] == "a" * 64
    assert record["correlation_id"] == "audit-correlation-123"
    serialized = json.dumps(page, ensure_ascii=True)
    assert "private prompt must not be persisted" not in serialized
    assert "credential-value" not in serialized


@pytest.mark.parametrize("data_path", ["/app/data", "C:/kirara-data"])
def test_data_path_is_not_replaced_by_legacy_relative_data_directory(
    data_path: str, monkeypatch
):
    monkeypatch.setattr(entry, "DATA_PATH", data_path)

    assert entry._config_path() == Path(data_path) / "config.yaml"
