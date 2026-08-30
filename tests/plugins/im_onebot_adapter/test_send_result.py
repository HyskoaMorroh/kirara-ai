"""需求 11：发出去的消息必须能被撤回。

`recall_message` 一直存在，`delete_msg` 也一直可用——但 `send_message` 返回
``None``，调用方拿不到刚发出那条消息的 `message_id`。于是「发一条提示，30 秒后
撤回」这种再普通不过的用法在本项目里做不到：撤回接口有，可没人知道要撤谁。

参考实现（`chatgpt-mirai-qq-bot-onebot-adapter`）返回一个带 `message_id` 的结果
对象，本项目此前完全没有对应物。这不是「风格差异」，是一条能力缺口：
上游在 `send_msg` 的响应里明明给了 `message_id`，我们收下、落库、然后丢掉。

这些用例要求那个 ID 一路回到调用方。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


class _Logger:
    def warning(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def info(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def debug(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    def error(self, *_args: Any, **_kwargs: Any) -> None:
        return None


def _adapter(responses: list[dict]) -> tuple[OneBotAdapter, list[tuple[str, dict]]]:
    """An adapter with no persistence, whose actions return canned responses."""
    adapter = object.__new__(OneBotAdapter)
    adapter.logger = _Logger()
    adapter.config = OneBotConfig()
    adapter.database_manager = None
    adapter._recipient_locks = {}
    calls: list[tuple[str, dict]] = []
    pending = list(responses)

    async def call_action(action: str, **params: Any) -> dict:
        calls.append((action, params))
        return pending.pop(0) if pending else {}

    adapter._call_action = call_action  # type: ignore[method-assign]
    return adapter, calls


def _message(text: str = "hello") -> IMMessage:
    return IMMessage(ChatSender.from_c2c_chat("200", "User"), [TextMessage(text)])


@pytest.mark.asyncio
async def test_send_message_returns_the_upstream_message_id():
    adapter, calls = _adapter([{"message_id": 4242}])

    result = await adapter.send_message(_message(), ChatSender.from_c2c_chat("200", "U"))

    assert result is not None, "send_message 没有返回结果，调用方无法撤回刚发的消息"
    assert result.message_ids == ("4242",)
    # 第一页的 ID 是「这条回复」的代表：撤回一条多页回复时，调用方最常想撤的是
    # 第一页（后续页会跟着被用户忽略），因此单独给出。
    assert result.message_id == "4242"
    assert calls and calls[0][0] == "send_private_msg"


@pytest.mark.asyncio
async def test_every_page_of_a_long_reply_reports_its_own_id():
    """长回复分页发送时，每一页都有自己的 `message_id`。

    只回第一页的 ID 等于「后面几页撤不掉」——用户看到的是撤回一半的回复，
    比不撤更糟。
    """
    adapter, _ = _adapter([{"message_id": 1}, {"message_id": 2}, {"message_id": 3}])
    # 让渲染切成三页。
    adapter._render_message_batches = _fake_batches(3)  # type: ignore[method-assign]

    result = await adapter.send_message(_message(), ChatSender.from_c2c_chat("200", "U"))

    assert result.message_ids == ("1", "2", "3")
    assert result.message_id == "1"


@pytest.mark.asyncio
async def test_a_response_without_a_message_id_yields_an_empty_tuple():
    """上游不回 `message_id` 是允许的，不能因此编一个出来。

    编一个（比如 0 或空串）会让调用方拿它去撤回，然后撤到别人的消息上，
    或者静默失败——两者都比「明确告诉你拿不到」糟。
    """
    adapter, _ = _adapter([{"status": "ok"}])

    result = await adapter.send_message(_message(), ChatSender.from_c2c_chat("200", "U"))

    assert result.message_ids == ()
    assert result.message_id is None


@pytest.mark.asyncio
async def test_an_empty_message_reports_nothing_sent():
    """空内容不发送，也就没有任何 ID。"""
    adapter, calls = _adapter([])
    adapter._render_message_batches = _fake_batches(0)  # type: ignore[method-assign]

    result = await adapter.send_message(_message(""), ChatSender.from_c2c_chat("200", "U"))

    assert result is not None
    assert result.message_ids == ()
    assert result.message_id is None
    assert not calls


@pytest.mark.asyncio
async def test_the_result_carries_the_delivery_id_for_correlation():
    """结果里必须能拿到本次投递的逻辑 ID，用来和日志、时间线对上。"""
    adapter, _ = _adapter([{"message_id": 7}])

    result = await adapter.send_message(
        _message(), ChatSender.from_c2c_chat("200", "U"), delivery_id="abc123"
    )

    assert result.delivery_id == "abc123"


@pytest.mark.asyncio
async def test_a_recall_can_use_the_returned_id_directly():
    """返回的 ID 必须能原样交给 `recall_message`。

    如果两者的类型或形态不一致（一个是 str、一个要 int），调用方就得自己转换，
    而那正是「拿到了 ID 却撤不掉」的来源。
    """
    adapter, calls = _adapter([{"message_id": 55}, {}])

    result = await adapter.send_message(_message(), ChatSender.from_c2c_chat("200", "U"))
    assert result.message_id is not None
    await adapter.recall_message(result.message_id)

    assert calls[-1][0] == "delete_msg"
    assert calls[-1][1]["message_id"] == 55


def _fake_batches(count: int):
    async def render(_message: Any) -> list[list[Any]]:
        from aiocqhttp import MessageSegment

        return [[MessageSegment.text(f"page-{index}")] for index in range(count)]

    return render
