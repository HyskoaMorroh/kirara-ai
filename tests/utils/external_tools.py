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
- 三条 MCP integration 用例要 `npx` 拉起 context7。

而「PATH 上有没有 `npx`」不是那三条用例真正的前置条件。GitHub runner **装了
Node**，于是 `require_tool("npx")` 不 skip，用例接着去做一次真实的
`npx -y @upstash/context7-mcp`——那是一次**联网下载**，而 MCP 连接预算是
`startup_timeout_ms`（默认 120 秒）。注册表慢一点，用例就在 120 秒处超时，
报出来的是「连接到 MCP 服务器 context7 超时」，与被测行为无关。

证据是同一个提交上三次运行的结果互相矛盾：ubuntu-py3.11 在 `Run Tests` 里过、
在 `Docker build latest` 里失败；ubuntu-py3.13 反过来。同一个测试、同一份代码，
成败取决于当次 npm 注册表的响应速度——这不是被测代码的性质，
而是门禁本身在赌网络。它还真的挡住过一次镜像发布。

因此判据要换成「这个包**能不能从本地缓存拉起**」：见
:func:`require_npx_package`。

把「这个工具在不在」收在这里一处，而不是让每个文件各写一遍：各写一遍的结果就是
上面那三份——判断存在、写法不同、且都漏了 ``FileNotFoundError`` 这条路径。
"""

from __future__ import annotations

import shutil
import subprocess

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


def require_npx_package(package: str, *, reason: str) -> None:
    """Skip unless ``package`` can be launched by ``npx`` **without a download**.

    为什么不能只判断 `npx` 在不在
    ----------------------------
    `npx -y <pkg>` 在包不在本地缓存时会去 npm 注册表下载。调用方（MCP 连接）
    给的预算是 ``startup_timeout_ms``，默认 120 秒；注册表慢一点，用例就在那里
    超时，而报出来的是「连接到 MCP 服务器超时」——一个指向被测代码的结论，
    实际原因是网络。这类失败在 CI 上表现为同一个提交时好时坏。

    这里用 ``npm exec --offline`` 探一次：``--offline`` 把 npm 的 cache 模式设成
    ``only-if-cached``，缓存里没有就立刻以 ``ENOTCACHED`` 失败（实测 0.7 秒），
    绝不联网。命中缓存时同样很快（实测 1.0 秒），因为它只解析、不执行
    context7 自身——用 ``node --version`` 作为被执行的命令，
    这样探针不会真的拉起一个 MCP 服务器进程。

    于是三种处境被分开了：**装了 Node 且包已缓存**（真的跑这条 integration
    用例）、**装了 Node 但包没缓存**（skip，因为跑它等于赌注册表延迟）、
    **没装 Node**（skip，运行时镜像本来就不装）。
    """
    require_tool("npx", reason=reason)
    # 必须用 `which` 解析出的**完整路径**：Windows 上 npm 是 `npm.cmd`，
    # 而 `subprocess.run(["npm", ...])` 不带 shell 时只会补 `.exe`，
    # 于是抛 FileNotFoundError——探针会退化成「在 Windows 上永远 skip」，
    # 那等于把这条 integration 用例在半个矩阵上删掉，且没有症状。
    npm_executable = shutil.which("npm")
    if npm_executable is None:
        pytest.skip(f"npm 不可用：{reason}")
    try:
        completed = subprocess.run(
            [
                npm_executable,
                "exec",
                "--offline",
                f"--package={package}",
                "--",
                "node",
                "--version",
            ],
            capture_output=True,
            # 探针本身也要有上界：npm 在极端情况下（锁竞争）会卡住，
            # 而一个卡住的探针与它要防的那个超时是同一种故障。
            timeout=90,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        pytest.skip(f"无法探测 npx 包 {package}（{type(error).__name__}）：{reason}")
    if completed.returncode != 0:
        pytest.skip(
            f"{package} 不在本地 npm 缓存里，拉起它需要联网下载：{reason}。"
            f"要在本机跑这条用例，先执行 `npx -y {package} --help`。"
        )
