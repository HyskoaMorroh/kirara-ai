import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter, TelegramConfig


def _adapter(*, drop_pending_updates: bool = False) -> TelegramAdapter:
    adapter = object.__new__(TelegramAdapter)
    adapter.config = TelegramConfig(
        token="123456:super-secret-token",
        drop_pending_updates=drop_pending_updates,
    )
    adapter.bot = SimpleNamespace(get_me=AsyncMock(return_value=SimpleNamespace(id=1)))
    adapter.logger = MagicMock()
    adapter.application = SimpleNamespace(
        initialize=AsyncMock(),
        start=AsyncMock(),
        stop=AsyncMock(),
        shutdown=AsyncMock(),
        running=True,
        updater=SimpleNamespace(
            running=True,
            start_polling=AsyncMock(),
            stop=AsyncMock(),
        ),
    )
    return adapter


def test_telegram_config_keeps_pending_updates_by_default_and_redacts_token():
    config = TelegramConfig(token="123456:super-secret-token")

    assert config.drop_pending_updates is False
    assert "super-secret-token" not in repr(config)
    assert "super-secret-token" not in str(config)
    assert "redacted" in repr(config).lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("drop_pending_updates", [False, True])
async def test_start_applies_configured_pending_update_policy(drop_pending_updates: bool):
    adapter = _adapter(drop_pending_updates=drop_pending_updates)

    await adapter.start()

    adapter.application.updater.start_polling.assert_awaited_once_with(
        drop_pending_updates=drop_pending_updates
    )


@pytest.mark.asyncio
async def test_stop_closes_each_running_telegram_component():
    adapter = _adapter()

    await adapter.stop()

    adapter.application.updater.stop.assert_awaited_once_with()
    adapter.application.stop.assert_awaited_once_with()
    adapter.application.shutdown.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_stop_propagates_cancellation_without_continuing_shutdown():
    adapter = _adapter()
    adapter.application.updater.stop.side_effect = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await adapter.stop()

    adapter.application.stop.assert_not_awaited()
    adapter.application.shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_logs_and_propagates_unexpected_shutdown_failure():
    adapter = _adapter()
    adapter.application.stop.side_effect = OSError("telegram transport failed")

    with pytest.raises(OSError, match="telegram transport failed"):
        await adapter.stop()

    adapter.logger.opt.assert_called_once_with(exception=True)
    adapter.logger.opt.return_value.error.assert_called_once()
    adapter.application.shutdown.assert_not_awaited()


@pytest.mark.asyncio
async def test_stop_ignores_only_a_confirmed_already_stopped_updater_race():
    adapter = _adapter()
    adapter.application.updater.stop.side_effect = RuntimeError("not running")

    async def stop_after_race():
        adapter.application.updater.running = False
        raise RuntimeError("not running")

    adapter.application.updater.stop.side_effect = stop_after_race

    await adapter.stop()

    adapter.application.stop.assert_awaited_once_with()
    adapter.application.shutdown.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_start_command_enters_the_shared_agent_runtime():
    adapter = _adapter()
    message = SimpleNamespace()
    adapter.convert_to_message = AsyncMock(return_value=message)
    adapter.dispatcher = SimpleNamespace(dispatch=AsyncMock())
    update = SimpleNamespace(message=SimpleNamespace(reply_text=AsyncMock()))

    await adapter.command_start(update, SimpleNamespace())

    adapter.convert_to_message.assert_awaited_once_with(update)
    adapter.dispatcher.dispatch.assert_awaited_once_with(
        adapter, message, require_agent=True
    )
    update.message.reply_text.assert_not_awaited()


def test_telegram_message_filter_includes_documents():
    from kirara_ai.plugins.im_telegram_adapter.adapter import TELEGRAM_MESSAGE_FILTER

    assert "Document" in str(TELEGRAM_MESSAGE_FILTER)
