from pathlib import Path
from shutil import copytree
import socket

from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.workflow.core.block import BlockRegistry
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry
from kirara_ai.workflow.implementations.blocks import register_system_blocks
from kirara_ai.workflow.implementations.workflows.system_workflows import (
    register_system_workflows,
)
from kirara_ai.workflow.persistence import FileTransaction


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "a4-data"


def test_a4_data_loads_without_overwriting_user_state(tmp_path, monkeypatch):
    def reject_network(*args, **kwargs):
        raise AssertionError(f"A4 registry-only proof attempted network access: {args!r}")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    monkeypatch.setattr(socket.socket, "connect", reject_network)

    data_dir = tmp_path / "data"
    copytree(FIXTURE_DIR, data_dir)
    workflows_dir = data_dir / "workflows"
    rules_dir = data_dir / "dispatch_rules"

    config = ConfigLoader.load_config(str(data_dir / "config.yaml"), GlobalConfig)
    assert [model.id for model in config.llms.api_backends[0].models] == [
        "manual-primary",
        "manual-fallback",
    ]
    assert config.plugins.enable == ["fixture.plugin"]
    assert config.mcp.servers[0].url == "http://127.0.0.1:9/mcp"
    assert config.mcp.servers[0].enable is False

    container = DependencyContainer()
    block_registry = BlockRegistry()
    register_system_blocks(block_registry)
    container.register(BlockRegistry, block_registry)

    workflow_registry = WorkflowRegistry(container)
    container.register(WorkflowRegistry, workflow_registry)
    FileTransaction.recover_directory(workflows_dir)
    workflow_registry.load_workflows(str(workflows_dir))
    register_system_workflows(workflow_registry)

    edited = workflow_registry.get("chat:plain_text")
    assert edited is not None
    assert edited.name == "A4 edited plain text workflow"
    manual_node = edited.nodes_by_name["manual_chat"]
    assert manual_node.position == {"x": 144, "y": 288}
    assert manual_node.spec.kwargs["model_name"] == "manual-primary"
    assert manual_node.spec.kwargs["fallback_model_1"] == "manual-fallback"
    assert manual_node.spec.kwargs["fallback_model_2"] == "manual-third-choice"

    custom = workflow_registry.get("custom:annotated")
    assert custom is not None
    assert custom.nodes_by_name["source_text"].position == {"x": 32, "y": 64}
    assert custom.nodes_by_name["result_text"].position == {"x": 480, "y": 320}

    deleted = workflow_registry.get("chat:time_aware")
    assert deleted is None
    assert not (workflows_dir / "chat" / "time_aware.yaml").exists()
    assert "chat:time_aware" in workflow_registry.deleted_preset_workflow_ids

    dispatch_registry = DispatchRuleRegistry(container)
    container.register(DispatchRuleRegistry, dispatch_registry)
    FileTransaction.recover_directory(rules_dir)
    dispatch_registry.load_rules(str(rules_dir))

    legacy = dispatch_registry.get_rule("legacy_fixture_chat")
    assert legacy is not None
    assert legacy.workflow_id == "custom:annotated"
    assert legacy.rule_groups[0].rules[0].type == "prefix"
    assert legacy.rule_groups[0].rules[0].config == {"prefix": "/legacy"}

    message = IMMessage(
        ChatSender.from_c2c_chat("fixture-user", "Fixture User"),
        [TextMessage("/legacy hello")],
    )
    assert legacy.match(message, workflow_registry, container)
    assert legacy.get_workflow(container) is not None

    workflow_registry.persist_builder(
        "chat", "plain_text", edited
    )
    workflow_registry.persist_builder(
        "custom", "annotated", custom
    )
    dispatch_registry.save_rules(str(rules_dir))

    reloaded_workflow_registry = WorkflowRegistry(container)
    FileTransaction.recover_directory(workflows_dir)
    reloaded_workflow_registry.load_workflows(str(workflows_dir))
    register_system_workflows(reloaded_workflow_registry)
    reloaded_edited = reloaded_workflow_registry.get("chat:plain_text")
    assert reloaded_edited is not None
    assert reloaded_edited.name == "A4 edited plain text workflow"
    assert reloaded_edited.nodes_by_name["manual_chat"].position == {
        "x": 144,
        "y": 288,
    }
    assert reloaded_edited.nodes_by_name["manual_chat"].spec.kwargs == manual_node.spec.kwargs
    assert reloaded_workflow_registry.get("chat:time_aware") is None
    assert "chat:time_aware" in reloaded_workflow_registry.deleted_preset_workflow_ids

    reloaded_container = DependencyContainer()
    reloaded_container.register(WorkflowRegistry, reloaded_workflow_registry)
    reloaded_dispatch_registry = DispatchRuleRegistry(reloaded_container)
    FileTransaction.recover_directory(rules_dir)
    reloaded_dispatch_registry.load_rules(str(rules_dir))
    reloaded_legacy = reloaded_dispatch_registry.get_rule("legacy_fixture_chat")
    assert reloaded_legacy is not None
    assert reloaded_legacy.workflow_id == "custom:annotated"
    assert reloaded_legacy.rule_groups[0].rules[0].config == {"prefix": "/legacy"}
