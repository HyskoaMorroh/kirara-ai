from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from kirara_ai.plugin_manager.resource_catalog import (
    OFFICE_RESEARCH_PROMPT,
    ResourceCatalogService,
    _BUILTINS,
)
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService


def test_ensure_builtins_upgrades_stale_builtin_without_losing_pinned_version(
    tmp_path: Path,
):
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    catalog = ResourceCatalogService(lifecycle)
    current_item = next(
        item for item in _BUILTINS if item["catalog_id"] == "prompt:office-research"
    )
    legacy_item = deepcopy(current_item)
    legacy_item["version"] = "1.0.0"
    legacy_item["content"] = "Legacy office prompt.\n"

    catalog._install_builtin(legacy_item)
    assert lifecycle.get_resource("prompt.office-research")["current_version"] == "1.0.0"

    catalog.ensure_builtins()

    resource = lifecycle.get_resource("prompt.office-research")
    assert resource["current_version"] == current_item["version"] == "1.0.1"
    assert [item["version"] for item in resource["versions"]] == ["1.0.0", "1.0.1"]
    assert lifecycle.read_entry("prompt.office-research", "1.0.0") == legacy_item[
        "content"
    ]
    assert lifecycle.read_entry("prompt.office-research", "1.0.1") == OFFICE_RESEARCH_PROMPT

    pinned = lifecycle.resolve_binding(
        "prompt.office-research",
        "prompt",
        version="1.0.0",
        enabled=False,
        version_policy="fixed",
    )
    assert pinned.version == "1.0.0"


@pytest.mark.parametrize("run_count", [2])
def test_ensure_builtins_is_idempotent_after_builtin_upgrade(tmp_path: Path, run_count: int):
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    catalog = ResourceCatalogService(lifecycle)

    for _ in range(run_count):
        catalog.ensure_builtins()

    resource = lifecycle.get_resource("prompt.office-research")
    assert resource["current_version"] == "1.0.1"
    assert [item["version"] for item in resource["versions"]] == ["1.0.1"]


def test_ai_debug_builtin_is_a_capability_gated_command_hook(tmp_path: Path):
    lifecycle = ResourceLifecycleService(tmp_path / "data")
    catalog = ResourceCatalogService(lifecycle)

    catalog.ensure_builtins()

    resource = lifecycle.get_resource("hook.ai-debug")
    # 不钉具体版本号：这条要断言的是「命令型 + 权限门禁」这个形态，
    # 而事件集合每次扩充都会抬版本号（见
    # `tests/plugin_manager/test_builtin_hook_event_coverage.py`）。
    # 钉死版本号会让一次正当的扩充变成这条测试红，
    # 而改一个数字让它变绿又什么都没验证。
    version = resource["current_version"]
    assert set(resource["permissions"]) == {"workflow.read", "process.execute"}
    declaration = __import__("json").loads(
        lifecycle.read_entry("hook.ai-debug", version)
    )
    for event in ("SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"):
        hook = declaration["events"][event]
        assert hook["type"] == "command"
        assert hook["command"][:3] == ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command"]
        assert hook["required_capabilities"] == ["process.execute"]
