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


@pytest.mark.asyncio
async def test_rate_limited_action_failure_is_retried_not_dead_lettered(tmp_path: Path):
    """限流类 `ActionFailed` 是「稍后可以」，不能直接判死。

    `ActionFailed` 混装了两类失败：参数错误、权限不足重试一万次也不会变；
    限流、上游忙等一会儿就好。此前全部走 dead_letter，于是一次群内限流就
    永久丢掉一页回复——用户收到的是不完整的答案且没有任何提示。

    用 503 而不是 1200 作为例子：503 是 HTTP 语义透传，一定发生在动作处理器
    开始之前，因此重试不会产生重复消息。1200 的语义见下面两条用例。
    """
    from aiocqhttp import ActionFailed

    database = make_database(tmp_path)
    calls = 0

    async def rate_limited_then_ok(_action, **_params):
        nonlocal calls
        calls += 1
        if calls < 3:
            raise ActionFailed({"retcode": 503, "status": "failed"})
        return {"message_id": 11}

    service = OneBotOutboxService(
        database,
        rate_limited_then_ok,
        max_attempts=3,
        retry_delay_seconds=0,
    )
    service.enqueue("throttled-1", "account:c2c:1", "send_private_msg", {})

    result = await service.deliver("throttled-1")

    assert result.status == "accepted"
    assert calls == 3


@pytest.mark.asyncio
async def test_handler_exception_retcode_is_ambiguous_not_retried(tmp_path: Path):
    """`retcode` 1200 表示动作处理器已经开始执行然后抛错，属于结果未知。

    证据来自 LLOneBot / LuckyLilliaBot 的 `BaseAction.websocketHandle`：
    payload 校验失败返回 **1400**（还没开始做），`_handle` 抛错返回 **1200**
    （已经在做了）。把 1200 当成「稍后可以」去重试，等于在一条**可能已经发出去**
    的消息上再发一次——需求 19.4 的硬约束是「不会重复发送」，
    而丢一页有 dead_letter/ambiguous 记录可查，重复发送则直接呈现给用户。

    因此风险不对称时选择保守：标记 ambiguous、不再重发、也不假装成功。
    """
    from aiocqhttp import ActionFailed

    database = make_database(tmp_path)
    calls = 0

    async def handler_threw(_action, **_params):
        nonlocal calls
        calls += 1
        raise ActionFailed({"retcode": 1200, "status": "failed"})

    service = OneBotOutboxService(
        database,
        handler_threw,
        max_attempts=5,
        retry_delay_seconds=0,
    )
    service.enqueue("handler-threw-1", "account:c2c:1", "send_private_msg", {})

    result = await service.deliver("handler-threw-1")

    assert result.status == "ambiguous"
    assert calls == 1, "结果未知的投递不得重发"

    # 同一个投递再次触发也不得真正重发（终态幂等）。
    again = await service.deliver("handler-threw-1")
    assert again.status == "ambiguous"
    assert calls == 1


@pytest.mark.asyncio
async def test_payload_rejection_retcode_is_dead_lettered_immediately(tmp_path: Path):
    """`retcode` 1400 是 payload 校验失败：同一份 payload 重试永远不会通过。

    此前 1400 在可重试集合里，于是一份格式永久错误的动作会被重试到
    `outbox_max_attempts` 用尽——纯粹是反复打扰上游，且把真正的错误
    埋在多次相同的失败日志里。
    """
    from aiocqhttp import ActionFailed

    database = make_database(tmp_path)
    calls = 0

    async def payload_rejected(_action, **_params):
        nonlocal calls
        calls += 1
        raise ActionFailed({"retcode": 1400, "status": "failed"})

    service = OneBotOutboxService(
        database,
        payload_rejected,
        max_attempts=5,
        retry_delay_seconds=0,
    )
    service.enqueue("bad-payload-1", "account:c2c:1", "send_private_msg", {})

    result = await service.deliver("bad-payload-1")

    assert result.status == "dead_letter"
    assert calls == 1, "payload 永久错误只应尝试一次"


@pytest.mark.asyncio
async def test_permanent_action_failure_is_still_dead_lettered_immediately(tmp_path: Path):
    """参数错误这类明确拒绝必须一次判死，不能反复打扰上游。"""
    from aiocqhttp import ActionFailed

    database = make_database(tmp_path)
    calls = 0

    async def permission_denied(_action, **_params):
        nonlocal calls
        calls += 1
        raise ActionFailed({"retcode": 100, "status": "failed"})

    service = OneBotOutboxService(
        database,
        permission_denied,
        max_attempts=5,
        retry_delay_seconds=0,
    )
    service.enqueue("denied-1", "account:c2c:1", "send_private_msg", {})

    result = await service.deliver("denied-1")

    assert result.status == "dead_letter"
    assert calls == 1, "永久失败只应尝试一次"


@pytest.mark.asyncio
async def test_rate_limited_failure_still_respects_the_attempt_ceiling(tmp_path: Path):
    """限流重试仍受 max_attempts 约束，不能变成无限重试。"""
    from aiocqhttp import ActionFailed

    database = make_database(tmp_path)
    calls = 0

    async def always_throttled(_action, **_params):
        nonlocal calls
        calls += 1
        raise ActionFailed({"retcode": 503, "status": "failed"})

    service = OneBotOutboxService(
        database,
        always_throttled,
        max_attempts=2,
        retry_delay_seconds=0,
    )
    service.enqueue("throttled-2", "account:c2c:1", "send_private_msg", {})

    result = await service.deliver("throttled-2")

    assert result.status == "dead_letter"
    assert calls == 2
