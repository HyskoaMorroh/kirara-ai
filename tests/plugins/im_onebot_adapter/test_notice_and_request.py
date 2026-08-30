"""通知与请求事件必须留下痕迹，且不得派发进工作流。

`_handle_notice` 曾是 `return None`：被踢出群、被禁言这类会直接导致「机器人不回话」
的事件完全无声，排查时只能看到发送失败、看不到原因。请求事件（好友申请、入群邀请）
更是**根本没有订阅**——管理员在 QQ 里看到一个悬而未决的申请，服务端日志里一个字都没有。

同时这两类都不能进工作流：它们不是消息，没有回复语义，硬塞进去会让每次群成员
变动都跑一遍模型（重复计费 + 莫名回复）。
"""

from __future__ import annotations

import pytest

from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter


class _Logger:
    def __init__(self) -> None:
        self.info: list[str] = []
        self.warning: list[str] = []
        self.debug: list[str] = []

    def __getattr__(self, name):  # pragma: no cover - 只为兜住未用到的级别
        return lambda *args, **kwargs: None


class _RecordingLogger:
    def __init__(self) -> None:
        self.records: list[tuple[str, str]] = []

    def info(self, message: str) -> None:
        self.records.append(("info", message))

    def warning(self, message: str) -> None:
        self.records.append(("warning", message))

    def debug(self, message: str) -> None:
        self.records.append(("debug", message))

    def error(self, message: str) -> None:  # pragma: no cover
        self.records.append(("error", message))


def adapter_with_logger() -> tuple[OneBotAdapter, _RecordingLogger]:
    adapter = object.__new__(OneBotAdapter)
    logger = _RecordingLogger()
    adapter.logger = logger

    class _Dispatcher:
        async def dispatch(self, *_args, **_kwargs):  # pragma: no cover
            raise AssertionError("通知与请求事件不得派发进工作流")

    adapter.dispatcher = _Dispatcher()
    return adapter, logger


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("notice_type", "expected_fragment"),
    [
        ("group_recall", "撤回"),
        ("group_increase", "成员增加"),
        ("group_decrease", "成员减少"),
        ("group_ban", "禁言"),
        ("friend_add", "好友"),
        ("group_upload", "文件"),
    ],
)
async def test_known_notices_are_logged_with_a_readable_description(
    notice_type: str, expected_fragment: str
):
    adapter, logger = adapter_with_logger()

    await adapter._handle_notice(
        {"notice_type": notice_type, "self_id": 1, "user_id": 2, "group_id": 900}
    )

    assert logger.records, f"{notice_type} 没有留下任何记录"
    level, message = logger.records[-1]
    assert level in {"info", "warning"}
    assert expected_fragment in message
    assert notice_type in message
    assert "group=900" in message


@pytest.mark.asyncio
async def test_an_unknown_notice_type_is_still_recorded():
    """未知通知类型不得静默丢弃：新的 OneBot 实现会加新类型。"""
    adapter, logger = adapter_with_logger()

    await adapter._handle_notice({"notice_type": "some_future_notice", "self_id": 1})

    assert logger.records
    assert "some_future_notice" in logger.records[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize("notice_type", ["group_decrease", "group_ban"])
async def test_notices_that_disable_the_bot_are_warnings(notice_type: str):
    """被移出群或被禁言会立刻改变可用性，必须比 info 更醒目。"""
    adapter, logger = adapter_with_logger()

    await adapter._handle_notice(
        {"notice_type": notice_type, "self_id": 77, "user_id": 77, "group_id": 900}
    )

    level, message = logger.records[-1]
    assert level == "warning"
    assert "可用性" in message


@pytest.mark.asyncio
@pytest.mark.parametrize("notice_type", ["group_decrease", "group_ban"])
async def test_the_same_notice_about_someone_else_stays_informational(notice_type: str):
    """别人被踢或被禁言不影响本账号，不该升级为 warning。"""
    adapter, logger = adapter_with_logger()

    await adapter._handle_notice(
        {"notice_type": notice_type, "self_id": 77, "user_id": 12345, "group_id": 900}
    )

    assert logger.records[-1][0] == "info"


@pytest.mark.asyncio
async def test_a_private_notice_reports_c2c_scope():
    adapter, logger = adapter_with_logger()

    await adapter._handle_notice({"notice_type": "friend_recall", "self_id": 1, "user_id": 2})

    assert "c2c" in logger.records[-1][1]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("request_type", "expected"),
    [("friend", "好友申请"), ("group", "群相关申请")],
)
async def test_requests_are_logged_and_never_auto_accepted(
    request_type: str, expected: str
):
    """自动同意入群邀请是安全决定，不该由框架代替部署者做。"""
    adapter, logger = adapter_with_logger()

    await adapter._handle_request(
        {"request_type": request_type, "sub_type": "invite", "self_id": 1, "group_id": 900}
    )

    level, message = logger.records[-1]
    assert level == "info"
    assert expected in message
    assert "不会自动同意" in message


@pytest.mark.asyncio
async def test_an_unknown_request_type_is_still_recorded():
    adapter, logger = adapter_with_logger()

    await adapter._handle_request({"request_type": "some_future_request", "self_id": 1})

    assert "some_future_request" in logger.records[-1][1]


@pytest.mark.asyncio
async def test_notice_and_request_handling_never_dispatches_a_workflow():
    """派发器被替换成会抛错的桩：这两类事件一旦进工作流就立刻暴露。"""
    adapter, _ = adapter_with_logger()

    await adapter._handle_notice({"notice_type": "group_increase", "self_id": 1})
    await adapter._handle_request({"request_type": "friend", "self_id": 1})


@pytest.mark.asyncio
async def test_malformed_events_do_not_raise():
    """畸形事件只应失去一条日志，不能让上游连接因异常中断。"""
    adapter, logger = adapter_with_logger()

    await adapter._handle_notice({})
    await adapter._handle_request({})

    assert len(logger.records) == 2


@pytest.mark.asyncio
async def test_typing_indicator_uses_the_onebot_extension_action():
    """LLOneBot / NapCat 都实现了 `set_input_status`，不该谎称「不支持」。

    此前这里只记一条「OneBot 不支持输入状态」——对这两个最常用的实现来说那句话
    是错的，而且白白丢掉了一个能让长回复期间界面不显得卡死的提示。
    """
    from kirara_ai.im.sender import ChatSender

    adapter, _ = adapter_with_logger()
    calls: list[tuple[str, dict]] = []

    async def record(action: str, **params):
        calls.append((action, params))
        return {}

    adapter._call_action = record  # type: ignore[method-assign]

    await adapter.set_chat_editing_state(ChatSender.from_c2c_chat("12345", "用户"))

    assert calls == [("set_input_status", {"user_id": 12345, "event_type": 1})]


@pytest.mark.asyncio
async def test_clearing_the_typing_indicator_sends_the_cancel_value():
    from kirara_ai.im.sender import ChatSender

    adapter, _ = adapter_with_logger()
    calls: list[tuple[str, dict]] = []

    async def record(action: str, **params):
        calls.append((action, params))
        return {}

    adapter._call_action = record  # type: ignore[method-assign]

    await adapter.set_chat_editing_state(
        ChatSender.from_c2c_chat("12345", "用户"), is_editing=False
    )

    assert calls[0][1]["event_type"] == 0


@pytest.mark.asyncio
async def test_an_implementation_without_the_action_loses_only_the_indicator():
    """标准里没有的动作必须容错：不支持只应让提示消失，不能影响发送。"""
    from aiocqhttp import ApiNotAvailable
    from kirara_ai.im.sender import ChatSender

    adapter, logger = adapter_with_logger()

    async def refuse(_action: str, **_params):
        raise ApiNotAvailable()

    adapter._call_action = refuse  # type: ignore[method-assign]

    # 必须不抛：调用方是发送路径。
    await adapter.set_chat_editing_state(ChatSender.from_c2c_chat("12345", "用户"))

    assert any("set_input_status" in message for _, message in logger.records)


@pytest.mark.asyncio
async def test_group_chats_skip_the_typing_indicator():
    """群聊没有对应语义，直接跳过而不是发一个必然失败的动作。"""
    from kirara_ai.im.sender import ChatSender

    adapter, _ = adapter_with_logger()
    calls: list[str] = []

    async def record(action: str, **_params):
        calls.append(action)
        return {}

    adapter._call_action = record  # type: ignore[method-assign]

    await adapter.set_chat_editing_state(
        ChatSender.from_group_chat("12345", "900", "用户")
    )

    assert calls == []


@pytest.mark.asyncio
async def test_a_non_numeric_user_id_does_not_break_the_send_path():
    from kirara_ai.im.sender import ChatSender

    adapter, logger = adapter_with_logger()

    async def unreachable(_action: str, **_params):  # pragma: no cover
        raise AssertionError("不该带着非数字 user_id 调用上游")

    adapter._call_action = unreachable  # type: ignore[method-assign]

    await adapter.set_chat_editing_state(ChatSender.from_c2c_chat("not-a-number", "用户"))

    assert any("非数字" in message for _, message in logger.records)
