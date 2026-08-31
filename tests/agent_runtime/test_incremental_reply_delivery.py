"""需求 4：`incremental` 模式下，生成中的内容必须真的推给用户。

`aggregate` 只是把流在服务端吃完再发一条完整消息——用户端从来没收到过流式，
等待期间界面上什么都没有。它买到的是首字节超时、静默超时与首字节前的故障转移，
不是「用户看得见的流式」。

`incremental` 在 `aggregate` 之上多一步：把逐步生成的内容推给具备
「编辑已发出消息」能力的渠道。四条边界：

1. **不具备该能力的渠道自动退回 `aggregate`。** 在 QQ / 企业微信上逐步推送只能
   变成几十条碎片消息，比一条完整回复更糟。
2. **推送失败不让整轮失败。** 增量是体验优化；整段投递路径仍会在最后发出完整回复。
3. **工具轮不做增量。** 工具调用的中间产物（`tool_calls`）不是给用户看的文本，
   把它推出去等于把内部状态当回复。
4. **最终文本与整段投递逐字一致**，否则同一个机器人在同一渠道给出两种排版。
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from kirara_ai.agent_runtime.executor import (
    IncrementalReplyDelivery,
    resolve_reply_stream_mode,
)
from kirara_ai.im.adapter import IncrementalReplyHandle


class _EditingChannel:
    """一个具备编辑能力的假渠道，记录每一次写入。"""

    def __init__(self, *, fail_on_begin: bool = False, fail_on_update: bool = False):
        self.fail_on_begin = fail_on_begin
        self.fail_on_update = fail_on_update
        self.begun = 0
        self.updates: list[str] = []
        self.finished: list[str] = []

    async def begin_incremental_reply(self, recipient: Any) -> Optional[IncrementalReplyHandle]:
        self.begun += 1
        if self.fail_on_begin:
            return None
        return IncrementalReplyHandle(message_id="1", chat_id="c")

    async def update_incremental_reply(self, handle: IncrementalReplyHandle, text: str) -> None:
        if self.fail_on_update:
            raise RuntimeError("edit failed")
        self.updates.append(text)

    async def finish_incremental_reply(self, handle: IncrementalReplyHandle, text: str) -> None:
        self.finished.append(text)


class _PlainChannel:
    """没有编辑能力的渠道（QQ / 企业微信的形态）。"""


@pytest.mark.asyncio
async def test_incremental_pushes_the_running_text_to_the_channel():
    channel = _EditingChannel()
    delivery = IncrementalReplyDelivery(channel, recipient=object())

    await delivery.start()
    await delivery.push("你好")
    await delivery.push("你好，世界")
    await delivery.complete("你好，世界。")

    assert channel.begun == 1
    # 每次推的是「到目前为止的完整文本」，不是增量片段。
    assert channel.updates == ["你好", "你好，世界"]
    # 收尾交出最终文本，与整段投递路径逐字一致。
    assert channel.finished == ["你好，世界。"]


@pytest.mark.asyncio
async def test_a_channel_without_editing_support_is_inert():
    delivery = IncrementalReplyDelivery(_PlainChannel(), recipient=object())

    await delivery.start()
    await delivery.push("片段")
    await delivery.complete("完整")

    # 不具备编辑能力的渠道上，增量投递整条链路是空操作——
    # 逐步推送在那里只能变成几十条碎片消息。
    assert delivery.active is False


@pytest.mark.asyncio
async def test_a_failed_begin_disables_further_pushes():
    channel = _EditingChannel(fail_on_begin=True)
    delivery = IncrementalReplyDelivery(channel, recipient=object())

    await delivery.start()
    await delivery.push("片段")
    await delivery.complete("完整")

    # 拿不到占位消息就没有可改写的目标：后续推送必须停止，
    # 而不是对着一个 None 句柄反复尝试。
    assert delivery.active is False
    assert channel.updates == []
    assert channel.finished == []


@pytest.mark.asyncio
async def test_a_failed_push_does_not_raise_and_stops_pushing():
    channel = _EditingChannel(fail_on_update=True)
    delivery = IncrementalReplyDelivery(channel, recipient=object())

    await delivery.start()
    # 不抛：整段投递路径仍会在最后把完整回复发出去。
    await delivery.push("片段")

    # 一次失败之后不再重试：一个正在限流的渠道上反复改写只会加深限流，
    # 而每次失败都要付一次往返。
    assert delivery.active is False


@pytest.mark.asyncio
async def test_empty_text_is_not_pushed():
    channel = _EditingChannel()
    delivery = IncrementalReplyDelivery(channel, recipient=object())

    await delivery.start()
    await delivery.push("")
    await delivery.push("   ")

    # 空文本改写会把占位消息变成空消息（平台会拒绝），或者把「正在生成」抹掉，
    # 让用户以为回复已经结束。
    assert channel.updates == []


@pytest.mark.asyncio
async def test_complete_without_start_is_a_no_op():
    channel = _EditingChannel()
    delivery = IncrementalReplyDelivery(channel, recipient=object())

    await delivery.complete("完整")

    # 一轮里根本没走增量路径（例如工具轮）时，收尾不该凭空发一条消息出来。
    assert channel.finished == []


@pytest.mark.asyncio
async def test_none_channel_is_accepted_and_inert():
    delivery = IncrementalReplyDelivery(None, recipient=None)

    await delivery.start()
    await delivery.push("x")
    await delivery.complete("y")

    # WebUI 的 HTTP 路径没有适配器对象。构造一个惰性实例比在调用点到处判空好：
    # 判空散落在调用点时，漏掉一处就是一次 AttributeError 打断整轮。
    assert delivery.active is False


def test_incremental_mode_still_takes_the_streaming_request_path():
    # `incremental` 是在 `aggregate` 之上加一步推送，取回方式相同。
    # 若它没被算作流式档，首字节超时与首字节前故障转移会一起失效。
    resolved = resolve_reply_stream_mode(
        agent_mode="incremental",
        channel_modes={},
        channel_type="telegram",
        process_mode="off",
    )
    assert resolved == "incremental"
