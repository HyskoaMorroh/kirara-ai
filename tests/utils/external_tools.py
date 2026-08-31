"""Shared guards for tests that need a tool the runtime does not ship.

CI 的镜像内测试（`run-tests.yml` 的 Docker image validation）把仓库挂进容器再跑
整个 `./tests`。那个容器是**运行时**镜像：只有 Python、ffmpeg、libmagic1，
没有 `git`、没有 Node/`npx`——它不需要它们，产品也不需要。

于是一类测试在那里必然失败，而失败原因与被测行为无关：

- 三份门禁用 `git ls-files` / `git ls-tree` 枚举跟踪内容（私有路径、审计产物、
  运行期数据库）。它们各自写过一个「git 不可用就 skip」的判断，但只看
  ``returncode != 0``——而可执行文件根本不存在时 ``subprocess.run`` 抛
  ``FileNotFoundError``，判断句一次都没执行到。
- `test_version_management.py` 的发布身份夹具用真实 Git 仓库，同一个原因。
- 两条 MCP integration 用例要 `npx` 拉起 context7。

把「这个工具在不在」收在这里一处，而不是让每个文件各写一遍：各写一遍的结果就是
上面那三份——判断存在、写法不同、且都漏了 ``FileNotFoundError`` 这条路径。
"""

from __future__ import annotations

import shutil

import pytest


def require_tool(name: str, *, reason: str) -> None:
    """Skip the calling test when ``name`` is not on PATH.

    用 :func:`shutil.which` 而不是「跑一次看它报什么错」：后者要为每个工具各写一遍
    异常处理，而缺失这件事与工具本身无关，是同一个判断。
    """
    if shutil.which(name) is None:
        pytest.skip(f"{name} 不可用：{reason}")


def require_git(reason: str = "无法枚举 Git 跟踪内容") -> None:
    """Skip when Git is missing (the runtime image does not ship it)."""
    require_tool("git", reason=reason)
