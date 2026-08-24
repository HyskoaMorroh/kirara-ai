import asyncio
from pathlib import Path

import pytest
from sqlalchemy import select

from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugins.im_onebot_adapter.outbox import (
    OneBotDelivery,
    OneBotOutboxService,
    OneBotRetryableError,
)


def make_database(tmp_path: Path) -> DatabaseManager:
    database = DatabaseManager(
        DependencyContainer(),
        database_url=f"sqlite:///{(tmp_path / 'outbox.db').as_posix()}",
    )
    database.initialize()
    return database


def row(database: DatabaseManager, delivery_id: str) -> OneBotDelivery:
    with database.get_session() as session:
        return session.execute(
            select(OneBotDelivery).where(OneBotDelivery.delivery_id == delivery_id)
        ).scalar_one()


@pytest.mark.asyncio
async def test_enqueue_uses_stable_delivery_id_and_is_idempotent(tmp_path: Path):
    database = make_database(tmp_path)
    service = OneBotOutboxService(database, lambda *_args, **_kwargs: None)

    first = service.enqueue(
        delivery_id="delivery-1",
        recipient_key="100:c2c:200",
        action="send_private_msg",
        params={"user_id": 200, "message": []},
    )
    second = service.enqueue(
        delivery_id="delivery-1",
        recipient_key="100:c2c:200",
        action="send_private_msg",
        params={"user_id": 999, "message": []},
    )

    assert first.id == second.id
    assert first.recipient_sequence == second.recipient_sequence == 1
    assert row(database, "delivery-1").status == "queued"


@pytest.mark.asyncio
async def test_same_recipient_is_ordered_but_other_recipients_can_progress(tmp_path: Path):
    database = make_database(tmp_path)
    started: list[str] = []
    first_started = asyncio.Event()
    other_started = asyncio.Event()
    release = asyncio.Event()

    async def send(_action, **params):
        delivery_id = params["delivery_id"]
        started.append(delivery_id)
        if delivery_id == "a-1":
            first_started.set()
        if delivery_id == "b-1":
            other_started.set()
        await release.wait()
        return {"message_id": len(started)}

    service = OneBotOutboxService(database, send)
    service.enqueue("a-1", "account:c2c:1", "send_private_msg", {"delivery_id": "a-1"})
    service.enqueue("a-2", "account:c2c:1", "send_private_msg", {"delivery_id": "a-2"})
    service.enqueue("b-1", "account:c2c:2", "send_private_msg", {"delivery_id": "b-1"})

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
async def test_accepted_means_upstream_accepted_but_client_receipt_is_unknown(tmp_path: Path):
    database = make_database(tmp_path)

    async def send(_action, **_params):
        return {"status": "ok", "message_id": 42}

    service = OneBotOutboxService(database, send)
    service.enqueue("accepted-1", "account:c2c:1", "send_private_msg", {})

    result = await service.deliver("accepted-1")
    saved = row(database, "accepted-1")

    assert result.status == "accepted"
    assert result.upstream_accepted is True
    assert result.client_received is None
    assert saved.response_json is not None


@pytest.mark.asyncio
async def test_timeout_becomes_ambiguous_and_is_never_resent_automatically(tmp_path: Path):
    database = make_database(tmp_path)
    send = 0

    async def timeout(_action, **_params):
        nonlocal send
        send += 1
        raise asyncio.TimeoutError()

    service = OneBotOutboxService(database, timeout)
    service.enqueue("ambiguous-1", "account:c2c:1", "send_private_msg", {})

    first = await service.deliver("ambiguous-1")
    second = await service.deliver("ambiguous-1")

    assert first.status == second.status == "ambiguous"
    assert send == 1
    assert row(database, "ambiguous-1").attempt_count == 1


@pytest.mark.asyncio
async def test_definite_transient_failure_retries_with_a_finite_limit(tmp_path: Path):
    database = make_database(tmp_path)
    calls = 0

    async def eventually_succeeds(_action, **_params):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise OneBotRetryableError("temporary upstream refusal")
        return {"message_id": 7}

    service = OneBotOutboxService(
        database,
        eventually_succeeds,
        max_attempts=3,
        retry_delay_seconds=0,
    )
    service.enqueue("retry-1", "account:c2c:1", "send_private_msg", {})

    result = await service.deliver("retry-1")

    assert result.status == "accepted"
    assert calls == 3
    assert row(database, "retry-1").attempt_count == 3


@pytest.mark.asyncio
async def test_retry_exhaustion_moves_delivery_to_dead_letter(tmp_path: Path):
    database = make_database(tmp_path)

    async def always_refused(_action, **_params):
        raise OneBotRetryableError("temporary upstream refusal")

    service = OneBotOutboxService(
        database,
        always_refused,
        max_attempts=2,
        retry_delay_seconds=0,
    )
    service.enqueue("dead-1", "account:c2c:1", "send_private_msg", {})

    result = await service.deliver("dead-1")

    assert result.status == "dead_letter"
    assert row(database, "dead-1").attempt_count == 2


@pytest.mark.asyncio
async def test_restart_marks_orphaned_sending_ambiguous_and_keeps_pending_rows(tmp_path: Path):
    database = make_database(tmp_path)
    service = OneBotOutboxService(database, lambda *_args, **_kwargs: None)
    service.enqueue("sending-1", "account:c2c:1", "send_private_msg", {})
    service.enqueue("queued-1", "account:c2c:1", "send_private_msg", {})
    service.enqueue("retry-wait-1", "account:c2c:2", "send_private_msg", {})

    with database.get_session() as session:
        session.query(OneBotDelivery).filter_by(delivery_id="sending-1").update(
            {"status": "sending", "attempt_count": 1}
        )
        session.query(OneBotDelivery).filter_by(delivery_id="retry-wait-1").update(
            {"status": "retry_wait"}
        )
        session.commit()

    recovered = OneBotOutboxService(database, lambda *_args, **_kwargs: None)
    recovered.recover_on_startup()

    assert row(database, "sending-1").status == "ambiguous"
    assert row(database, "queued-1").status == "queued"
    assert row(database, "retry-wait-1").status == "retry_wait"


@pytest.mark.asyncio
async def test_accepted_page_is_idempotent_when_delivery_is_requested_again(tmp_path: Path):
    database = make_database(tmp_path)
    calls = 0

    async def send(_action, **_params):
        nonlocal calls
        calls += 1
        return {"message_id": calls}

    service = OneBotOutboxService(database, send)
    service.enqueue("page-1", "account:c2c:1", "send_private_msg", {})

    await service.deliver("page-1")
    again = await service.deliver("page-1")

    assert again.status == "accepted"
    assert calls == 1
