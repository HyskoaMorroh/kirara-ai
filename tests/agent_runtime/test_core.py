from types import SimpleNamespace

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    ChannelContext,
    ResourceBinding,
    ResourceSnapshot,
    SessionPolicy,
    effective_mcp_allowlist,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender


def make_message(*, user_id: str = "same-user", group_id: str | None = None) -> IMMessage:
    if group_id is None:
        sender = ChatSender.from_c2c_chat(user_id, "Researcher")
    else:
        sender = ChatSender.from_group_chat(user_id, group_id, "Researcher")
    return IMMessage(sender, [TextMessage("hello")])


def test_channel_context_uses_adapter_identity_and_redacts_external_values():
    adapter = SimpleNamespace(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="bot-account",
    )
    message = make_message()
    message.sender.raw_metadata["credential"] = "credential-value"

    context = ChannelContext.from_message(adapter, message)

    assert context.channel_type == "telegram"
    assert context.adapter_instance == "telegram-main"
    assert context.account_scope == "bot-account"
    assert context.conversation_scope == "c2c:same-user"
    assert context.sender_scope == "same-user"
    assert "credential-value" not in str(context.redacted())
    assert context.session_key == (
        "telegram/telegram-main/bot-account/c2c:same-user/same-user"
    )


def test_channel_context_keeps_group_and_sender_scopes_distinct():
    adapter = SimpleNamespace(channel_type="onebot", adapter_instance="onebot-a")

    context = ChannelContext.from_message(
        adapter, make_message(user_id="user-a", group_id="group-a")
    )

    assert context.conversation_scope == "group:group-a"
    assert context.sender_scope == "user-a"


def test_channel_context_uses_onebot_event_account_before_adapter_default():
    adapter = SimpleNamespace(
        channel_type="onebot",
        adapter_instance="onebot-webhook",
        account_scope="configured-account",
    )
    message = make_message()
    message.sender.raw_metadata["onebot_self_id"] = "bot-42"

    context = ChannelContext.from_message(adapter, message)

    assert context.account_scope == "bot-42"


def test_channel_context_uses_config_identifier_but_never_credentials():
    adapter = SimpleNamespace(
        channel_type="qqbot",
        adapter_instance="qq-main",
        config=SimpleNamespace(
            app_id="qq-app-42",
            app_secret="secret-value",
            token="token-value",
        ),
    )

    context = ChannelContext.from_message(adapter, make_message())

    assert context.account_scope == "qq-app-42"
    assert "secret-value" not in context.session_key
    assert "token-value" not in context.session_key


def test_agent_selection_prefers_session_then_account_then_channel_then_default():
    registry = AgentRegistry()
    registry.register(AgentDefinition(agent_id="default", model_priority=("model-a",)))
    registry.register(AgentDefinition(agent_id="channel", model_priority=("model-b",)))
    registry.register(AgentDefinition(agent_id="account", model_priority=("model-c",)))
    registry.register(AgentDefinition(agent_id="session", model_priority=("model-d",)))
    registry.bind_channel("telegram", "channel")
    registry.bind_account("telegram", "telegram-main", "bot-account", "account")

    context = ChannelContext(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="bot-account",
        conversation_scope="c2c:user-a",
        sender_scope="user-a",
    )

    assert registry.resolve(context).agent_id == "account"
    assert registry.resolve(context, session_agent_id="session").agent_id == "session"


def test_resource_snapshot_is_immutable_and_preserves_versions():
    bindings = [
        ResourceBinding(
            resource_id="prompt-main",
            resource_type="prompt",
            version="1.2.0",
            content_sha256="a" * 64,
            enabled=True,
            permissions=(),
        ),
        ResourceBinding(
            resource_id="skill-search",
            resource_type="skill",
            version="2.0.0",
            content_sha256="b" * 64,
            enabled=True,
            permissions=("workflow.read",),
        ),
    ]

    snapshot = ResourceSnapshot.create(bindings, model_id="model-a")

    assert [item.resource_id for item in snapshot.resources] == [
        "prompt-main",
        "skill-search",
    ]
    assert snapshot.model_id == "model-a"
    with pytest.raises(TypeError):
        snapshot.resources += ()


def test_resource_binding_accepts_only_fixed_or_current_version_policy():
    binding = ResourceBinding(
        resource_id="prompt",
        resource_type="prompt",
        version="1.0.0",
        content_sha256="a" * 64,
        version_policy="current",
    )

    assert binding.version_policy == "current"
    with pytest.raises(ValueError, match="version policy"):
        ResourceBinding(
            resource_id="prompt",
            resource_type="prompt",
            version="1.0.0",
            content_sha256="a" * 64,
            version_policy="floating",
        )


def test_mcp_tools_require_the_intersection_of_all_runtime_allowlists():
    assert effective_mcp_allowlist(
        agent_allowlist={"search", "write"},
        session_allowlist={"search", "write"},
        workflow_allowlist={"search"},
        connected_tools={"search", "other"},
    ) == frozenset({"search"})


def test_session_policy_can_only_narrow_agent_tools():
    policy = SessionPolicy.from_allowlists(
        agent_allowlist={"search", "write"},
        session_allowlist={"search"},
    )
    assert policy.mcp_allowlist == frozenset({"search"})
    with pytest.raises(ValueError):
        SessionPolicy.from_allowlists(
            agent_allowlist={"search"},
            session_allowlist={"search", "write"},
        )
