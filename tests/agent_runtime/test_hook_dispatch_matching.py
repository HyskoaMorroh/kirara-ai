"""A tool-scoped hook must not run on unrelated tool calls."""

from __future__ import annotations

import json

import pytest

from kirara_ai.agent_runtime.core import (
    AgentDefinition,
    ChannelContext,
    ResourceBinding,
    ResourceSnapshot,
)
from kirara_ai.agent_runtime.hooks import AgentHookRuntime


class StubResourceService:
    """Returns one hook declaration and accepts the runtime's revalidation."""

    def __init__(self, declaration: dict):
        self.declaration = declaration

    def read_entry(self, resource_id: str, version: str) -> str:
        return json.dumps(self.declaration)

    def resolve_binding(self, resource_id, resource_type, **_kwargs):
        return binding()


def binding() -> ResourceBinding:
    return ResourceBinding(
        resource_id="hook.demo",
        resource_type="hook",
        version="1.0.0",
        content_sha256="0" * 64,
        source="local",
        permissions=("workflow.read",),
        enabled=True,
    )


def snapshot() -> ResourceSnapshot:
    return ResourceSnapshot(resources=(binding(),))


def agent() -> AgentDefinition:
    return AgentDefinition(
        agent_id="agent-1",
        model_priority=("model-a",),
        capabilities=frozenset({"workflow.read"}),
    )


def context() -> ChannelContext:
    return ChannelContext(
        channel_type="onebot",
        adapter_instance="onebot-1",
        account_scope="onebot-1",
        conversation_scope="c2c:100",
        sender_scope="user:100",
    )


def make_runtime(declaration: dict, calls: list[str]) -> AgentHookRuntime:
    def record(payload):
        calls.append(str(payload.get("tool_name")))
        return None

    return AgentHookRuntime(
        resource_service=StubResourceService(declaration),
        handlers={"record": record},
    )


def declaration(**event_extra) -> dict:
    return {"events": {"PreToolUse": {"handler": "record", **event_extra}}}


@pytest.mark.asyncio
async def test_a_matching_tool_runs_the_hook():
    calls: list[str] = []
    runtime = make_runtime(declaration(matcher="Bash"), calls)

    outcome = await runtime.run_event(
        "PreToolUse",
        agent=agent(),
        context=context(),
        snapshot=snapshot(),
        payload={"tool_name": "Bash"},
    )

    assert outcome.executed == 1
    assert calls == ["Bash"]


@pytest.mark.asyncio
async def test_a_non_matching_tool_skips_the_hook_entirely():
    calls: list[str] = []
    runtime = make_runtime(declaration(matcher="Bash"), calls)

    outcome = await runtime.run_event(
        "PreToolUse",
        agent=agent(),
        context=context(),
        snapshot=snapshot(),
        payload={"tool_name": "Read"},
    )

    assert outcome.executed == 0
    assert calls == []


@pytest.mark.asyncio
async def test_a_hook_without_a_matcher_keeps_running_on_every_tool():
    calls: list[str] = []
    runtime = make_runtime(declaration(), calls)

    outcome = await runtime.run_event(
        "PreToolUse",
        agent=agent(),
        context=context(),
        snapshot=snapshot(),
        payload={"tool_name": "AnyTool"},
    )

    assert outcome.executed == 1
    assert calls == ["AnyTool"]


@pytest.mark.asyncio
async def test_a_disabled_event_does_not_run():
    calls: list[str] = []
    runtime = make_runtime(declaration(enabled=False), calls)

    outcome = await runtime.run_event(
        "PreToolUse",
        agent=agent(),
        context=context(),
        snapshot=snapshot(),
        payload={"tool_name": "Bash"},
    )

    assert outcome.executed == 0
    assert calls == []


@pytest.mark.asyncio
async def test_a_skipped_hook_cannot_block_the_tool_call():
    """A denying hook that does not match must not deny anything."""
    calls: list[str] = []
    runtime = make_runtime(declaration(matcher="Bash", deny=True), calls)

    outcome = await runtime.run_event(
        "PreToolUse",
        agent=agent(),
        context=context(),
        snapshot=snapshot(),
        payload={"tool_name": "Read"},
    )

    assert outcome.blocked is False
