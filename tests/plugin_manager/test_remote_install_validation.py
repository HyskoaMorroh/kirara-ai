"""远程 Skill 安装入口的输入校验契约。

两处缺陷都在「创建者门禁之后」，因此不是权限提升，而是**未校验输入落到磁盘
写入路径上**：

1. `POST /api/resource/remote-install` 曾把请求体整个 `**payload` splat 进
   `install_skill`。同级路由都走 `_strict_json_object`，只有这一条不走：多带
   一个键就抛 `TypeError`，被 `_lifecycle_error` 变成不带信息的 500；少带
   `directory` 同样是 500 而不是 400。更要紧的是，`install_skill` 将来任何一个
   关键字参数都会变成远程可设。

2. `_validate_directory(".")` 在所有形态检查**之前**直接返回，于是 `"."`
   绕过 `_DIRECTORY_PART`、`..`、`//`、`\\` 全部判据。`"."` 的语义是「整个仓库
   就是一个 Skill」，那是 `_resolve_skill_directory` 返回 `None` 时由
   `_fetch_skill_files` 自己填的**结果值**，不是用户可以请求的输入：
   直接请求 `"."` 会把整个仓库（上限 4096 个成员 / 128 MB）当成一个 Skill 装进来，
   而 `source_key` 变成 `owner/repo:.`，重复安装检测也识别不到。

`"  ."`（前置空格）此前同样命中那条早退路径，因此一并钉住。
"""

from __future__ import annotations

import pytest

from kirara_ai.plugin_manager.resource_sources import (ResourceSourceError,
                                                       ResourceSourceService)


class TestDirectoryValidation:
    @pytest.mark.parametrize("raw", [".", " . ", "./", "/."])
    def test_the_repository_root_cannot_be_requested_as_a_directory(self, raw: str):
        """`"."` 是内部结果值，不是可请求的输入。"""
        with pytest.raises(ResourceSourceError):
            ResourceSourceService._validate_directory(raw)

    @pytest.mark.parametrize(
        "raw",
        ["..", "a/../b", "a//b", "a\\b", "", "   ", "-lead", ".hidden"],
    )
    def test_malformed_directories_are_still_rejected(self, raw: str):
        with pytest.raises(ResourceSourceError):
            ResourceSourceService._validate_directory(raw)

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("skills/agent-browser", "skills/agent-browser"),
            ("agent-browser", "agent-browser"),
            ("/etc", "etc"),
            ("a/b/c", "a/b/c"),
        ],
    )
    def test_valid_directories_are_normalized(self, raw: str, expected: str):
        assert ResourceSourceService._validate_directory(raw) == expected

    def test_source_key_cannot_be_built_for_the_repository_root(self):
        """`owner/repo:.` 这种 source_key 不能由用户输入构造出来。"""
        with pytest.raises(ResourceSourceError):
            ResourceSourceService.source_key("owner", "repo", ".")

    def test_source_key_for_a_real_directory_is_stable(self):
        assert (
            ResourceSourceService.source_key("owner", "repo", "skills/x")
            == "owner/repo:skills/x"
        )


class TestRepositoryIsItselfASkill:
    """仓库本身就是一个 Skill 时，`"."` 仍然必须能作为**结果**产出。

    这条路径由 `_resolve_skill_directory` 返回 `None` 触发，
    `_fetch_skill_files` 再把 `resolved_directory` 记成 `"."`。
    拒绝用户输入 `"."` 不能顺手把这条内部路径也堵死。
    """

    def test_resolution_reports_the_root_as_none_for_a_root_level_skill(self):
        members = {
            "repo-abc/SKILL.md": b"---\nname: x\n---\nbody",
            "repo-abc/run.sh": b"echo",
        }

        resolved = ResourceSourceService._resolve_skill_directory(
            members, "repo-abc", "x"
        )

        assert resolved is None

    def test_a_named_subdirectory_still_resolves(self):
        members = {
            "repo-abc/README.md": b"x",
            "repo-abc/skills/agent-browser/SKILL.md": b"---\nname: ab\n---\nb",
        }

        resolved = ResourceSourceService._resolve_skill_directory(
            members, "repo-abc", "agent-browser"
        )

        assert resolved == "skills/agent-browser"

    def test_the_internal_root_marker_still_builds_a_source_url(self):
        """`_skill_source_url` 对内部的 `"."` 结果值仍然要给出仓库根地址。"""
        assert (
            ResourceSourceService._skill_source_url("o", "n", "main", ".")
            == "https://github.com/o/n/tree/main"
        )
