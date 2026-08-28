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
from kirara_ai.plugins.im_wecom_adapter.adapter import WecomAdapter, WecomConfig
from kirara_ai.plugins.im_wecom_adapter.delegates import split_long_message
from kirara_ai.plugins.im_wecom_adapter.outbox import WecomDelivery


def make_adapter(tmp_path: Path, *, timeout: float = 0.05) -> tuple[WecomAdapter, DatabaseManager]:
    database = DatabaseManager(
        DependencyContainer(),
        database_url=f"sqlite:///{(tmp_path / 'wecom-adapter.db').as_posix()}",
    )
    database.initialize()

    adapter = object.__new__(WecomAdapter)
    adapter.config = WecomConfig(
        app_id="app-id",
        secret="secret",
        token="token",
        encoding_aes_key="encoding-key",
        send_timeout_seconds=timeout,
    )
    adapter.adapter_instance = "wecom-main"
    adapter.logger = MagicMock()
    adapter.database_manager = database
    adapter.api_delegate = SimpleNamespace(
        send_text=AsyncMock(return_value={"errcode": 0}),
        send_media=AsyncMock(return_value={"errcode": 0}),
    )
    adapter._outbox = None
    adapter.reply_tasks = {}
    adapter._background_tasks = set()
    adapter.is_running = False
    return adapter, database


def rows(database: DatabaseManager) -> list[WecomDelivery]:
    with database.get_session() as session:
        return list(
            session.execute(
                select(WecomDelivery).order_by(WecomDelivery.created_at, WecomDelivery.id)
            ).scalars()
        )


def recipient(user_id: str = "member-1") -> ChatSender:
    return ChatSender.from_c2c_chat(user_id, "WeCom member")


def fake_image(data: bytes = b"image-bytes") -> ImageMessage:
    image = object.__new__(ImageMessage)
    image.get_data = AsyncMock(return_value=data)
    return image


@pytest.mark.asyncio
async def test_all_units_are_persisted_before_wecom_network_calls(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    observed_counts: list[int] = []
    network_calls: list[str] = []

    async def send_text(app_id: str, user_id: str, text: str):
        network_calls.append("text")
        with database.get_session() as session:
            observed_counts.append(int(session.scalar(select(func.count(WecomDelivery.id))) or 0)
            )
        assert (app_id, user_id, text) == ("app-id", "member-1", "hello")
        return {"errcode": 0}

    async def send_media(app_id: str, user_id: str, media_type: str, media_bytes):
        network_calls.append("media")
        with database.get_session() as session:
            observed_counts.append(int(session.scalar(select(func.count(WecomDelivery.id))) or 0)
            )
        assert (app_id, user_id, media_type) == ("app-id", "member-1", "image")
        assert media_bytes.read() == b"image-bytes"
        return {"errcode": 0}

    adapter.api_delegate.send_text = send_text
    adapter.api_delegate.send_media = send_media

    message = IMMessage(
        ChatSender.get_bot_sender(),
        [TextMessage("hello"), fake_image()],
    )
    await adapter.send_message(
        message,
        recipient(),
        delivery_id="logical-1",
    )

    persisted = rows(database)
    assert len(persisted) == 2
    assert observed_counts == [2, 2]
    assert [item.status for item in persisted] == ["accepted", "accepted"]
    assert network_calls == ["text", "media"]
    assert [event.stage for event in message.delivery_timeline] == [
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_succeeded",
    ]
    assert message.delivery_timeline[-1].details["retry_count"] == 0


@pytest.mark.asyncio
async def test_implicit_delivery_id_is_stable_across_message_instances(tmp_path: Path):
    adapter, database = make_adapter(tmp_path, timeout=1)

    await adapter.send_message(
        IMMessage(ChatSender.get_bot_sender(), [TextMessage("same reply")]),
        recipient(),
    )
    await adapter.send_message(
        IMMessage(ChatSender.get_bot_sender(), [TextMessage("same reply")]),
        recipient(),
    )

    assert len(rows(database)) == 1
    assert adapter.api_delegate.send_text.await_count == 1


@pytest.mark.asyncio
async def test_wecom_timeout_is_ambiguous_and_is_not_resent(tmp_path: Path):
    adapter, database = make_adapter(tmp_path, timeout=0.01)
    calls = 0

    async def hangs(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)

    adapter.api_delegate.send_text = hangs
    message = IMMessage(ChatSender.get_bot_sender(), [TextMessage("hello")])

    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(message, recipient(), delivery_id="logical-timeout")
    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(message, recipient(), delivery_id="logical-timeout")

    assert calls == 1
    assert rows(database)[0].status == "ambiguous"


@pytest.mark.asyncio
async def test_wecom_48001_without_callback_is_reported_to_caller(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    adapter.api_delegate.send_text = AsyncMock(
        side_effect=RuntimeError("Error code: 48001, api unauthorized")
    )
    message = IMMessage(ChatSender.get_bot_sender(), [TextMessage("hello")])

    with pytest.raises(RuntimeError, match="48001"):
        await adapter.send_message(
            message,
            recipient(),
            delivery_id="logical-no-passive-callback",
        )

    assert rows(database)[0].status == "dead_letter"
    assert [event.stage for event in message.delivery_timeline] == [
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_failed",
    ]


@pytest.mark.asyncio
async def test_wecom_48001_with_callback_records_passive_reply_success(tmp_path: Path):
    adapter, _database = make_adapter(tmp_path)
    adapter.api_delegate.send_text = AsyncMock(
        side_effect=RuntimeError("Error code: 48001, api unauthorized")
    )
    reply_task = asyncio.get_running_loop().create_future()
    adapter.reply_tasks["incoming-1"] = reply_task
    callback_recipient = ChatSender.from_c2c_chat(
        "member-1",
        "WeCom member",
        metadata={"reply": "incoming-1"},
    )
    message = IMMessage(ChatSender.get_bot_sender(), [TextMessage("hello")])

    await adapter.send_message(
        message,
        callback_recipient,
        delivery_id="logical-passive-fallback",
    )

    assert reply_task.result() == "hello"
    assert [event.stage for event in message.delivery_timeline] == [
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_succeeded",
    ]
    assert message.delivery_timeline[-1].details["delivery_mode"] == "passive_reply"


@pytest.mark.asyncio
async def test_wecom_active_success_does_not_duplicate_callback_reply(tmp_path: Path):
    adapter, _database = make_adapter(tmp_path)
    assert adapter._ensure_outbox().claim_inbound("incoming-active", "member-1")
    reply_task = asyncio.get_running_loop().create_future()
    adapter.reply_tasks["incoming-active"] = reply_task
    callback_recipient = ChatSender.from_c2c_chat(
        "member-1",
        "WeCom member",
        metadata={"reply": "incoming-active"},
    )
    message = IMMessage(ChatSender.get_bot_sender(), [TextMessage("hello")])

    await adapter.send_message(
        message,
        callback_recipient,
        delivery_id="logical-active-success",
    )

    assert reply_task.result() is None
    assert message.delivery_timeline[-1].stage == "send_succeeded"
    assert message.delivery_timeline[-1].details["delivery_mode"] == "active"


@pytest.mark.asyncio
async def test_wecom_ambiguous_active_send_does_not_fall_back_to_passive_reply(
    tmp_path: Path,
):
    adapter, _database = make_adapter(tmp_path, timeout=0.01)
    assert adapter._ensure_outbox().claim_inbound("incoming-timeout", "member-1")

    async def hangs(*_args, **_kwargs):
        await asyncio.sleep(1)

    adapter.api_delegate.send_text = hangs
    reply_task = asyncio.get_running_loop().create_future()
    adapter.reply_tasks["incoming-timeout"] = reply_task
    callback_recipient = ChatSender.from_c2c_chat(
        "member-1",
        "WeCom member",
        metadata={"reply": "incoming-timeout"},
    )
    message = IMMessage(ChatSender.get_bot_sender(), [TextMessage("hello")])

    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(
            message,
            callback_recipient,
            delivery_id="logical-ambiguous-callback",
        )

    assert reply_task.done() is False
    assert message.delivery_timeline[-1].stage == "send_failed"


@pytest.mark.asyncio
async def test_stop_cancels_and_waits_for_background_dispatch_tasks(tmp_path: Path):
    adapter, _database = make_adapter(tmp_path)
    adapter.config.host = None
    adapter._stop_standalone_server = AsyncMock()
    task = asyncio.create_task(asyncio.sleep(60))
    adapter._background_tasks.add(task)
    adapter.is_running = True

    await adapter.stop()

    assert task.done()
    assert adapter._background_tasks == set()
    assert adapter.is_running is False


def test_split_long_message_bounds_utf8_long_single_line_with_page_labels():
    pages = split_long_message("中文🙂" * 300, max_length=180)

    assert len(pages) > 1
    assert all(len(page.encode("utf-8")) <= 180 for page in pages)
    assert pages[0].startswith(f"第 1 页 / 共 {len(pages)} 页\n")
    assert pages[-1].startswith(
        f"第 {len(pages)} 页 / 共 {len(pages)} 页\n"
    )


def test_split_long_message_bounds_long_code_line_and_keeps_markers():
    pages = split_long_message(
        "［代码 text］\n" + ("x" * 1000) + "\n［/代码］",
        max_length=180,
    )

    assert len(pages) > 1
    assert all(len(page.encode("utf-8")) <= 180 for page in pages)
    assert all("［代码 text］" in page and "［/代码］" in page for page in pages)
