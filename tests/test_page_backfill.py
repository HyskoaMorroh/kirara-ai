"""分页不能因为「块多」就把每个块单独发一条（需求 19.4）。

`_split_structured_body` 在每个代码围栏和每个框线表处 `flush_regular()`，然后把
那个块的分页结果直接 `extend` 进 `chunks`——**从不回填**。于是一段
「标题 + 正文 + 代码 + 表格」× N 的技术回答，每个块各占一页，而每页只装了几十字节。

实测（单页上限 3800 字节，页数上限 100）：

| 小节数 | 源大小 | 页数 | 均页 | 利用率 | 截断 |
|---|---|---|---|---|---|
| 10 | 2898 B | 1 | 2898 B | 76.3% | 否 |
| 20 | 5828 B | 80 | 96 B | **2.5%** | 否 |
| 30 | 8758 B | 100 | 98 B | **2.6%** | **是** |

两个后果，都对应 19.4 的硬性要求：

1. **一条 8.7 KB 的回复被截断**，而它离单页上限 × 页数上限（380 KB）差两个数量级。
   19.4 要求「全部发送、内容不得丢失」。
2. **用户被 80–100 条消息轰炸**。19.4 要求「按平台安全长度拆分」，
   而每页 96 字节显然不是「安全长度」的意思——它是「一个块一条」。

这是「回复内容可能不够全有时候出现数据丢失」比页码更靠根本的成因：页码那条让人
**以为**内容不全，这条是内容**真的**没了。

## 判据

块边界仍然是**优先**切点，但不是**强制**切点。相邻的小块只要合起来还装得下，
就应该留在同一页里。四条边界：

1. **超过上限时仍然在块边界切。** 回填只针对「合起来还装得下」的情况。
2. **单个块自身超限时的行为不变**：代码块补围栏、表格补边框，逐段发。
3. **顺序绝不改变。** 回填只合并相邻块，不重排。
4. **代码围栏与表格边框在合并后仍然完整。** 合并的是「已经完整的块」，
   不是把两个块的内部拼在一起。
"""

from __future__ import annotations

from kirara_ai.im import text_render
from kirara_ai.im.text_render import split_structured_text
from kirara_ai.plugins.im_onebot_adapter.render import render_onebot_text

SECTION = """## 小节 {n}

这一段说明温度参数的作用，以及它如何影响接受概率。

```python
value_{n} = compute({n})
```

| 参数 | 值 |
| --- | --- |
| T | {n} |
"""


def _source(section_count: int) -> str:
    return "\n\n".join(SECTION.format(n=index) for index in range(section_count))


def _rendered(section_count: int) -> str:
    """走真实管线：先渲染再分页。

    直接把原始 Markdown 交给 `split_structured_text` 是错的——它按 `┌`（已渲染的
    框线表）与围栏识别块，而原始 Markdown 里表格还是 `|` 管道符，于是整段被当作
    普通正文合并，测不到「一个块一条」那个缺陷。
    """
    return render_onebot_text(_source(section_count))


def _pages(source: str, limit: int = 3800) -> list[str]:
    return split_structured_text(source, max_bytes=limit, max_pages=100)


def _fill_ratio(pages: list[str], limit: int = 3800) -> float:
    if not pages:
        return 0.0
    used = sum(len(page.encode("utf-8")) for page in pages)
    return used / (len(pages) * limit)


class TestSmallBlocksAreBackfilled:
    def test_twenty_sections_do_not_become_eighty_messages(self):
        """5.8 KB 的回复此前变成 80 条，每条 96 字节。"""
        pages = _pages(_rendered(20))

        assert len(pages) <= 4, f"{len(pages)} 条消息装 5.8 KB 内容"

    def test_the_fill_ratio_is_reasonable(self):
        pages = _pages(_rendered(20))

        assert _fill_ratio(pages) > 0.5, (
            f"页利用率 {_fill_ratio(pages):.1%}，每页只装了一点点"
        )

    def test_thirty_sections_are_no_longer_truncated(self):
        """8.7 KB 离 380 KB 的理论预算差两个数量级，不该被截断。"""
        from kirara_ai.plugins.im_onebot_adapter.render import (
            paginate_onebot_text_or_truncate,
        )

        _pages_out, truncated = paginate_onebot_text_or_truncate(_source(30))

        assert truncated is False

    def test_a_hundred_sections_still_fit(self):
        from kirara_ai.plugins.im_onebot_adapter.render import (
            paginate_onebot_text_or_truncate,
        )

        pages, truncated = paginate_onebot_text_or_truncate(_source(100))

        assert truncated is False
        assert len(pages) < 100


class TestOrderAndStructureAreKept:
    def test_content_order_is_unchanged(self):
        pages = _pages(_rendered(20))
        joined = "\n".join(pages)

        positions = [joined.index(f"小节 {index}") for index in range(20)]
        assert positions == sorted(positions)

    def test_no_content_is_lost(self):
        pages = _pages(_rendered(20))
        joined = "\n".join(pages)

        for index in range(20):
            assert f"value_{index} = compute({index})" in joined

    def test_every_page_stays_within_the_limit(self):
        pages = _pages(_rendered(20))

        for page in pages:
            assert len(page.encode("utf-8")) <= 3800

    def test_code_fences_stay_balanced_on_every_page(self):
        pages = _pages(_rendered(20))

        for page in pages:
            assert page.count("```") % 2 == 0, page[:120]

    def test_box_tables_keep_their_borders(self):
        pages = _pages(_rendered(20))

        for page in pages:
            assert page.count("┌") == page.count("└"), page[:120]


class TestOversizedBlocksAreUnchanged:
    def test_a_long_code_block_is_still_split_with_complete_fences(self):
        body = "\n".join(f"print({index})" for index in range(400))
        pages = _pages(f"```python\n{body}\n```")

        code_pages = [page for page in pages if "```python" in page]
        assert len(code_pages) > 1
        for page in code_pages:
            assert page.count("```") == 2

    def test_a_long_table_still_repeats_its_header(self):
        header = "| 参数 | 值 |\n| --- | --- |"
        rows = "\n".join(f"| p{index} | {index} |" for index in range(200))
        # 必须先渲染：`split_structured_text` 按 `┌` 识别框线表，
        # 原始 Markdown 里表格还是管道符，那样测的是普通正文分页。
        pages = _pages(render_onebot_text(f"{header}\n{rows}"), limit=400)

        table_pages = [page for page in pages if "┌" in page]
        assert len(table_pages) > 1
        for page in table_pages:
            assert "参数" in page

    def test_a_single_oversized_paragraph_still_splits(self):
        pages = _pages("很长的一段说明文字。" * 800)

        assert len(pages) > 1
        for page in pages:
            assert len(page.encode("utf-8")) <= 3800


class TestBlockBoundariesRemainPreferred:
    def test_a_page_break_still_lands_on_a_block_boundary_when_possible(self):
        """回填不等于取消块边界：装不下时仍然优先在块之间切。"""
        pages = _pages(_rendered(20))

        # 每一页都应以某个块的开头起始（标题、正文、围栏或表格上边框），
        # 而不是从一个代码块的中间开始。
        for page in pages:
            first = page.lstrip().splitlines()[0] if page.strip() else ""
            assert not first.startswith("value_"), f"页从代码块中间开始：{first!r}"

    def test_the_existing_single_page_behaviour_is_unchanged(self):
        source = _rendered(3)

        assert _pages(source) == [source]

    def test_an_empty_source_is_unchanged(self):
        assert split_structured_text("", max_bytes=100) == [""]


class TestTelegramAndWecomBenefitToo:
    def test_character_accounting_backfills_as_well(self):
        """Telegram 按字符计数；回填逻辑不能只对字节口径生效。"""
        pages = split_structured_text(_rendered(20), max_length=4096, max_pages=100)

        assert len(pages) <= 4

    def test_the_wecom_code_style_still_backfills(self):
        pages = split_structured_text(
            _rendered(20), max_bytes=1800, max_pages=100, code_style="wecom"
        )

        assert len(pages) < 20
        assert _fill_ratio(pages, 1800) > 0.4

    def test_the_shared_helper_is_used_by_all_three(self):
        """三家共用同一个分段实现，否则回填只修好一家。"""
        assert callable(text_render.split_structured_text)
