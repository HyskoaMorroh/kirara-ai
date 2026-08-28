import asyncio
import socket
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiocqhttp import MessageSegment

from kirara_ai.im.message import AtElement, FileMessage, ImageMessage
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import (
    OneBotActionTimeoutError,
    OneBotAdapter,
)
import kirara_ai.plugins.im_onebot_adapter.adapter as adapter_module
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig
from kirara_ai.im.message import IMMessage, MentionElement, ReplyElement, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.dispatch.dispatcher import WorkflowDispatcher


def test_onebot_adapter_receives_ioc_dependencies_without_string_annotations():
    container = DependencyContainer()
    web_server = object.__new__(WebServer)
    dispatcher = object.__new__(WorkflowDispatcher)
    config = OneBotConfig()
    container.register(WebServer, web_server)
    container.register(WorkflowDispatcher, dispatcher)
    container.register(OneBotConfig, config)

    adapter = Inject(container).create(OneBotAdapter)()

    assert adapter.web_server is web_server
    assert adapter.dispatcher is dispatcher
    assert adapter.config is config


@pytest.mark.asyncio
async def test_file_message_degrades_to_text_when_onebot_has_no_file_segment():
    adapter = object.__new__(OneBotAdapter)
    adapter._media_url = AsyncMock(return_value="https://example.test/report.pdf")
    element = object.__new__(FileMessage)
    element.path = "/tmp/report.pdf"
    element.url = "https://example.test/report.pdf"
    element.format = "pdf"

    segment = await adapter._to_segment(element)

    assert isinstance(segment, MessageSegment)
    assert segment.type == "text"
    assert "文件：report.pdf" in segment.data["text"]
    assert "https://example.test/report.pdf" in segment.data["text"]


@pytest.mark.asyncio
async def test_legacy_at_element_still_converts_to_onebot_at_segment():
    adapter = object.__new__(OneBotAdapter)

    segment = await adapter._to_segment(AtElement("123", "旧工作流用户"))

    assert segment is not None
    assert segment.type == "at"
    assert segment.data["qq"] == "123"


class FakeWebServer:
    def __init__(self):
        self.app = SimpleNamespace(routes=[])

    def mount_app(self, prefix, app):
        self.app.routes.append(SimpleNamespace(path=prefix, app=app))


def make_adapter(*, config=None, web_server=None, dispatcher=None):
    container = DependencyContainer()
    config = config or OneBotConfig()
    container.register(OneBotConfig, config)
    if web_server is not None:
        container.register(WebServer, web_server)
    if dispatcher is not None:
        container.register(WorkflowDispatcher, dispatcher)
    return Inject(container).create(OneBotAdapter)()


def test_onebot_config_round_trip_keeps_websocket_url():
    config = OneBotConfig()

    restored = OneBotConfig.model_validate(config.model_dump())

    assert restored.websocket_url == config.websocket_url
    assert restored.websocket_path == config.websocket_path
    assert restored.heartbeat_timeout_seconds == 90


@pytest.mark.asyncio
async def test_start_stop_is_idempotent_and_can_mount_again():
    adapter = make_adapter(web_server=FakeWebServer())

    await adapter.start()
    await adapter.start()
    assert len(adapter.web_server.app.routes) == 1

    await adapter.stop()
    await adapter.stop()
    assert adapter.web_server.app.routes == []

    await adapter.start()
    assert len(adapter.web_server.app.routes) == 1
    await adapter.stop()


@pytest.mark.asyncio
async def test_start_rejects_duplicate_websocket_path():
    web_server = FakeWebServer()
    first = make_adapter(web_server=web_server)
    second = make_adapter(
        config=OneBotConfig(websocket_url=first.config.websocket_url),
        web_server=web_server,
    )

    await first.start()
    with pytest.raises(RuntimeError, match="路径已被占用"):
        await second.start()
    assert len(web_server.app.routes) == 1
    await first.stop()


@pytest.mark.asyncio
async def test_standalone_start_binds_port_and_can_restart():
    adapter = make_adapter(
        config=OneBotConfig(host="127.0.0.1", port=0),
    )

    await adapter.start()
    first_port = adapter._standalone_port
    assert first_port is not None and first_port > 0
    with socket.create_connection(("127.0.0.1", first_port), timeout=1):
        pass

    await adapter.stop()
    assert adapter._server_task is None
    assert adapter._server_shutdown_event is None

    await adapter.start()
    second_port = adapter._standalone_port
    assert second_port is not None and second_port > 0
    with socket.create_connection(("127.0.0.1", second_port), timeout=1):
        pass
    await adapter.stop()


@pytest.mark.asyncio
async def test_standalone_start_surfaces_port_conflict():
    occupied = socket.socket()
    occupied.bind(("127.0.0.1", 0))
    occupied.listen()
    port = occupied.getsockname()[1]
    adapter = make_adapter(
        config=OneBotConfig(host="127.0.0.1", port=port),
    )

    try:
        with pytest.raises(OSError):
            await adapter.start()
        assert adapter._started is False
        assert adapter._server_task is None
    finally:
        occupied.close()


@pytest.mark.asyncio
async def test_standalone_start_surfaces_server_startup_failure(monkeypatch):
    adapter = make_adapter(
        config=OneBotConfig(host="127.0.0.1", port=0),
    )

    async def fail_server(*args, **kwargs):
        raise RuntimeError("server startup failed")

    monkeypatch.setattr(adapter_module, "worker_serve", fail_server)

    with pytest.raises(RuntimeError, match="server startup failed"):
        await adapter.start()
    assert adapter._started is False
    assert adapter._server_task is None


@pytest.mark.asyncio
async def test_empty_text_does_not_send_an_invalid_onebot_message():
    adapter = object.__new__(OneBotAdapter)
    adapter.bot = SimpleNamespace(call_action=AsyncMock(return_value={"message_id": 1}))
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("")],
    )

    await adapter.send_message(message, ChatSender.from_c2c_chat("100", "用户"))

    adapter.bot.call_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_heartbeat_pruning_removes_stale_connection():
    adapter = make_adapter(
        config=OneBotConfig(
            heartbeat_interval=1,
            heartbeat_timeout_seconds=3,
        ),
        web_server=FakeWebServer(),
    )
    await adapter.start()
    await adapter._handle_meta(
        {"self_id": 123, "meta_event_type": "lifecycle", "sub_type": "connect"}
    )
    assert "123" in adapter.connections

    adapter.connections["123"]["last_heartbeat"] -= 10
    adapter._prune_stale_connections()

    assert adapter.connections == {}
    await adapter.stop()


def test_default_heartbeat_timeout_tolerates_llonebot_sixty_second_interval():
    adapter = make_adapter(config=OneBotConfig(heartbeat_interval=15))
    adapter._started = True
    adapter.connections["100"] = {"last_heartbeat": 100.0}

    adapter._prune_stale_connections(now=160.0)

    assert adapter.get_health_snapshot(now=160.0).status == "connected"
    assert adapter.get_health_snapshot(now=160.0).last_heartbeat_age_seconds == 60.0

    adapter._prune_stale_connections(now=190.1)

    assert adapter.get_health_snapshot(now=190.1).status == "stale"


def test_health_snapshot_distinguishes_waiting_and_connected():
    adapter = make_adapter(config=OneBotConfig(heartbeat_interval=1))

    # Before the first start there is nothing to be disconnected from; that case
    # now has its own status so a fresh container does not look like a failure.
    # See tests/plugins/im_onebot_adapter/test_connection_states.py.
    assert adapter.get_health_snapshot().status == "initializing"

    adapter._started = True
    assert adapter.get_health_snapshot().status == "waiting"

    adapter.connections["100"] = {"last_heartbeat": 1.0}
    assert adapter.get_health_snapshot(now=1.0).status == "connected"

    # A stop after a successful start is still reported as disconnected.
    adapter.connections.clear()
    adapter._started = False
    assert adapter.get_health_snapshot(now=1.0).status == "disconnected"


@pytest.mark.asyncio
async def test_profile_action_timeout_degrades_to_fallback_profile():
    adapter = make_adapter(config=OneBotConfig(action_timeout_seconds=0.01))

    async def never_returns(*args, **kwargs):
        await asyncio.sleep(1)

    adapter.bot.call_action = AsyncMock(side_effect=never_returns)

    profile = await adapter.query_user_profile(
        ChatSender.from_c2c_chat("100", "用户")
    )

    assert profile.user_id == "100"
    assert profile.display_name == "用户"


@pytest.mark.asyncio
async def test_send_message_uses_private_and_group_actions():
    adapter = object.__new__(OneBotAdapter)
    adapter.bot = SimpleNamespace(call_action=AsyncMock(return_value={"message_id": 1}))
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    await adapter.send_message(message, ChatSender.from_c2c_chat("100", "用户"))
    await adapter.send_message(
        message,
        ChatSender.from_group_chat("100", "200", "用户"),
    )

    calls = adapter.bot.call_action.await_args_list
    assert calls[0].args == ("send_private_msg",)
    assert calls[0].kwargs["user_id"] == 100
    assert calls[1].args == ("send_group_msg",)
    assert calls[1].kwargs["group_id"] == 200


@pytest.mark.asyncio
async def test_send_message_propagates_onebot_api_failure():
    adapter = object.__new__(OneBotAdapter)
    adapter.bot = SimpleNamespace(
        call_action=AsyncMock(side_effect=RuntimeError("OneBot API unavailable"))
    )
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    with pytest.raises(RuntimeError, match="OneBot API unavailable"):
        await adapter.send_message(message, ChatSender.from_c2c_chat("100", "用户"))


@pytest.mark.asyncio
async def test_onebot_action_has_a_bound_and_does_not_retry_after_timeout():
    adapter = make_adapter(config=OneBotConfig(action_timeout_seconds=0.01))

    async def never_returns(*args, **kwargs):
        await asyncio.sleep(1)

    adapter.bot.call_action = AsyncMock(side_effect=never_returns)
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    with pytest.raises(OneBotActionTimeoutError):
        await adapter.send_message(message, ChatSender.from_c2c_chat("100", "用户"))

    assert adapter.bot.call_action.await_count == 1


@pytest.mark.asyncio
async def test_onebot_management_actions_keep_legacy_capabilities(monkeypatch):
    adapter = make_adapter()
    adapter.bot.call_action = AsyncMock(return_value={"status": "ok"})
    sleep = AsyncMock()
    monkeypatch.setattr(adapter_module.asyncio, "sleep", sleep)

    await adapter.recall_message("42", delay=1.5)
    await adapter.mute_user("200", "100", 60)
    await adapter.unmute_user("200", "100")
    await adapter.kick_user("200", "100")

    sleep.assert_awaited_once_with(1.5)
    calls = adapter.bot.call_action.await_args_list
    assert calls[0].args == ("delete_msg",)
    assert calls[0].kwargs == {"message_id": 42}
    assert calls[1].args == ("set_group_ban",)
    assert calls[1].kwargs == {"group_id": 200, "user_id": 100, "duration": 60}
    assert calls[2].args == ("set_group_ban",)
    assert calls[2].kwargs == {"group_id": 200, "user_id": 100, "duration": 0}
    assert calls[3].args == ("set_group_kick",)
    assert calls[3].kwargs == {
        "group_id": 200,
        "user_id": 100,
        "reject_add_request": False,
    }


@pytest.mark.asyncio
async def test_onebot_management_actions_reject_negative_durations():
    adapter = make_adapter()
    adapter.bot.call_action = AsyncMock(return_value={"status": "ok"})

    with pytest.raises(ValueError, match="撤回延迟"):
        await adapter.recall_message(42, delay=-1)
    with pytest.raises(ValueError, match="禁言时长"):
        await adapter.mute_user(200, 100, -1)

    adapter.bot.call_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_message_keeps_reply_and_mention_on_first_page():
    adapter = object.__new__(OneBotAdapter)
    adapter.bot = SimpleNamespace(call_action=AsyncMock(return_value={"message_id": 1}))
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[
            MentionElement(ChatSender.get_bot_sender()),
            ReplyElement("42"),
            TextMessage("内容。" * 1500),
        ],
    )

    await adapter.send_message(message, ChatSender.from_c2c_chat("100", "用户"))

    calls = adapter.bot.call_action.await_args_list
    assert len(calls) > 1
    first_segments = calls[0].kwargs["message"]
    assert [segment.type for segment in first_segments[:2]] == ["at", "reply"]
    assert first_segments[-1].type == "text"
    assert first_segments[-1].data["text"].startswith("第 1 页 / 共 ")
    assert all(call.kwargs["message"][-1].type == "text" for call in calls)


@pytest.mark.asyncio
async def test_inbound_message_is_converted_and_dispatched():
    dispatcher = AsyncMock()
    adapter = make_adapter(dispatcher=dispatcher)
    adapter.self_id = "999"

    await adapter._handle_message(
        {
            "self_id": 999,
            "user_id": 100,
            "group_id": 200,
            "sender": {"card": "群成员", "nickname": "昵称"},
            "message": [
                {"type": "text", "data": {"text": "问题"}},
                {"type": "at", "data": {"qq": "999"}},
                {"type": "at", "data": {"qq": "123"}},
            ],
        }
    )

    dispatcher.dispatch.assert_awaited_once()
    _, converted = dispatcher.dispatch.await_args.args
    assert converted.sender.group_id == "200"
    assert [type(element) for element in converted.message_elements] == [
        TextMessage,
        MentionElement,
    ]


@pytest.mark.asyncio
async def test_inbound_private_message_uses_c2c_sender():
    adapter = make_adapter()

    converted = await adapter.convert_to_message(
        {
            "self_id": 999,
            "user_id": 100,
            "sender": {"nickname": "私聊用户"},
            "message": [
                {"type": "reply", "data": {"id": 42}},
                {"type": "text", "data": {"text": "私聊问题"}},
            ],
        }
    )

    assert converted.sender.group_id is None
    assert converted.sender.user_id == "100"
    assert converted.sender.display_name == "私聊用户"
    assert [type(element) for element in converted.message_elements] == [
        ReplyElement,
        TextMessage,
    ]


@pytest.mark.asyncio
async def test_interleaved_accounts_keep_event_self_id_on_each_sender():
    adapter = make_adapter()

    first, second = await asyncio.gather(
        adapter.convert_to_message(
            {
                "self_id": 10001,
                "user_id": 30001,
                "sender": {"nickname": "甲"},
                "message": [{"type": "text", "data": {"text": "第一条"}}],
            }
        ),
        adapter.convert_to_message(
            {
                "self_id": 10002,
                "user_id": 30002,
                "sender": {"nickname": "乙"},
                "message": [{"type": "text", "data": {"text": "第二条"}}],
            }
        ),
    )

    assert first.sender.raw_metadata["onebot_self_id"] == "10001"
    assert second.sender.raw_metadata["onebot_self_id"] == "10002"


@pytest.mark.asyncio
async def test_send_actions_route_private_and_group_replies_to_target_accounts():
    adapter = make_adapter()
    adapter.connections = {
        "10001": {"last_heartbeat": 1.0},
        "10002": {"last_heartbeat": 1.0},
    }
    adapter.bot.call_action = AsyncMock(return_value={"message_id": 1})
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    await adapter.send_message(
        message,
        ChatSender.from_c2c_chat(
            "30001", "私聊用户", metadata={"onebot_self_id": "10001"}
        ),
    )
    await adapter.send_message(
        message,
        ChatSender.from_group_chat(
            "30002",
            "40002",
            "群成员",
            metadata={"onebot_self_id": "10002"},
        ),
    )

    calls = adapter.bot.call_action.await_args_list
    assert calls[0].args == ("send_private_msg",)
    assert calls[0].kwargs["self_id"] == "10001"
    assert calls[1].args == ("send_group_msg",)
    assert calls[1].kwargs["self_id"] == "10002"


@pytest.mark.asyncio
async def test_management_actions_route_to_explicit_target_accounts():
    adapter = make_adapter()
    adapter.connections = {
        "10001": {"last_heartbeat": 1.0},
        "10002": {"last_heartbeat": 1.0},
    }
    adapter.bot.call_action = AsyncMock(return_value={"status": "ok"})

    await adapter.recall_message("42", self_id="10001")
    await adapter.mute_user("40002", "30002", 60, self_id="10002")
    await adapter.unmute_user("40001", "30001", self_id="10001")
    await adapter.kick_user("40002", "30002", self_id="10002")

    calls = adapter.bot.call_action.await_args_list
    assert [call.kwargs["self_id"] for call in calls] == [
        "10001",
        "10002",
        "10001",
        "10002",
    ]


@pytest.mark.asyncio
async def test_multi_account_actions_reject_missing_target_self_id():
    adapter = make_adapter()
    adapter.connections = {
        "10001": {"last_heartbeat": 1.0},
        "10002": {"last_heartbeat": 1.0},
    }
    adapter.bot.call_action = AsyncMock(return_value={"status": "ok"})
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    with pytest.raises(ValueError, match="缺少目标 self_id"):
        await adapter.send_message(
            message, ChatSender.from_group_chat("30001", "40001", "群成员")
        )
    with pytest.raises(ValueError, match="缺少目标 self_id"):
        await adapter.recall_message("42")
    with pytest.raises(ValueError, match="缺少目标 self_id"):
        await adapter.mute_user("40001", "30001", 60)
    with pytest.raises(ValueError, match="缺少目标 self_id"):
        await adapter.kick_user("40001", "30001")

    adapter.bot.call_action.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_group_on_different_accounts_has_independent_page_locks():
    adapter = make_adapter()
    adapter.connections = {
        "10001": {"last_heartbeat": 1.0},
        "10002": {"last_heartbeat": 1.0},
    }
    both_entered = asyncio.Event()
    entered = 0

    async def synchronize_action(action, **params):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=0.2)
        return {"message_id": entered}

    adapter.bot.call_action = AsyncMock(side_effect=synchronize_action)
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    await asyncio.gather(
        adapter.send_message(
            message,
            ChatSender.from_group_chat(
                "30001",
                "40001",
                "甲",
                metadata={"onebot_self_id": "10001"},
            ),
        ),
        adapter.send_message(
            message,
            ChatSender.from_group_chat(
                "30002",
                "40001",
                "乙",
                metadata={"onebot_self_id": "10002"},
            ),
        ),
    )

    assert entered == 2
    assert adapter._recipient_locks == {}


@pytest.mark.asyncio
async def test_profile_cache_is_isolated_by_onebot_self_id():
    adapter = make_adapter()
    adapter.connections = {
        "10001": {"last_heartbeat": 1.0},
        "10002": {"last_heartbeat": 1.0},
    }
    adapter.bot.call_action = AsyncMock(
        side_effect=[
            {"user_id": 30001, "nickname": "账号一看到的昵称"},
            {"user_id": 30001, "nickname": "账号二看到的昵称"},
        ]
    )

    first = await adapter.query_user_profile(
        ChatSender.from_c2c_chat(
            "30001", "用户", metadata={"onebot_self_id": "10001"}
        )
    )
    second = await adapter.query_user_profile(
        ChatSender.from_c2c_chat(
            "30001", "用户", metadata={"onebot_self_id": "10002"}
        )
    )

    assert first.username == "账号一看到的昵称"
    assert first.display_name == "账号一看到的昵称"
    assert second.username == "账号二看到的昵称"
    assert second.display_name == "账号二看到的昵称"
    assert adapter.bot.call_action.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "unsafe_source",
    [
        "file:///etc/passwd",
        "C:/Windows/win.ini",
        "../secrets.env",
        "\\\\server\\share\\secret.png",
        "http://127.0.0.1/private.png",
        "http://169.254.169.254/latest/meta-data/",
        "ftp://example.com/image.png",
    ],
)
async def test_inbound_media_rejects_local_private_and_unknown_sources(
    monkeypatch, unsafe_source
):
    adapter = make_adapter()
    create = AsyncMock()
    monkeypatch.setattr(adapter, "_create_inbound_media_element", create, raising=False)

    converted = await adapter.convert_to_message(
        {
            "self_id": 10001,
            "user_id": 30001,
            "sender": {"nickname": "用户"},
            "message": [
                {"type": "image", "data": {"url": unsafe_source}},
                {"type": "text", "data": {"text": "保留文字"}},
            ],
        }
    )

    create.assert_not_awaited()
    assert converted.content == "保留文字"


@pytest.mark.asyncio
async def test_inbound_media_downloads_public_url_before_creating_element(monkeypatch):
    adapter = make_adapter()
    download = AsyncMock(return_value=b"safe image bytes")
    monkeypatch.setattr(adapter, "_download_inbound_media", download, raising=False)
    create = AsyncMock(return_value=TextMessage("[图片]"))
    monkeypatch.setattr(adapter, "_create_inbound_media_element", create, raising=False)

    converted = await adapter.convert_to_message(
        {
            "self_id": 10001,
            "user_id": 30001,
            "sender": {"nickname": "用户"},
            "message": [
                {
                    "type": "image",
                    "data": {
                        "url": "https://cdn.example.com/image.png",
                        "file": "image.png",
                    },
                }
            ],
        }
    )

    download.assert_awaited_once_with("https://cdn.example.com/image.png")
    create.assert_awaited_once_with(
        "image",
        b"safe image bytes",
        {"url": "https://cdn.example.com/image.png", "file": "image.png"},
    )
    assert converted.content == "[图片]"


@pytest.mark.asyncio
async def test_inbound_media_accepts_bounded_inline_base64_without_network(monkeypatch):
    adapter = make_adapter()
    download = AsyncMock()
    monkeypatch.setattr(adapter, "_download_inbound_media", download, raising=False)
    create = AsyncMock(return_value=TextMessage("[图片]"))
    monkeypatch.setattr(adapter, "_create_inbound_media_element", create, raising=False)

    converted = await adapter.convert_to_message(
        {
            "self_id": 10001,
            "user_id": 30001,
            "sender": {"nickname": "用户"},
            "message": [
                {
                    "type": "image",
                    "data": {
                        "url": "data:image/png;base64,c2FmZSBpbWFnZSBieXRlcw==",
                        "file": "image.png",
                    },
                }
            ],
        }
    )

    download.assert_not_awaited()
    create.assert_awaited_once_with(
        "image",
        b"safe image bytes",
        {
            "url": "data:image/png;base64,c2FmZSBpbWFnZSBieXRlcw==",
            "file": "image.png",
        },
    )
    assert converted.content == "[图片]"


@pytest.mark.asyncio
async def test_inbound_media_failure_is_logged_and_does_not_drop_text(monkeypatch):
    adapter = make_adapter()
    monkeypatch.setattr(
        adapter,
        "_download_inbound_media",
        AsyncMock(side_effect=TimeoutError("download timed out")),
        raising=False,
    )
    create = AsyncMock()
    monkeypatch.setattr(adapter, "_create_inbound_media_element", create, raising=False)
    adapter.logger = SimpleNamespace(warning=MagicMock())

    converted = await adapter.convert_to_message(
        {
            "self_id": 10001,
            "user_id": 30001,
            "sender": {"nickname": "用户"},
            "message": [
                {
                    "type": "image",
                    "data": {"url": "https://cdn.example.com/image.png"},
                },
                {"type": "text", "data": {"text": "保留文字"}},
            ],
        }
    )

    create.assert_not_awaited()
    adapter.logger.warning.assert_called_once()
    assert converted.content == "保留文字"


@pytest.mark.asyncio
async def test_media_conversion_does_not_block_adapter_event_loop(monkeypatch):
    adapter = make_adapter()

    monkeypatch.setattr(
        adapter,
        "_download_inbound_media",
        AsyncMock(return_value=b"safe image bytes"),
        raising=False,
    )

    def slow_create_message_element(*args, **kwargs):
        time.sleep(0.08)
        return TextMessage("图片")

    monkeypatch.setattr(adapter_module, "create_message_element", slow_create_message_element)

    conversion = asyncio.create_task(
        adapter.convert_to_message(
            {
                "user_id": 100,
                "sender": {"nickname": "用户"},
                "message": [
                    {
                        "type": "image",
                        "data": {"url": "https://cdn.example.com/image.png"},
                    }
                ],
            }
        )
    )
    await asyncio.sleep(0.02)

    assert not conversion.done()
    converted = await conversion
    assert converted.content == "图片"


@pytest.mark.asyncio
async def test_paginated_messages_to_same_recipient_do_not_interleave():
    adapter = make_adapter()
    sent: list[str] = []

    async def record_action(action, **params):
        sent.append(params["message"][-1].data["text"])
        await asyncio.sleep(0)
        return {"message_id": len(sent)}

    adapter.bot.call_action = AsyncMock(side_effect=record_action)
    recipient = ChatSender.from_c2c_chat("100", "用户")
    first = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("A。" * 3000)],
    )
    second = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("B。" * 3000)],
    )

    await asyncio.gather(
        adapter.send_message(first, recipient),
        adapter.send_message(second, recipient),
    )

    message_order = ["A" if "A。" in page else "B" for page in sent]
    assert message_order in [
        ["A"] * message_order.count("A") + ["B"] * message_order.count("B"),
        ["B"] * message_order.count("B") + ["A"] * message_order.count("A"),
    ]
    assert adapter._recipient_locks == {}


@pytest.mark.asyncio
async def test_messages_to_different_recipients_are_not_globally_serialized():
    adapter = make_adapter()
    both_entered = asyncio.Event()
    entered = 0

    async def synchronize_action(action, **params):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), timeout=0.2)
        return {"message_id": entered}

    adapter.bot.call_action = AsyncMock(side_effect=synchronize_action)
    message = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage("回复")],
    )

    await asyncio.gather(
        adapter.send_message(message, ChatSender.from_c2c_chat("100", "甲")),
        adapter.send_message(message, ChatSender.from_c2c_chat("200", "乙")),
    )

    assert entered == 2
