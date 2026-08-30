"""需求 11：合并转发不能只留一个占位。

`forward` 段目前产出 `[合并转发：<id>]`。这在「不静默丢消息」这一层是对的
（比返回 `None` 让整条消息变空好），但它把内容也一起丢了：用户转发了一段
对话过来问「这里说的对吗」，模型收到的只有一个 ID。

参考实现同样没有展开——`get_forward_msg` 在两边都没有调用点。这是共同空白，
不是照抄漏了。

展开有真实成本，因此**默认关闭**，并且必须有三道边界：

1. **深度上限**。合并转发可以嵌套（转发里包含另一段转发），无界递归会把一次
   消息转换变成一串上游调用。
2. **条数上限**。一段转发可能有几百条；全部展开会让 system 消息爆掉，
   而排版层随后又要把它切成几十页。
3. **失败回落到占位**。展开是增强，不是前提：`get_forward_msg` 失败（权限不足、
   ID 过期、上游未实现）时必须退回原来的占位文本，绝不能让整条消息失败。
"""

from __future__ import annotations

from typing import Any

import pytest

from kirara_ai.im.message import TextMessage
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


class _Logger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str) -> None:
        self.messages.append(message)

    def info(self, message: str) -> None:
        self.messages.append(message)

    def debug(self, message: str) -> None:
        return None

    def error(self, message: str) -> None:
        self.messages.append(message)


def _adapter(responses: dict[str, Any], **config_kwargs) -> tuple[OneBotAdapter, list[str]]:
    adapter = object.__new__(OneBotAdapter)
    adapter.logger = _Logger()
    adapter.config = OneBotConfig(**config_kwargs)
    adapter.connections = {}
    requested: list[str] = []

    async def call_action(action: str, **params: Any) -> Any:
        if action != "get_forward_msg":
            return {}
        forward_id = str(params.get("id"))
        requested.append(forward_id)
        if forward_id not in responses:
            raise RuntimeError("forward message not found")
        return responses[forward_id]

    adapter._call_action = call_action  # type: ignore[method-assign]
    return adapter, requested


def _node(text: str, nickname: str = "某人") -> dict[str, Any]:
    return {
        "type": "node",
        "data": {
            "nickname": nickname,
            "content": [{"type": "text", "data": {"text": text}}],
        },
    }


def _event(forward_id: str = "fw-1") -> dict[str, Any]:
    return {
        "self_id": 100,
        "user_id": 200,
        "message_id": 1,
        "message": [{"type": "forward", "data": {"id": forward_id}}],
    }


@pytest.mark.asyncio
async def test_expansion_is_off_by_default():
    """默认不展开：行为与升级前逐字节一致，也不会凭空多出上游调用。"""
    adapter, requested = _adapter({"fw-1": {"messages": [_node("秘密")]}})

    message = await adapter.convert_to_message(_event())

    assert not requested
    assert "[合并转发：fw-1]" in message.content


@pytest.mark.asyncio
async def test_enabled_expansion_inlines_the_forwarded_texts():
    adapter, requested = _adapter(
        {"fw-1": {"messages": [_node("第一句", "甲"), _node("第二句", "乙")]}},
        expand_forward_messages=True,
    )

    message = await adapter.convert_to_message(_event())

    assert requested == ["fw-1"]
    assert "第一句" in message.content
    assert "第二句" in message.content
    # 发言人要保留：一段对话去掉说话的人就无法判断「谁说的对」。
    assert "甲" in message.content and "乙" in message.content


@pytest.mark.asyncio
async def test_a_failed_expansion_falls_back_to_the_placeholder():
    """展开是增强而不是前提：失败必须退回占位，不能让整条消息失败。"""
    adapter, requested = _adapter({}, expand_forward_messages=True)

    message = await adapter.convert_to_message(_event("missing"))

    assert requested == ["missing"]
    assert "[合并转发：missing]" in message.content
    assert adapter.logger.messages, "失败必须留下一条日志，否则无法排查"


@pytest.mark.asyncio
async def test_the_node_count_is_capped():
    """条数有上限：几百条全部展开会让 system 消息爆掉。"""
    nodes = [_node(f"第 {index} 句") for index in range(50)]
    adapter, _ = _adapter(
        {"fw-1": {"messages": nodes}},
        expand_forward_messages=True,
        forward_max_nodes=3,
    )

    message = await adapter.convert_to_message(_event())

    assert "第 0 句" in message.content
    assert "第 2 句" in message.content
    assert "第 3 句" not in message.content
    # 必须说明被截断了：静默截断会让人以为转发里只有三条。
    assert "省略" in message.content or "截断" in message.content


@pytest.mark.asyncio
async def test_nested_forwards_stop_at_the_depth_limit():
    """嵌套转发不得无界递归——每一层都是一次真实的上游调用。"""
    adapter, requested = _adapter(
        {
            "fw-1": {
                "messages": [
                    _node("外层"),
                    {"type": "forward", "data": {"id": "fw-2"}},
                ]
            },
            "fw-2": {
                "messages": [
                    _node("内层"),
                    {"type": "forward", "data": {"id": "fw-3"}},
                ]
            },
            "fw-3": {"messages": [_node("再内层")]},
        },
        expand_forward_messages=True,
        forward_max_depth=2,
    )

    message = await adapter.convert_to_message(_event())

    assert "外层" in message.content
    assert "内层" in message.content
    # 第三层超出深度上限：不再请求，也不产出它的内容。
    assert "fw-3" not in requested
    assert "再内层" not in message.content


@pytest.mark.asyncio
async def test_a_self_referencing_forward_cannot_loop():
    """自引用是最短的无限递归，必须在第一次重复时就停。"""
    adapter, requested = _adapter(
        {"fw-1": {"messages": [_node("我"), {"type": "forward", "data": {"id": "fw-1"}}]}},
        expand_forward_messages=True,
        forward_max_depth=5,
    )

    message = await adapter.convert_to_message(_event())

    assert requested.count("fw-1") == 1
    assert "我" in message.content


@pytest.mark.asyncio
async def test_media_inside_a_forward_becomes_a_readable_marker():
    """转发里的图片不下载——那会把一次消息转换变成一串下载。

    给出可读标记即可：模型至少知道「这里有一张图」。
    """
    adapter, _ = _adapter(
        {
            "fw-1": {
                "messages": [
                    {
                        "type": "node",
                        "data": {
                            "nickname": "甲",
                            "content": [
                                {"type": "image", "data": {"url": "https://x/y.png"}}
                            ],
                        },
                    }
                ]
            }
        },
        expand_forward_messages=True,
    )

    message = await adapter.convert_to_message(_event())

    assert "https://x/y.png" not in message.content
    assert "[图片]" in message.content


@pytest.mark.asyncio
async def test_the_expansion_is_a_single_text_element():
    """展开结果是一条文本元素，交给统一排版层处理。

    拆成几十个元素会让分页层按元素边界切，得到的页数与内容长度无关。
    """
    adapter, _ = _adapter(
        {"fw-1": {"messages": [_node("一"), _node("二")]}},
        expand_forward_messages=True,
    )

    message = await adapter.convert_to_message(_event())

    texts = [
        element for element in message.message_elements
        if isinstance(element, TextMessage)
    ]
    assert len(texts) == 1
