from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry


def test_deleted_preset_workflow_is_not_restored_after_restart(tmp_path):
    container = DependencyContainer()
    registry = WorkflowRegistry(container)
    registry.workflows_dir = str(tmp_path)
    registry.register_preset_workflow("chat", "sample", WorkflowBuilder("Sample"))

    registry.delete("chat", "sample")

    restarted_registry = WorkflowRegistry(container)
    restarted_registry.workflows_dir = str(tmp_path)
    restarted_registry._load_preset_tombstones()
    restarted_registry.register_preset_workflow("chat", "sample", WorkflowBuilder("Sample"))

    assert restarted_registry.get("chat:sample") is None


def test_recreating_a_deleted_preset_clears_its_tombstone(tmp_path):
    container = DependencyContainer()
    registry = WorkflowRegistry(container)
    registry.workflows_dir = str(tmp_path)
    registry.register_preset_workflow("chat", "sample", WorkflowBuilder("Sample"))
    registry.delete("chat", "sample")

    registry.register("chat", "sample", WorkflowBuilder("Custom Sample"))

    assert registry.get("chat:sample") is not None
    assert "chat:sample" not in registry.deleted_preset_workflow_ids
