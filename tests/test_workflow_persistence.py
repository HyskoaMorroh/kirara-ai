from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.block import BlockRegistry
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.builder import WorkflowBuilder
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry
from kirara_ai.workflow.persistence import FileMutation, FileTransaction


def _writer(text: str):
    def write(staged_path: Path) -> None:
        staged_path.write_text(text, encoding="utf-8")

    return write


def _transaction_files(directory: Path) -> list[Path]:
    return sorted(
        path
        for path in directory.rglob("*")
        if path.name.startswith(FileTransaction.JOURNAL_PREFIX)
        or path.name.endswith(".staged")
        or path.name.endswith(".backup")
    )


def _workflow_registry(tmp_path: Path) -> WorkflowRegistry:
    container = DependencyContainer()
    container.register(BlockRegistry, BlockRegistry())
    registry = WorkflowRegistry(container)
    registry.workflows_dir = str(tmp_path)
    return registry


def test_journal_write_failure_removes_prepared_files(tmp_path, monkeypatch):
    target = tmp_path / "chat.yaml"
    target.write_text("old", encoding="utf-8")

    def fail_journal(path, journal):
        raise OSError("journal unavailable")

    monkeypatch.setattr(FileTransaction, "_write_journal", staticmethod(fail_journal))

    with pytest.raises(OSError, match="journal unavailable"):
        FileTransaction(
            tmp_path,
            [FileMutation.replace(target, _writer("new"))],
        ).commit()

    assert target.read_text(encoding="utf-8") == "old"
    assert _transaction_files(tmp_path) == []


def test_stage_writer_failure_leaves_no_transaction_files(tmp_path):
    target = tmp_path / "chat.yaml"
    target.write_text("old", encoding="utf-8")

    def fail_after_write(staged_path: Path) -> None:
        staged_path.write_text("partial", encoding="utf-8")
        raise OSError("stage failed")

    with pytest.raises(OSError, match="stage failed"):
        FileTransaction(
            tmp_path,
            [FileMutation.replace(target, fail_after_write)],
        ).commit()

    assert target.read_text(encoding="utf-8") == "old"
    assert _transaction_files(tmp_path) == []


def test_handled_failure_after_first_publish_restores_both_files(
    tmp_path, monkeypatch
):
    first = tmp_path / "rules.yaml"
    second = tmp_path / ".preset_rule_deletions.json"
    first.write_text("old-rules", encoding="utf-8")
    second.write_text("old-tombstones", encoding="utf-8")
    original_publish = FileTransaction._publish_entry
    published = 0

    def fail_after_first(entry):
        nonlocal published
        original_publish(entry)
        published += 1
        if published == 1:
            raise OSError("publish failed")

    monkeypatch.setattr(
        FileTransaction, "_publish_entry", staticmethod(fail_after_first)
    )

    with pytest.raises(OSError, match="publish failed"):
        FileTransaction(
            tmp_path,
            [
                FileMutation.replace(first, _writer("new-rules")),
                FileMutation.replace(second, _writer("new-tombstones")),
            ],
        ).commit()

    assert first.read_text(encoding="utf-8") == "old-rules"
    assert second.read_text(encoding="utf-8") == "old-tombstones"
    assert _transaction_files(tmp_path) == []


def test_interrupted_publish_is_completed_by_startup_recovery(tmp_path, monkeypatch):
    first = tmp_path / "rules.yaml"
    second = tmp_path / ".preset_rule_deletions.json"
    first.write_text("old-rules", encoding="utf-8")
    second.write_text("old-tombstones", encoding="utf-8")
    original_publish = FileTransaction._publish_entry
    published = 0

    def interrupt_after_first(entry):
        nonlocal published
        original_publish(entry)
        published += 1
        if published == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        FileTransaction, "_publish_entry", staticmethod(interrupt_after_first)
    )

    with pytest.raises(KeyboardInterrupt):
        FileTransaction(
            tmp_path,
            [
                FileMutation.replace(first, _writer("new-rules")),
                FileMutation.replace(second, _writer("new-tombstones")),
            ],
        ).commit()

    monkeypatch.setattr(FileTransaction, "_publish_entry", original_publish)
    FileTransaction.recover_directory(tmp_path)

    assert first.read_text(encoding="utf-8") == "new-rules"
    assert second.read_text(encoding="utf-8") == "new-tombstones"
    assert _transaction_files(tmp_path) == []


def test_rolling_back_journal_restores_previous_file(tmp_path):
    target = tmp_path / "workflow.yaml"
    target.write_text("new", encoding="utf-8")
    backup = tmp_path / ".workflow.yaml.tx.0.backup"
    backup.write_text("old", encoding="utf-8")
    staged = tmp_path / ".workflow.yaml.tx.0.staged"
    journal_path = tmp_path / ".kirara-transaction-tx.json"
    journal_path.write_text(
        json.dumps(
            {
                "version": FileTransaction.JOURNAL_VERSION,
                "transaction_id": "tx",
                "state": "rolling_back",
                "entries": [
                    {
                        "target": str(target.resolve()),
                        "staged": str(staged.resolve()),
                        "backup": str(backup.resolve()),
                        "delete": False,
                        "had_target": True,
                        "state": "published",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    FileTransaction.recover_directory(tmp_path)

    assert target.read_text(encoding="utf-8") == "old"
    assert _transaction_files(tmp_path) == []


def test_transaction_rejects_target_outside_root(tmp_path):
    outside = tmp_path.parent / "outside.yaml"

    with pytest.raises(ValueError, match="outside"):
        FileTransaction(
            tmp_path,
            [FileMutation.replace(outside, _writer("new"))],
        ).commit()

    assert not outside.exists()
    assert _transaction_files(tmp_path) == []


def test_workflow_registry_uses_its_configured_directory(tmp_path):
    registry = _workflow_registry(tmp_path)

    resolved = Path(registry.resolve_workflow_path("chat", "normal"))

    assert resolved == tmp_path / "chat" / "normal.yaml"
    assert resolved.parent.is_dir()


def test_workflow_snapshot_is_an_immutable_stable_view():
    registry = WorkflowRegistry(DependencyContainer())
    first = WorkflowBuilder("First")
    second = WorkflowBuilder("Second")
    registry.register("chat", "first", first)

    snapshot = registry.snapshot_builders()
    registry.unregister("chat", "first")
    registry.register("chat", "second", second)

    assert snapshot == (("chat:first", first),)
    assert registry.snapshot_builders() == (("chat:second", second),)


def test_failed_workflow_delete_keeps_yaml_tombstone_and_registry_consistent(
    tmp_path, monkeypatch
):
    registry = _workflow_registry(tmp_path)
    builder = WorkflowBuilder("Sample")
    registry.register_preset_workflow("chat", "sample", builder)
    workflow_path = Path(registry.resolve_workflow_path("chat", "sample"))
    workflow_path.write_text("name: Sample\nblocks: []\nwires: []\n", encoding="utf-8")
    original_publish = FileTransaction._publish_entry
    published = 0

    def fail_after_first(entry):
        nonlocal published
        original_publish(entry)
        published += 1
        if published == 1:
            raise OSError("publish failed")

    monkeypatch.setattr(
        FileTransaction, "_publish_entry", staticmethod(fail_after_first)
    )

    with pytest.raises(OSError, match="publish failed"):
        registry.delete_persisted("chat", "sample")

    assert workflow_path.is_file()
    assert registry.get("chat:sample") is builder
    assert "chat:sample" not in registry.deleted_preset_workflow_ids
    tombstone_path = tmp_path / ".preset_tombstones.json"
    assert not tombstone_path.exists()


def test_renaming_a_preset_persists_old_tombstone_and_recreating_it_clears_it(
    tmp_path
):
    registry = _workflow_registry(tmp_path)
    original = WorkflowBuilder("Preset")
    registry.register_preset_workflow("chat", "sample", original)
    original_path = Path(registry.resolve_workflow_path("chat", "sample"))
    original.save_to_yaml(str(original_path), registry.container)

    renamed = WorkflowBuilder("Renamed")
    registry.persist_builder(
        "chat",
        "renamed",
        renamed,
        previous_group_id="chat",
        previous_workflow_id="sample",
    )

    assert not original_path.exists()
    assert registry.get("chat:sample") is None
    assert registry.get("chat:renamed") is renamed
    assert "chat:sample" in registry.deleted_preset_workflow_ids

    recreated = WorkflowBuilder("Recreated")
    registry.persist_builder("chat", "sample", recreated)

    assert registry.get("chat:sample") is recreated
    assert "chat:sample" not in registry.deleted_preset_workflow_ids


def test_failed_workflow_serialization_does_not_create_or_register_it(
    tmp_path, monkeypatch
):
    registry = _workflow_registry(tmp_path)
    builder = WorkflowBuilder("Broken")

    def fail_save(file_path, container):
        Path(file_path).write_text("partial", encoding="utf-8")
        raise OSError("serialization failed")

    monkeypatch.setattr(builder, "save_to_yaml", fail_save)

    with pytest.raises(OSError, match="serialization failed"):
        registry.persist_builder("chat", "broken", builder, create_only=True)

    assert registry.get("chat:broken") is None
    assert not Path(registry.resolve_workflow_path("chat", "broken")).exists()
    assert _transaction_files(tmp_path) == []


def test_interrupted_preset_rename_recovers_new_yaml_and_old_tombstone(
    tmp_path, monkeypatch
):
    registry = _workflow_registry(tmp_path)
    original = WorkflowBuilder("Preset")
    registry.register_preset_workflow("chat", "sample", original)
    original_path = Path(registry.resolve_workflow_path("chat", "sample"))
    original.save_to_yaml(str(original_path), registry.container)
    renamed = WorkflowBuilder("Renamed")
    renamed_path = Path(registry.resolve_workflow_path("chat", "renamed"))
    original_publish = FileTransaction._publish_entry
    published = 0

    def interrupt_after_first(entry):
        nonlocal published
        original_publish(entry)
        published += 1
        if published == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr(
        FileTransaction, "_publish_entry", staticmethod(interrupt_after_first)
    )

    with pytest.raises(KeyboardInterrupt):
        registry.persist_builder(
            "chat",
            "renamed",
            renamed,
            previous_group_id="chat",
            previous_workflow_id="sample",
        )

    monkeypatch.setattr(FileTransaction, "_publish_entry", original_publish)
    FileTransaction.recover_directory(tmp_path)

    assert renamed_path.is_file()
    assert not original_path.exists()
    assert json.loads(
        (tmp_path / ".preset_tombstones.json").read_text(encoding="utf-8")
    ) == ["chat:sample"]
    assert _transaction_files(tmp_path) == []


def test_dispatch_save_rolls_back_rules_and_tombstones_together(
    tmp_path, monkeypatch
):
    workflow_registry = _workflow_registry(tmp_path / "workflows")
    workflow_registry.container.register(WorkflowRegistry, workflow_registry)
    registry = DispatchRuleRegistry(workflow_registry.container)
    rules_dir = tmp_path / "dispatch"
    registry.save_rules(str(rules_dir))
    rules_path = rules_dir / "rules.yaml"
    tombstones_path = rules_dir / ".preset_tombstones.json"
    old_rules = rules_path.read_bytes()
    old_tombstones = tombstones_path.read_bytes()
    registry.deleted_preset_rule_ids.add("sample")
    original_publish = FileTransaction._publish_entry
    published = 0

    def fail_after_first(entry):
        nonlocal published
        original_publish(entry)
        published += 1
        if published == 1:
            raise OSError("second file unavailable")

    monkeypatch.setattr(
        FileTransaction, "_publish_entry", staticmethod(fail_after_first)
    )

    with pytest.raises(OSError, match="second file unavailable"):
        registry.save_rules(str(rules_dir))

    assert rules_path.read_bytes() == old_rules
    assert tombstones_path.read_bytes() == old_tombstones
    assert _transaction_files(rules_dir) == []
