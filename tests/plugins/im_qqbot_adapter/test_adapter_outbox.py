import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import func, select
from ymbotpy.errors import AuthenticationFailedError, SequenceNumberError, ServerError

from kirara_ai.database import DatabaseManager
from kirara_ai.im.message import FileMessage, IMMessage, ImageMessage, TextMessage
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


def fake_file(data: bytes = b"file-bytes") -> FileMessage:
    file = object.__new__(FileMessage)
    file.get_data = AsyncMock(return_value=data)
    return file


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


@pytest.mark.asyncio
async def test_qqbot_renders_math_and_paginates_utf8_text_before_persisting(
    tmp_path: Path,
):
    adapter, database = make_persistent_adapter(tmp_path)
    source = "温度 $T \\to 0$，面积 $a \\times b$。" + ("中文🙂" * 1500)

    await adapter.send_message(
        IMMessage(ChatSender.get_bot_sender(), [TextMessage(source)]),
        c2c_recipient(),
        delivery_id="logical-pages",
    )

    persisted = deliveries(database, "logical-pages")
    contents = [json.loads(str(item.params_json))["content"] for item in persisted]
    assert len(contents) > 1
    assert all(len(content.encode("utf-8")) <= 3800 for content in contents)
    assert contents[0].startswith(f"第 1 页 / 共 {len(contents)} 页\n")
    assert contents[-1].startswith(
        f"第 {len(contents)} 页 / 共 {len(contents)} 页\n"
    )
    assert all("\\to" not in content and "$" not in content for content in contents)
    assert "→" in "".join(contents) and "×" in "".join(contents)
    assert [item.status for item in persisted] == ["accepted"] * len(persisted)


@pytest.mark.asyncio
async def test_qqbot_keeps_text_image_file_and_following_text_in_order(
    tmp_path: Path,
):
    adapter, database = make_persistent_adapter(tmp_path)
    message = IMMessage(
        ChatSender.get_bot_sender(),
        [
            TextMessage("before"),
            fake_image(),
            fake_file(),
            TextMessage("after"),
        ],
    )

    await adapter.send_message(message, c2c_recipient(), delivery_id="logical-mixed")

    persisted = deliveries(database, "logical-mixed")
    payloads = [json.loads(str(item.params_json)) for item in persisted]
    assert len(persisted) == 4
    assert [payload.get("content") for payload in payloads] == [
        "before",
        None,
        None,
        "after",
    ]
    assert [item.media_file_type for item in persisted] == [None, 1, 4, None]
    assert [payload["msg_seq"] for payload in payloads] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_qqbot_partial_page_timeout_does_not_resend_accepted_page(
    tmp_path: Path,
):
    adapter, database = make_persistent_adapter(tmp_path, send_timeout_seconds=0.01)
    calls = 0

    async def first_accepts_second_times_out(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {"id": "accepted"}
        await asyncio.sleep(1)

    adapter.api.post_c2c_message = AsyncMock(side_effect=first_accepts_second_times_out)
    message = IMMessage(
        ChatSender.get_bot_sender(),
        [TextMessage("内容🙂" * 3000)],
    )

    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(
            message,
            c2c_recipient(),
            delivery_id="logical-partial",
        )
    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(
            message,
            c2c_recipient(),
            delivery_id="logical-partial",
        )

    persisted = deliveries(database, "logical-partial")
    assert len(persisted) > 2
    assert persisted[0].status == "accepted"
    assert persisted[1].status == "ambiguous"
    assert all(item.status == "queued" for item in persisted[2:])
    assert calls == 2


@pytest.mark.asyncio
async def test_qqbot_message_timeline_records_local_delivery_stages(tmp_path: Path):
    adapter, _database = make_persistent_adapter(tmp_path)
    message = IMMessage(ChatSender.get_bot_sender(), [TextMessage("hello")])

    await adapter.send_message(message, c2c_recipient(), delivery_id="logical-timeline")

    events = message.delivery_timeline
    assert [event.stage for event in events] == [
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_succeeded",
    ]
    assert events[1].details["segment_count"] == 1
    assert events[-1].details["retry_count"] == 0
    assert [event.timestamp for event in events] == sorted(
        event.timestamp for event in events
    )


@pytest.mark.asyncio
async def test_qqbot_message_timeline_records_send_failure_category(tmp_path: Path):
    adapter, _database = make_persistent_adapter(
        tmp_path,
        send_timeout_seconds=0.01,
    )

    async def hangs(**_kwargs):
        await asyncio.sleep(1)

    adapter.api.post_c2c_message = AsyncMock(side_effect=hangs)
    message = IMMessage(ChatSender.get_bot_sender(), [TextMessage("hello")])

    with pytest.raises(asyncio.TimeoutError):
        await adapter.send_message(
            message,
            c2c_recipient(),
            delivery_id="logical-timeline-failure",
        )

    assert [event.stage for event in message.delivery_timeline] == [
        "formatting_started",
        "formatting_completed",
        "send_started",
        "send_failed",
    ]
    failed = message.delivery_timeline[-1]
    assert failed.details["error_type"] == "TimeoutError"
    assert failed.details["retry_count"] == 0


@pytest.mark.asyncio
async def test_qqbot_timeline_counts_media_upload_retries(tmp_path: Path):
    adapter, database = make_persistent_adapter(tmp_path, max_attempts=2)
    adapter.api._http.request = AsyncMock(
        side_effect=[
            ServerError("temporary upload refusal"),
            {
                "file_uuid": "media-id",
                "file_info": "media-token",
                "ttl": 60,
            },
        ]
    )
    message = IMMessage(ChatSender.get_bot_sender(), [fake_image()])

    await adapter.send_message(
        message,
        c2c_recipient(),
        delivery_id="logical-upload-retry-timeline",
    )

    persisted = deliveries(database, "logical-upload-retry-timeline")
    assert persisted[0].upload_attempt_count == 2
    assert persisted[0].attempt_count == 1
    assert message.delivery_timeline[-1].stage == "send_succeeded"
    assert message.delivery_timeline[-1].details["retry_count"] == 1


@pytest.mark.asyncio
async def test_qqbot_oversized_reply_is_truncated_instead_of_lost(tmp_path: Path):
    """超出总字节预算时用户必须收到前几页 + 截断提示，不能一条都收不到。

    OneBot 用 `paginate_with_truncation_notice`，QQBot 却直接调用会抛
    `ValueError` 的 `split_structured_text`。异常从 `_render_send_units`
    一路穿出 `send_message`，于是**整条回复消失**——正是需求 19.4
    「全部发送、内容不得丢失」要禁止的失败形态，而且比截断更糟：
    用户连「还有更多」都不知道。
    """
    adapter, database = make_persistent_adapter(tmp_path)
    # 每行 100 个汉字（300 字节），1400 行约 420 KB，按 3800 字节分页会超过
    # 100 页上限。用多行而不是一条超长单行，是为了让分页保持按行线性，
    # 测试本身不引入二次复杂度。
    source = "\n".join("中" * 100 for _ in range(1400))

    await adapter.send_message(
        IMMessage(ChatSender.get_bot_sender(), [TextMessage(source)]),
        c2c_recipient(),
        delivery_id="logical-oversized",
    )

    persisted = deliveries(database, "logical-oversized")
    contents = [json.loads(str(item.params_json))["content"] for item in persisted]
    assert contents, "整条回复丢失：一个投递单元都没有落库"
    assert all(len(content.encode("utf-8")) <= 3800 for content in contents)
    assert "已截断" in contents[-1]
    assert [item.status for item in persisted] == ["accepted"] * len(persisted)
