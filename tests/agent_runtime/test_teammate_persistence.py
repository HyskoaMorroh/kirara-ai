"""Teammates 的持久化与 API 契约（需求 8）。

Agent 定义会落到 `data/agents/registry.json` 并通过 REST 暴露。队友列表如果只在
内存里，重启后委派工具就凭空消失——那是最难排查的一类「昨天还能用」。

同时钉住向后兼容：早于本特性的注册表文件没有 `teammate_agent_ids` 键，
读到它必须当成「不启用」而不是报错，否则升级即宕机。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from kirara_ai.agent_runtime.core import AgentDefinition, AgentRegistry


def definition(agent_id: str, *, teammates: tuple[str, ...] = ()) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        model_priority=("model-a",),
        teammate_agent_ids=teammates,
    )


def test_teammates_survive_a_registry_reload(tmp_path: Path):
    """队友列表必须落盘：只存在内存里等于重启后委派能力消失。"""
    registry = AgentRegistry(tmp_path)
    registry.register(definition("helper"))
    registry.register(definition("lead", teammates=("helper",)))

    reloaded = AgentRegistry(tmp_path)

    assert reloaded.get("lead").teammate_agent_ids == ("helper",)


def test_a_registry_written_before_this_feature_still_loads(tmp_path: Path):
    """旧注册表没有该键，缺省为「不启用」而不是报错——升级不能宕机。"""
    registry = AgentRegistry(tmp_path)
    registry.register(definition("solo"))

    path = tmp_path / "agents" / "registry.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    for agent in payload["agents"]:
        agent.pop("teammate_agent_ids", None)
    path.write_text(json.dumps(payload), encoding="utf-8")

    reloaded = AgentRegistry(tmp_path)

    assert reloaded.get("solo").teammate_agent_ids == ()


def test_registry_rejects_self_delegation_on_write(tmp_path: Path):
    """自委派在定义期就被拒，注册表里不可能出现这种记录。"""
    registry = AgentRegistry(tmp_path)

    with pytest.raises(ValueError, match="teammate"):
        registry.register(definition("loop", teammates=("loop",)))


def test_api_payload_exposes_and_accepts_teammates():
    """REST 层必须能读能写，否则界面上配不出来。"""
    from kirara_ai.web.api.agent import routes

    registry = AgentRegistry()
    lead = definition("lead", teammates=("helper",))
    registry.register(definition("helper"))
    registry.register(lead)

    payload = routes._agent_payload(lead, registry)
    assert payload["teammate_agent_ids"] == ["helper"]

    rebuilt = routes._agent_from_payload(
        {"agent_id": "lead", "model_priority": ["model-a"], "teammate_agent_ids": ["helper"]}
    )
    assert rebuilt.teammate_agent_ids == ("helper",)


def test_api_rejects_a_non_string_teammate_list():
    """类型错误要在边界上拒绝，而不是让它变成一个奇怪的工具名。"""
    from kirara_ai.web.api.agent import routes

    with pytest.raises(ValueError, match="teammate_agent_ids"):
        routes._agent_from_payload(
            {
                "agent_id": "lead",
                "model_priority": ["model-a"],
                "teammate_agent_ids": [{"agent_id": "helper"}],
            }
        )


def test_updating_an_agent_without_the_field_keeps_existing_teammates():
    """局部更新不得把没提到的字段清空——那会让「改个名字」顺手废掉委派。"""
    from kirara_ai.web.api.agent import routes

    existing = definition("lead", teammates=("helper",))

    updated = routes._agent_from_payload({"display_name": "主控"}, existing)

    assert updated.teammate_agent_ids == ("helper",)
    assert updated.display_name == "主控"
