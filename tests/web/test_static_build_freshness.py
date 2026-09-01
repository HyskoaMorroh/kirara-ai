"""过期的本地 WebUI 构建必须在 readiness 里说出来（需求 14 的可观测性）。

`app.py:71` 把 `$PWD/web` 作为静态目录。仓库根的 `web/` 是 gitignored 的本地
构建产物，而 `webui/` 才是源码。两者版本各走各的：

- `webui/package.json` 现在是 `3.3.0-b14`
- 仓库根 `web/version.json` 是 `0.1.1-beta.3`

差了整整一个大版本。直接跑本地源码起服务时，浏览器看到的是那份旧界面——
它没有 skills.sh 来源选择器、没有依赖提示、没有本轮改的任何东西。

失败形态是最难查的一类：**后端是新的，前端是旧的**。用户按新文档去点一个按钮，
按钮不存在；他会以为文档写错了，或者功能没做。而 API 探针、健康检查、
版本号一致性检查全都通过——因为它们查的是后端。

Docker 部署不受影响（`Dockerfile` 重新拷贝 `webui/dist`），所以这个陷阱只在
本地开发与源码部署时出现，恰好是最不容易被 CI 覆盖到的路径。

这里锁住：readiness 能把「静态目录版本」与「后端版本」的落差报出来，
且报的是可判断的结构化字段，不是一句人肉去比的文本。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.web.utils import get_installed_webui_version


def _write_version(path: Path, package_version: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "version.json").write_text(
        json.dumps({"version": f"v{package_version}", "packageVersion": package_version}),
        encoding="utf-8",
    )


def test_reading_an_installed_version(tmp_path: Path):
    """自检：版本读取本身能工作，后面的断言才有意义。"""
    _write_version(tmp_path / "web", "3.3.0-b14")

    assert get_installed_webui_version(tmp_path / "web") == "3.3.0-b14"


def test_a_missing_static_build_reports_unknown(tmp_path: Path):
    """没有构建产物时返回 unknown，而不是抛错或返回空串。"""
    assert get_installed_webui_version(tmp_path / "nope") == "unknown"


class TestStaticBuildFreshness:
    """`static_build_matches_backend` 判定静态构建是否与后端同版本。"""

    def test_matching_versions_are_fresh(self):
        from kirara_ai.web.utils import static_build_freshness

        result = static_build_freshness(
            installed="3.3.0-b14", expected="3.3.0-b14"
        )

        assert result["status"] == "current"
        assert result["stale"] is False

    def test_a_different_version_is_stale(self):
        """版本不同就是过期——不需要比较大小，不同即需要重建。"""
        from kirara_ai.web.utils import static_build_freshness

        result = static_build_freshness(
            installed="0.1.1-beta.3", expected="3.3.0-b14"
        )

        assert result["status"] == "stale"
        assert result["stale"] is True
        assert result["installed"] == "0.1.1-beta.3"
        assert result["expected"] == "3.3.0-b14"

    def test_an_unknown_install_is_not_reported_as_stale(self):
        """读不到版本与「读到了一个旧版本」是两种处境。

        前者可能是构建产物根本不存在（纯 API 部署完全合法），把它报成
        「过期」会让一个正常部署看起来有问题。
        """
        from kirara_ai.web.utils import static_build_freshness

        result = static_build_freshness(installed="unknown", expected="3.3.0-b14")

        assert result["status"] == "unknown"
        assert result["stale"] is False

    def test_an_unknown_expectation_is_also_unknown(self):
        """后端自己的版本号读不到时，无从判断落差。"""
        from kirara_ai.web.utils import static_build_freshness

        result = static_build_freshness(installed="3.3.0-b14", expected="unknown")

        assert result["status"] == "unknown"
        assert result["stale"] is False

    def test_a_prerelease_suffix_difference_still_counts(self):
        """`3.3.0-b14` 与 `3.3.0-b13` 是两份不同的构建，不能算同版本。"""
        from kirara_ai.web.utils import static_build_freshness

        result = static_build_freshness(installed="3.3.0-b13", expected="3.3.0-b14")

        assert result["stale"] is True

    def test_whitespace_does_not_create_a_false_mismatch(self):
        """版本号两侧的空白不该被当成版本差异。"""
        from kirara_ai.web.utils import static_build_freshness

        result = static_build_freshness(installed=" 3.3.0-b14 ", expected="3.3.0-b14")

        assert result["stale"] is False
        assert result["status"] == "current"
