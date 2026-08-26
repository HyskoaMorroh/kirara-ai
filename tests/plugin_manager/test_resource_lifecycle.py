import asyncio
import hashlib
import json
import stat
import threading
import zipfile
from pathlib import Path

import pytest

from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.resource_lifecycle import (
    ResourceLifecycleService,
    ResourceStateError,
    ResourceValidationError,
)
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.workflow.base import Workflow


def _manifest(
    resource_id: str = "demo.skill",
    version: str = "1.0.0",
    *,
    body: str = "prompt body that must not enter the audit log",
    permissions: list[str] | None = None,
    entry: str = "main.py",
    resource_type: str = "skill",
) -> tuple[dict, dict[str, bytes]]:
    files = {
        entry: body.encode("utf-8"),
        "README.txt": b"metadata only",
    }
    file_records = [
        {
            "path": path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for path, content in files.items()
    ]
    content_hash = hashlib.sha256(
        b"".join(
            f"{item['path']}:{item['size']}:{item['sha256']}\n".encode("ascii")
            for item in sorted(file_records, key=lambda item: item["path"])
        )
    ).hexdigest()
    return (
        {
            "resource_id": resource_id,
            "type": resource_type,
            "version": version,
            "source": "local-test-source",
            "entry": entry,
            "permissions": permissions or ["workflow.read"],
            "files": file_records,
            "content_sha256": content_hash,
        },
        files,
    )


def _write_archive(
    path: Path,
    manifest: dict,
    files: dict[str, bytes],
    *,
    extra_members: dict[str, bytes] | None = None,
    symlink_member: str | None = None,
) -> Path:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for member, content in files.items():
            archive.writestr(member, content)
        for member, content in (extra_members or {}).items():
            archive.writestr(member, content)
        if symlink_member is not None:
            info = zipfile.ZipInfo(symlink_member)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, b"outside-target")
    return path


def _service(tmp_path: Path, registry=None) -> ResourceLifecycleService:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    container.register(BlockRegistry, BlockRegistry())
    return ResourceLifecycleService(
        tmp_path / "data",
        workflow_registry=registry,
        container=container,
    )


def test_rejects_absolute_parent_and_windows_traversal_members(tmp_path: Path):
    service = _service(tmp_path)
    manifest, files = _manifest()

    for unsafe_name in ("/absolute.txt", "../escape.txt", r"..\escape.txt"):
        archive = _write_archive(
            tmp_path / f"{len(unsafe_name)}.zip",
            manifest,
            files,
            extra_members={unsafe_name: b"bad"},
        )
        with pytest.raises(ResourceValidationError):
            service.install_archive(archive)


def test_rejects_zip_symbolic_links(tmp_path: Path):
    service = _service(tmp_path)
    manifest, files = _manifest()
    archive = _write_archive(
        tmp_path / "symlink.zip", manifest, files, symlink_member="link"
    )

    with pytest.raises(ResourceValidationError):
        service.install_archive(archive)


@pytest.mark.parametrize(
    "manifest_patch",
    [
        {"entry": None},
        {"permissions": None},
        {"files": None},
        {"content_sha256": None},
    ],
)
def test_rejects_manifest_missing_required_fields(tmp_path: Path, manifest_patch: dict):
    service = _service(tmp_path)
    manifest, files = _manifest()
    manifest.update(manifest_patch)
    archive = _write_archive(tmp_path / "missing.zip", manifest, files)

    with pytest.raises(ResourceValidationError):
        service.install_archive(archive)


def test_rejects_unknown_permission_and_content_hash_mismatch(tmp_path: Path):
    service = _service(tmp_path)
    manifest, files = _manifest(permissions=["filesystem.write"])
    archive = _write_archive(tmp_path / "permission.zip", manifest, files)

    with pytest.raises(ResourceValidationError):
        service.install_archive(archive)

    manifest, files = _manifest()
    manifest["content_sha256"] = "0" * 64
    archive = _write_archive(tmp_path / "hash.zip", manifest, files)
    with pytest.raises(ResourceValidationError):
        service.install_archive(archive)


def test_rejects_duplicate_id_and_version_downgrade(tmp_path: Path):
    service = _service(tmp_path)
    manifest, files = _manifest(version="2.0.0")
    service.install_archive(_write_archive(tmp_path / "first.zip", manifest, files))

    with pytest.raises(ResourceValidationError):
        service.install_archive(_write_archive(tmp_path / "duplicate.zip", manifest, files))

    older, older_files = _manifest(version="1.9.0")
    with pytest.raises(ResourceValidationError):
        service.update_archive(
            _write_archive(tmp_path / "older.zip", older, older_files)
        )


def test_new_resources_are_disabled_and_enable_requires_explicit_confirmation(tmp_path: Path):
    service = _service(tmp_path)
    manifest, files = _manifest()
    service.install_archive(_write_archive(tmp_path / "resource.zip", manifest, files))

    assert service.get_resource("demo.skill")["enabled"] is False
    with pytest.raises(ResourceStateError):
        service.enable("demo.skill")
    service.enable("demo.skill", confirmed=True)
    assert service.get_resource("demo.skill")["enabled"] is True


def test_permission_changes_require_reconfirmation(tmp_path: Path):
    service = _service(tmp_path)
    manifest, files = _manifest()
    service.install_archive(_write_archive(tmp_path / "v1.zip", manifest, files))
    service.enable("demo.skill", confirmed=True)

    changed, changed_files = _manifest(version="2.0.0", permissions=["workflow.read", "workflow.write"])
    service.update_archive(_write_archive(tmp_path / "v2.zip", changed, changed_files))
    resource = service.get_resource("demo.skill")
    assert resource["enabled"] is False
    with pytest.raises(ResourceStateError):
        service.enable("demo.skill")
    service.enable("demo.skill", confirmed=True)
    assert service.get_resource("demo.skill")["enabled"] is True


def test_failed_update_keeps_previous_version_available(tmp_path: Path, monkeypatch):
    service = _service(tmp_path)
    first, first_files = _manifest(body="old body")
    service.install_archive(_write_archive(tmp_path / "old.zip", first, first_files))

    newer, newer_files = _manifest(version="2.0.0", body="new body")
    archive = _write_archive(tmp_path / "new.zip", newer, newer_files)
    original_writer = service._write_registry

    def fail_write(*args, **kwargs):
        raise OSError("simulated registry failure")

    monkeypatch.setattr(service, "_write_registry", fail_write)
    with pytest.raises(OSError):
        service.update_archive(archive)
    monkeypatch.setattr(service, "_write_registry", original_writer)

    resource = service.get_resource("demo.skill")
    assert resource["current_version"] == "1.0.0"
    assert (service.installed_path / "demo.skill" / "1.0.0" / "main.py").read_text() == "old body"


def test_read_entry_can_pin_a_registered_version_after_resource_update(tmp_path: Path):
    service = _service(tmp_path)
    first, first_files = _manifest(body="old body")
    service.install_archive(_write_archive(tmp_path / "read-v1.zip", first, first_files))
    service.enable("demo.skill", confirmed=True)

    second, second_files = _manifest(version="2.0.0", body="new body")
    service.update_archive(_write_archive(tmp_path / "read-v2.zip", second, second_files))

    assert service.read_entry("demo.skill") == "new body"
    assert service.read_entry("demo.skill", version="1.0.0") == "old body"


def test_registry_and_enabled_entry_survive_service_recreation(tmp_path: Path):
    service = _service(tmp_path)
    manifest, files = _manifest(body="persisted skill instructions")
    service.install_archive(
        _write_archive(tmp_path / "persisted.zip", manifest, files)
    )
    service.enable("demo.skill", confirmed=True)

    restarted_service = _service(tmp_path)

    resource = restarted_service.get_resource("demo.skill")
    assert resource["enabled"] is True
    assert resource["current_version"] == "1.0.0"
    assert restarted_service.read_entry("demo.skill", version="1.0.0") == (
        "persisted skill instructions"
    )


def test_storage_status_describes_server_managed_relative_paths(tmp_path: Path):
    service = _service(tmp_path)

    status = service.get_storage_status()

    assert status == {
        "mode": "server_managed",
        "data_root": ".",
        "resource_root": "resources",
        "install_root": "resources/installed",
        "backup_root": "resources/backups",
        "writable": True,
        "versioned": True,
    }
    assert str(service.data_path) not in json.dumps(status)


def test_audit_excludes_body_payload_and_credentials(tmp_path: Path):
    service = _service(tmp_path)
    secret_body = "PROMPT_BODY credential-token cookie-value"
    manifest, files = _manifest(body=secret_body)
    service.install_archive(_write_archive(tmp_path / "audit.zip", manifest, files))
    service.enable("demo.skill", confirmed=True)

    audit_text = service.audit_path.read_text(encoding="utf-8")
    assert secret_body not in audit_text
    assert "credential-token" not in audit_text
    assert "cookie-value" not in audit_text
    assert "content_sha256" in audit_text
    assert "operation" in audit_text


def test_runtime_audit_normalizes_legacy_hook_component_and_result_field(tmp_path: Path):
    service = _service(tmp_path)
    service.audit_path.write_text(
        json.dumps(
            {
                "component": "agent_hook_runtime",
                "operation": "run_event",
                "event": "PreToolUse",
                "result": "success",
                "correlation_id": "legacy-correlation",
                "timestamp": "2026-08-27T00:00:00+00:00",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    page = service.list_audit(
        component="agent_hook",
        outcome="success",
        correlation_id="legacy-correlation",
    )

    assert page["total"] == 1
    assert page["items"][0]["component"] == "agent_hook"
    assert page["items"][0]["outcome"] == "success"


def test_runtime_audit_concurrent_writes_remain_valid_json_lines(tmp_path: Path):
    service = _service(tmp_path)
    thread_count = 8
    records_per_thread = 20

    def write_records(worker: int) -> None:
        for index in range(records_per_thread):
            service.append_runtime_audit(
                {
                    "component": "agent_runtime",
                    "operation": "turn",
                    "status": "completed",
                    "correlation_id": f"worker-{worker}-{index}",
                }
            )

    threads = [
        threading.Thread(target=write_records, args=(worker,))
        for worker in range(thread_count)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = service.audit_path.read_text(encoding="utf-8").splitlines()
    records = [json.loads(line) for line in lines]
    assert len(records) == thread_count * records_per_thread
    assert len({record["correlation_id"] for record in records}) == len(records)


def test_runtime_audit_write_failure_does_not_escape(tmp_path: Path):
    service = _service(tmp_path)
    unwritable_target = tmp_path / "audit-as-directory"
    unwritable_target.mkdir()
    service.audit_path = unwritable_target

    service.append_runtime_audit(
        {
            "component": "agent_runtime",
            "operation": "turn",
            "status": "completed",
        }
    )


class _WorkflowRegistry:
    def __init__(self, workflow):
        self.workflow = workflow

    def get_workflow(self, workflow_id, container):
        return self.workflow if workflow_id == "chat:normal" else None


def test_install_can_bind_existing_workflow_but_not_missing_workflow(tmp_path: Path):
    workflow = Workflow("normal", [], [])
    service = _service(tmp_path, _WorkflowRegistry(workflow))
    manifest, files = _manifest()
    service.install_archive(_write_archive(tmp_path / "bind.zip", manifest, files))

    with pytest.raises(ResourceStateError):
        service.bind_workflow("demo.skill", "chat:missing")
    service.bind_workflow("demo.skill", "chat:normal")
    assert service.get_resource("demo.skill")["workflow_id"] == "chat:normal"


@pytest.mark.asyncio
async def test_disabled_resource_is_blocked_and_enabled_resource_uses_workflow_executor(tmp_path: Path):
    workflow = Workflow("normal", [], [])
    service = _service(tmp_path, _WorkflowRegistry(workflow))
    manifest, files = _manifest()
    service.install_archive(_write_archive(tmp_path / "execute.zip", manifest, files))
    service.bind_workflow("demo.skill", "chat:normal")

    with pytest.raises(ResourceStateError):
        await service.execute("demo.skill")
    service.enable("demo.skill", confirmed=True)
    assert await service.execute("demo.skill") == {}


def test_restore_only_accepts_registered_versions_for_bound_resource(tmp_path: Path):
    workflow = Workflow("normal", [], [])
    service = _service(tmp_path, _WorkflowRegistry(workflow))
    first, first_files = _manifest(body="first")
    service.install_archive(_write_archive(tmp_path / "restore-v1.zip", first, first_files))
    second, second_files = _manifest(version="2.0.0", body="second")
    service.update_archive(_write_archive(tmp_path / "restore-v2.zip", second, second_files))

    with pytest.raises(ResourceStateError):
        service.restore_version("demo.skill", "1.0.0", confirmed=True)
    service.bind_workflow("demo.skill", "chat:normal")
    with pytest.raises(ResourceValidationError):
        service.restore_version("demo.skill", "9.0.0", confirmed=True)
    service.restore_version("demo.skill", "1.0.0", confirmed=True)
    assert service.get_resource("demo.skill")["current_version"] == "1.0.0"
    assert service.get_resource("demo.skill")["enabled"] is False


def test_prompt_and_session_use_the_same_registered_resource_boundary(tmp_path: Path):
    service = _service(tmp_path)
    for resource_type in ("prompt", "session", "mcp"):
        manifest, files = _manifest(
            resource_id=f"demo.{resource_type}",
            resource_type=resource_type,
            entry="README.txt",
        )
        service.install_archive(
            _write_archive(tmp_path / f"{resource_type}.zip", manifest, files)
        )

    assert {item["type"] for item in service.list_resources()} == {"prompt", "session", "mcp"}
