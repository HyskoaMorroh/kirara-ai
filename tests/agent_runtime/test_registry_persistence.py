from pathlib import Path

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    ChannelContext,
    ResourceBinding,
)


def make_agent(agent_id: str = "research") -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        display_name="Research assistant",
        model_priority=("primary", "backup"),
        provider_allowlist=frozenset({"provider-a"}),
        capabilities=frozenset({"chat"}),
        prompt_bindings=(
            ResourceBinding(
                resource_id="prompt-main",
                resource_type="prompt",
                version="1.0.0",
                content_sha256="a" * 64,
                source="skills.sh",
            ),
        ),
        skill_bindings=(
            ResourceBinding(
                resource_id="skill-search",
                resource_type="skill",
                version="2.0.0",
                content_sha256="b" * 64,
                permissions=("workflow.read",),
            ),
        ),
        mcp_bindings=(
            ResourceBinding(
                resource_id="context7",
                resource_type="mcp",
                version="1.0.0",
                content_sha256="c" * 64,
            ),
        ),
        mcp_allowlist=frozenset({"search"}),
    )


def make_context() -> ChannelContext:
    return ChannelContext(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="bot-1",
        conversation_scope="c2c:user-1",
        sender_scope="user-1",
    )


def test_registry_reloads_agents_default_and_all_binding_scopes(tmp_path: Path):
    registry = AgentRegistry(tmp_path)
    agent = make_agent()
    registry.register(agent)
    registry.set_default(agent.agent_id)
    registry.bind_channel("telegram", agent.agent_id)
    registry.bind_account("telegram", "telegram-main", "bot-1", agent.agent_id)
    registry.bind_session(make_context(), agent.agent_id)

    restored = AgentRegistry(tmp_path)

    assert restored.agents[agent.agent_id] == agent
    assert restored.resolve(make_context()).agent_id == agent.agent_id
    assert restored.default_agent_id == agent.agent_id
    assert restored.to_dict()["account_bindings"] == [
        {
            "channel_type": "telegram",
            "adapter_instance": "telegram-main",
            "account_scope": "bot-1",
            "agent_id": agent.agent_id,
        }
    ]


def test_registry_keeps_memory_and_disk_state_when_atomic_write_fails(tmp_path: Path, monkeypatch):
    registry = AgentRegistry(tmp_path)
    registry.register(make_agent())
    before_disk = (tmp_path / "agents" / "registry.json").read_text(encoding="utf-8")

    def fail_write(_state):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(registry, "_write_state", fail_write)

    with pytest.raises(OSError):
        registry.register(make_agent("second"))

    assert list(registry.agents) == ["research"]
    assert (tmp_path / "agents" / "registry.json").read_text(encoding="utf-8") == before_disk
    assert list(AgentRegistry(tmp_path).agents) == ["research"]


def test_registry_rejects_removing_agent_with_session_binding(tmp_path: Path):
    registry = AgentRegistry(tmp_path)
    registry.register(make_agent())
    registry.bind_session("telegram/telegram-main/bot-1/c2c:user-1/user-1", "research")

    with pytest.raises(ValueError, match="session binding"):
        registry.remove("research")

