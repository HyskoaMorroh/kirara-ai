"""「导入已有」必须能发现已经在盘上的资源包，而不只能再上传一次。

需求 10 把五项 Skills 管理能力并列：检查更新、从备份中恢复、从ZIP安装、
**导入已有**、发现技能。当前「导入已有」（``POST /resources/imports``）只接受
浏览器上传的 ZIP，随后走与「从ZIP安装」完全相同的 ``install_archive``——
两者在机制上是同一件事，只是审计口不同。

这让「导入已有」这个名字落不到实处。它真正要覆盖的场景是：

- 运维用 ``scp`` 把一批技能包放进了服务器的 ``resources/imports``；
- 从备份目录里翻出旧包，想装回来但不想经过浏览器上传；
- 包有几十 MB，走浏览器上传既慢又容易断。

以上三种情况下用户手里没有「可上传的文件」，他要的是「服务器上已经有的那些，
列出来让我选」。

四条边界必须成立：

- **只扫 ``resources/imports`` 之内。** 让请求方指定扫描目录等于给出一个任意
  文件系统读取接口。
- **只列，不装。** 发现是只读的；安装仍走原有的确认与校验链路。
- **已装过的包要标出来，而不是从列表里消失。** 消失会让人以为文件没放对，
  于是反复重传同一个包。
- **坏包不让整份列表打不开。** 一个损坏的 ZIP 只影响它自己那一行。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugin_manager.resource_lifecycle import (
    ResourceLifecycleService,
    ResourceValidationError,
)
from kirara_ai.workflow.core.block.registry import BlockRegistry


def _manifest(
    resource_id: str = "demo.skill",
    version: str = "1.0.0",
    *,
    resource_type: str = "skill",
) -> tuple[dict, dict[str, bytes]]:
    files = {"main.py": b"body", "README.txt": b"metadata only"}
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
            "entry": "main.py",
            "permissions": ["workflow.read"],
            "files": file_records,
            "content_sha256": content_hash,
        },
        files,
    )


def _write_archive(path: Path, manifest: dict, files: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for member, content in files.items():
            archive.writestr(member, content)
    return path


def _service(tmp_path: Path) -> ResourceLifecycleService:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    container.register(BlockRegistry, BlockRegistry())
    return ResourceLifecycleService(
        tmp_path / "data",
        workflow_registry=None,
        container=container,
    )


def _stage(service: ResourceLifecycleService, name: str, **kwargs) -> Path:
    manifest, files = _manifest(**kwargs)
    return _write_archive(service.imports_path / name, manifest, files)


def test_discovers_an_archive_already_on_disk(tmp_path: Path):
    service = _service(tmp_path)
    _stage(service, "demo.zip")

    found = service.discover_importable_archives()

    assert len(found) == 1
    entry = found[0]
    assert entry["resource_id"] == "demo.skill"
    assert entry["version"] == "1.0.0"
    assert entry["type"] == "skill"
    assert entry["installed"] is False
    # 文件名而不是绝对路径：宿主路径不该经由接口流出去。
    assert entry["file_name"] == "demo.zip"
    assert "imports_path" not in entry
    assert str(tmp_path) not in json.dumps(entry)


def test_empty_imports_directory_is_not_an_error(tmp_path: Path):
    service = _service(tmp_path)

    # 「目录里什么都没有」是常态，不是故障。
    assert service.discover_importable_archives() == []


def test_already_installed_archive_is_flagged_not_hidden(tmp_path: Path):
    service = _service(tmp_path)
    staged = _stage(service, "demo.zip")
    service.install_archive(staged)

    found = service.discover_importable_archives()

    # 从列表里消失会让人以为文件没放对，于是反复重传同一个包。
    assert len(found) == 1
    assert found[0]["installed"] is True
    assert found[0]["installed_version"] == "1.0.0"


def test_an_upgrade_candidate_reports_the_installed_version(tmp_path: Path):
    service = _service(tmp_path)
    service.install_archive(_stage(service, "v1.zip"))
    (service.imports_path / "v1.zip").unlink()
    _stage(service, "v2.zip", version="2.0.0")

    entry = service.discover_importable_archives()[0]

    assert entry["version"] == "2.0.0"
    assert entry["installed"] is True
    assert entry["installed_version"] == "1.0.0"
    # 「已装 1.0.0、盘上有 2.0.0」与「已装 2.0.0」处置不同，必须能分辨。
    assert entry["is_upgrade"] is True


def test_a_broken_archive_does_not_break_the_whole_listing(tmp_path: Path):
    service = _service(tmp_path)
    _stage(service, "good.zip")
    (service.imports_path / "broken.zip").write_bytes(b"not a zip at all")

    found = {entry["file_name"]: entry for entry in service.discover_importable_archives()}

    assert set(found) == {"good.zip", "broken.zip"}
    assert found["good.zip"]["error"] is None
    # 坏包单独标错，而不是让整份列表打不开——那会把一个坏文件放大成功能不可用。
    assert found["broken.zip"]["error"] is not None
    assert found["broken.zip"]["resource_id"] is None


def test_non_zip_files_are_ignored(tmp_path: Path):
    service = _service(tmp_path)
    _stage(service, "good.zip")
    (service.imports_path / "notes.txt").write_text("scp scratch", encoding="utf-8")

    found = service.discover_importable_archives()

    # 运维往这个目录里放临时文件很常见；把它们列成「待导入资源」是噪音。
    assert [entry["file_name"] for entry in found] == ["good.zip"]


def test_discovery_never_installs_anything(tmp_path: Path):
    service = _service(tmp_path)
    _stage(service, "demo.zip")

    service.discover_importable_archives()

    # 发现是只读的：安装仍必须经过原有的确认与校验链路。
    assert service.list_resources() == []


def test_import_by_file_name_installs_the_discovered_archive(tmp_path: Path):
    service = _service(tmp_path)
    _stage(service, "demo.zip")

    record = service.import_discovered_archive("demo.zip")

    assert record["resource_id"] == "demo.skill"
    # 与上传路径同一套边界：新装资源一律停用且需确认。
    assert record["enabled"] is False
    assert record["confirmation_required"] is True


def test_import_by_file_name_rejects_traversal(tmp_path: Path):
    service = _service(tmp_path)
    outside = tmp_path / "outside.zip"
    manifest, files = _manifest()
    _write_archive(outside, manifest, files)

    with pytest.raises(ResourceValidationError):
        service.import_discovered_archive("../outside.zip")


def test_import_by_file_name_rejects_absolute_paths(tmp_path: Path):
    service = _service(tmp_path)
    manifest, files = _manifest()
    outside = _write_archive(tmp_path / "abs.zip", manifest, files)

    with pytest.raises(ResourceValidationError):
        service.import_discovered_archive(str(outside))


def test_import_by_file_name_rejects_a_missing_file(tmp_path: Path):
    service = _service(tmp_path)

    with pytest.raises(ResourceValidationError):
        service.import_discovered_archive("nope.zip")


def test_import_by_file_name_rejects_a_nested_subdirectory(tmp_path: Path):
    service = _service(tmp_path)
    nested = service.imports_path / "sub"
    manifest, files = _manifest()
    _write_archive(nested / "demo.zip", manifest, files)

    # 只认这一层的文件名。允许子路径就等于把「文件名」悄悄变成「相对路径」，
    # 而那要重新论证一遍穿越安全性。
    with pytest.raises(ResourceValidationError):
        service.import_discovered_archive("sub/demo.zip")
