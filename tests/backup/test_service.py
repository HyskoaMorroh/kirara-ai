import json
import zipfile
from pathlib import Path

import pytest

from kirara_ai.backup.service import BackupService, BackupValidationError


def write_project_data(data_path: Path, marker: str, config: str | None = None) -> None:
    (data_path / "dispatch_rules").mkdir(parents=True)
    (data_path / "workflows" / "chat").mkdir(parents=True)
    (data_path / "memory").mkdir()
    (data_path / "media" / "files").mkdir(parents=True)
    (data_path / "db").mkdir()
    (data_path / "plugins" / "custom_plugin").mkdir(parents=True)
    (data_path / "web").mkdir()
    (data_path / "fonts").mkdir()
    (data_path / "venv").mkdir()
    (data_path / "logs").mkdir()
    (data_path / "backups").mkdir()

    # 固定 newline="\n"，避免 Windows 把 \n 翻译成 \r\n 导致夹具字节数与 Linux 不一致
    (data_path / "config.yaml").write_text(
        config or f"web:\n  host: 127.0.0.1\n  port: 8080\nsystem:\n  timezone: Asia/Shanghai\n# {marker}\n",
        encoding="utf-8",
        newline="\n",
    )
    (data_path / "dispatch_rules" / "rules.yaml").write_text(
        f"- rule_id: {marker}\n  workflow_id: chat:normal\n", encoding="utf-8", newline="\n"
    )
    (data_path / "workflows" / "chat" / "normal.yaml").write_text(
        f"name: {marker}\nblocks: []\n", encoding="utf-8", newline="\n"
    )
    (data_path / "memory" / "state.json").write_text(
        json.dumps({"marker": marker}), encoding="utf-8", newline="\n"
    )
    (data_path / "media" / "files" / "example.txt").write_text(marker, encoding="utf-8", newline="\n")
    (data_path / "db" / "kirara.db").write_text(marker, encoding="utf-8", newline="\n")
    (data_path / "plugins" / "custom_plugin" / "plugin.py").write_text(
        "PLUGIN = True\n", encoding="utf-8", newline="\n"
    )
    (data_path / "web" / "password.hash").write_text(marker, encoding="utf-8", newline="\n")
    (data_path / "fonts" / "custom.ttf").write_text(marker, encoding="utf-8", newline="\n")
    (data_path / "auto_detect_state.json").write_text("{}", encoding="utf-8", newline="\n")
    (data_path / "venv" / "ignored.txt").write_text("ignore", encoding="utf-8", newline="\n")
    (data_path / "logs" / "ignored.log").write_text("ignore", encoding="utf-8", newline="\n")
    (data_path / "backups" / "old.kirara-backup.zip").write_text("ignore", encoding="utf-8", newline="\n")
    (data_path / "cache.pyc").write_bytes(b"ignore")


def test_create_backup_contains_allowed_data_and_manifest(tmp_path: Path):
    data_path = tmp_path / "data"
    write_project_data(data_path, "source")
    service = BackupService(data_path)

    archive_path = service.create_backup()
    manifest = service.inspect_backup(archive_path)

    assert archive_path.parent == data_path / "backups"
    assert manifest.components == {
        "auto_detect_state.json",
        "config.yaml",
        "db",
        "dispatch_rules",
        "fonts",
        "media",
        "memory",
        "plugins",
        "web",
        "workflows",
    }
    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "manifest.json" in names
    assert "workflows/chat/normal.yaml" in names
    assert "plugins/custom_plugin/plugin.py" in names
    assert "web/password.hash" in names
    assert not any(name.startswith("venv/") for name in names)
    assert not any(name.startswith("logs/") for name in names)
    assert not any(name.startswith("backups/") for name in names)
    assert not any(name.endswith(".pyc") for name in names)


def test_inspect_backup_rejects_path_traversal(tmp_path: Path):
    archive_path = tmp_path / "unsafe.kirara-backup.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("../outside.txt", "unsafe")

    with pytest.raises(BackupValidationError, match="unsafe archive path"):
        BackupService(tmp_path / "data").inspect_backup(archive_path)


def test_inspect_backup_rejects_modified_file(tmp_path: Path):
    data_path = tmp_path / "data"
    write_project_data(data_path, "source")
    service = BackupService(data_path)
    archive_path = service.create_backup()
    modified_archive_path = tmp_path / "modified.kirara-backup.zip"

    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(modified_archive_path, "w") as modified:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "workflows/chat/normal.yaml":
                # 从归档原始字节派生篡改内容，只翻转一个字节且保持长度不变；
                # 这样先行的大小校验会通过，断言才真正命中校验和校验分支
                original = content
                content = original[:1] + bytes([original[1] ^ 0x01]) + original[2:]
                assert len(content) == len(original)
                assert content != original
            modified.writestr(item, content)

    with pytest.raises(BackupValidationError, match="checksum mismatch"):
        service.inspect_backup(modified_archive_path)


def test_inspect_backup_rejects_resized_file(tmp_path: Path):
    data_path = tmp_path / "data"
    write_project_data(data_path, "source")
    service = BackupService(data_path)
    archive_path = service.create_backup()
    resized_archive_path = tmp_path / "resized.kirara-backup.zip"

    with zipfile.ZipFile(archive_path) as source, zipfile.ZipFile(resized_archive_path, "w") as resized:
        for item in source.infolist():
            content = source.read(item.filename)
            if item.filename == "workflows/chat/normal.yaml":
                # 追加字节改变长度，用于单独覆盖大小校验分支
                content = content + b"extra: true\n"
            resized.writestr(item, content)

    with pytest.raises(BackupValidationError, match="file size mismatch"):
        service.inspect_backup(resized_archive_path)


def test_restore_replaces_components_and_creates_rollback(tmp_path: Path):
    source_data_path = tmp_path / "source-data"
    target_data_path = tmp_path / "target-data"
    write_project_data(source_data_path, "source")
    write_project_data(target_data_path, "target")
    archive_path = BackupService(source_data_path).create_backup()

    result = BackupService(target_data_path).restore_backup(archive_path)

    assert result.rollback_path.parent == target_data_path / "backups"
    assert result.rollback_path.name.startswith("kirara-rollback-")
    assert result.rollback_path.exists()
    assert BackupService(target_data_path).list_rollbacks() == [result.rollback_path]
    assert (target_data_path / "workflows" / "chat" / "normal.yaml").read_text(encoding="utf-8") == "name: source\nblocks: []\n"
    assert (target_data_path / "config.yaml").read_text(encoding="utf-8").endswith("# source\n")
    assert "workflows" in result.restored_components
    assert "config.yaml" in result.restored_components


def test_restore_rejects_invalid_configuration_without_changing_target(tmp_path: Path):
    source_data_path = tmp_path / "source-data"
    target_data_path = tmp_path / "target-data"
    write_project_data(source_data_path, "source", config="web:\n  port: invalid\n")
    write_project_data(target_data_path, "target")
    archive_path = BackupService(source_data_path).create_backup()

    with pytest.raises(BackupValidationError, match="configuration validation failed"):
        BackupService(target_data_path).restore_backup(archive_path)

    assert (target_data_path / "config.yaml").read_text(encoding="utf-8").endswith("# target\n")


def test_restore_rolls_back_when_component_replacement_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    source_data_path = tmp_path / "source-data"
    target_data_path = tmp_path / "target-data"
    write_project_data(source_data_path, "source")
    write_project_data(target_data_path, "target")
    archive_path = BackupService(source_data_path).create_backup()
    service = BackupService(target_data_path)
    replace_component = service._replace_component

    def fail_when_replacing_workflows(*args, **kwargs):
        if args[1].name == "workflows":
            raise OSError("simulated replacement failure")
        return replace_component(*args, **kwargs)

    monkeypatch.setattr(service, "_replace_component", fail_when_replacing_workflows)

    with pytest.raises(OSError, match="simulated replacement failure"):
        service.restore_backup(archive_path)

    assert (target_data_path / "config.yaml").read_text(encoding="utf-8").endswith("# target\n")
    assert (target_data_path / "workflows" / "chat" / "normal.yaml").read_text(encoding="utf-8") == "name: target\nblocks: []\n"
