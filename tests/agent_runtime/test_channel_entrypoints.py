from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kirara_ai.agent_runtime import ChannelContext
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_qqbot_adapter.adapter import QQBotAdapter
from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter
from kirara_ai.plugins.im_wecom_adapter.adapter import WecomAdapter


def _message(user_id="member-1"):
    return IMMessage(
        ChatSender.from_c2c_chat(user_id, "Researcher"),
        [TextMessage("hello")],
    )


@pytest.mark.asyncio
async def test_onebot_event_is_converted_and_sent_to_shared_dispatcher():
    adapter = object.__new__(OneBotAdapter)
    adapter.adapter_instance = "onebot-main"
    adapter.logger = MagicMock()
    adapter.dispatcher = SimpleNamespace(dispatch=AsyncMock())
    event = {
        "self_id": 10001,
        "user_id": 20002,
        "message": [{"type": "text", "data": {"text": "hello"}}],
    }

    await adapter._handle_message(event)

    adapter.dispatcher.dispatch.assert_awaited_once()
    source, message = adapter.dispatcher.dispatch.await_args.args
    assert adapter.dispatcher.dispatch.await_args.kwargs == {"require_agent": True}
    context = ChannelContext.from_message(source, message)
    assert context.session_key == "onebot/onebot-main/10001/c2c:20002/20002"
    assert message.content == "hello"


@pytest.mark.asyncio
async def test_qqbot_event_handler_uses_shared_dispatcher_after_normalization():
    adapter = object.__new__(QQBotAdapter)
    adapter.adapter_instance = "qq-main"
    adapter.logger = MagicMock()
    adapter.dispatcher = SimpleNamespace(dispatch=AsyncMock())
    adapter.convert_to_message = AsyncMock(return_value=_message())
    raw_event = object()

    await adapter.on_c2c_message_create(raw_event)

    adapter.convert_to_message.assert_awaited_once_with(raw_event)
    adapter.dispatcher.dispatch.assert_awaited_once()
    source, message = adapter.dispatcher.dispatch.await_args.args
    assert adapter.dispatcher.dispatch.await_args.kwargs == {"require_agent": True}
    context = ChannelContext.from_message(source, message)
    assert context.channel_type == "qqbot"
    assert context.adapter_instance == "qq-main"


@pytest.mark.asyncio
async def test_telegram_event_handler_uses_shared_dispatcher_after_normalization():
    adapter = object.__new__(TelegramAdapter)
    adapter.adapter_instance = "telegram-main"
    adapter.dispatcher = SimpleNamespace(dispatch=AsyncMock())
    adapter.convert_to_message = AsyncMock(return_value=_message())
    update = SimpleNamespace(message=object())

    await adapter.handle_message(update, SimpleNamespace())

    adapter.convert_to_message.assert_awaited_once_with(update)
    adapter.dispatcher.dispatch.assert_awaited_once()
    source, message = adapter.dispatcher.dispatch.await_args.args
    assert adapter.dispatcher.dispatch.await_args.kwargs == {"require_agent": True}
    context = ChannelContext.from_message(source, message)
    assert context.channel_type == "telegram"
    assert context.adapter_instance == "telegram-main"


@pytest.mark.asyncio
async def test_wecom_event_normalization_produces_shared_channel_identity():
    adapter = object.__new__(WecomAdapter)
    adapter.adapter_instance = "wecom-main"
    raw_message = SimpleNamespace(
        source="member-7",
        type="text",
        content="hello",
    )

    message = await adapter.convert_to_message(raw_message)

    context = ChannelContext.from_message(adapter, message)
    assert context.channel_type == "wecom"
    assert context.adapter_instance == "wecom-main"
    assert context.account_scope == "wecom-main"
    assert message.content == "hello"


@pytest.mark.asyncio
async def test_qqbot_group_event_enters_agent_runtime_dispatch_mode():
    adapter = object.__new__(QQBotAdapter)
    adapter.adapter_instance = "qq-main"
    adapter.logger = MagicMock()
    adapter.dispatcher = SimpleNamespace(dispatch=AsyncMock())
    adapter.convert_to_message = AsyncMock(return_value=_message())

    await adapter.on_group_at_message_create(object())

    adapter.convert_to_message.assert_awaited_once()
    adapter.dispatcher.dispatch.assert_awaited_once()
    assert adapter.dispatcher.dispatch.await_args.kwargs == {"require_agent": True}


@pytest.mark.asyncio
async def test_wecom_dispatch_callback_enters_agent_runtime_dispatch_mode(monkeypatch):
    adapter = object.__new__(WecomAdapter)
    adapter.adapter_instance = "wecom-main"
    adapter.dispatcher = SimpleNamespace(dispatch=AsyncMock())
    message = _message("hello")

    async def dispatch_message():
        await adapter.dispatcher.dispatch(adapter, message, require_agent=True)

    await dispatch_message()

    adapter.dispatcher.dispatch.assert_awaited_once_with(
        adapter, message, require_agent=True
    )
