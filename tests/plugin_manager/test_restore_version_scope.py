"""需求 10「从备份中恢复」：版本回退不能只对绑定了工作流的资源可用。

`restore_version` 有一道与恢复本身无关的前置检查：

    if not current.get("workflow_id"):
        raise ResourceStateError("resource is not bound to a workflow")

而 `workflow_id` 只有 workflow 类资源才有。skill、prompt、hook、mcp、memory
这五类**从设计上就没有**工作流绑定，于是这个接口对它们永远返回 409：
一个被升级搞坏的 Skill 明明还留着上一版的目录和备份，却没有任何办法回退。

界面上的表现是「回退按钮点了报错，错误信息说没绑定工作流」——一句与用户正在做的
事毫无关系的话，看起来像 bug 而不是限制，于是没人知道该怎么绕过。

恢复真正需要的前置条件只有三条，它们都已经在检查了：
显式确认、目标版本在注册表里、那个版本的目录还在磁盘上。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from kirara_ai.plugin_manager.resource_lifecycle import (
    ResourceLifecycleService,
    ResourceStateError,
    ResourceValidationError,
)


def _write_version(
    lifecycle: ResourceLifecycleService,
    resource_id: str,
    resource_type: str,
    version: str,
    body: str,
    entry: str = "SKILL.md",
) -> str:
    """在磁盘上放出一个完整的版本目录（正文 + manifest），返回它的内容摘要。

    摘要口径必须与 `_content_hash` 一致：`路径:字节数:文件摘要` 逐行拼接后再取
    sha256。直接对正文取摘要会让注册表与 manifest 对不上，恢复时报「不匹配」。
    """
    path = lifecycle.installed_path / resource_id / version
    path.mkdir(parents=True, exist_ok=True)
    encoded = body.encode("utf-8")
    (path / entry).write_bytes(encoded)
    file_sha = hashlib.sha256(encoded).hexdigest()
    content_sha = hashlib.sha256(
        f"{entry}:{len(encoded)}:{file_sha}\n".encode("ascii")
    ).hexdigest()
    (path / "manifest.json").write_text(
        json.dumps(
            {
                "resource_id": resource_id,
                "type": resource_type,
                "version": version,
                "source": "local",
                "entry": entry,
                "permissions": [],
                "files": [{"path": entry, "size": len(encoded), "sha256": file_sha}],
                "content_sha256": content_sha,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return content_sha


def _register(
    lifecycle: ResourceLifecycleService,
    resource_id: str,
    *,
    resource_type: str,
    workflow_id: str | None = None,
) -> None:
    """注册一个有两个版本的资源，当前指向 2.0.0。"""
    first = _write_version(lifecycle, resource_id, resource_type, "1.0.0", "# v1\n")
    second = _write_version(lifecycle, resource_id, resource_type, "2.0.0", "# v2\n")
    with lifecycle._lock:  # noqa: SLF001 - 测试直接构造注册表状态
        registry = lifecycle._registry
        registry["resources"][resource_id] = {
            "resource_id": resource_id,
            "type": resource_type,
            "current_version": "2.0.0",
            "entry": "SKILL.md",
            "content_sha256": second,
            "source": "local",
            "permissions": [],
            "enabled": True,
            "workflow_id": workflow_id,
            "versions": [
                {
                    "version": "1.0.0",
                    "entry": "SKILL.md",
                    "content_sha256": first,
                    "source": "local",
                    "permissions": [],
                },
                {
                    "version": "2.0.0",
                    "entry": "SKILL.md",
                    "content_sha256": second,
                    "source": "local",
                    "permissions": [],
                },
            ],
        }
        lifecycle._write_registry(registry)


@pytest.fixture()
def lifecycle(tmp_path: Path) -> ResourceLifecycleService:
    return ResourceLifecycleService(tmp_path / "runtime")


@pytest.mark.parametrize("resource_type", ["skill", "prompt", "hook", "mcp", "memory"])
def test_restore_works_for_resources_that_never_have_a_workflow(
    lifecycle: ResourceLifecycleService, resource_type: str
):
    _register(lifecycle, f"{resource_type}.example", resource_type=resource_type)

    restored = lifecycle.restore_version(
        f"{resource_type}.example", "1.0.0", confirmed=True
    )

    # 回归点：原实现对这五类资源一律 409「resource is not bound to a workflow」。
    assert restored["current_version"] == "1.0.0"


def test_restore_still_works_for_a_workflow_bound_resource(
    lifecycle: ResourceLifecycleService,
):
    # 只有绑定了工作流的资源才有 `workflow_id`；这里用 skill 加一个绑定来表示
    # 原本唯一能走通的那条路径，确认放宽前置条件没把它改坏。
    _register(lifecycle, "skill.bound", resource_type="skill", workflow_id="chat:demo")

    restored = lifecycle.restore_version("skill.bound", "1.0.0", confirmed=True)

    assert restored["current_version"] == "1.0.0"
    assert restored["workflow_id"] == "chat:demo"


def test_restore_still_requires_confirmation(lifecycle: ResourceLifecycleService):
    _register(lifecycle, "skill.example", resource_type="skill")

    with pytest.raises(ResourceStateError, match="confirmation"):
        lifecycle.restore_version("skill.example", "1.0.0")


def test_restore_rejects_a_version_that_is_not_registered(
    lifecycle: ResourceLifecycleService,
):
    _register(lifecycle, "skill.example", resource_type="skill")

    with pytest.raises(ResourceValidationError, match="not registered"):
        lifecycle.restore_version("skill.example", "9.9.9", confirmed=True)


def test_restore_rejects_a_version_whose_directory_is_gone(
    lifecycle: ResourceLifecycleService,
):
    _register(lifecycle, "skill.example", resource_type="skill")
    target = lifecycle.installed_path / "skill.example" / "1.0.0"
    for child in list(target.iterdir()):
        child.unlink()
    target.rmdir()

    # 注册表里有、磁盘上没有：这才是真正该拦的情况——恢复到一个不存在的目录
    # 会让资源指向空内容，而状态显示成「已恢复」。
    with pytest.raises(ResourceValidationError, match="unavailable"):
        lifecycle.restore_version("skill.example", "1.0.0", confirmed=True)


def test_restore_leaves_the_resource_disabled_pending_confirmation(
    lifecycle: ResourceLifecycleService,
):
    _register(lifecycle, "skill.example", resource_type="skill")

    restored = lifecycle.restore_version("skill.example", "1.0.0", confirmed=True)

    # 回退后的资源不自动重新启用：回退的内容可能与当前 Agent 绑定不兼容，
    # 静默启用等于把一次恢复变成一次未经审阅的上线。
    assert restored["enabled"] is False
    assert restored["confirmation_required"] is True


def test_restore_backs_up_the_version_it_replaces(lifecycle: ResourceLifecycleService):
    _register(lifecycle, "skill.example", resource_type="skill")

    lifecycle.restore_version("skill.example", "1.0.0", confirmed=True)

    backups = lifecycle.list_backups("skill.example")
    # 回退也要留一份：回退错了还能再回来，否则「回退」是单向的。
    assert any(entry.get("reason") == "before-restore" for entry in backups)
