"""Hook event declarations need a matcher and a per-event switch.

Claude Code and CC Switch both let one hook declaration say *which* tools an
event applies to, and let an event be turned off without deleting the hook. This
project dispatched every bound hook on every declared event with no filter, so:

- a `PreToolUse` hook written for one dangerous tool ran on every tool call,
  paying its process-spawn cost (and its blocking power) on unrelated calls;
- temporarily disabling one event meant editing and reinstalling the resource.

The matcher is matched against the tool name in the event payload. An event with
no matcher keeps the previous "applies to everything" behavior, so existing hook
declarations are unaffected.
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime.hooks import AgentHookRuntime


def spec(runtime: AgentHookRuntime, declaration: dict, event: str, binding):
    return runtime._spec_for_event(binding, declaration, event)


class Binding:
    resource_id = "hook.demo"
    version = "1.0.0"
    content_sha256 = "0" * 64
    source = "local"
    permissions = ("workflow.read", "process.execute")
    enabled = True
    resource_type = "hook"


@pytest.fixture()
def runtime() -> AgentHookRuntime:
    return AgentHookRuntime(handlers={"noop": lambda **_: None})


def handler_event(**extra) -> dict:
    return {"events": {"PreToolUse": {"handler": "noop", **extra}}}


def test_an_event_without_a_matcher_still_applies_to_every_tool(runtime):
    parsed = spec(runtime, handler_event(), "PreToolUse", Binding())

    assert parsed is not None
    assert parsed.matches_tool("anything") is True
    assert parsed.matches_tool(None) is True


def test_a_matcher_limits_the_event_to_the_named_tools(runtime):
    parsed = spec(runtime, handler_event(matcher="Bash"), "PreToolUse", Binding())

    assert parsed.matches_tool("Bash") is True
    assert parsed.matches_tool("Read") is False


def test_a_matcher_accepts_a_list_of_tool_names(runtime):
    parsed = spec(
        runtime, handler_event(matcher=["Bash", "Write"]), "PreToolUse", Binding()
    )

    assert parsed.matches_tool("Write") is True
    assert parsed.matches_tool("Read") is False


def test_a_matcher_is_a_regular_expression_like_claude_code(runtime):
    parsed = spec(runtime, handler_event(matcher="Edit|Write"), "PreToolUse", Binding())

    assert parsed.matches_tool("Edit") is True
    assert parsed.matches_tool("Write") is True
    assert parsed.matches_tool("Read") is False


def test_a_matcher_must_match_the_whole_tool_name(runtime):
    """`Bash` must not match `BashOutput`; a partial match would over-trigger."""
    parsed = spec(runtime, handler_event(matcher="Bash"), "PreToolUse", Binding())

    assert parsed.matches_tool("BashOutput") is False


def test_an_event_with_no_tool_in_the_payload_is_skipped_when_a_matcher_exists(runtime):
    parsed = spec(runtime, handler_event(matcher="Bash"), "PreToolUse", Binding())

    # SessionStart-style events carry no tool name; a tool-scoped hook must not fire.
    assert parsed.matches_tool(None) is False


def test_an_invalid_matcher_is_rejected_at_declaration_time(runtime):
    with pytest.raises(ValueError, match="matcher"):
        spec(runtime, handler_event(matcher="("), "PreToolUse", Binding())


def test_a_matcher_of_the_wrong_type_is_rejected(runtime):
    with pytest.raises(ValueError, match="matcher"):
        spec(runtime, handler_event(matcher=123), "PreToolUse", Binding())


def test_an_event_can_be_disabled_without_removing_the_hook(runtime):
    parsed = spec(runtime, handler_event(enabled=False), "PreToolUse", Binding())

    assert parsed is None


def test_an_event_is_enabled_by_default(runtime):
    assert spec(runtime, handler_event(), "PreToolUse", Binding()) is not None


def test_an_explicit_enabled_true_is_honored(runtime):
    assert spec(runtime, handler_event(enabled=True), "PreToolUse", Binding()) is not None


def test_a_non_boolean_enabled_flag_is_rejected(runtime):
    with pytest.raises(ValueError, match="enabled"):
        spec(runtime, handler_event(enabled="yes"), "PreToolUse", Binding())
