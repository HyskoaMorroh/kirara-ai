"""内置 AI 调试 Hook 必须覆盖 executor 真的会派发的每一个事件（需求 10）。

需求 10 点名「添加 hooks 进行 AI 功能调试」。`hook:ai-debug` 就是那个内置件：
它是使用者验证「Hook 到底有没有在跑」的唯一现成样本，也是文档里唯一被点名的
Hook 声明。

`tests/agent_runtime/test_hook_event_contract.py` 守的是**契约与实现一致**
（`HOOK_EVENTS` 声明的 11 个事件 executor 都派发）。这份守的是另一件事：
**那个内置件跟上了契约**。两者是独立的：上一轮补齐了 `SessionEnd` /
`SubagentStart` / `SubagentStop` 三个派发点，而内置 Hook 的声明仍是 8 个事件——
契约齐了、派发齐了，唯一的现成样本仍然漏三个。

漏掉的后果不是「少三条日志」，而是这三类事件在产品上**没有任何可验证的入口**：

- `SessionEnd` 是会话清理的挂钩点。用户想确认「会话结束时我的钩子跑了吗」，
  照内置件抄一份，抄到的声明里压根没有这个事件；
- `SubagentStart` / `SubagentStop` 是队友委派的前后。Teammates 模式下
  「委派出去的那一轮有没有被审计」正是最需要证据的地方。

而这个缺口完全静默：声明校验只查事件名在不在 `HOOK_EVENTS` 里，少写几个事件
永远不会报错；`/agents/hooks` 返回的是声明里有什么，不是「还能挂什么」。

顺带锁住内置件的版本：`ResourceCatalogService.install()` 只在
`bundled_version > installed_version` 时才推进已装的资源。加事件却不抬版本号，
等于只有全新部署能拿到新事件，而已经装过的部署永远停在旧声明上。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from kirara_ai.agent_runtime.audit_hook_command import _EVENTS as COMMAND_EVENTS
from kirara_ai.agent_runtime.hooks import HOOK_EVENTS
from kirara_ai.plugin_manager.resource_catalog import ResourceCatalogService
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService

_EXECUTOR = (
    Path(__file__).resolve().parents[2] / "kirara_ai" / "agent_runtime" / "executor.py"
)


def _dispatched_events() -> set[str]:
    """executor 真的会派发的事件名。与 hook 契约测试同一套解析。"""
    source = _EXECUTOR.read_text(encoding="utf-8")
    return set(re.findall(r'_run_hook\(\s*\n?\s*"([A-Za-z]+)"', source))


@pytest.fixture()
def declaration(tmp_path: Path) -> dict:
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    catalog = ResourceCatalogService(lifecycle)
    catalog.ensure_builtins()
    resource = lifecycle.get_resource("hook.ai-debug")
    return json.loads(
        lifecycle.read_entry("hook.ai-debug", resource["current_version"])
    )


def test_the_probe_finds_real_dispatch_points():
    """自检：解析确实读到了派发点，不是拿空集合互相比较。"""
    dispatched = _dispatched_events()

    assert len(dispatched) >= 8, f"只解析到 {len(dispatched)} 个派发点，正则可能失效"
    assert "SessionStart" in dispatched


def test_the_builtin_hook_declares_every_dispatched_event(declaration: dict):
    """内置件必须覆盖每一个真的会派发的事件。

    漏一个就等于那类事件在产品上没有可验证的样本：用户照内置件抄，
    抄到的声明里压根没有它。
    """
    declared = set(declaration["events"])
    missing = sorted(_dispatched_events() - declared)

    assert not missing, (
        f"内置 hook:ai-debug 没有声明这些已派发事件：{missing}。"
        "它是「Hook 有没有在跑」的唯一现成样本，漏掉的事件在产品上无从验证。"
    )


def test_it_does_not_declare_an_event_nobody_dispatches(declaration: dict):
    """反向也要一致：声明一个不会来的事件，用户会以为钩子没生效。"""
    undeclared = sorted(set(declaration["events"]) - _dispatched_events())

    assert not undeclared, (
        f"内置 hook 声明了 executor 不派发的事件：{undeclared}。"
        "它永远不触发，而界面上显示为已启用。"
    )


def test_every_declared_event_is_accepted_by_the_command(declaration: dict):
    """命令进程必须认得声明里的每一个事件名。

    `audit_hook_command` 对未知事件名返回退出码 2 并写 stderr——那在运行时
    表现为「钩子每次都失败」，而声明本身完全合法，校验一路通过。
    """
    for event in sorted(declaration["events"]):
        assert event in COMMAND_EVENTS, (
            f"内置 hook 声明了 {event}，但 audit_hook_command 不接受这个事件名，"
            "运行时会以退出码 2 失败"
        )


def test_each_event_passes_its_own_name_to_the_command(declaration: dict):
    """命令的最后一个参数就是事件名，不能全都传同一个。

    复制粘贴时最容易漏改的正是这一处，而漏改之后审计记录里每一条都写着
    同一个事件——比没有记录更糟，它给出一个错误的答案。
    """
    for event, spec in declaration["events"].items():
        assert spec["command"][-1] == event, (
            f"{event} 的命令传的是 {spec['command'][-1]!r}，审计记录会把事件类型记错"
        )


def test_every_event_keeps_the_capability_gate(declaration: dict):
    """每个事件都要保留权限门禁：它们都会真的起一个进程。"""
    for event, spec in declaration["events"].items():
        assert spec["type"] == "command", f"{event} 不是 command 型"
        assert spec["command"][:3] == [
            "{python}",
            "-m",
            "kirara_ai.agent_runtime.audit_hook_command",
        ], f"{event} 的命令前缀被改过"
        assert spec["required_capabilities"] == ["process.execute"], (
            f"{event} 少了 process.execute 门禁——它会起一个进程"
        )
        assert spec["required_permissions"] == ["process.execute"]
        # 超时与输出上界不能缺：一个卡住的钩子会把每一轮对话都拖到超时。
        assert isinstance(spec["timeout_ms"], int) and spec["timeout_ms"] > 0
        assert isinstance(spec["max_output_bytes"], int) and spec["max_output_bytes"] > 0


def test_the_declaration_covers_the_whole_contract(declaration: dict):
    """与 `HOOK_EVENTS` 对齐：契约里有的，内置件都能演示。

    这条与上面按派发点的断言互为补充：派发点是「现在真的会来」，
    `HOOK_EVENTS` 是「用户能挂什么」。两者当前相等，但断言两遍才能在
    其中一侧先变时立刻指出是哪一侧。
    """
    assert set(declaration["events"]) == set(HOOK_EVENTS)


def test_the_bundled_version_advances_installed_copies(tmp_path: Path):
    """加了事件必须抬版本号，否则已装的部署永远停在旧声明上。

    `ResourceCatalogService.install()` 只在 `bundled > installed` 时推进已装资源。
    改内容不改版本号的后果是「新部署有、老部署没有」，而两边的界面都显示
    「已安装、已启用」。
    """
    from packaging.version import Version

    lifecycle = ResourceLifecycleService(tmp_path / "data")
    catalog = ResourceCatalogService(lifecycle)
    catalog.ensure_builtins()
    version = Version(lifecycle.get_resource("hook.ai-debug")["current_version"])

    # 1.1.0 是只声明 8 个事件的那一版。覆盖到 11 个事件必须是一个更高的版本，
    # 否则从 1.1.0 升上来的部署拿不到新事件。
    assert version > Version("1.1.0"), (
        "内置 hook 的事件集合变了但版本号没抬——已装 1.1.0 的部署不会被推进"
    )
