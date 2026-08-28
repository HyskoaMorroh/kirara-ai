"""Hook declarations must be inspectable and dry-runnable before they go live.

A hook could previously only be validated by installing it and waiting for a real
request: a wrong event name, an invalid matcher regex, or a `command` in a place
that forbids it all surfaced on the production path. These endpoints answer
"what would this hook do?" and "would it run for this tool?" without executing a
handler or starting a process.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.agent_runtime.core import ResourceBinding
from kirara_ai.agent_runtime.hooks import AgentHookRuntime


class StubService:
    def __init__(self, declaration):
        self._declaration = declaration

    def read_entry(self, resource_id: str, version: str) -> str:
        if isinstance(self._declaration, str):
            return self._declaration
        return json.dumps(self._declaration)

    def resolve_binding(self, resource_id, resource_type, **_kwargs):
        return binding()


def binding(enabled: bool = True) -> ResourceBinding:
    return ResourceBinding(
        resource_id="hook.demo",
        resource_type="hook",
        version="1.0.0",
        content_sha256="0" * 64,
        source="local",
        permissions=("workflow.read", "process.execute"),
        enabled=enabled,
    )


def runtime(declaration) -> AgentHookRuntime:
    return AgentHookRuntime(
        resource_service=StubService(declaration),
        handlers={"noop": lambda payload: None},
    )


def declaration(**event) -> dict:
    return {"events": {"PreToolUse": {"handler": "noop", **event}}}


def test_describe_lists_the_declared_events():
    summary = runtime(declaration()).describe_binding(binding())

    assert summary["resource_id"] == "hook.demo"
    assert [item["event"] for item in summary["events"]] == ["PreToolUse"]
    assert summary["events"][0]["enabled"] is True
    assert summary["events"][0]["kind"] == "handler"


def test_describe_surfaces_the_matcher():
    summary = runtime(declaration(matcher="Bash|Write")).describe_binding(binding())

    assert summary["events"][0]["matcher"] == "Bash|Write"


def test_describe_marks_a_disabled_event():
    summary = runtime(declaration(enabled=False)).describe_binding(binding())

    assert summary["events"][0]["enabled"] is False


def test_describe_flags_a_command_hook_as_needing_process_execution():
    spec = {"events": {"PreToolUse": {"type": "command", "command": ["echo", "hi"]}}}

    summary = runtime(spec).describe_binding(binding())

    assert summary["events"][0]["requires_process_execution"] is True


def test_describe_reports_an_unsupported_event_instead_of_raising():
    summary = runtime({"events": {"NotAnEvent": {"handler": "noop"}}}).describe_binding(
        binding()
    )

    assert summary["events"][0]["error"] == "unsupported event"


def test_describe_reports_an_invalid_matcher_per_event():
    summary = runtime(declaration(matcher="(")).describe_binding(binding())

    assert "matcher" in summary["events"][0]["error"]


def test_describe_reports_a_broken_declaration_without_raising():
    summary = runtime("not json at all").describe_binding(binding())

    assert "error" in summary
    assert summary["events"] == []


def test_preview_says_a_matching_tool_would_run():
    result = runtime(declaration(matcher="Bash")).preview_event(
        binding(), "PreToolUse", tool_name="Bash"
    )

    assert result["would_run"] is True
    assert result["matcher"] == "Bash"


def test_preview_says_a_non_matching_tool_would_not_run():
    result = runtime(declaration(matcher="Bash")).preview_event(
        binding(), "PreToolUse", tool_name="Read"
    )

    assert result["would_run"] is False
    assert result["reason"] == "matcher_not_matched"


def test_preview_reports_a_disabled_binding():
    result = runtime(declaration()).preview_event(
        binding(enabled=False), "PreToolUse", tool_name="Bash"
    )

    assert result == {"would_run": False, "reason": "binding_disabled"}


def test_preview_reports_an_undeclared_event():
    result = runtime(declaration()).preview_event(
        binding(), "SessionStart", tool_name=None
    )

    assert result["would_run"] is False
    assert result["reason"] == "event_not_declared_or_disabled"


def test_preview_rejects_an_unsupported_event():
    result = runtime(declaration()).preview_event(binding(), "Nonsense")

    assert result == {"would_run": False, "reason": "unsupported_event"}


def test_preview_reports_an_invalid_declaration_rather_than_raising():
    result = runtime(declaration(matcher="(")).preview_event(
        binding(), "PreToolUse", tool_name="Bash"
    )

    assert result["would_run"] is False
    assert result["reason"] == "declaration_invalid"


def test_preview_never_executes_the_handler():
    calls: list[str] = []
    instance = AgentHookRuntime(
        resource_service=StubService(declaration()),
        handlers={"noop": lambda payload: calls.append("ran")},
    )

    instance.preview_event(binding(), "PreToolUse", tool_name="Bash")

    assert calls == []
