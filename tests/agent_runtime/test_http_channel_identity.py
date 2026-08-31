"""HTTP 入口必须与其他渠道一样进入统一关系模型。

需求 10 要求 Agent 不能只服务 OneBot 或 WebUI：每个入口都要落在
「渠道身份 → Agent → 上游模型/备用链 → Prompt/Skill/Memory/MCP」这条链上。

`HttpLegacyAdapter` 此前既不声明 `channel_type` 也不声明 `adapter_type`，
于是 `ChannelContext.from_message` 退回到类名推导，得到 `"httplegacy"`——
一个不在 `SUPPORTED_CHANNEL_TYPES` 里的值。后果是两条：

1. `AgentRegistry.bind_channel("httplegacy", ...)` 被 `_normalize_channel_type`
   直接拒绝，HTTP 入口**永远拿不到**渠道级 Agent 绑定；
2. `resolve()` 查 `_channel_bindings["httplegacy"]` 必然落空，只能退到全局默认。
   而 HTTP 路由用 `require_agent=True` 调派发，没有默认 Agent 的部署会直接失败。

其他四个渠道的身份都有测试钉住（`test_channel_entrypoints.py`），
唯独 HTTP 没有——这正是它掉队的原因。
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime import ChannelContext
from kirara_ai.agent_runtime.core import (SUPPORTED_CHANNEL_TYPES,
                                          AgentDefinition, AgentRegistry)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.plugins.im_http_legacy_adapter.adapter import HttpLegacyAdapter


def _message(user_id: str = "friend-default_session") -> IMMessage:
    return IMMessage(
        ChatSender.from_c2c_chat(user_id, "Researcher"),
        [TextMessage("hello")],
    )


def _adapter(instance: str = "http-main") -> HttpLegacyAdapter:
    adapter = object.__new__(HttpLegacyAdapter)
    adapter.adapter_instance = instance
    return adapter


def _registry(tmp_path, *agent_ids: str) -> AgentRegistry:
    registry = AgentRegistry(tmp_path)
    for agent_id in agent_ids:
        registry.register(
            AgentDefinition(agent_id=agent_id, model_priority=("openai:gpt-4o",))
        )
    return registry


def test_http_channel_type_is_declared_and_supported():
    """HTTP 入口的渠道类型必须是一个受支持的值，而不是类名推导的产物。"""
    context = ChannelContext.from_message(_adapter(), _message())

    assert context.channel_type == "http"
    assert context.channel_type in SUPPORTED_CHANNEL_TYPES


def test_an_agent_can_be_bound_to_the_http_channel(tmp_path):
    """渠道级绑定必须接受 HTTP——此前这一步直接抛「不支持的渠道类型」。"""
    registry = _registry(tmp_path / "agents", "agent-http")

    registry.bind_channel("http", "agent-http")

    assert registry.relation_summary("agent-http")["channels"] == ["http"]


def test_the_http_channel_binding_is_what_resolve_selects(tmp_path):
    """绑定之后 `resolve` 必须真的选中它，而不是退回全局默认。"""
    registry = _registry(tmp_path / "agents", "agent-default", "agent-http")
    registry.set_default("agent-default")
    registry.bind_channel("http", "agent-http")

    context = ChannelContext.from_message(_adapter(), _message())

    assert registry.resolve(context).agent_id == "agent-http"


def test_account_level_binding_also_works_for_http(tmp_path):
    """账号级绑定优先于渠道级——HTTP 也要能表达这一层。"""
    registry = _registry(tmp_path / "agents", "agent-http", "agent-http-a")
    registry.bind_channel("http", "agent-http")
    context = ChannelContext.from_message(_adapter("http-a"), _message())
    registry.bind_account(
        context.channel_type,
        context.adapter_instance,
        context.account_scope,
        "agent-http-a",
    )

    assert registry.resolve(context).agent_id == "agent-http-a"


def test_two_http_instances_keep_separate_identities():
    """同一进程里的两个 HTTP 适配器实例必须是两个身份。"""
    first = ChannelContext.from_message(_adapter("http-a"), _message())
    second = ChannelContext.from_message(_adapter("http-b"), _message())

    assert first.adapter_instance != second.adapter_instance
    assert first.session_key != second.session_key


@pytest.mark.parametrize("channel", sorted(SUPPORTED_CHANNEL_TYPES))
def test_every_supported_channel_can_be_bound(tmp_path, channel: str):
    """受支持集合与可绑定集合必须一致，不能再出现只在一边的渠道。"""
    registry = _registry(tmp_path / f"agents-{channel}", f"agent-{channel}")

    registry.bind_channel(channel, f"agent-{channel}")

    assert registry.relation_summary(f"agent-{channel}")["channels"] == [channel]
