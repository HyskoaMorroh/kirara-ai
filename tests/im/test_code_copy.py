"""QQ needs a real copy path for code, not a fake button.

QQ's OneBot message model has no interactive button, so a "copy" affordance
cannot be rendered — and rendering a dead one would be worse than none
(1.txt 19.3 forbids showing an unusable button). The equivalent path is to make
the code its own message: a standalone message whose entire body is the code
lets a long-press → select-all → copy in any QQ client yield exactly the code,
with no prose or page marker mixed in.

These tests pin that isolation, and pin that nothing is lost or reordered when
prose and code are interleaved.
"""

from __future__ import annotations

import pytest

from kirara_ai.im.text_render import (
    CODE_COPY_HINT,
    split_for_copyable_code,
)


def test_a_code_block_becomes_its_own_part():
    parts = split_for_copyable_code("说明文字\n\n```python\nprint(1)\n```\n\n后续说明")

    assert len(parts) == 3
    assert parts[0].is_code is False and parts[0].text == "说明文字"
    assert parts[1].is_code is True
    assert parts[2].is_code is False and parts[2].text == "后续说明"


def test_the_code_part_contains_only_the_code_and_its_fence():
    parts = split_for_copyable_code("前言\n\n```python\nprint(1)\nprint(2)\n```")

    code = next(part for part in parts if part.is_code)
    assert code.text == "```python\nprint(1)\nprint(2)\n```"
    assert "前言" not in code.text


def test_the_code_part_exposes_the_bare_code_for_copying():
    parts = split_for_copyable_code("```python\nprint(1)\nprint(2)\n```")

    code = next(part for part in parts if part.is_code)
    # The fence is display sugar; what the user copies must be the code itself.
    assert code.code == "print(1)\nprint(2)"
    assert code.language == "python"


def test_indentation_inside_the_code_is_preserved_byte_for_byte():
    source = "```python\ndef execute(text):\n    if text:\n        return text\n```"

    code = next(part for part in split_for_copyable_code(source) if part.is_code)

    assert code.code == "def execute(text):\n    if text:\n        return text"


def test_text_without_code_is_returned_as_one_part():
    parts = split_for_copyable_code("只有普通文字。")

    assert len(parts) == 1
    assert parts[0].is_code is False
    assert parts[0].text == "只有普通文字。"


def test_multiple_code_blocks_each_get_their_own_part():
    source = "a\n\n```py\n1\n```\n\nb\n\n```sh\nls\n```"

    parts = split_for_copyable_code(source)

    assert [part.is_code for part in parts] == [False, True, False, True]
    assert [part.language for part in parts if part.is_code] == ["py", "sh"]


def test_a_fence_without_a_language_is_still_isolated():
    parts = split_for_copyable_code("```\nplain code\n```")

    code = next(part for part in parts if part.is_code)
    assert code.language is None
    assert code.code == "plain code"


def test_no_content_is_lost_across_the_split():
    source = "开头\n\n```py\nx = 1\n```\n\n中间\n\n```sh\necho hi\n```\n\n结尾"

    parts = split_for_copyable_code(source)
    joined = "\n\n".join(part.text for part in parts)

    for fragment in ("开头", "x = 1", "中间", "echo hi", "结尾"):
        assert fragment in joined


def test_an_unclosed_fence_is_not_treated_as_code():
    """A truncated reply must not swallow the remaining prose into a code part."""
    parts = split_for_copyable_code("说明\n\n```py\nx = 1\n还没闭合")

    assert all(part.is_code is False for part in parts)
    assert "还没闭合" in "\n\n".join(part.text for part in parts)


def test_the_copy_hint_is_short_and_mentions_长按():
    # The hint replaces a button, so it has to tell the user what to actually do.
    assert len(CODE_COPY_HINT) <= 40
    assert "长按" in CODE_COPY_HINT or "复制" in CODE_COPY_HINT


def test_empty_input_yields_no_parts():
    assert split_for_copyable_code("") == []
    assert split_for_copyable_code("   \n  ") == []


@pytest.mark.parametrize("source", ["```py\n```", "```py\n\n```"])
def test_an_empty_code_block_is_dropped_rather_than_sent_as_a_blank_message(source: str):
    assert split_for_copyable_code(source) == []
