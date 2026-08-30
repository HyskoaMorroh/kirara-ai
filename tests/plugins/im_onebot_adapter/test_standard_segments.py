"""需求 11：OneBot V11 标准消息段的入站覆盖。

审计发现一批标准段在 `create_message_element` 里没有分支，因此到达时被静默丢弃
（返回 `None`）。丢弃本身是安全的，但后果是**整条消息可能变成空内容**：
一条只含 `poke`、`location` 或 `contact` 的消息进来，元素列表为空，
用户看到的是「机器人毫无反应」——而那和「机器人挂了」在观感上完全一样。

参考实现（`chatgpt-mirai-qq-bot-onebot-adapter`）同样没有这些段，
所以这不是「照抄漏了」，而是两边共同的空白。这里补上：每一段都给出
**可读的纯文本占位**，让下游至少知道「这里有一条什么」。

刻意不做的事：不把这些段变成富媒体元素。`location` 不是图片、`contact` 不是文件，
硬映射会让下游按错误的类型处理它们。占位文本是诚实的表达。
"""

from __future__ import annotations

import pytest

from kirara_ai.im.message import TextMessage
from kirara_ai.plugins.im_onebot_adapter.utils.message import create_message_element


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    def debug(self, message: str) -> None:
        return None


def _convert(msg_type: str, data: dict | None = None, **kwargs):
    return create_message_element(msg_type, data or {}, _Logger(), **kwargs)


@pytest.mark.parametrize(
    ("msg_type", "data", "expected_fragment"),
    [
        # 拍一拍：QQ 里是一个交互动作，纯文本侧只能说明「有人拍了你」。
        ("poke", {"type": "poke", "id": "1"}, "拍一拍"),
        # 位置分享：带标题时优先显示标题，它比经纬度更有信息量。
        ("location", {"lat": "39.9", "lon": "116.4", "title": "天安门"}, "天安门"),
        ("location", {"lat": "39.9", "lon": "116.4"}, "位置"),
        # 名片分享。
        ("contact", {"type": "qq", "id": "10001"}, "推荐联系人"),
        ("contact", {"type": "group", "id": "20002"}, "推荐群"),
        # 链接 / 音乐分享：标题是唯一值得展示的部分。
        ("share", {"url": "https://example.com", "title": "一篇文章"}, "一篇文章"),
        ("music", {"type": "163", "id": "123"}, "音乐分享"),
        # XML 卡片：内容是平台私有结构，不展开。
        ("xml", {"data": "<msg/>"}, "XML 卡片"),
        # 匿名发言标记。
        ("anonymous", {"flag": "abc"}, "匿名"),
    ],
)
def test_standard_segments_produce_readable_placeholders(
    msg_type, data, expected_fragment
):
    element = _convert(msg_type, data)

    assert isinstance(element, TextMessage), f"{msg_type} 段被静默丢弃"
    assert expected_fragment in element.text


def test_markdown_segment_keeps_its_text_instead_of_a_placeholder():
    """Markdown 段的 `content` 就是正文，必须原样保留而不是换成占位。

    换成「[Markdown]」会把一条真实回复变成一个标记——这是丢内容，
    比丢一个交互动作严重得多。
    """
    element = _convert("markdown", {"content": "# 标题\n正文"})

    assert isinstance(element, TextMessage)
    assert element.text == "# 标题\n正文"


def test_a_markdown_segment_without_content_is_dropped_rather_than_faked():
    """没有正文就没有内容可保留，返回 None 而不是编一个占位。"""
    assert _convert("markdown", {}) is None


def test_location_places_coordinates_when_there_is_no_title():
    element = _convert("location", {"lat": "39.9", "lon": "116.4"})

    assert isinstance(element, TextMessage)
    # 经纬度必须出现：没有标题时它是唯一的信息。
    assert "39.9" in element.text and "116.4" in element.text


def test_an_unknown_segment_is_still_ignored():
    """未知段保持忽略。

    给每一个未知类型都造一个占位，会让上游任何私有扩展都在回复里留下噪声。
    这条边界不变。
    """
    assert _convert("some_vendor_private_segment", {"x": 1}) is None


def test_conversion_failures_are_logged_and_do_not_raise():
    logger = _Logger()

    # `data` 不是映射：取字段会抛，必须被捕获成一次警告。
    element = create_message_element("location", None, logger)  # type: ignore[arg-type]

    assert element is None
    assert logger.warnings
