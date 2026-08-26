import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugins.im_qqbot_adapter.outbox import (
    QQBotDelivery,
    QQBotOutboxService,
    QQBotRetryableError,
)


def make_database(tmp_path: Path) -> DatabaseManager:
    database = DatabaseManager(
        DependencyContainer(),
        database_url=f"sqlite:///{(tmp_path / 'qq-outbox.db').as_posix()}",
    )
    database.initialize()
    return database


def row(database: DatabaseManager, delivery_id: str) -> QQBotDelivery:
    with database.get_session() as session:
        return session.execute(
            select(QQBotDelivery).where(QQBotDelivery.delivery_id == delivery_id)
        ).scalar_one()


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_and_sequences_per_recipient(tmp_path: Path):
    database = make_database(tmp_path)
    service = QQBotOutboxService(database, lambda *_args, **_kwargs: None)

    first = service.enqueue(
        "qq-delivery-1",
        "c2c:user-1",
        "message",
        {"msg_id": "incoming-1", "msg_seq": 1, "content": "hello"},
    )
    second = service.enqueue(
        "qq-delivery-1",
        "c2c:user-1",
        "message",
        {"msg_id": "different", "msg_seq": 99, "content": "changed"},
    )
    third = service.enqueue(
        "qq-delivery-2",
        "c2c:user-1",
        "message",
        {"msg_id": "incoming-1", "msg_seq": 2, "content": "world"},
    )

    assert first.id == second.id
    assert first.recipient_sequence == 1
    assert third.recipient_sequence == 2
    assert row(database, "qq-delivery-1").status == "queued"


@pytest.mark.asyncio
async def test_same_recipient_is_ordered_other_recipients_can_progress(tmp_path: Path):
    database = make_database(tmp_path)
    started: list[str] = []
    first_started = asyncio.Event()
    other_started = asyncio.Event()
    release = asyncio.Event()

    async def send(params):
        delivery_id = params["delivery_id"]
        started.append(delivery_id)
        if delivery_id == "a-1":
            first_started.set()
        if delivery_id == "b-1":
            other_started.set()
        await release.wait()
        return {"id": delivery_id}

    service = QQBotOutboxService(database, send)
    service.enqueue("a-1", "c2c:user-a", "message", {"delivery_id": "a-1"})
    service.enqueue("a-2", "c2c:user-a", "message", {"delivery_id": "a-2"})
    service.enqueue("b-1", "c2c:user-b", "message", {"delivery_id": "b-1"})

    tasks = [
        asyncio.create_task(service.deliver("a-1")),
        asyncio.create_task(service.deliver("a-2")),
        asyncio.create_task(service.deliver("b-1")),
    ]
    await asyncio.wait_for(first_started.wait(), timeout=1)
    await asyncio.wait_for(other_started.wait(), timeout=1)
    assert started[:2] == ["a-1", "b-1"]
    release.set()
    results = await asyncio.gather(*tasks)

    assert [result.status for result in results] == ["accepted", "accepted", "accepted"]
    assert started == ["a-1", "b-1", "a-2"]


@pytest.mark.asyncio
async def test_timeout_is_ambiguous_and_never_resent_automatically(tmp_path: Path):
    database = make_database(tmp_path)
    calls = 0

    async def timeout(_params):
        nonlocal calls
        calls += 1
        raise asyncio.TimeoutError()

    service = QQBotOutboxService(database, timeout)
    service.enqueue("ambiguous-1", "c2c:user-1", "message", {})

    first = await service.deliver("ambiguous-1")
    second = await service.deliver("ambiguous-1")

    assert first.status == second.status == "ambiguous"
    assert calls == 1


@pytest.mark.asyncio
async def test_explicit_server_refusal_retries_but_sequence_error_is_terminal(tmp_path: Path):
    database = make_database(tmp_path)
    calls = 0

    async def eventually_succeeds(_params):
        nonlocal calls
        calls += 1
        if calls < 2:
            raise QQBotRetryableError("temporary QQ server refusal")
        return {"id": "accepted"}

    service = QQBotOutboxService(
        database, eventually_succeeds, max_attempts=2, retry_delay_seconds=0
    )
    service.enqueue("retry-1", "c2c:user-1", "message", {})
    result = await service.deliver("retry-1")
    assert result.status == "accepted"
    assert calls == 2

    async def duplicate(_params):
        raise ValueError("same msg_id + msg_seq")

    duplicate_service = QQBotOutboxService(database, duplicate)
    duplicate_service.enqueue("duplicate-1", "c2c:user-1", "message", {})
    duplicate_result = await duplicate_service.deliver("duplicate-1")
    assert duplicate_result.status == "dead_letter"


@pytest.mark.asyncio
async def test_restart_quarantines_sending_but_resumes_queued_and_uploaded_media(
    tmp_path: Path,
):
    database = make_database(tmp_path)
    service = QQBotOutboxService(database, lambda *_args, **_kwargs: None)
    service.enqueue("sending-1", "c2c:user-1", "message", {})
    service.enqueue("queued-1", "c2c:user-1", "message", {})
    service.enqueue(
        "uploaded-1",
        "c2c:user-2",
        "media",
        {"msg_id": "incoming", "msg_seq": 1},
        media_file_type=1,
        media_data=b"image-bytes",
    )

    with database.get_session() as session:
        session.query(QQBotDelivery).filter_by(delivery_id="sending-1").update(
            {"status": "sending", "attempt_count": 1}
        )
        session.query(QQBotDelivery).filter_by(delivery_id="uploaded-1").update(
            {"status": "uploaded", "media_response_json": '{"file_info":"ok"}'}
        )
        session.commit()

    recovered = QQBotOutboxService(database, lambda *_args, **_kwargs: None)
    assert recovered.recover_on_startup() == 1
    assert row(database, "sending-1").status == "ambiguous"
    assert row(database, "queued-1").status == "queued"
    assert row(database, "uploaded-1").status == "uploaded"


@pytest.mark.asyncio
async def test_media_upload_is_checkpointed_before_message_send(tmp_path: Path):
    database = make_database(tmp_path)
    uploads = 0
    sends = 0

    async def upload(params):
        nonlocal uploads
        uploads += 1
        assert params["file_data"] == b"image-bytes"
        return {"file_info": "media-token"}

    async def send(params):
        nonlocal sends
        sends += 1
        assert params["media"] == {"file_info": "media-token"}
        return {"id": "accepted"}

    service = QQBotOutboxService(
        database,
        send,
        media_uploader=upload,
        retry_delay_seconds=0,
    )
    service.enqueue(
        "media-1",
        "c2c:user-1",
        "media",
        {"msg_id": "incoming", "msg_seq": 1},
        media_file_type=1,
        media_data=b"image-bytes",
    )

    result = await service.deliver("media-1")

    assert result.status == "accepted"
    assert uploads == sends == 1
    assert row(database, "media-1").status == "accepted"

