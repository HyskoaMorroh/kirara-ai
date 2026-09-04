"""`ensure_builtins()` 跑在启动路径上（`entry.py:434`），它必须无条件跑完。

现场形态：内置目录加进 skill 之后，这个函数第一次有了「需要出网才能完成」的条目。
两个后果各自独立：

1. **重复安装。** skill 条目声明的 `source_key` 是 `owner/repo:directory`，
   而装完之后 manifest 里存的是 `_fetch_skill_files` 解析出来的目录——两者不一定
   同一个字符串（上游把 skill 放在仓库根时解析成 `"."`）。`_installed_for_catalog`
   按 owner/repository 比对时又只认 `item["owner"]`，而内置条目根本没有那两个键，
   于是每次启动都当成「没装过」再装一遍，第二次撞
   `ResourceValidationError: resource ID is already installed`。

2. **无网即死机。** `_download_bytes` 抛 `OSError` 时整个 `ensure_builtins()`
   中断，`entry.py` 没有 try，进程起不来——一个「预置一条可选技能」的动作
   把「服务能不能启动」绑到了 github.com 的可达性上。离线部署、公司代理、
   GitHub 抽风都会变成启动失败。

判据：**启动路径上的可选动作，失败只能降级，不能阻断。** 前 11 条本地内置
必须照常装好，出网那条失败就跳过并留下可诊断的记录。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import kirara_ai.plugin_manager.resource_sources as resource_sources
from kirara_ai.plugin_manager.resource_catalog import _BUILTINS, ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService

_LOCAL_BUILTINS = tuple(item for item in _BUILTINS if item["type"] != "skill")


def _offline_catalog(tmp_path: Path) -> tuple[ResourceLifecycleService, ResourceCatalogService]:
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    return lifecycle, ResourceCatalogService(lifecycle)


def test_ensure_builtins_survives_a_dead_network(tmp_path: Path):
    """出网失败不能让启动挂掉，本地内置照常装完。"""
    lifecycle, catalog = _offline_catalog(tmp_path)

    with patch.object(
        resource_sources.ResourceSourceService,
        "_download_bytes",
        side_effect=OSError("Name or service not known"),
    ):
        catalog.ensure_builtins()

    installed = {resource["resource_id"] for resource in lifecycle.list_resources()}
    for item in _LOCAL_BUILTINS:
        expected = str(item["catalog_id"]).replace(":", ".", 1)
        assert expected in installed, f"{expected} 没装上——出网失败连累了本地条目"


def test_ensure_builtins_records_why_a_remote_builtin_was_skipped(tmp_path: Path):
    """跳过要留痕：界面得能说清「为什么这条没有」，而不是静默消失。"""
    _, catalog = _offline_catalog(tmp_path)

    with patch.object(
        resource_sources.ResourceSourceService,
        "_download_bytes",
        side_effect=OSError("Name or service not known"),
    ):
        catalog.ensure_builtins()

    skipped = catalog.builtin_provisioning_report()
    assert [entry["catalog_id"] for entry in skipped] == ["skill:agent-browser"]
    assert skipped[0]["reason"]
    assert "Name or service not known" in skipped[0]["reason"]


def test_ensure_builtins_is_idempotent_when_the_remote_directory_resolves_elsewhere(
    tmp_path: Path,
):
    """上游把 Skill 放在仓库根 → manifest 存 `"."`，与内置声明的目录不同字符串。

    第二次启动必须认出它已经装过，而不是再装一遍撞「already installed」。
    """
    lifecycle, catalog = _offline_catalog(tmp_path)
    calls: list[int] = []

    def _fake_install_skill(*, owner, name, branch=None, directory, source_key=None):
        calls.append(1)
        resolved = catalog.sources.resolved_source_key(owner, name, ".")
        archive = catalog.sources._build_skill_archive(
            resource_id="skill.rootlevel",
            source_key=resolved,
            source_url=f"https://github.com/{owner}/{name}",
            version="1.0.0",
            metadata={
                "provider": "github",
                "owner": owner,
                "repository": name,
                "branch": branch or "main",
                "directory": ".",
                "name": "agent-browser",
                "description": "",
            },
            files={"SKILL.md": b"---\nname: agent-browser\n---\n"},
        )
        temporary = lifecycle.imports_path / "root-level.zip"
        temporary.write_bytes(archive)
        try:
            installed = lifecycle.install_archive(temporary)
        finally:
            temporary.unlink(missing_ok=True)
        installed["source_key"] = resolved
        return installed

    with patch.object(catalog.sources, "install_skill", side_effect=_fake_install_skill):
        catalog.ensure_builtins()
        catalog.ensure_builtins()

    assert calls == [1], "第二次启动又下载了一遍——没认出已装的资源"
    # 这条用例问的是「**远端**技能有没有被装两遍」，因此只看那一条的身份。
    # 随包技能（`bundled_dir`）也会被 `ensure_builtins()` 装上，它们与本用例
    # 无关；把它们一起要求会让这条断言变成「内置里只许有一个 skill」，
    # 而那不是它想守的边界。
    bundled_ids = {
        str(item["catalog_id"]).replace(":", ".", 1)
        for item in _BUILTINS
        if item.get("bundled_dir")
    }
    remote_skills = [
        resource["resource_id"]
        for resource in lifecycle.list_resources()
        if resource["type"] == "skill" and resource["resource_id"] not in bundled_ids
    ]
    assert remote_skills == ["skill.rootlevel"]
