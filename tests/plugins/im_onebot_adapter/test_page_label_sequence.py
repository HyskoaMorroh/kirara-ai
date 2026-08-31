"""一条回复里的页码必须是一个连续序列（需求 6：「回复内容可能不够全」）。

代码块要单独成条（QQ 上没有复制按钮，整条即代码才能长按全选），因此
`_text_pages` 把正文与代码拆成若干 `CopyablePart` 分别分页。可页码是**每一段
各自**算的，于是一条「正文 + 代码 + 正文」的回复会这样发出去：

    第 1 页 / 共 2 页   ← 正文
    第 2 页 / 共 2 页
    ```python …```      ← 代码
    ↑ 代码已单独成条…
    第 1 页 / 共 2 页   ← 又是第 1 页
    第 2 页 / 共 2 页

用户被告知「共 2 页」却收到 6 条消息，其中「第 1 页」出现两次。他的结论只能是
「内容不全 / 丢数据」——而内容其实一条都没少。这是现场报障里「回复内容可能不够
全有时候出现数据丢失」最直接的解释：数据没丢，页码在说谎。

另外两处同源缺陷：

- **页码被放进了代码消息里。** 长按复制会把「第 1 页 / 共 3 页」一起复制走，
  粘进编辑器就是坏代码——而代码单独成条的全部目的就是让它可以整段复制。
- **复制指引每页都发一次。** 一个 3 页的代码块会跟 3 条「↑ 代码已单独成条」。

## 判据：用户收到几条消息，页码就数到几

页码存在的唯一目的是回答「我收齐了吗」。它必须与**用户实际收到的消息条数**
对齐，而不是与某个内部分段结构对齐。复制指引不是内容页，不参与计数。
"""

from __future__ import annotations

import re

import pytest

from kirara_ai.im.text_render import (
    CODE_COPY_HINT,
    PAGE_LABEL_PATTERN,
    code_copy_hint,
)
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig
from kirara_ai.plugins.im_onebot_adapter.render import render_onebot_text

PROSE = "接受准则决定劣解被采纳的概率，温度越高越宽松，随后逐步收敛至全局最优。" * 60
CODE = "```python\n" + "\n".join(f"    step_{i} = anneal(state, T)" for i in range(120)) + "\n```"

TOTAL_PATTERN = re.compile(r"第 (\d+) 页 / 共 (\d+) 页")


def _adapter(**kwargs) -> OneBotAdapter:
    """一个只用于渲染的适配器实例。

    `_text_pages` 只读 `config`；用 `object.__new__` 避开整套依赖注入，
    与既有渲染测试同一形态。
    """
    adapter = object.__new__(OneBotAdapter)
    adapter.config = OneBotConfig(**kwargs)
    return adapter


def _messages(text: str, **kwargs) -> list[tuple[str, bool]]:
    """模拟 `_render_message_batches` 对一段文本产出的消息序列。

    返回 ``(消息文本, 是否为代码页)``，其中复制指引作为独立消息出现——
    与产品代码同一规则：一段连续的代码页之后只跟一条指引。
    """
    pages = _adapter(**kwargs)._text_pages(render_onebot_text(text))
    messages: list[tuple[str, bool]] = []
    for index, (page, is_code) in enumerate(pages):
        messages.append((page, is_code))
        if not is_code:
            continue
        following = pages[index + 1][1] if index + 1 < len(pages) else False
        if following:
            continue
        run = 1
        cursor = index - 1
        while cursor >= 0 and pages[cursor][1]:
            run += 1
            cursor -= 1
        messages.append((code_copy_hint(run), False))
    return messages


def _is_hint(text: str) -> bool:
    return text.startswith(CODE_COPY_HINT)


def _labels(messages: list[tuple[str, bool]]) -> list[tuple[int, int]]:
    found: list[tuple[int, int]] = []
    for text, _is_code in messages:
        match = TOTAL_PATTERN.search(text)
        if match:
            found.append((int(match.group(1)), int(match.group(2))))
    return found


class TestPageNumbersAreOneSequence:
    def test_a_prose_code_prose_reply_numbers_continuously(self):
        """页码必须是一个连续序列，中间不能重新从 1 开始。

        代码消息不带页码（长按复制会把它一起复制走），但它在序列里**占一位**：
        跳号是刻意的——总数与用户收到的内容条数一致，而缺的那个号正是那条代码。
        """
        messages = _messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}")
        labels = _labels(messages)

        indexes = [index for index, _total in labels]
        assert indexes == sorted(indexes), f"页码不是递增序列：{indexes}"
        assert len(set(indexes)) == len(indexes), f"同一个页码出现了多次：{indexes}"
        assert indexes[0] == 1

    def test_every_page_claims_the_same_total(self):
        labels = _labels(_messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}"))

        totals = {total for _index, total in labels}
        assert len(totals) == 1, f"同一条回复声明了多个总页数：{totals}"

    def test_the_total_matches_the_number_of_content_messages(self):
        """页码要与用户实际收到的内容条数对齐，复制指引不算内容页。"""
        messages = _messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}")
        labels = _labels(messages)

        content_count = sum(1 for text, _ in messages if not _is_hint(text))
        assert labels
        assert labels[0][1] == content_count

    def test_the_last_label_is_the_total(self):
        """最后一条带页码的消息必须说「第 N 页 / 共 N 页」，否则用户以为还有下一条。

        代码块结尾的回复例外——那时最后一条内容是代码，它本来就不带页码。
        """
        labels = _labels(_messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}"))

        last_index, total = labels[-1]
        assert last_index == total

    def test_the_first_page_says_page_one(self):
        labels = _labels(_messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}"))

        assert labels[0][0] == 1


class TestCodeMessagesStayCopyable:
    def test_a_code_message_carries_no_page_label(self):
        """长按复制会把页码一起复制走，粘进编辑器就是坏代码。"""
        messages = _messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}")

        code_messages = [text for text, is_code in messages if is_code]
        assert code_messages
        for text in code_messages:
            assert not PAGE_LABEL_PATTERN.search(text), (
                f"代码消息里有页码：{text[:60]!r}"
            )

    def test_a_code_message_starts_with_its_fence(self):
        messages = _messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}")

        code_messages = [text for text, is_code in messages if is_code]
        for text in code_messages:
            assert text.lstrip().startswith("```")

    def test_the_copy_hint_is_sent_once_per_code_block(self):
        """一个代码块跟一条指引；3 页的代码块跟 3 条是噪声。"""
        messages = _messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}")

        hints = sum(1 for text, _ in messages if _is_hint(text))
        assert hints == 1, f"一个代码块发了 {hints} 条复制指引"

    def test_a_multi_message_code_block_says_how_many_messages_it_spans(self):
        """代码消息不能带页码，但「这段共几条」这个问题仍然要回答。

        否则一段被拆成 5 条的代码，用户无法判断自己是否漏了中间某一条——
        而那正是「数据丢失」的观感来源。
        """
        long_code = (
            "```python\n"
            + "\n".join(f"    value_{index} = compute({index})" for index in range(400))
            + "\n```"
        )
        messages = _messages(f"{PROSE}\n\n{long_code}")

        code_count = sum(1 for _text, is_code in messages if is_code)
        assert code_count > 1
        hints = [text for text, _ in messages if _is_hint(text)]
        assert len(hints) == 1
        assert f"共 {code_count} 条" in hints[0]


class TestSinglePageRepliesAreUnchanged:
    def test_a_short_reply_carries_no_page_label(self):
        """既有行为不变：一页装得下时不加页码。"""
        messages = _messages("一句话就够了。")

        assert len(messages) == 1
        assert not PAGE_LABEL_PATTERN.search(messages[0][0])

    def test_a_short_reply_with_code_still_has_no_labels(self):
        """两条消息（正文 + 代码）不是「分页」，加页码只会让人以为内容被切开了。"""
        messages = _messages("看这段：\n\n```python\nprint(1)\n```\n\n就这样。")

        # 三条：正文、代码、复制指引。它们都在一页之内，因此不该有页码。
        assert not any(
            PAGE_LABEL_PATTERN.search(text) for text, _ in messages
        ), [text[:20] for text, _ in messages]


class TestIsolationCanStillBeTurnedOff:
    def test_mixed_layout_keeps_a_single_sequence(self):
        """关掉代码单独成条时只有一段，页码本来就是连续的——不能被新逻辑改坏。"""
        labels = _labels(
            _messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}", isolate_code_messages=False)
        )

        indexes = [index for index, _total in labels]
        assert indexes == list(range(1, len(indexes) + 1))


class TestNoContentIsLost:
    def test_every_code_line_survives_pagination(self):
        """页码是否说谎与内容是否真丢是两件事；这条钉住后者。"""
        messages = _messages(f"{PROSE}\n\n{CODE}\n\n{PROSE}")
        joined = "\n".join(text for text, _ in messages)

        for index in range(120):
            assert f"step_{index} = anneal(state, T)" in joined
