from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentHookRuntime,
    ChannelContext,
    ResourceBinding,
    ResourceSnapshot,
)
from kirara_ai.web.auth.principal import RuntimePrincipal, runtime_principal_context


HASH = "a" * 64
CREATOR = RuntimePrincipal(subject="command-hook-test-creator", is_creator=True)


@pytest.fixture
def creator_principal():
    with runtime_principal_context(CREATOR):
        yield


def _context() -> ChannelContext:
    return ChannelContext(
        channel_type="webui",
        adapter_instance="web-main",
        account_scope="account",
        conversation_scope="conversation",
        sender_scope="sender",
    )


def _agent(*, capabilities: set[str] | None = None) -> AgentDefinition:
    return AgentDefinition(
        agent_id="command-hook-agent",
        owner_subject=CREATOR.subject,
        model_priority=("model",),
        capabilities=frozenset(capabilities or set()),
        hook_bindings=(
            ResourceBinding(
                resource_id="hook.command",
                resource_type="hook",
                version="1.0.0",
                content_sha256=HASH,
                permissions=("workflow.read", "process.execute"),
            ),
        ),
    )


def _snapshot(agent: AgentDefinition) -> ResourceSnapshot:
    return agent.snapshot()


def _command_hook(command: list[str], **extra: object) -> str:
    event = {
        "type": "command",
        "command": command,
        "required_permissions": ["process.execute"],
        **extra,
    }
    return json.dumps({"events": {"UserPromptSubmit": event}})


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_command_hook_receives_redacted_json_and_returns_structured_context(tmp_path: Path):
    output_file = tmp_path / "hook-input.json"
    script = (
        "import json, pathlib, sys; "
        f"pathlib.Path({str(output_file)!r}).write_text(sys.stdin.read(), encoding='utf-8'); "
        "print(json.dumps({'continue': True, 'systemMessage': 'hook observed', "
        "'hookSpecificOutput': {'additionalContext': 'runtime context'}}))"
    )
    runtime = AgentHookRuntime(
        resource_loader={
            "hook.command": _command_hook([sys.executable, "-c", script])
        }.__getitem__,
    )

    outcome = await runtime.run_event(
        "UserPromptSubmit",
        agent=_agent(capabilities={"process.execute"}),
        context=_context(),
        snapshot=_snapshot(_agent(capabilities={"process.execute"})),
        payload={"text": "hello", "api_token": "do-not-forward"},
    )

    assert outcome.status == "success"
    assert outcome.executed == 1
    assert outcome.additional_context == ("runtime context",)
    assert outcome.system_messages == ("hook observed",)
    received = json.loads(output_file.read_text(encoding="utf-8"))
    assert received["text"] == "hello"
    assert received["api_token"] == "[redacted]"


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_command_hook_is_denied_without_process_capability(tmp_path: Path):
    marker = tmp_path / "ran"
    script = f"import pathlib; pathlib.Path({str(marker)!r}).write_text('ran')"
    runtime = AgentHookRuntime(
        resource_loader={
            "hook.command": _command_hook([sys.executable, "-c", script])
        }.__getitem__,
    )
    agent = _agent()

    outcome = await runtime.run_event(
        "UserPromptSubmit",
        agent=agent,
        context=_context(),
        snapshot=_snapshot(agent),
        payload={},
    )

    assert outcome.status == "error"
    assert "PermissionError" in outcome.reasons
    assert not marker.exists()


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_command_hook_timeout_is_recorded_and_process_is_stopped():
    runtime = AgentHookRuntime(
        resource_loader={
            "hook.command": _command_hook(
                [sys.executable, "-c", "import time; time.sleep(5)"],
                timeout_ms=50,
            )
        }.__getitem__,
    )
    agent = _agent(capabilities={"process.execute"})

    outcome = await runtime.run_event(
        "UserPromptSubmit",
        agent=agent,
        context=_context(),
        snapshot=_snapshot(agent),
        payload={},
    )

    assert outcome.status == "timeout"
    assert outcome.executed == 0
    assert "timeout" in outcome.reasons


@pytest.mark.asyncio
@pytest.mark.usefixtures("creator_principal")
async def test_pretool_command_hook_can_deny_with_codex_permission_decision():
    command = [
        sys.executable,
        "-c",
        "print('{\"hookSpecificOutput\": {\"permissionDecision\": \"deny\", "
        "\"permissionDecisionReason\": \"policy\"}}')",
    ]
    declaration = json.dumps(
        {
            "events": {
                "PreToolUse": {
                    "type": "command",
                    "command": command,
                    "required_permissions": ["process.execute"],
                }
            }
        }
    )
    runtime = AgentHookRuntime(resource_loader={"hook.command": declaration}.__getitem__)
    agent = _agent(capabilities={"process.execute"})

    outcome = await runtime.run_event(
        "PreToolUse",
        agent=agent,
        context=_context(),
        snapshot=_snapshot(agent),
        payload={"tool_name": "write", "arguments": {}},
    )

    assert outcome.blocked is True
    assert outcome.permission_decision == "deny"
    assert outcome.permission_decision_reason == "policy"
