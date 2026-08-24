from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService


def _archive(path: Path, version: str, body: str) -> Path:
    files = {"main.md": body.encode(), "README.md": b"metadata"}
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
        "entry": "main.md",
        "permissions": [],
        "files": records,
        "content_sha256": content_hash,
    }
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def test_backups_have_stable_ids_and_can_be_listed_deleted_and_restored(tmp_path: Path):
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    lifecycle.install_archive(_archive(tmp_path / "v1.zip", "1.0.0", "first"))
    lifecycle.update_archive(_archive(tmp_path / "v2.zip", "2.0.0", "second"))

    backups = lifecycle.list_backups("backup.demo")

    assert backups
    assert backups[0]["backup_id"]
    assert backups[0]["resource_id"] == "backup.demo"
    assert "data" not in json.dumps(backups)
    backup_id = backups[0]["backup_id"]

    lifecycle.restore_backup(backup_id, confirmed=True)
    assert lifecycle.get_resource("backup.demo")["current_version"] == "1.0.0"
    assert lifecycle.read_entry("backup.demo") == "first"

    lifecycle.delete_backup(backup_id)
    assert all(item["backup_id"] != backup_id for item in lifecycle.list_backups())

