import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from ymbotpy.errors import AuthenticationFailedError, SequenceNumberError, ServerError

from kirara_ai.database import DatabaseManager
from kirara_ai.im.message import IMMessage, ImageMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugins.im_qqbot_adapter.adapter import QQBotAdapter, QQBotConfig
from kirara_ai.plugins.im_qqbot_adapter.outbox import QQBotDelivery


def make_persistent_adapter(
    tmp_path: Path,
    *,
    send_timeout_seconds: float = 0.05,
    max_attempts: int = 2,
) -> tuple[QQBotAdapter, DatabaseManager]:
    database = DatabaseManager(
        DependencyContainer(),
        database_url=f"sqlite:///{(tmp_path / 'qq-adapter.db').as_posix()}",
    )
    database.initialize()

    adapter = object.__new__(QQBotAdapter)
    adapter.config = QQBotConfig(
        app_id="app-id",
        app_secret="app-secret",
        token="token",
        send_timeout_seconds=send_timeout_seconds,
        outbox_max_attempts=max_attempts,
        outbox_retry_delay_seconds=0,
    )
    adapter.logger = MagicMock()
    adapter.database_manager = database
    adapter.api = SimpleNamespace(
        post_c2c_message=AsyncMock(return_value={"id": "accepted"}),
        post_group_message=AsyncMock(return_value={"id": "accepted"}),
        _http=SimpleNamespace(
            request=AsyncMock(
                return_value={
                    "file_uuid": "media-id",
                    "file_info": "media-token",
                    "ttl": 60,
                }
            )
        ),
    )
    adapter._outbox = None
    adapter._outbox_resume_task = None
    adapter._mounted_route = None
    adapter._mount_path = None
    adapter._started = False
    adapter._http_open = False
    adapter.user = None
    adapter.robot = None
    return adapter, database


def c2c_recipient(
    user_id: str = "recipient-id",
    message_id: str = "incoming-message-id",
) -> ChatSender:
    return ChatSender.from_c2c_chat(
        user_id,
        "QQ user",
        metadata={"message_id": message_id},
    )


def fake_image(data: bytes = b"image-bytes") -> ImageMessage:
    image = object.__new__(ImageMessage)
    image.get_data = AsyncMock(return_value=data)
    return image


def deliveries(database: DatabaseManager, logical_id: str) -> list[QQBotDelivery]:
    with database.get_session() as session:
        return list(
            session.execute(
                select(QQBotDelivery)
                .where(QQBotDelivery.logical_delivery_id == logical_id)
                .order_by(QQBotDelivery.recipient_sequence)
            ).scalars()
        )


@pytest.mark.asyncio
async def test_all_units_are_persisted_before_network_and_msg_seq_starts_at_one(
    tmp_path: Path,
):
    adapter, database = make_persistent_adapter(tmp_path)
    observed_persisted_counts: list[int] = []

    async def accepted(**_kwargs):
        with database.get_session() as session:
            observed_persisted_counts.append(
                int(session.scalar(select(func.count(QQBotDelivery.id))) or 0)
            )
        return {"id": f"accepted-{len(observed_persisted_counts)}"}

    adapter.api.post_c2c_message = AsyncMock(side_effect=accepted)
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("hello"), fake_image()],
    )

    await adapter.send_message(message, c2c_recipient(), delivery_id="logical-1")

    rows = deliveries(database, "logical-1")
    assert len(rows) == 2
    assert observed_persisted_counts == [2, 2]
    assert [json.loads(str(item.params_json))["msg_seq"] for item in rows] == [1, 2]
    assert [item.status for item in rows] == ["accepted", "accepted"]
    assert rows[1].media_data == b"image-bytes"
    assert rows[1].media_response_json is not None


@pytest.mark.asyncio
async def test_send_timeout_is_ambiguous_and_same_logical_delivery_is_not_resent(
    tmp_path: Path,
):
    adapter, database = make_persistent_adapter(
        tmp_path,
        send_timeout_seconds=0.01,
    )
    calls = 0

    async def hangs(**_kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(1)

    adapter.api.post_c2c_message = AsyncMock(side_effect=hangs)
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("hello")],
    )

    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(message, c2c_recipient(), delivery_id="logical-timeout")
    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(message, c2c_recipient(), delivery_id="logical-timeout")

    assert calls == 1
    assert deliveries(database, "logical-timeout")[0].status == "ambiguous"


@pytest.mark.asyncio
async def test_server_error_retries_but_authentication_and_sequence_errors_are_terminal(
    tmp_path: Path,
):
    adapter, database = make_persistent_adapter(tmp_path, max_attempts=2)
    adapter.api.post_c2c_message = AsyncMock(
        side_effect=[ServerError("temporary refusal"), {"id": "accepted"}]
    )

    await adapter.send_message(
        IMMessage(ChatSender.get_bot_sender(), [TextMessage("retry")]),
        c2c_recipient(),
        delivery_id="logical-retry",
    )

    retried = deliveries(database, "logical-retry")[0]
    assert retried.status == "accepted"
    assert retried.attempt_count == 2

    for logical_id, user_id, sdk_error in (
        ("logical-auth", "recipient-auth", AuthenticationFailedError("unauthorized")),
        (
            "logical-sequence",
            "recipient-sequence",
            SequenceNumberError("duplicate sequence"),
        ),
    ):
        adapter.api.post_c2c_message = AsyncMock(side_effect=sdk_error)
        with pytest.raises(type(sdk_error)):
            await adapter.send_message(
                IMMessage(ChatSender.get_bot_sender(), [TextMessage("terminal")]),
                c2c_recipient(user_id=user_id),
                delivery_id=logical_id,
            )
        assert deliveries(database, logical_id)[0].status == "dead_letter"
        assert adapter.api.post_c2c_message.await_count == 1


@pytest.mark.asyncio
async def test_implicit_delivery_id_is_stable_across_message_instances(tmp_path: Path):
    adapter, database = make_persistent_adapter(tmp_path)
    recipient = c2c_recipient(message_id="stable-incoming-id")

    await adapter.send_message(
        IMMessage(ChatSender.get_bot_sender(), [TextMessage("same reply")]),
        recipient,
    )
    await adapter.send_message(
        IMMessage(ChatSender.get_bot_sender(), [TextMessage("same reply")]),
        recipient,
    )

    with database.get_session() as session:
        persisted = int(session.scalar(select(func.count(QQBotDelivery.id))) or 0)
    assert persisted == 1
    assert adapter.api.post_c2c_message.await_count == 1


def test_health_snapshot_exposes_outbox_counts_without_account_identifiers(
    tmp_path: Path,
):
    adapter, _database = make_persistent_adapter(tmp_path)
    adapter._started = True
    adapter.user = {
        "id": "account-id-must-not-be-exposed",
        "username": "research-bot",
    }
    adapter._ensure_outbox().enqueue(
        "health-1",
        "c2c:recipient-id",
        "post_c2c_message",
        {"openid": "recipient-id", "msg_id": "incoming", "msg_seq": 1},
    )

    payload = adapter.get_health_snapshot().model_dump()

    assert payload["adapter_started"] is True
    assert payload["outbox"]["queued"] == 1
    assert "account-id-must-not-be-exposed" not in str(payload)
