import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select

from kirara_ai.database import DatabaseManager
from kirara_ai.im.message import IMMessage, ImageMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugins.im_telegram_adapter.adapter import (
    TelegramAdapter,
    TelegramConfig,
    split_telegram_message,
)
from kirara_ai.plugins.im_telegram_adapter.outbox import (
    TelegramDelivery,
    TelegramInboundReceipt,
)


def make_adapter(
    tmp_path: Path,
    *,
    timeout: float = 0.05,
) -> tuple[TelegramAdapter, DatabaseManager]:
    database = DatabaseManager(
        DependencyContainer(),
        database_url=f"sqlite:///{(tmp_path / 'telegram-adapter.db').as_posix()}",
    )
    database.initialize()

    bot = SimpleNamespace(
        get_me=AsyncMock(return_value=SimpleNamespace(id=1, username="research_bot")),
        send_chat_action=AsyncMock(return_value=True),
        send_message=AsyncMock(return_value={"message_id": 1}),
        send_photo=AsyncMock(return_value={"message_id": 2}),
        send_voice=AsyncMock(return_value={"message_id": 3}),
        send_video=AsyncMock(return_value={"message_id": 4}),
        send_document=AsyncMock(return_value={"message_id": 5}),
    )
    adapter = object.__new__(TelegramAdapter)
    adapter.config = TelegramConfig(
        token="123456:secret-token",
        send_timeout_seconds=timeout,
        outbox_max_attempts=2,
        outbox_retry_delay_seconds=0,
    )
    adapter.adapter_instance = "telegram-main"
    adapter.logger = MagicMock()
    adapter.database_manager = database
    adapter.bot = bot
    adapter.application = SimpleNamespace(
        bot=bot,
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
    adapter.dispatcher = SimpleNamespace(dispatch=AsyncMock())
    adapter.me = SimpleNamespace(id=1, username="research_bot")
    adapter._outbox = None
    adapter._recovery_task = None
    return adapter, database


def recipient(user_id: str = "member-1") -> ChatSender:
    return ChatSender.from_c2c_chat(user_id, "Telegram member")


def fake_image(data: bytes = b"image-bytes") -> ImageMessage:
    image = object.__new__(ImageMessage)
    image.get_data = AsyncMock(return_value=data)
    return image


def deliveries(database: DatabaseManager) -> list[TelegramDelivery]:
    with database.get_session() as session:
        return list(
            session.execute(
                select(TelegramDelivery).order_by(
                    TelegramDelivery.recipient_sequence
                )
            ).scalars()
        )


@pytest.mark.asyncio
async def test_all_telegram_units_are_persisted_before_ordered_network_calls(
    tmp_path: Path,
):
    adapter, database = make_adapter(tmp_path)
    observed_counts: list[int] = []
    network_calls: list[str] = []

    async def send_message(**_kwargs):
        network_calls.append("text")
        with database.get_session() as session:
            observed_counts.append(
                int(session.scalar(select(func.count(TelegramDelivery.id))) or 0)
            )
        return {"message_id": 1}

    async def send_photo(**_kwargs):
        network_calls.append("photo")
        with database.get_session() as session:
            observed_counts.append(
                int(session.scalar(select(func.count(TelegramDelivery.id))) or 0)
            )
        return {"message_id": 2}

    adapter.application.bot.send_message = AsyncMock(side_effect=send_message)
    adapter.application.bot.send_photo = AsyncMock(side_effect=send_photo)

    message = IMMessage(
        ChatSender.get_bot_sender(),
        [TextMessage("hello"), fake_image()],
    )
    await adapter.send_message(
        message,
        recipient(),
        delivery_id="logical-1",
    )

    rows = deliveries(database)
    assert observed_counts == [2, 2]
    assert network_calls == ["text", "photo"]
    assert [item.page_index for item in rows] == [0, 1]
    assert [item.page_count for item in rows] == [2, 2]
    assert [item.status for item in rows] == ["accepted", "accepted"]
    assert [event.stage for event in message.delivery_timeline] == [
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_succeeded",
    ]
    assert message.delivery_timeline[-1].details["retry_count"] == 0


@pytest.mark.asyncio
async def test_telegram_timeout_is_ambiguous_and_same_delivery_is_not_resent(
    tmp_path: Path,
):
    adapter, database = make_adapter(tmp_path, timeout=0.01)
    calls = 0

    async def hangs(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)

    adapter.application.bot.send_message = AsyncMock(side_effect=hangs)
    message = IMMessage(
        ChatSender.get_bot_sender(),
        [TextMessage("hello")],
    )

    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(
            message,
            recipient(),
            delivery_id="logical-timeout",
        )
    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(
            message,
            recipient(),
            delivery_id="logical-timeout",
        )

    assert calls == 1
    assert deliveries(database)[0].status == "ambiguous"


@pytest.mark.asyncio
async def test_duplicate_telegram_update_is_dispatched_once(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    converted = IMMessage(
        recipient(),
        [TextMessage("question")],
    )
    adapter.convert_to_message = AsyncMock(return_value=converted)
    update = SimpleNamespace(
        update_id=42,
        message=SimpleNamespace(
            chat_id=123,
            to_dict=lambda: {"message_id": 9, "chat": {"id": 123}},
            reply_text=AsyncMock(),
        ),
        to_dict=lambda: {
            "update_id": 42,
            "message": {"message_id": 9, "chat": {"id": 123}},
        },
    )

    await adapter.handle_message(update, SimpleNamespace())
    await adapter.handle_message(update, SimpleNamespace())

    adapter.dispatcher.dispatch.assert_awaited_once_with(
        adapter, converted, require_agent=True
    )
    with database.get_session() as session:
        receipt = session.execute(select(TelegramInboundReceipt)).scalar_one()
        assert receipt.update_id == "42"
        assert receipt.status == "completed"
        assert receipt.payload_json is None


@pytest.mark.asyncio
async def test_start_recovers_persisted_inbound_and_outbound_work(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    outbox = adapter._ensure_outbox()
    assert outbox.claim_inbound(
        77,
        "chat:123",
        payload={
            "update_id": 77,
            "message": {"message_id": 10, "chat": {"id": 123}},
        },
    )
    outbox.enqueue(
        "queued-1",
        "telegram-main:c2c:123",
        "text",
        {
            "_action": "text",
            "chat_id": "123",
            "text": "queued reply",
            "parse_mode": "MarkdownV2",
        },
        page_index=0,
        page_count=1,
    )
    recovered_update = SimpleNamespace(
        update_id=77,
        message=SimpleNamespace(chat_id=123, reply_text=AsyncMock()),
    )
    adapter._deserialize_update = MagicMock(return_value=recovered_update)
    adapter.convert_to_message = AsyncMock(
        return_value=IMMessage(recipient("123"), [TextMessage("recovered")])
    )

    await adapter.start()
    assert adapter._recovery_task is not None
    await adapter._recovery_task

    adapter.dispatcher.dispatch.assert_awaited_once()
    adapter.application.bot.send_message.assert_awaited_once_with(
        chat_id="123",
        text="queued reply",
        parse_mode="MarkdownV2",
    )
    with database.get_session() as session:
        receipt = session.execute(select(TelegramInboundReceipt)).scalar_one()
        assert receipt.status == "completed"
        assert receipt.payload_json is None


def test_split_telegram_message_labels_pages_and_bounds_long_single_line():
    pages = split_telegram_message("中文🙂" * 200, max_length=180)

    assert len(pages) > 1
    assert all(len(page) <= 180 for page in pages)
    assert pages[0].startswith(f"第 1 页 / 共 {len(pages)} 页\n")
    assert pages[-1].startswith(
        f"第 {len(pages)} 页 / 共 {len(pages)} 页\n"
    )


def test_split_telegram_message_bounds_long_code_line_and_keeps_fences():
    pages = split_telegram_message(
        "```text\n" + ("x" * 1000) + "\n```",
        max_length=180,
    )

    assert len(pages) > 1
    assert all(len(page) <= 180 for page in pages)
    assert all(page.count("```") == 2 for page in pages)
