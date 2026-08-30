import re

import pytest

from kirara_ai.plugins.im_onebot_adapter.render import (
    paginate_onebot_text,
    paginate_onebot_text_or_truncate,
    render_onebot_text,
)


def _without_page_label(page: str) -> str:
    return re.sub(r"^第 \d+ 页 / 共 \d+ 页\n", "", page)


def test_render_onebot_text_cleans_latex_without_changing_code():
    source = (
        "概率 $P(\\Delta E) = \\exp(-\\Delta E / T)$，且 $T \\to 0$。\n\n"
        "```python\nformula = r'P(\\Delta E) = \\exp(-\\Delta E / T)'\n```"
    )

    rendered = render_onebot_text(source)

    prose, code = rendered.split("```python", 1)
    assert "$" not in prose
    assert "\\Delta" not in prose
    assert "\\exp" not in prose
    assert "\\to" not in prose
    assert "Δ" in prose and "exp" in prose and "→" in prose
    assert r"P(\Delta E) = \exp(-\Delta E / T)" in code


def test_render_onebot_text_converts_markdown_table_to_box_table():
    source = "| 参数 | 含义 |\n| --- | --- |\n| T0 | 初始温度 |\n| alpha | 冷却系数 |"

    rendered = render_onebot_text(source)

    assert "┌" in rendered and "└" in rendered
    assert "│ 参数  " in rendered
    assert "| --- |" not in rendered


def test_render_onebot_text_preserves_markdown_like_content_inside_code():
    source = "```text\n| key | value |\n| --- | --- |\n```"

    assert render_onebot_text(source) == source


def test_render_onebot_text_preserves_non_latex_braces():
    source = 'JSON: {"temperature": 0.7, "messages": [{"role": "user"}]}'

    assert render_onebot_text(source) == source


def test_paginate_onebot_text_does_not_label_a_single_page():
    text = "一条无需分页的 QQ 回复。"

    assert paginate_onebot_text(text, max_bytes=180) == [text]


def test_paginate_onebot_text_uses_utf8_byte_limit_and_page_labels():
    pages = paginate_onebot_text("第一段。" * 80, max_bytes=180)

    assert len(pages) > 1
    assert all(len(page.encode("utf-8")) <= 180 for page in pages)
    assert pages[0].startswith(f"第 1 页 / 共 {len(pages)} 页\n")
    assert pages[-1].startswith(f"第 {len(pages)} 页 / 共 {len(pages)} 页\n")
    assert "".join(_without_page_label(page) for page in pages) == "第一段。" * 80


def test_paginate_onebot_text_keeps_code_fences_complete():
    source = "```python\n" + "\n".join(f"print({index})" for index in range(80)) + "\n```"

    pages = paginate_onebot_text(source, max_bytes=180)

    assert len(pages) > 1
    assert all(_without_page_label(page).count("```") == 2 for page in pages)
    assert all("```python\n" in page and page.rstrip().endswith("```") for page in pages)
    recovered = []
    for page in pages:
        body = _without_page_label(page)
        recovered.extend(body.removeprefix("```python\n").removesuffix("\n```").splitlines())
    assert recovered == [f"print({index})" for index in range(80)]


def test_paginate_code_block_never_exceeds_byte_limit_at_boundary():
    source = "```text\n" + ("中" * 58) + "\n```"

    assert len(source.encode("utf-8")) > 180

    pages = paginate_onebot_text(source, max_bytes=180)

    assert len(pages) > 1
    assert all(len(page.encode("utf-8")) <= 180 for page in pages)


def test_paginate_long_table_row_falls_back_without_failing_whole_reply():
    source = "\n".join(
        [
            "┌───┬───┐",
            "│ 键 │ 值 │",
            "├───┼───┤",
            "│ 1 │ " + "超" * 200 + " │",
            "└───┴───┘",
        ]
    )

    pages = paginate_onebot_text(source, max_bytes=180)

    assert len(pages) > 1
    assert all(len(page.encode("utf-8")) <= 180 for page in pages)
    assert any("表格行（单元格过长，已折行）" in page for page in pages)


def test_paginate_onebot_text_repeats_table_frame_and_header():
    source = "\n".join(
        [
            "┌──────┬──────────┐",
            "│ 参数 │ 含义     │",
            "├──────┼──────────┤",
            *[f"│ {index:>4} │ 第{index:02d}项    │" for index in range(30)],
            "└──────┴──────────┘",
        ]
    )

    pages = paginate_onebot_text(source, max_bytes=260)

    assert len(pages) > 1
    for page in pages:
        body = _without_page_label(page)
        assert body.startswith("┌──────┬──────────┐\n│ 参数 │ 含义     │\n├")
        assert body.endswith("└──────┴──────────┘")
        assert len(page.encode("utf-8")) <= 260


@pytest.mark.parametrize("source", ["x" * 10_000, "中" * 5_000])
def test_paginate_unbroken_text_makes_bounded_progress_without_data_loss(source):
    pages = paginate_onebot_text(source, max_bytes=180)

    assert 1 < len(pages) < 200
    assert all(len(page.encode("utf-8")) <= 180 for page in pages)
    assert "".join(_without_page_label(page) for page in pages) == source


def test_paginate_long_single_line_code_keeps_fences_and_content():
    code = "x" * 10_000
    pages = paginate_onebot_text(f"```text\n{code}\n```", max_bytes=180)

    assert 1 < len(pages) < 200
    assert all(_without_page_label(page).count("```") == 2 for page in pages)
    recovered = "".join(
        _without_page_label(page)
        .removeprefix("```text\n")
        .removesuffix("\n```")
        .rstrip("\n")
        for page in pages
    )
    assert recovered == code


def test_paginate_rejects_content_beyond_total_byte_limit():
    with pytest.raises(ValueError, match="总字节上限"):
        paginate_onebot_text("x" * 1000, max_bytes=180, max_total_bytes=999)


def test_paginate_rejects_more_than_configured_page_limit():
    with pytest.raises(ValueError, match="页数上限"):
        paginate_onebot_text(
            "x" * 1000,
            max_bytes=180,
            max_pages=2,
            max_total_bytes=10_000,
        )


def test_over_budget_reply_is_truncated_instead_of_lost():
    """OneBot 发送路径遇到超预算内容必须截断，不能抛错丢掉整条回复。

    `paginate_onebot_text` 仍保留严格语义（抛错）供需要它的调用方使用；
    发送路径改用 `paginate_onebot_text_or_truncate`。
    """
    pages, truncated = paginate_onebot_text_or_truncate(
        "内容" * 5000, max_bytes=200, max_pages=3
    )

    assert truncated is True
    assert 0 < len(pages) <= 3
    assert "已截断" in pages[-1]


def test_normal_reply_is_not_truncated_by_the_new_path():
    """预算内的回复必须与严格路径给出完全相同的结果。"""
    text = "普通回复内容。" * 20

    strict = paginate_onebot_text(text)
    lenient, truncated = paginate_onebot_text_or_truncate(text)

    assert truncated is False
    assert lenient == strict
