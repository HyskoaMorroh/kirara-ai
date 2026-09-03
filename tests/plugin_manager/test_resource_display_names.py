"""已安装资源要在顶层带上显示名与描述，否则「按名称搜索」永远命中不了。

发现过程：需求 10 的搜索框写着「搜索名称、ID 或描述」，而
`webui/src/views/resources/resourceFilter.ts` 读的是 `resource.name` /
`resource.description`——注册表记录里**从来没有这两个字段**。
`author_document(name=...)` 把它们写进 `source_metadata`，
`install_skill` 也写在那里，而目录安装（`_install_builtin`）连写都没写：
目录条目自己有 `name`「Office and Research Assistant」和一整句中文描述，
建 manifest 时被丢掉了。

后果不是「少匹配一个字段」，而是**三个匹配面里有两个从未命中过任何东西**：
输入框承诺按名称和描述搜索，实际只有 ID 那一面在工作。而这个缺陷不会被类型
检查发现——谓词的入参类型把那两个字段声明成可选，传一个没有它们的对象
完全合法。

这组测试锁住的边界：

1. 目录安装的资源在顶层带上目录里那份名称与描述。
2. 自建文本资源同样在顶层带上（此前只在 `source_metadata` 里）。
3. 确实没有名字的资源，`name` 是 `None` 而不是空字符串——
   「不知道叫什么」与「叫空字符串」是两件事。
4. 顶层已有非空值时不被元数据覆盖。
5. **投影不是第二份存储**：改元数据后投影跟着变，不存在两份各自漂移的值。
6. 修补显示名不抬版本号、不改摘要——那两样由内容决定，与显示无关。
7. 修补是「显示元数据」专用口，不能借它改更新来源（`owner` 等）。
8. 已经装好的旧资源在下次启动（`ensure_builtins`）时补上名字，
   且**不产生新版本**：为补一行显示名而升版本会在版本列表里留下一条
   与内容无关的记录，并触发一次多余备份。
9. 按名称搜索因此真的能命中。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from kirara_ai.plugin_manager.resource_catalog import _BUILTINS, ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import (
    ResourceLifecycleService,
    ResourceStateError,
)


def _catalog_item(catalog_id: str) -> dict:
    return next(item for item in _BUILTINS if item["catalog_id"] == catalog_id)


def _prompt_archive(
    root: Path,
    resource_id: str,
    version: str,
    *,
    body: str,
    name: str | None,
    extra: dict | None,
) -> Path:
    """打一个通得过 `_validate_archive` 的提示词包。

    摘要算法与产品代码逐字节一致（`path:size:sha256\\n` 拼接再哈希）——
    自己算而不是调私有方法，是为了让这组用例在打包细节被改动时也失败，
    那正是「自建的包与内置的包同形」这个约定被破坏的时刻。
    """

    data = body.encode("utf-8")
    record = {
        "path": "PROMPT.md",
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    digest = hashlib.sha256(
        f"{record['path']}:{record['size']}:{record['sha256']}\n".encode("ascii")
    ).hexdigest()
    metadata: dict = {"provider": "authored"}
    if name:
        metadata["name"] = name
    if extra:
        metadata.update(extra)
    manifest = {
        "resource_id": resource_id,
        "type": "prompt",
        "version": version,
        "source": f"authored://local/prompt/{resource_id}",
        "source_metadata": metadata,
        "entry": "PROMPT.md",
        "permissions": ["workflow.read"],
        "files": [record],
        "content_sha256": digest,
    }
    path = root / f"{resource_id}-{version}.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        archive.writestr("PROMPT.md", data)
    return path


def _install_zip(
    lifecycle: ResourceLifecycleService,
    root: Path,
    resource_id: str,
    version: str,
    *,
    name: str | None = None,
    extra: dict | None = None,
) -> dict:
    archive = _prompt_archive(
        root, resource_id, version, body=f"{version} 的正文\n", name=name, extra=extra
    )
    return lifecycle.install_archive(archive)


def _update_zip(
    lifecycle: ResourceLifecycleService,
    root: Path,
    resource_id: str,
    version: str,
    *,
    name: str | None = None,
    extra: dict | None = None,
) -> dict:
    archive = _prompt_archive(
        root, resource_id, version, body=f"{version} 的正文\n", name=name, extra=extra
    )
    return lifecycle.update_archive(archive, expected_resource_id=resource_id)


@pytest.fixture()
def lifecycle(tmp_path: Path) -> ResourceLifecycleService:
    return ResourceLifecycleService(tmp_path / "data")


def test_a_catalog_installed_resource_carries_the_catalog_name(lifecycle):
    """目录条目自己有名字和描述，装完不该丢在半路上。"""
    catalog = ResourceCatalogService(lifecycle)
    item = _catalog_item("prompt:office-research")

    catalog.install("prompt:office-research")

    resource = lifecycle.get_resource("prompt.office-research")
    assert resource["name"] == item["name"]
    assert resource["description"] == item["description"]


def test_an_authored_resource_carries_its_name_at_the_top_level(lifecycle):
    lifecycle.author_document(
        resource_id="prompt.mine",
        resource_type="prompt",
        content="先给结论。\n",
        name="办公助手",
        description="邮件与会议",
    )

    resource = lifecycle.get_resource("prompt.mine")
    assert resource["name"] == "办公助手"
    assert resource["description"] == "邮件与会议"


def test_a_nameless_resource_reports_none_rather_than_an_empty_string(lifecycle):
    """「不知道叫什么」与「叫空字符串」是两件事。

    前者该让界面回落到 ID 显示，后者会让列表上出现一个空白的名字栏。
    """
    lifecycle.author_document(
        resource_id="prompt.bare",
        resource_type="prompt",
        content="没有名字的提示词。\n",
    )

    resource = lifecycle.get_resource("prompt.bare")
    assert resource["name"] is None
    assert resource["description"] is None
    # 字段必须**存在**：缺字段与 `None` 在 JSON 里是两种形状，
    # 前端 `resource.name` 读到 `undefined` 就回到了这个缺陷的起点。
    assert "name" in resource and "description" in resource


def test_the_projection_follows_the_metadata_instead_of_duplicating_it(lifecycle):
    """投影不是第二份存储：改元数据后读出来跟着变。

    存成两个真正的顶层字段就会有两份可以各自漂移，而漂移之后没有症状——
    列表显示旧名字、更新检查用新名字，两边都「有值」。
    """
    lifecycle.author_document(
        resource_id="prompt.rename",
        resource_type="prompt",
        content="正文。\n",
        name="旧名字",
    )

    lifecycle.set_display_metadata("prompt.rename", name="新名字")

    resource = lifecycle.get_resource("prompt.rename")
    assert resource["name"] == "新名字"
    assert resource["source_metadata"]["name"] == "新名字"


def test_patching_a_display_name_changes_neither_version_nor_digest(lifecycle):
    """显示名不参与摘要，也不该抬版本号。"""
    lifecycle.author_document(
        resource_id="prompt.stable",
        resource_type="prompt",
        content="正文。\n",
        name="原名",
    )
    before = lifecycle.get_resource("prompt.stable")

    after = lifecycle.set_display_metadata("prompt.stable", description="补一句描述")

    assert after["current_version"] == before["current_version"]
    assert after["content_sha256"] == before["content_sha256"]
    assert [item["version"] for item in after["versions"]] == [
        item["version"] for item in before["versions"]
    ]


def test_a_blank_value_clears_the_field_while_none_leaves_it_alone(lifecycle):
    """「没提供」与「明确清空」是两件事，用同一个值表达会让清空做不到。"""
    lifecycle.author_document(
        resource_id="prompt.clear",
        resource_type="prompt",
        content="正文。\n",
        name="要清掉的名字",
        description="保留的描述",
    )

    patched = lifecycle.set_display_metadata("prompt.clear", name="   ")

    assert patched["name"] is None
    assert patched["description"] == "保留的描述", "只传 name 不该动 description"


def test_the_display_patch_cannot_repoint_the_update_source(lifecycle):
    """这个入口按构造只接受两个键，不能借它改「去哪里取下一版」。

    `owner` / `repository` / `branch` / `directory` / `catalog_id` 决定更新来源，
    改它们等于把资源指向另一个上游——那是安装路径的权限。
    """
    lifecycle.author_document(
        resource_id="prompt.source",
        resource_type="prompt",
        content="正文。\n",
    )

    with pytest.raises(TypeError):
        lifecycle.set_display_metadata("prompt.source", owner="attacker")  # type: ignore[call-arg]

    metadata = lifecycle.get_resource("prompt.source")["source_metadata"]
    assert "owner" not in metadata


def test_an_already_installed_resource_gains_its_name_without_a_new_version(lifecycle):
    """已经装好的旧资源要在下次启动时补上名字，且不产生新版本。

    这条针对的是升级路径：修复之前装的资源，其 `source_metadata` 里没有
    名称。若只在新安装时写入，那些资源会永远没有名字——而它们恰恰是
    真实部署里的绝大多数。
    """
    catalog = ResourceCatalogService(lifecycle)
    item = dict(_catalog_item("prompt:office-research"))
    # 复现修复前的形状：元数据里只有来源标识，没有显示名。
    legacy_metadata = {
        "provider": "catalog",
        "catalog_id": item["catalog_id"],
        "tags": item["tags"],
    }
    catalog._install_builtin(item)
    resource_id = "prompt.office-research"
    installed = lifecycle.get_resource(resource_id)
    lifecycle._registry["resources"][resource_id]["source_metadata"] = legacy_metadata
    assert lifecycle.get_resource(resource_id)["name"] is None

    catalog.ensure_builtins()

    repaired = lifecycle.get_resource(resource_id)
    assert repaired["name"] == item["name"]
    assert repaired["current_version"] == installed["current_version"], "补显示名不该抬版本"
    assert len(repaired["versions"]) == len(installed["versions"]), "补显示名不该新增版本记录"
    assert lifecycle.list_backups(resource_id) == [], "补显示名不该触发备份"


def test_searching_by_the_catalog_name_now_matches(lifecycle):
    """这条特性存在的理由：搜索框承诺的名称面必须真的能命中。"""
    catalog = ResourceCatalogService(lifecycle)
    catalog.install("prompt:office-research")

    hits = lifecycle.search_resources("Office and Research")

    assert [item["resource_id"] for item in hits] == ["prompt.office-research"]


def test_a_missing_resource_still_fails_loudly(lifecycle):
    """改一条不存在的资源要报错，而不是静默建出一条只有显示名的记录。"""
    with pytest.raises(ResourceStateError):
        lifecycle.set_display_metadata("prompt.absent", name="x")


class TestARenameSurvivesEveryVersionChange:
    """改过的显示名不能被下一次升级或回滚静默丢掉。

    `source_metadata` 在升级与回滚时**整体替换**，这对 `owner` / `repository` /
    `branch` / `directory` / `catalog_id` 是对的——它们说的是「下一版去哪里取」。
    但显示名是用户对这条资源的称呼，跟着一起被替换掉的后果是：
    名字变成 `None`、列表回落到显示 ID，而用户会以为是自己的重命名没保存上。
    新清单往往压根不声明名称（手工打包的 ZIP、`author_document_version(name=None)`
    都不写），所以这不是边缘情况，而是升级路径的常态。

    优先级在两种情形下相反，两条都要钉住：升级时新清单更新，回滚时现存记录更新。
    """

    def test_an_upgrade_whose_manifest_is_silent_keeps_the_rename(self, lifecycle, tmp_path):
        _install_zip(lifecycle, tmp_path, "prompt.up", "1.0.0", name="上游原名")
        lifecycle.set_display_metadata("prompt.up", name="我改的名字")

        _update_zip(lifecycle, tmp_path, "prompt.up", "1.0.1")

        assert lifecycle.get_resource("prompt.up")["name"] == "我改的名字"

    def test_an_upgrade_that_names_the_resource_wins(self, lifecycle, tmp_path):
        """新清单明确给了名称就用它——那是上游这一版的说法，用户还能再改回来。"""
        _install_zip(lifecycle, tmp_path, "prompt.named", "1.0.0", name="旧名")
        lifecycle.set_display_metadata("prompt.named", name="我改的名字")

        _update_zip(lifecycle, tmp_path, "prompt.named", "1.0.1", name="上游新名")

        assert lifecycle.get_resource("prompt.named")["name"] == "上游新名"

    def test_rolling_back_a_version_does_not_undo_the_rename(self, lifecycle):
        """回滚的是内容。旧版本记录里存的是**当时**的叫法，比重命名更旧。"""
        lifecycle.author_document(
            resource_id="prompt.roll",
            resource_type="prompt",
            content="第一版\n",
            name="最初的名字",
        )
        lifecycle.author_document_version("prompt.roll", content="第二版\n", version="1.0.1")
        lifecycle.set_display_metadata("prompt.roll", name="我改的名字")

        lifecycle.restore_version("prompt.roll", "1.0.0", confirmed=True)

        resource = lifecycle.get_resource("prompt.roll")
        assert resource["current_version"] == "1.0.0", "正文要真的回退"
        assert resource["name"] == "我改的名字", "但名字不该跟着回退"

    def test_restoring_a_backup_does_not_undo_the_rename(self, lifecycle):
        lifecycle.author_document(
            resource_id="prompt.bak",
            resource_type="prompt",
            content="第一版\n",
            name="最初的名字",
        )
        lifecycle.author_document_version("prompt.bak", content="第二版\n", version="1.0.1")
        lifecycle.set_display_metadata("prompt.bak", name="我改的名字")
        backup = lifecycle.list_backups("prompt.bak")[-1]

        lifecycle.restore_backup(backup["backup_id"], confirmed=True)

        assert lifecycle.get_resource("prompt.bak")["name"] == "我改的名字"

    def test_a_cleared_name_stays_cleared_after_an_upgrade(self, lifecycle, tmp_path):
        """「清空名称」也是一个用户动作，不该在下一次升级时被悄悄撤销。"""
        _install_zip(lifecycle, tmp_path, "prompt.clr", "1.0.0", name="要清掉的名字")
        lifecycle.set_display_metadata("prompt.clr", name="   ")

        _update_zip(lifecycle, tmp_path, "prompt.clr", "1.0.1")

        assert lifecycle.get_resource("prompt.clr")["name"] is None

    def test_the_update_source_still_follows_the_new_manifest(self, lifecycle, tmp_path):
        """带过去的只有显示名。来源坐标必须跟新清单走，否则更新会去错地方。"""
        _install_zip(
            lifecycle, tmp_path, "prompt.src", "1.0.0", extra={"owner": "old-owner"}
        )

        _update_zip(
            lifecycle, tmp_path, "prompt.src", "1.0.1", extra={"owner": "new-owner"}
        )

        metadata = lifecycle.get_resource("prompt.src")["source_metadata"]
        assert metadata["owner"] == "new-owner"
