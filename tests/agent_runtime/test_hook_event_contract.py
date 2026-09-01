"""`HOOK_EVENTS` 里声明的每个事件都必须真的被派发（需求 10）。

Hook 是「按生命周期事件挂钩子」这套机制的全部。`HOOK_EVENTS` 是它对外的契约：
用户照这份清单写声明、`/agent/hooks` 只读接口照它列描述、内置 `hook:ai-debug`
照它选事件。

但其中三个事件从未被 executor 派发：`SessionEnd`、`SubagentStart`、`SubagentStop`。
它们通过了声明校验、落了盘、在界面上显示为"已启用"，运行时一次都不触发。

这个失效是完全静默的：
- 声明校验只查事件名在不在 `HOOK_EVENTS` 里（`hooks.py` 的 `_validate_events`），
  在就通过；
- `hooks.py:958` 甚至专门为 `SubagentStop` 写了 `decision: block` 的解析分支——
  代码本身认为这个事件会来；
- `/agent/hooks` 返回的是声明里的事件列表，不是"真的会触发"的事件列表。

于是用户挂一个 `SessionEnd` 钩子做清理、挂 `SubagentStop` 审计队友委派，
调试到最后才发现钩子从来没跑过，而没有任何一处提示过他。

两条路可选：派发这三个事件，或从契约里删掉它们。**删是错的**——
`SubagentStart`/`SubagentStop` 有天然的派发点（`_run_teammate` 的委派前后），
`SessionEnd` 也有（会话历史被清理时）。契约先于实现存在不是问题，
实现缺失却不声明才是。

这里锁住契约与实现一致：`HOOK_EVENTS` 的每一项都能在 executor 找到派发点。
"""

from __future__ import annotations

import re
from pathlib import Path

from kirara_ai.agent_runtime.hooks import HOOK_EVENTS

# parents[2] 才是仓库根：本文件在 tests/agent_runtime/ 下，parents[1] 是 tests/。
_EXECUTOR = (
    Path(__file__).resolve().parents[2] / "kirara_ai" / "agent_runtime" / "executor.py"
)


def _dispatched_events() -> set[str]:
    """从 executor 源码里取出所有被派发的事件名。

    读源码而不是 monkeypatch 跑一遍：`SubagentStart` 这类事件只在特定分支
    （队友委派）出现，靠跑一轮主流程覆盖不到，而覆盖不到的分支恰恰是缺口所在。
    """
    source = _EXECUTOR.read_text(encoding="utf-8")
    # 派发点的形态是 `self._run_hook(\n    "EventName",` 或 `self._run_hook("EventName"`。
    return set(re.findall(r'_run_hook\(\s*\n?\s*"([A-Za-z]+)"', source))


def test_every_declared_hook_event_has_a_dispatch_point():
    """声明了却不派发的事件，是一个能通过校验、能启用、永不触发的配置。"""
    missing = sorted(HOOK_EVENTS - _dispatched_events())

    assert not missing, (
        f"这些事件在 HOOK_EVENTS 里声明但 executor 从不派发：{missing}。"
        "用户照契约写的钩子会静默失效——要么补派发点，要么从契约里移除。"
    )


def test_no_event_is_dispatched_without_being_declared():
    """反向也要一致：派发一个未声明的事件，用户无从知道可以挂它。"""
    undeclared = sorted(_dispatched_events() - HOOK_EVENTS)

    assert not undeclared, (
        f"这些事件被派发但不在 HOOK_EVENTS 里：{undeclared}。"
        "声明校验会拒绝用户为它们写钩子，于是这个派发点永远没有消费者。"
    )


def test_the_probe_actually_finds_dispatch_points():
    """自检：解析确实读到了派发点，而不是拿两个空集合互相比较。

    没有这条，上面两个断言在正则写坏时会双双变成永远为真的空壳。
    """
    dispatched = _dispatched_events()

    assert len(dispatched) >= 8, f"只解析到 {len(dispatched)} 个派发点，正则可能失效"
    # 这几个是主流程上确定存在的，任何一个缺失都说明解析出了问题。
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
        assert event in dispatched, f"主流程事件 {event} 没被解析到，正则失效"
