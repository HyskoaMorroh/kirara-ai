from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from kirara_ai.plugin_manager.resource_lifecycle import (
    ResourceLifecycleService,
    ResourceValidationError,
)


def _archive(path: Path, version: str, body: str, *, entry: str = "main.md") -> Path:
    files = {entry: body.encode(), "README.md": b"metadata"}
    records = [
        {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
        for name, content in files.items()
    ]
    content_hash = hashlib.sha256(
        b"".join(
            f"{item['path']}:{item['size']}:{item['sha256']}\n".encode()
            for item in sorted(records, key=lambda item: item["path"])
        )
    ).hexdigest()
    manifest = {
        "resource_id": "backup.demo",
        "type": "prompt",
        "version": version,
        "source": "local-test",
        "entry": entry,
        "permissions": [],
        "files": records,
        "content_sha256": content_hash,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _backup_fixture(tmp_path: Path) -> tuple[ResourceLifecycleService, str, Path]:
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    lifecycle.install_archive(_archive(tmp_path / "v1.zip", "1.0.0", "first"))
    lifecycle.update_archive(_archive(tmp_path / "v2.zip", "2.0.0", "second"))
    backup_id = lifecycle.list_backups("backup.demo")[0]["backup_id"]
    backup_path = lifecycle.resource_path / "backups" / "backup.demo" / backup_id
    return lifecycle, backup_id, backup_path


def test_backups_have_stable_ids_and_can_be_listed_deleted_and_restored(tmp_path: Path):
    lifecycle, backup_id, _ = _backup_fixture(tmp_path)

    backups = lifecycle.list_backups("backup.demo")

    assert backups
    assert backups[0]["backup_id"]
    assert backups[0]["resource_id"] == "backup.demo"
    assert "data" not in json.dumps(backups)
    lifecycle.restore_backup(backup_id, confirmed=True)
    assert lifecycle.get_resource("backup.demo")["current_version"] == "1.0.0"
    assert lifecycle.read_entry("backup.demo") == "first"

    lifecycle.delete_backup(backup_id)
    assert all(item["backup_id"] != backup_id for item in lifecycle.list_backups())


def test_restore_accepts_declared_nested_resource_directories(tmp_path: Path):
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    lifecycle.install_archive(
        _archive(tmp_path / "nested-v1.zip", "1.0.0", "first", entry="prompts/main.md")
    )
    lifecycle.update_archive(
        _archive(tmp_path / "nested-v2.zip", "2.0.0", "second", entry="prompts/main.md")
    )
    backup_id = lifecycle.list_backups("backup.demo")[0]["backup_id"]

    lifecycle.restore_backup(backup_id, confirmed=True)

    assert lifecycle.get_resource("backup.demo")["current_version"] == "1.0.0"
    assert lifecycle.read_entry("backup.demo") == "first"


@pytest.mark.parametrize(
    "tamper",
    [
        lambda backup_path: (backup_path / "main.md").write_text(
            "tampered", encoding="utf-8"
        ),
        lambda backup_path: (backup_path / "undeclared.py").write_text(
            "print('unexpected')", encoding="utf-8"
        ),
        lambda backup_path: (backup_path / "README.md").unlink(),
    ],
    ids=["changed-file", "undeclared-file", "missing-file"],
)
def test_restore_rejects_tampered_backup_file_sets(tmp_path: Path, tamper):
    lifecycle, backup_id, backup_path = _backup_fixture(tmp_path)
    tamper(backup_path)

    with pytest.raises(ResourceValidationError):
        lifecycle.restore_backup(backup_id, confirmed=True)

    assert lifecycle.get_resource("backup.demo")["current_version"] == "2.0.0"


def test_restore_rejects_symbolic_link_members(tmp_path: Path, monkeypatch):
    lifecycle, backup_id, backup_path = _backup_fixture(tmp_path)
    linked_path = backup_path / "main.md"
    original_is_symlink = Path.is_symlink

    def report_target_as_symlink(path: Path) -> bool:
        return path == linked_path or original_is_symlink(path)

    monkeypatch.setattr(Path, "is_symlink", report_target_as_symlink)

    with pytest.raises(ResourceValidationError):
        lifecycle.restore_backup(backup_id, confirmed=True)


@pytest.mark.parametrize("target", ["manifest", "metadata"])
def test_restore_rejects_content_digest_mismatches(tmp_path: Path, target: str):
    lifecycle, backup_id, backup_path = _backup_fixture(tmp_path)
    filename = "manifest.json" if target == "manifest" else "backup.json"
    path = backup_path / filename
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["content_sha256"] = "0" * 64
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ResourceValidationError):
        lifecycle.restore_backup(backup_id, confirmed=True)
