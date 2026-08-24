from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

import kirara_ai.entry as entry
from kirara_ai.agent_runtime import AgentRegistry, AgentRuntimeExecutor
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.mcp_module.manager import MCPServerManager
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService


def test_config_path_is_inside_configured_data_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(entry, "DATA_PATH", str(tmp_path / "vps-data"))

    assert entry._config_path() == tmp_path / "vps-data" / "config.yaml"


def test_init_agent_runtime_registers_shared_registry_and_versioned_loader(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(entry, "DATA_PATH", str(tmp_path / "vps-data"))
    container = DependencyContainer()
    resource_service = MagicMock(spec=ResourceLifecycleService)
    llm_manager = MagicMock(spec=LLMManager)
    mcp_manager = MagicMock(spec=MCPServerManager)
    container.register(ResourceLifecycleService, resource_service)
    container.register(LLMManager, llm_manager)
    container.register(MCPServerManager, mcp_manager)

    runtime = entry.init_agent_runtime(container)

    assert isinstance(container.resolve(AgentRegistry), AgentRegistry)
    assert container.resolve(AgentRuntimeExecutor) is runtime
    assert runtime.agent_registry is container.resolve(AgentRegistry)
    assert runtime.llm_manager is llm_manager
    assert runtime.mcp_manager is mcp_manager
    assert runtime._load_resource("skill-a", "1.0.0") is resource_service.read_entry.return_value
    resource_service.read_entry.assert_called_once_with("skill-a", "1.0.0")


@pytest.mark.parametrize("data_path", ["/app/data", "C:/kirara-data"])
def test_data_path_is_not_replaced_by_legacy_relative_data_directory(
    data_path: str, monkeypatch
):
    monkeypatch.setattr(entry, "DATA_PATH", data_path)

    assert entry._config_path() == Path(data_path) / "config.yaml"
