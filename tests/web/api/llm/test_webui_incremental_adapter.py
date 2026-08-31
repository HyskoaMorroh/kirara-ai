"""WebUI 的增量投递协议本身（需求 4）。

`WebUIAdapter` 是四个渠道里唯一一个「平台侧天然支持逐步显示」的：一条 SSE 事件
就是一次改写，不需要 Telegram 那样的 `editMessageText`。这一组测试钉住它作为
`IncrementalDeliveryAdapter` 的行为，不经过 HTTP 层——路由的契约在
`tests/web/api/llm/test_webui_chat_stream.py`。

核心是**送增量而不是送全文**。协议规定 `update_incremental_reply` 收到的是「到目前
为止的完整文本」（Telegram 需要它来整条改写），而 SSE 是纯追加的：往浏览器送全文
会让每条事件都随回复变长，一段 8 KB 的回复要传 O(n²) 字节。
"""

from __future__ import annotations

import pytest

from kirara_ai.im.adapter import IncrementalDeliveryAdapter
from kirara_ai.im.sender import ChatSender
from kirara_ai.web.api.llm.webui_adapter import WebUIAdapter, WebUIStreamSink


def _recipient() -> ChatSender:
    return ChatSender.from_c2c_chat(user_id="research-1", display_name="Researcher")


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []

    async def __call__(self, kind: str, text: str) -> None:
        self.events.append((kind, text))


def test_the_adapter_satisfies_the_incremental_protocol():
    assert isinstance(WebUIAdapter(), IncrementalDeliveryAdapter)


@pytest.mark.asyncio
async def test_an_adapter_without_a_sink_declines_the_handle():
    """没有 sink 时必须返回 ``None``。

    协议规定调用方要能接受 ``None`` 并退回整段投递，因此这一步就把非流式路由上的
    整条增量链路关掉了——旧路由不会因为这个改动多付任何开销。
    """
    adapter = WebUIAdapter()
    assert await adapter.begin_incremental_reply(_recipient()) is None


@pytest.mark.asyncio
async def test_pushes_carry_only_the_newly_added_text():
    recorder = _Recorder()
    adapter = WebUIAdapter(stream_sink=recorder)
    handle = await adapter.begin_incremental_reply(_recipient())
    assert handle is not None

    # 运行时按协议送「到目前为止的全文」。
    await adapter.update_incremental_reply(handle, "模拟")
    await adapter.update_incremental_reply(handle, "模拟回火")
    await adapter.update_incremental_reply(handle, "模拟回火算法")

    assert recorder.events == [
        ("delta", "模拟"),
        ("delta", "回火"),
        ("delta", "算法"),
    ]
    # 拼回去等于全文：客户端按追加拼接得到的与服务端一致。
    assert "".join(text for _, text in recorder.events) == "模拟回火算法"


@pytest.mark.asyncio
async def test_an_unchanged_text_pushes_nothing():
    """节流让同一份全文被送两次时不该产出一条空事件。"""
    recorder = _Recorder()
    adapter = WebUIAdapter(stream_sink=recorder)
    handle = await adapter.begin_incremental_reply(_recipient())
    await adapter.update_incremental_reply(handle, "模拟")
    await adapter.update_incremental_reply(handle, "模拟")
    assert recorder.events == [("delta", "模拟")]


@pytest.mark.asyncio
async def test_a_rewritten_prefix_becomes_a_reset_not_a_silent_append():
    """上游改写了已交付的前缀时必须让客户端整段替换。

    模型极少这样做，但真发生时按尾巴追加会让浏览器拼出一段与服务端**不同**的
    文本，而两边都认为自己是对的。
    """
    recorder = _Recorder()
    adapter = WebUIAdapter(stream_sink=recorder)
    handle = await adapter.begin_incremental_reply(_recipient())
    await adapter.update_incremental_reply(handle, "模拟回火")
    await adapter.update_incremental_reply(handle, "模拟退火算法")

    assert recorder.events == [
        ("delta", "模拟回火"),
        ("reset", "模拟退火算法"),
    ]


@pytest.mark.asyncio
async def test_finishing_flushes_the_last_segment():
    """收尾必须补齐最后一段。

    否则节流或最后一个片段没触发推送时，浏览器端停在半句话上而日志显示成功。
    """
    recorder = _Recorder()
    adapter = WebUIAdapter(stream_sink=recorder)
    handle = await adapter.begin_incremental_reply(_recipient())
    await adapter.update_incremental_reply(handle, "模拟")
    await adapter.finish_incremental_reply(handle, "模拟回火算法")

    assert recorder.events[-1] == ("delta", "回火算法")
    assert "".join(text for _, text in recorder.events) == "模拟回火算法"


@pytest.mark.asyncio
async def test_finishing_with_the_already_delivered_text_pushes_nothing():
    recorder = _Recorder()
    adapter = WebUIAdapter(stream_sink=recorder)
    handle = await adapter.begin_incremental_reply(_recipient())
    await adapter.update_incremental_reply(handle, "模拟回火")
    await adapter.finish_incremental_reply(handle, "模拟回火")
    assert recorder.events == [("delta", "模拟回火")]


@pytest.mark.asyncio
async def test_send_message_still_captures_the_reply():
    """整段投递路径不受影响：非流式路由靠它取回复。"""
    from kirara_ai.im.message import IMMessage, TextMessage

    adapter = WebUIAdapter()
    message = IMMessage(sender=_recipient(), message_elements=[TextMessage("hello")])
    await adapter.send_message(message, _recipient())
    assert adapter.reply is message


@pytest.mark.asyncio
async def test_the_sink_drains_in_order_and_stops_at_the_sentinel():
    sink = WebUIStreamSink()
    await sink.emit("delta", "甲")
    await sink.emit("delta", "乙")
    sink.close()
    drained = [item async for item in sink.drain()]
    assert drained == [("delta", "甲"), ("delta", "乙")]


@pytest.mark.asyncio
async def test_a_sink_closed_without_events_drains_empty():
    """生产者一个事件都没产出就结束时，消费端必须**结束**而不是永远等待。

    不放哨兵的那条路上界面会停在「正在生成」，而后端已经没有人在干活。
    """
    sink = WebUIStreamSink()
    sink.close()
    assert [item async for item in sink.drain()] == []
