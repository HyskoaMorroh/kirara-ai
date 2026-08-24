from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kirara_ai.agent_runtime import (
    AgentDefinition,
    AgentRegistry,
    AgentRuntimeExecutor,
    ChannelContext,
    RuntimeResult,
    RuntimeStatus,
)
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import CombinedDispatchRule
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry


class _WorkflowRegistry:
    def get_workflow(self, workflow_id, container):
        return SimpleNamespace(name=workflow_id)


class _DispatchRegistry:
    def __init__(self, rules):
        self.rules = list(rules)

    def get_active_rules(self):
        return self.rules


class _Runtime:
    def __init__(self, result: RuntimeResult):
        self.result = result
        self.calls = []

    async def run(self, context, message, **options):
        self.calls.append((context, message, options))
        return self.result


def _message(text: str = "hello") -> IMMessage:
    return IMMessage(
        ChatSender.from_c2c_chat("sender-a", "Sender"),
        [TextMessage(text)],
    )


def _rule(*, agent_id=None, metadata=None) -> CombinedDispatchRule:
    return CombinedDispatchRule(
        rule_id="rule-a",
        name="Rule A",
        workflow_id="chat:normal",
        agent_id=agent_id,
        rule_groups=[],
        metadata=metadata or {},
    )


def _dispatcher(rule, runtime=None, registry=None):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(WorkflowRegistry, _WorkflowRegistry())
    container.register(DispatchRuleRegistry, _DispatchRegistry([rule]))
    if runtime is not None:
        container.register(AgentRuntimeExecutor, runtime)
    if registry is not None:
        container.register(AgentRegistry, registry)
    return WorkflowDispatcher(container)


def _agent(agent_id: str) -> AgentDefinition:
    return AgentDefinition(agent_id=agent_id, model_priority=("model-a",))


def test_dispatch_rule_persists_formal_agent_binding_and_reads_legacy_metadata():
    explicit = _rule(agent_id="research-agent")
    legacy = _rule(metadata={"agent_id": "legacy-agent"})

    assert explicit.bound_agent_id == "research-agent"
    assert explicit.model_dump()["agent_id"] == "research-agent"
    assert legacy.bound_agent_id == "legacy-agent"


@pytest.mark.asyncio
async def test_agent_rule_runs_runtime_with_channel_context_and_replies_once():
    result = RuntimeResult(status=RuntimeStatus.COMPLETED, text="runtime reply")
    runtime = _Runtime(result)
    dispatcher = _dispatcher(_rule(agent_id="research-agent"), runtime)
    adapter = SimpleNamespace(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="bot-a",
        send_message=AsyncMock(),
    )
    message = _message()

    returned = await dispatcher.dispatch(adapter, message)

    assert returned is result
    assert len(runtime.calls) == 1
    context, runtime_message, options = runtime.calls[0]
    assert isinstance(context, ChannelContext)
    assert context.session_key == "telegram/telegram-main/bot-a/c2c:sender-a/sender-a"
    assert runtime_message is message
    assert options["session_agent_id"] == "research-agent"
    adapter.send_message.assert_awaited_once()
    reply, recipient = adapter.send_message.await_args.args
    assert reply.content == "runtime reply"
    assert recipient is message.sender


@pytest.mark.asyncio
async def test_agent_rule_sends_safe_confirmation_prompt_without_executing_again():
    result = RuntimeResult(
        status=RuntimeStatus.AWAITING_CONFIRMATION,
        confirmation_id="confirm-123",
    )
    runtime = _Runtime(result)
    dispatcher = _dispatcher(_rule(metadata={"agent_id": "research-agent"}), runtime)
    adapter = SimpleNamespace(send_message=AsyncMock())

    returned = await dispatcher.dispatch(adapter, _message("publish it"))

    assert returned is result
    assert len(runtime.calls) == 1
    adapter.send_message.assert_awaited_once()
    prompt = adapter.send_message.await_args.args[0].content
    assert "confirm-123" in prompt
    assert "publish it" not in prompt


@pytest.mark.asyncio
async def test_agent_rule_raises_a_sanitized_error_when_runtime_fails():
    result = RuntimeResult(
        status=RuntimeStatus.FAILED,
        error={"type": "ProviderFailure", "message": "credential-value"},
    )
    runtime = _Runtime(result)
    dispatcher = _dispatcher(_rule(agent_id="research-agent"), runtime)
    adapter = SimpleNamespace(send_message=AsyncMock())

    with pytest.raises(RuntimeError, match="ProviderFailure") as error:
        await dispatcher.dispatch(adapter, _message())

    assert "credential-value" not in str(error.value)
    adapter.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_rule_without_agent_binding_keeps_the_workflow_execution_path(monkeypatch):
    run = AsyncMock(return_value={"workflow": "completed"})

    class _WorkflowExecutor:
        def __init__(self, container):
            self.container = container

        async def run(self):
            return await run()

    monkeypatch.setattr(
        "kirara_ai.workflow.core.dispatch.dispatcher.WorkflowExecutor",
        _WorkflowExecutor,
    )
    dispatcher = _dispatcher(_rule())
    adapter = SimpleNamespace(send_message=AsyncMock())

    returned = await dispatcher.dispatch(adapter, _message())

    assert returned == {"workflow": "completed"}
    run.assert_awaited_once()
    adapter.send_message.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("binding", "expected"),
    [
        ("session", "session-agent"),
        ("account", "account-agent"),
        ("channel", "channel-agent"),
        ("default", "default-agent"),
    ],
)
async def test_registry_binding_resolves_without_explicit_dispatch_agent(binding, expected):
    registry = AgentRegistry()
    for agent_id in {
        "session-agent",
        "account-agent",
        "channel-agent",
        "default-agent",
    }:
        registry.register(_agent(agent_id))
    adapter = SimpleNamespace(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="bot-a",
        send_message=AsyncMock(),
    )
    message = _message()
    context = ChannelContext.from_message(adapter, message)
    if binding == "session":
        registry.bind_session(context, expected)
    elif binding == "account":
        registry.bind_account(
            context.channel_type,
            context.adapter_instance,
            context.account_scope,
            expected,
        )
    elif binding == "channel":
        registry.bind_channel(context.channel_type, expected)
    else:
        registry.set_default(expected)

    runtime = _Runtime(RuntimeResult(status=RuntimeStatus.COMPLETED, text=expected))
    dispatcher = _dispatcher(_rule(), runtime, registry)

    await dispatcher.dispatch(adapter, message)

    assert runtime.calls[0][2]["session_agent_id"] == expected
    assert adapter.send_message.await_args.args[0].content == expected


@pytest.mark.asyncio
async def test_explicit_dispatch_agent_wins_over_registry_binding():
    registry = AgentRegistry()
    registry.register(_agent("bound-agent"))
    registry.register(_agent("explicit-agent"))
    registry.set_default("bound-agent")
    runtime = _Runtime(RuntimeResult(status=RuntimeStatus.COMPLETED, text="ok"))
    dispatcher = _dispatcher(_rule(agent_id="explicit-agent"), runtime, registry)
    adapter = SimpleNamespace(
        channel_type="telegram",
        adapter_instance="telegram-main",
        account_scope="bot-a",
        send_message=AsyncMock(),
    )

    await dispatcher.dispatch(adapter, _message())

    assert runtime.calls[0][2]["session_agent_id"] == "explicit-agent"


@pytest.mark.asyncio
async def test_registry_without_any_binding_keeps_the_workflow_path(monkeypatch):
    run = AsyncMock(return_value={"workflow": "completed"})

    class _WorkflowExecutor:
        def __init__(self, container):
            self.container = container

        async def run(self):
            return await run()

    monkeypatch.setattr(
        "kirara_ai.workflow.core.dispatch.dispatcher.WorkflowExecutor",
        _WorkflowExecutor,
    )
    registry = AgentRegistry()
    dispatcher = _dispatcher(_rule(), _Runtime(RuntimeResult(status=RuntimeStatus.COMPLETED)), registry)

    returned = await dispatcher.dispatch(SimpleNamespace(send_message=AsyncMock()), _message())

    assert returned == {"workflow": "completed"}
    run.assert_awaited_once()


@pytest.mark.asyncio
async def test_disabled_registry_binding_is_not_silently_downgraded():
    registry = AgentRegistry()
    registry.register(_agent("disabled-agent"))
    registry.update(AgentDefinition(agent_id="disabled-agent", model_priority=("model-a",), enabled=False))
    registry.set_default = lambda _agent_id: None  # type: ignore[method-assign]
    registry._default_agent_id = "disabled-agent"  # test-only invalid state
    runtime = _Runtime(RuntimeResult(status=RuntimeStatus.COMPLETED))
    dispatcher = _dispatcher(_rule(), runtime, registry)

    with pytest.raises(ValueError, match="disabled"):
        await dispatcher.dispatch(SimpleNamespace(send_message=AsyncMock()), _message())
