"""Cross-channel page-marker and renderer-ownership contracts.

Two divergences this pins:

1. WeCom carried its own splitter that prefixed pages with ``[i/N]`` while every
   other channel used ``第 N 页 / 共 M 页``. One bot therefore produced two
   different page-marker formats depending on which app the user was in.
2. That splitter duplicated `split_structured_text`'s job (paragraph-aware
   splitting, table framing, code fencing), so the two could — and did — drift.
"""

from __future__ import annotations

import re

from kirara_ai.im.text_render import PAGE_LABEL_PATTERN, split_structured_text
from kirara_ai.plugins.im_wecom_adapter.delegates import (
    markdown_to_plain_text,
    split_long_message,
)

LEGACY_WECOM_MARKER = re.compile(r"^\[\d+/\d+\]")


def long_plain_text(paragraphs: int = 60) -> str:
    return "\n\n".join(f"第 {index} 段内容，用于触发分段。" * 6 for index in range(paragraphs))


def test_wecom_pages_use_the_shared_page_marker():
    pages = split_long_message(long_plain_text(), max_length=600)

    assert len(pages) > 1
    for page in pages:
        assert PAGE_LABEL_PATTERN.search(page), page[:60]
        assert not LEGACY_WECOM_MARKER.match(page), "WeCom 不应再使用 [i/N] 页码格式"


def test_wecom_and_shared_splitter_agree_on_page_count():
    text = long_plain_text()

    wecom_pages = split_long_message(text, max_length=600)
    shared_pages = split_structured_text(
        text, max_bytes=600, max_total_bytes=None, code_style="wecom"
    )

    assert wecom_pages == shared_pages


def test_short_wecom_text_is_not_paginated():
    pages = split_long_message("一句短回复。", max_length=1800)

    assert pages == ["一句短回复。"]
    assert not PAGE_LABEL_PATTERN.search(pages[0])


def test_wecom_pages_respect_the_byte_budget():
    limit = 600
    pages = split_long_message(long_plain_text(), max_length=limit)

    for page in pages:
        assert len(page.encode("utf-8")) <= limit


def test_wecom_code_block_keeps_its_own_fence_style():
    source = "说明文字\n\n```python\ndef execute(text):\n    return text\n```"

    rendered = markdown_to_plain_text(source)

    # WeCom cannot render a markdown fence, so its own bracket fence stays; that
    # is a rendering difference, not a second parsing pipeline.
    assert "［代码 python］" in rendered
    assert "```" not in rendered
    # Indentation inside the code body must survive verbatim.
    assert "    return text" in rendered


def test_wecom_pagination_does_not_lose_content():
    text = long_plain_text()
    pages = split_long_message(text, max_length=600)

    stripped = "".join(PAGE_LABEL_PATTERN.sub("", page) for page in pages)
    # Every non-whitespace character of the source must survive pagination.
    assert re.sub(r"\s", "", stripped) == re.sub(r"\s", "", text)
