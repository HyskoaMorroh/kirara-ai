from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    ChannelContext,
    ResourceBinding,
    SessionStore,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent


def _message(*, user_id: str = "same-user", group_id: str | None = None, bot_id: str | None = None):
    metadata = {"onebot_self_id": bot_id} if bot_id is not None else None
    if group_id is None:
        sender = ChatSender.from_c2c_chat(user_id, "Researcher", metadata=metadata)
    else:
        sender = ChatSender.from_group_chat(
            user_id,
            group_id,
            "Researcher",
            metadata=metadata,
        )
    return IMMessage(sender, [TextMessage("hello")])


def _adapter(channel_type: str, instance: str, account: str | None = None, *, app_id: str | None = None):
    config = SimpleNamespace(app_id=app_id) if app_id is not None else None
    return SimpleNamespace(
        channel_type=channel_type,
        adapter_instance=instance,
        account_scope=account,
        config=config,
    )


def _agent(agent_id: str, resource_type: str) -> AgentDefinition:
    digest = hashlib.sha256(f"{agent_id}:{resource_type}".encode("utf-8")).hexdigest()
    return AgentDefinition(
        agent_id=agent_id,
        model_priority=(f"primary-{agent_id}", f"backup-{agent_id}"),
        prompt_bindings=(
            ResourceBinding(
                resource_id=f"{agent_id}-resource",
                resource_type=resource_type,
                version="1.0.0",
                content_sha256=digest,
            ),
        )
        if resource_type == "prompt"
        else (),
        skill_bindings=(
            ResourceBinding(
                resource_id=f"{agent_id}-resource",
                resource_type=resource_type,
                version="1.0.0",
                content_sha256=digest,
            ),
        )
        if resource_type == "skill"
        else (),
        memory_bindings=(
            ResourceBinding(
                resource_id=f"{agent_id}-resource",
                resource_type=resource_type,
                version="1.0.0",
                content_sha256=digest,
            ),
        )
        if resource_type == "memory"
        else (),
        mcp_bindings=(
            ResourceBinding(
                resource_id=f"{agent_id}-resource",
                resource_type=resource_type,
                version="1.0.0",
                content_sha256=digest,
            ),
        )
        if resource_type == "mcp"
        else (),
        hook_bindings=(
            ResourceBinding(
                resource_id=f"{agent_id}-resource",
                resource_type=resource_type,
                version="1.0.0",
                content_sha256=digest,
            ),
        )
        if resource_type == "hook"
        else (),
    )


@pytest.mark.parametrize(
    ("adapter", "message", "agent_id", "resource_type", "expected_account"),
    [
        (_adapter("wecom", "wecom-main", app_id="corp-main"), _message(), "agent-wecom", "prompt", "corp-main"),
        (_adapter("qqbot", "qq-main", app_id="qq-app"), _message(), "agent-qq", "skill", "qq-app"),
        (_adapter("telegram", "telegram-main", "telegram-bot"), _message(), "agent-telegram", "memory", "telegram-bot"),
        (_adapter("onebot", "onebot-gateway", "fallback"), _message(bot_id="onebot-1"), "agent-onebot", "mcp", "onebot-1"),
        (_adapter("webui", "http-main", "web-session"), _message(), "agent-webui", "hook", "web-session"),
    ],
)
def test_all_inbound_channels_resolve_their_account_agent_and_resource(
    adapter, message, agent_id, resource_type, expected_account
):
    registry = AgentRegistry()
    agent = _agent(agent_id, resource_type)
    registry.register(agent)

    context = ChannelContext.from_message(adapter, message)
    registry.bind_account(
        context.channel_type,
        context.adapter_instance,
        context.account_scope,
        agent_id,
    )

    resolved = registry.resolve(context)

    assert context.account_scope == expected_account
    assert resolved.agent_id == agent_id
    assert resolved.model_priority == (
        f"primary-{agent_id}",
        f"backup-{agent_id}",
    )
    assert [item.resource_type for item in resolved.resource_bindings] == [resource_type]
    assert resolved.resource_bindings[0].resource_id == f"{agent_id}-resource"


def test_onebot_self_id_is_an_account_boundary_for_agent_and_session_resolution():
    registry = AgentRegistry()
    registry.register(_agent("onebot-a", "mcp"))
    registry.register(_agent("onebot-b", "mcp"))
    adapter = _adapter("onebot", "shared-gateway", "fallback")
    first = ChannelContext.from_message(adapter, _message(bot_id="bot-a"))
    second = ChannelContext.from_message(adapter, _message(bot_id="bot-b"))
    registry.bind_account("onebot", "shared-gateway", "bot-a", "onebot-a")
    registry.bind_account("onebot", "shared-gateway", "bot-b", "onebot-b")

    assert first.account_scope == "bot-a"
    assert second.account_scope == "bot-b"
    assert first.session_key != second.session_key
    assert registry.resolve(first).agent_id == "onebot-a"
    assert registry.resolve(second).agent_id == "onebot-b"


def test_group_conversation_scope_is_shared_but_sender_scope_remains_distinct():
    adapter = _adapter("wecom", "wecom-main", "corp-main")
    first = ChannelContext.from_message(adapter, _message(user_id="member-a", group_id="group-1"))
    second = ChannelContext.from_message(adapter, _message(user_id="member-b", group_id="group-1"))

    assert first.conversation_scope == second.conversation_scope == "group:group-1"
    assert first.sender_scope == "member-a"
    assert second.sender_scope == "member-b"
    assert first.session_key != second.session_key


def test_session_store_does_not_share_history_between_channel_accounts(tmp_path):
    store = SessionStore(tmp_path)
    contexts = [
        ChannelContext("telegram", "telegram-main", "bot-a", "c2c:user", "user"),
        ChannelContext("telegram", "telegram-main", "bot-b", "c2c:user", "user"),
        ChannelContext("webui", "http-main", "web", "c2c:user", "user"),
    ]

    for index, context in enumerate(contexts):
        store.save_history(
            context.session_key,
            [
                LLMChatMessage(
                    role="user",
                    content=[LLMChatTextContent(text=f"message-{index}")],
                )
            ],
            agent_id=f"agent-{index}",
        )

    for index, context in enumerate(contexts):
        history = store.load_history(context.session_key, agent_id=f"agent-{index}")
        assert [item.content[0].text for item in history] == [f"message-{index}"]

    assert store.load_history(contexts[0].session_key, agent_id="agent-1") == []
