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

    await adapter.send_message(
        IMMessage(ChatSender.get_bot_sender(), [TextMessage("hello"), fake_image()]),
        recipient(),
        delivery_id="logical-1",
    )

    persisted = rows(database)
    assert len(persisted) == 2
    assert observed_counts == [2, 2]
    assert [item.status for item in persisted] == ["accepted", "accepted"]
    assert network_calls == ["text", "media"]


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
