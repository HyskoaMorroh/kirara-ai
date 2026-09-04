"""发布门禁不能赌 npm 注册表的响应速度。

发现过程
------
`v3.3.0b16` 的镜像发布被一条 integration 用例挡住了：

    FAILED tests/agent_runtime/test_context7_integration.py::
        test_real_context7_mcp_completes_agent_turn_after_model_failover
        - assert False is True

日志里的时间戳说清了原因：

    05:28:53  Connecting to MCP server context7
    05:30:53  连接到 MCP 服务器 context7 超时      ← 正好 120 秒

`require_tool("npx")` 只问「PATH 上有没有 npx」。GitHub runner **装了 Node**，
所以它不 skip，用例接着做一次真实的 `npx -y @upstash/context7-mcp`——那是联网下载，
而 MCP 连接预算是 `startup_timeout_ms`（默认 120_000 毫秒）。

它是网络竞态而不是代码缺陷，证据是同一个提交上三次运行互相矛盾：
ubuntu-py3.11 在 `Run Tests` 里通过、在 `Docker build latest` 里失败；
ubuntu-py3.13 反过来。

这组测试锁住的边界
----------------
1. 探针**绝不联网**：命中与不命中都必须在秒级返回。
2. 三种处境分开：没有 Node、有 Node 但包没缓存、包已缓存。
3. 探针自身有超时上界——一个卡住的探针与它要防的那个超时是同一种故障。
4. 三个调用点都用新判据，不能有任何一处漏改回到「只看 npx 在不在」。
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

from tests.utils.external_tools import require_npx_package, require_tool

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: 那三条靠 npx 拉起 context7 的 integration 用例。
_NPX_CALL_SITES = (
    "tests/agent_runtime/test_context7_integration.py",
    "tests/agent_runtime/test_persistent_resource_runtime.py",
)


def _pretend_node_is_installed(monkeypatch) -> None:
    """让 `shutil.which` 报告 npx / npm 都在。

    这两条断言问的是「缓存未命中那一支说了什么」，而**不是**「这台机器有没有
    Node」。不 stub 的话，运行时镜像里没有 Node，探针在第一道 `require_tool("npx")`
    就 skip 掉，断言拿到的是「npx 不可用」——测试于是在镜像里失败，
    而失败原因与被断言的行为无关。这正是本文件要修的那种形态。
    """
    monkeypatch.setattr(
        "tests.utils.external_tools.shutil.which", lambda name: f"/usr/bin/{name}"
    )


class TestTheProbeNeverReachesTheNetwork:
    def test_a_missing_package_skips_instead_of_downloading(self):
        """缓存里没有的包必须立刻 skip，而不是去下载它。

        这一条用**真的** npm 探一次，因此它是唯一能证明「不联网」的用例——
        stub 掉 `subprocess.run` 就等于把被证明的那件事替换成了假设。
        代价是它需要本机有 npm：没有 npm 时这个问题在这台机器上无从回答，
        所以显式 skip（运行时镜像就是这种处境）。
        """
        require_tool("npm", reason="这一条要用真的 npm 证明探针不联网")

        started = time.monotonic()
        with pytest.raises(pytest.skip.Exception) as caught:
            require_npx_package(
                "@kirara-ai/definitely-not-a-real-package-xyz",
                reason="探针测试",
            )
        elapsed = time.monotonic() - started

        # 30 秒是很宽的上界：实测 0.7 秒。真去联网下载会撞上
        # 调用方那个 120 秒的预算，而这里的判据是「远远快于它」。
        assert elapsed < 30, f"探针耗时 {elapsed:.1f} 秒，说明它在联网"
        assert "本地 npm 缓存" in str(caught.value)

    def test_the_skip_message_says_how_to_run_it_locally(self, monkeypatch):
        """skip 不能只说「跳过了」——要说清怎样让它跑起来。

        这一条只问文案，因此把「有没有 Node」与「包在不在缓存里」都固定下来：
        它在任何机器上都必须给出同一个答案。
        """
        _pretend_node_is_installed(monkeypatch)
        monkeypatch.setattr(
            "tests.utils.external_tools.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, b"", b"ENOTCACHED"),
        )

        with pytest.raises(pytest.skip.Exception) as caught:
            require_npx_package("@kirara-ai/another-absent-package", reason="探针测试")

        message = str(caught.value)
        assert "npx -y" in message
        assert "@kirara-ai/another-absent-package" in message
        assert "本地 npm 缓存" in message

    def test_the_probe_uses_offline_mode(self):
        """`--offline` 是「绝不联网」这件事的唯一实现手段，不能被去掉。

        去掉它探针照样「工作」——只是变回了它要修的那个赌注册表的形态，
        而那种回归在本机（包已缓存）完全没有症状。
        """
        source = (REPOSITORY_ROOT / "tests/utils/external_tools.py").read_text(
            encoding="utf-8"
        )

        assert '"--offline"' in source, "探针必须用 --offline，否则它会联网下载"
        assert "only-if-cached" in source, "要写明 --offline 的含义"

    def test_the_probe_has_its_own_timeout(self):
        """卡住的探针与它要防的那个超时是同一种故障。"""
        source = (REPOSITORY_ROOT / "tests/utils/external_tools.py").read_text(
            encoding="utf-8"
        )

        assert "timeout=90" in source
        assert "TimeoutExpired" in source, "探针超时必须被接住并转成 skip"


class TestItSeparatesThreeSituations:
    def test_no_node_at_all_skips(self, monkeypatch):
        """运行时镜像不装 Node，那不是「MCP 集成坏了」。"""
        monkeypatch.setattr(
            "tests.utils.external_tools.shutil.which", lambda name: None
        )

        with pytest.raises(pytest.skip.Exception) as caught:
            require_npx_package("@upstash/context7-mcp", reason="需要 Node")

        assert "npx" in str(caught.value)

    def test_a_cached_package_does_not_skip(self, monkeypatch):
        """包已缓存时用例必须真的跑——否则这条 integration 用例等于被删掉。"""
        monkeypatch.setattr(
            "tests.utils.external_tools.shutil.which", lambda name: f"/usr/bin/{name}"
        )
        monkeypatch.setattr(
            "tests.utils.external_tools.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, b"v24\n", b""),
        )

        require_npx_package("@upstash/context7-mcp", reason="需要 Node")

    def test_a_probe_that_cannot_start_skips(self, monkeypatch):
        """`npm` 存在但起不来（OSError）时同样是环境问题，不是被测行为。"""
        monkeypatch.setattr(
            "tests.utils.external_tools.shutil.which", lambda name: f"/usr/bin/{name}"
        )

        def explode(*args, **kwargs):
            raise OSError("no exec")

        monkeypatch.setattr("tests.utils.external_tools.subprocess.run", explode)

        with pytest.raises(pytest.skip.Exception) as caught:
            require_npx_package("@upstash/context7-mcp", reason="需要 Node")

        assert "OSError" in str(caught.value)

    def test_a_probe_timeout_skips(self, monkeypatch):
        monkeypatch.setattr(
            "tests.utils.external_tools.shutil.which", lambda name: f"/usr/bin/{name}"
        )

        def hang(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd="npm", timeout=90)

        monkeypatch.setattr("tests.utils.external_tools.subprocess.run", hang)

        with pytest.raises(pytest.skip.Exception) as caught:
            require_npx_package("@upstash/context7-mcp", reason="需要 Node")

        assert "TimeoutExpired" in str(caught.value)


class TestEveryCallSiteUsesTheNewCriterion:
    @pytest.mark.parametrize("relative", _NPX_CALL_SITES)
    def test_no_call_site_still_only_checks_for_npx(self, relative: str):
        """漏改一处就会在那一条上恢复原样，而本机看不出区别。"""
        source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")

        assert 'require_tool("npx"' not in source, (
            f"{relative} 仍在只判断 npx 是否存在——它会在 runner 上联网下载"
        )
        assert "require_npx_package(" in source

    def test_require_tool_itself_still_exists_for_git(self):
        """`require_tool` 没有被删掉：三份 Git 门禁仍然用它，那里判据是对的
        （`git` 不存在就是不存在，不涉及下载）。
        """
        with pytest.raises(pytest.skip.Exception):
            require_tool("definitely-not-an-installed-binary-xyz", reason="探针测试")
