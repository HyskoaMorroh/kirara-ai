import re

import pytest

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.im import text_render


def _without_page_label(page: str) -> str:
    return re.sub(r"^第 \d+ 页 / 共 \d+ 页\n", "", page)


def test_parse_text_document_preserves_structured_markdown_blocks():
    source = (
        "# 标题\n\n"
        "普通段落含 [项目链接](https://example.com/docs)。\n\n"
        "- 第一项\n- 第二项\n\n"
        "> 引用内容\n\n"
        "| 参数 | 含义 |\n| --- | --- |\n| T | 温度 |\n\n"
        "```python\nprint('hello')\n```\n\n"
        "公式 $T \\to 0$。"
    )

    document = text_render.parse_text_document(source)

    assert [block.kind for block in document.blocks] == [
        text_render.TextBlockKind.HEADING,
        text_render.TextBlockKind.PARAGRAPH,
        text_render.TextBlockKind.LIST,
        text_render.TextBlockKind.QUOTE,
        text_render.TextBlockKind.TABLE,
        text_render.TextBlockKind.CODE,
        text_render.TextBlockKind.PARAGRAPH,
    ]
    assert document.blocks[0].level == 1
    assert document.blocks[1].links[0].url == "https://example.com/docs"
    assert document.blocks[4].rows == (("参数", "含义"), ("T", "温度"))
    assert document.blocks[5].language == "python"
    assert document.blocks[5].text == "print('hello')"


def test_render_plain_text_degrades_math_and_tables_without_touching_code():
    source = (
        "变化 $T \\to 0$，面积 $a \\times b$。\n\n"
        "| 参数 | 值 |\n| --- | --- |\n| T | 0 |\n\n"
        "```python\nformula = r'$T \\to 0$'\n```"
    )

    rendered = text_render.render_plain_text(
        text_render.parse_text_document(source)
    )

    prose, code = rendered.split("```python", 1)
    assert "$" not in prose
    assert "\\to" not in prose
    assert "→" in prose and "×" in prose
    assert "┌" in prose and "└" in prose
    assert r"formula = r'$T \to 0$'" in code


def test_render_plain_text_degrades_common_math_delimiters_and_inequalities():
    source = r"$a \le b$，\(x \ge y\)，以及 \[z \rightarrow 1\]。"

    rendered = text_render.render_plain_text(source)

    assert "$" not in rendered
    assert r"\(" not in rendered and r"\)" not in rendered
    assert r"\[" not in rendered and r"\]" not in rendered
    assert r"\le" not in rendered and r"\ge" not in rendered
    assert "a ≤ b" in rendered
    assert "x ≥ y" in rendered
    assert "z → 1" in rendered


def test_split_structured_text_uses_utf8_limit_labels_and_keeps_code_complete():
    source = "```python\n" + ("print('中文🙂')\n" * 80) + "```"

    pages = text_render.split_structured_text(source, max_bytes=180)

    assert len(pages) > 1
    assert all(len(page.encode("utf-8")) <= 180 for page in pages)
    assert all(page.startswith("第 ") for page in pages)
    assert all(_without_page_label(page).count("```") == 2 for page in pages)
    recovered = "".join(
        _without_page_label(page)
        .removeprefix("```python\n")
        .removesuffix("\n```")
        for page in pages
    )
    assert recovered == "print('中文🙂')\n" * 80


def test_split_structured_text_repeats_table_header_and_never_loses_rows():
    source = "\n".join(
        [
            "┌──────┬──────────┐",
            "│ 参数 │ 含义     │",
            "├──────┼──────────┤",
            *[f"│ {index:>4} │ 第{index:02d}项    │" for index in range(30)],
            "└──────┴──────────┘",
        ]
    )

    pages = text_render.split_structured_text(source, max_bytes=260)

    assert len(pages) > 1
    assert all(len(page.encode("utf-8")) <= 260 for page in pages)
    for page in pages:
        body = _without_page_label(page)
        assert body.startswith("┌──────┬──────────┐\n│ 参数 │ 含义     │\n├")
        assert body.endswith("└──────┴──────────┘")
    recovered_rows = [
        line
        for page in pages
        for line in _without_page_label(page).splitlines()
        if re.match(r"^│\s+\d+\s+│", line)
    ]
    assert recovered_rows == [f"│ {index:>4} │ 第{index:02d}项    │" for index in range(30)]


def test_split_structured_text_keeps_markdown_links_atomic():
    link = "[完整链接](https://example.com/a-very-long-path)"
    source = ("前置内容。" * 5) + f"\n\n{link}\n\n" + ("后置内容。" * 5)

    pages = text_render.split_structured_text(source, max_bytes=100)

    assert len(pages) > 1
    bodies = [_without_page_label(page) for page in pages]
    assert sum(link in body for body in bodies) == 1
    assert all(
        ("[完整链接]" not in body and "https://example.com" not in body)
        or link in body
        for body in bodies
    )


def test_im_message_delivery_timeline_is_optional_immutable_and_serialized():
    """The timeline must survive serialization so a slow reply can be explained.

    This previously asserted the opposite — that `to_dict()` excluded the
    timeline. That exclusion was the defect: the stage timestamps lived only in
    memory, so nothing could be logged or returned afterwards and "why did that
    reply take 30 seconds" had no answer. Immutability of a recorded event and
    the timezone-aware timestamp are still required.
    """
    message = IMMessage(
        ChatSender.get_bot_sender(),
        [TextMessage("hello")],
    )
    empty = message.to_dict()
    assert empty["delivery_timeline"] == []
    assert empty["delivery_durations"] == {}

    event = message.record_delivery_stage("formatting_started", adapter="test")

    assert message.delivery_timeline == (event,)
    assert event.timestamp.tzinfo is not None
    assert event.details["adapter"] == "test"
    with pytest.raises(TypeError):
        event.details["adapter"] = "changed"  # type: ignore[index]

    serialized = message.to_dict()
    assert [item["stage"] for item in serialized["delivery_timeline"]] == [
        "formatting_started"
    ]
    # Timestamps have to be JSON-safe strings for a log line or an API response.
    assert isinstance(serialized["delivery_timeline"][0]["timestamp"], str)
    assert serialized["delivery_timeline"][0]["details"]["adapter"] == "test"
