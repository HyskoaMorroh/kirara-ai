"""自身消息回声与新增消息段的入站处理。

回声必须在去重之前丢掉：它的 `message_id` 与入站消息不同，去重收据看不出这是
自己发的，机器人会开始回复自己。`mface` / `forward` 此前没有映射，只发一个市场
表情的消息到达时元素列表为空——整条消息被当成空内容，用户看到机器人毫无反应。
"""

from __future__ import annotations

import pytest

from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.utils.message import create_message_element


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.debugs: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def debug(self, message: str) -> None:
        self.debugs.append(message)


def bare_adapter() -> OneBotAdapter:
    adapter = object.__new__(OneBotAdapter)
    adapter.logger = _Logger()
    return adapter


@pytest.mark.parametrize(
    "event",
    [
        {"post_type": "message_sent", "self_id": 100, "user_id": 200, "message_id": 1},
        {"post_type": "message", "self_id": 100, "user_id": 100, "message_id": 2},
    ],
)
def test_self_originated_events_are_recognized(event):
    assert bare_adapter()._is_self_originated(event) is True


@pytest.mark.parametrize(
    "event",
    [
        {"post_type": "message", "self_id": 100, "user_id": 200, "message_id": 3},
        {"post_type": "message", "message_id": 4},
        {},
    ],
)
def test_normal_inbound_events_are_not_treated_as_self_originated(event):
    assert bare_adapter()._is_self_originated(event) is False


@pytest.mark.asyncio
async def test_self_echo_is_dropped_before_dedup_and_dispatch():
    """回声必须在去重与派发之前被丢掉。"""
    adapter = bare_adapter()
    dispatched: list[object] = []

    class _Dispatcher:
        async def dispatch(self, *_args, **_kwargs):
            dispatched.append(_args)

    adapter.dispatcher = _Dispatcher()
    called = {"receipts": False}

    def _receipts():
        called["receipts"] = True
        return None

    adapter._ensure_inbound_receipts = _receipts  # type: ignore[method-assign]

    await adapter._handle_message(
        {"post_type": "message_sent", "self_id": 1, "user_id": 1, "message_id": 9}
    )

    assert dispatched == []
    assert called["receipts"] is False, "回声不应消耗去重收据"


def test_mface_with_downloaded_bytes_becomes_an_image():
    """已经下载到字节时按图片处理（走的是适配器的 media_data 路径）。

    不用真实 URL：``ImageMessage(url=...)`` 会在构造时尝试下载，测试里那会变成
    一次真实网络请求。适配器本身也是先下载再构造，因此这里覆盖的是同一条路径。
    """
    from kirara_ai.im.message import ImageMessage

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    element = create_message_element(
        "mface", {"summary": "[开心]"}, _Logger(), media_data=png
    )

    assert isinstance(element, ImageMessage)


def test_mface_without_a_url_falls_back_to_its_summary():
    from kirara_ai.im.message import TextMessage

    element = create_message_element("mface", {"summary": "[开心]"}, _Logger())

    assert isinstance(element, TextMessage)
    assert element.text == "[开心]"


def test_mface_without_any_metadata_still_yields_a_placeholder():
    """绝不能返回 None：那会让「只发了一个表情」变成空消息。"""
    from kirara_ai.im.message import TextMessage

    element = create_message_element("mface", {}, _Logger())

    assert isinstance(element, TextMessage)
    assert element.text == "[表情]"


def test_forward_segment_yields_a_visible_placeholder():
    from kirara_ai.im.message import TextMessage

    element = create_message_element("forward", {"id": "abc123"}, _Logger())

    assert isinstance(element, TextMessage)
    assert "合并转发" in element.text
    assert "abc123" in element.text


def test_dice_and_rps_segments_report_their_result():
    from kirara_ai.im.message import TextMessage

    dice = create_message_element("dice", {"result": 4}, _Logger())
    rps = create_message_element("rps", {"result": 2}, _Logger())
    shake = create_message_element("shake", {}, _Logger())

    for element in (dice, rps, shake):
        assert isinstance(element, TextMessage)
    assert "4" in dice.text
    assert "猜拳" in rps.text
    assert "抖动" in shake.text


def test_unknown_segment_types_are_still_ignored():
    """未知段保持原行为（返回 None），避免为每个新段编造占位文本。"""
    assert create_message_element("some_future_type", {}, _Logger()) is None
