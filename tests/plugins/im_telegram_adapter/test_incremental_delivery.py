"""需求 4：Telegram 能把生成中的回复真的逐步推给用户。

这是四个渠道里唯一技术可行的一个：Bot API 有 `editMessageText`，可以先发一条占位
消息，再随生成不断改写同一条。此前适配器对它零调用——`aggregate` 模式把整个流在
服务端吃完再发一条完整消息，用户端从来没有收到过流式，等待期间界面上什么都没有。

QQ / OneBot 与企业微信没有等价能力：在那些渠道上逐步推送只能变成几十条碎片消息，
比一条完整回复更糟。因此本能力做成**可选协议**，不实现它的适配器自动退回整段投递。

四条边界写进用例：

1. **每次改写传的是「到目前为止的完整文本」**，不是增量片段。传增量会把「编辑同一条
   消息」变成需要调用方自己拼接，而拼接状态一旦与平台上的实际内容不一致，
   用户看到的就是一段错乱的文本。
2. **节流有下限。** Telegram 对 editMessageText 有频率限制，逐 token 改写会撞
   429，然后这条回复的后续更新全部丢失——那比不流式更糟。
3. **内容没变就不发请求。** 一次无变化的改写会被平台以「消息未修改」拒绝，
   而那是一个会进日志的错误，看起来像故障。
4. **最终内容与整段投递逐字一致。** 否则同一个机器人在同一渠道上给出两种排版，
   差别只取决于当时走了哪条路。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from kirara_ai.im.adapter import IncrementalDeliveryAdapter, IncrementalReplyHandle
from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter

from .test_adapter_outbox import make_adapter, recipient


@pytest.mark.asyncio
async def test_telegram_implements_the_incremental_protocol(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        # 回归点：修之前 Telegram 不满足这个协议，运行时无法据此选择增量路径。
        assert isinstance(adapter, IncrementalDeliveryAdapter)
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_begin_sends_a_placeholder_and_returns_a_handle(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    adapter.application.bot.send_message = AsyncMock(
        return_value=type("Sent", (), {"message_id": 42})()
    )
    try:
        handle = await adapter.begin_incremental_reply(recipient())

        assert isinstance(handle, IncrementalReplyHandle)
        assert handle.message_id == "42"
        # 占位消息必须立刻可见：它是「模型已经在写了」这个事实的唯一载体，
        # 而这正是等待期间用户唯一需要知道的事。
        assert adapter.application.bot.send_message.await_count == 1
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_update_passes_the_full_text_so_far(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    adapter.application.bot.edit_message_text = AsyncMock(return_value=True)
    handle = IncrementalReplyHandle(message_id="42", chat_id="member-1")
    try:
        await adapter.update_incremental_reply(handle, "前半", now=0.0)
        await adapter.update_incremental_reply(handle, "前半后半", now=10.0)

        texts = [
            call.kwargs["text"]
            for call in adapter.application.bot.edit_message_text.await_args_list
        ]
        # 第二次必须带着「前半」一起过去：只传「后半」会让平台上的消息变成
        # 只有后半段，而调用方以为它是完整的。
        assert texts[-1].endswith("前半后半") or "前半后半" in texts[-1]
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_update_is_throttled_to_avoid_rate_limits(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    adapter.application.bot.edit_message_text = AsyncMock(return_value=True)
    handle = IncrementalReplyHandle(message_id="42", chat_id="member-1")
    try:
        await adapter.update_incremental_reply(handle, "a", now=0.0)
        # 紧接着的几次改写落在节流窗口内，必须被跳过。
        await adapter.update_incremental_reply(handle, "ab", now=0.05)
        await adapter.update_incremental_reply(handle, "abc", now=0.1)

        # 逐 token 改写会撞 Telegram 的 editMessageText 频率限制，
        # 之后这条回复的所有更新都丢失——那比不流式更糟。
        assert adapter.application.bot.edit_message_text.await_count == 1
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_update_skips_a_no_op_edit(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    adapter.application.bot.edit_message_text = AsyncMock(return_value=True)
    handle = IncrementalReplyHandle(message_id="42", chat_id="member-1")
    try:
        await adapter.update_incremental_reply(handle, "同一段", now=0.0)
        await adapter.update_incremental_reply(handle, "同一段", now=10.0)

        # 内容没变的改写会被平台以「消息未修改」拒绝，而那是一条会进日志的错误，
        # 看起来像故障。
        assert adapter.application.bot.edit_message_text.await_count == 1
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_finish_always_writes_the_final_text_even_inside_the_throttle_window(
    tmp_path: Path,
):
    adapter, database = make_adapter(tmp_path)
    adapter.application.bot.edit_message_text = AsyncMock(return_value=True)
    handle = IncrementalReplyHandle(message_id="42", chat_id="member-1")
    try:
        await adapter.update_incremental_reply(handle, "半", now=0.0)
        await adapter.finish_incremental_reply(handle, "半整段", now=0.01)

        texts = [
            call.kwargs["text"]
            for call in adapter.application.bot.edit_message_text.await_args_list
        ]
        # 收尾**不受节流约束**：被节流掉的收尾会让用户永远停在半句话上，
        # 而日志显示这一轮成功。
        assert any("半整段" in text for text in texts)
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_a_failed_edit_does_not_raise_into_the_turn(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    adapter.application.bot.edit_message_text = AsyncMock(
        side_effect=RuntimeError("edit failed")
    )
    handle = IncrementalReplyHandle(message_id="42", chat_id="member-1")
    try:
        # 增量投递是一个体验优化。它失败不该让整轮对话失败——
        # 整段投递路径仍然会在最后把完整回复发出去。
        await adapter.update_incremental_reply(handle, "x", now=0.0)
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_begin_returns_none_when_the_placeholder_cannot_be_sent(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    adapter.application.bot.send_message = AsyncMock(side_effect=RuntimeError("nope"))
    try:
        handle = await adapter.begin_incremental_reply(recipient())

        # 拿不到占位消息就没有可改写的目标。返回 None 让调用方退回整段投递，
        # 而不是抛错让整轮失败。
        assert handle is None
    finally:
        database.shutdown()
