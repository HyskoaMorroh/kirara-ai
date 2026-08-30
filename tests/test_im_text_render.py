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


def test_currency_amounts_are_not_mistaken_for_math():
    """成对的 `$` 不等于数学公式；金额被当公式剥离会直接改变语义。

    `$5 and $7` 里两个 `$` 恰好配对，旧规则据此把中间内容当成 LaTeX 处理，
    结果把货币符号全部吃掉，用户读到的是「价格 5 和 7」——数字还在，
    单位没了。判定必须看内容像不像 LaTeX，而不是只看定界符配不配对。
    """
    rendered = text_render.render_plain_text("price $5 and $7 total")

    assert rendered == "price $5 and $7 total"


def test_currency_before_a_real_formula_survives():
    """同一段里既有金额又有真公式时，只处理公式。"""
    rendered = text_render.render_plain_text(r"成本 $30，收敛到 $x \to 0$")

    assert "$30" in rendered
    assert "x → 0" in rendered
    assert r"\to" not in rendered


def test_bare_latex_commands_outside_delimiters_are_degraded():
    r"""定界符外的裸命令同样要降级。

    模型经常输出不带 `$` 的 `\to`、`\times`。旧实现只在定界符内替换，
    于是这些命令原样进入 QQ——正是 1.txt 19.2 明确禁止的「成片的 \to」。
    """
    rendered = text_render.render_plain_text(r"速度 \to 0，面积 \times 2，且 \le 5")

    assert "→" in rendered
    assert "×" in rendered
    assert "≤" in rendered
    assert "\\" not in rendered


def test_extended_latex_commands_are_covered():
    """常见但此前未覆盖的命令必须有可读结果，而不是退化成裸单词。"""
    rendered = text_render.render_plain_text(
        r"$\int f$、$\partial x$、$\theta$、$\nabla u$、$\in S$、$\forall n$"
    )

    assert "∫" in rendered
    assert "∂" in rendered
    assert "θ" in rendered
    assert "∇" in rendered
    assert "∈" in rendered
    assert "∀" in rendered


def test_latex_environments_and_line_breaks_degrade_readably():
    r"""`\begin{}/\end{}` 不能退化成 `begincases`，换行命令要真的换行。"""
    rendered = text_render.render_plain_text(
        r"$\begin{cases} a = 1 \\ b = 2 \end{cases}$"
    )

    assert "begincases" not in rendered
    assert "endcases" not in rendered
    assert "a = 1" in rendered and "b = 2" in rendered
    assert "\\" not in rendered


def test_fenced_code_is_still_untouched_by_the_bare_command_pass():
    """扫描非围栏正文不得波及代码块内部。"""
    source = "说明 " + chr(92) + "to 结果\n\n```python\nprint(r'" + chr(92) + "to')\n```"

    rendered = text_render.render_plain_text(source)

    prose, code = rendered.split("```python", 1)
    assert "→" in prose
    assert r"print(r'\to')" in code


def test_wide_tables_degrade_to_a_vertical_field_layout():
    """宽表必须改成纵向字段布局，不能靠等宽字体硬撑。

    QQ 没有等宽字体，8 列中文表渲染出来每行 97 显示列，在任何客户端上都会
    按窗口宽度随机折行——框线对不齐，读者反而看不出哪个值属于哪一列。
    1.txt 19.3 明确要求宽表走纵向或分组布局。
    """
    header = "| " + " | ".join(f"列名称{index}" for index in range(1, 9)) + " |"
    separator = "|" + "---|" * 8
    row = "| " + " | ".join(f"数据内容{index}" for index in range(1, 9)) + " |"

    rendered = text_render.convert_markdown_tables("\n".join([header, separator, row]))

    assert "┌" not in rendered, "宽表不应再渲染成框线表"
    for index in range(1, 9):
        assert f"列名称{index}：数据内容{index}" in rendered
    for line in rendered.split("\n"):
        assert text_render.display_width(line) <= text_render.MAX_TABLE_DISPLAY_WIDTH


def test_narrow_tables_keep_the_box_layout():
    """窄表仍走框线表：既有观感不能因为新增降级而改变。"""
    source = "| 参数 | 值 |\n| --- | --- |\n| T | 0 |"

    rendered = text_render.convert_markdown_tables(source)

    assert "┌" in rendered and "└" in rendered
    assert "参数：T" not in rendered


def test_wide_table_groups_stay_separated_per_row():
    """多行宽表的每一行必须是独立分组，不能把所有值串成一片。"""
    header = "| " + " | ".join(f"字段{index}" for index in range(1, 9)) + " |"
    separator = "|" + "---|" * 8
    rows = [
        "| " + " | ".join(f"甲{index}" for index in range(1, 9)) + " |",
        "| " + " | ".join(f"乙{index}" for index in range(1, 9)) + " |",
    ]

    rendered = text_render.convert_markdown_tables(
        "\n".join([header, separator, *rows])
    )

    assert rendered.count("字段1：") == 2, "每行数据都应重复一次字段名"
    assert "字段1：甲1" in rendered and "字段1：乙1" in rendered


def test_a_wide_table_without_a_header_row_still_degrades_readably():
    """没有表头分隔行时不能丢内容，只是无法给出字段名。"""
    rows = [
        "| " + " | ".join(f"很长的中文内容{index}" for index in range(1, 9)) + " |"
    ]

    rendered = text_render.convert_markdown_tables("\n".join(rows))

    for index in range(1, 9):
        assert f"很长的中文内容{index}" in rendered


def test_split_never_breaks_paired_markdown_markers():
    """成对的行内标记不能被切到两页。

    `*aaa...*` 在 40 字节上限下曾被劈成四页：第一页尾部挂着未闭合的 `*`，
    最后一页开头凭空多出一个 `*`。两页都不是合法 Markdown，QQ 上直接显示成
    星号乱码。1.txt 19.4 明确禁止拆坏 Markdown 标记。
    """
    emphasis = "*" + "a" * 60 + "*"

    pages = text_render.split_structured_text(emphasis, max_bytes=120)
    bodies = [_without_page_label(page) for page in pages]

    assert sum(emphasis in body for body in bodies) == 1
    for body in bodies:
        assert body.count("*") % 2 == 0, f"页内星号数必须成对：{body!r}"


def test_split_keeps_inline_code_spans_whole():
    """行内代码同理：反引号被劈开会留下未闭合反引号。"""
    span = "`" + "x" * 40 + "`"
    source = ("前置文本。" * 6) + span + ("后置文本。" * 6)

    pages = text_render.split_structured_text(source, max_bytes=140)
    bodies = [_without_page_label(page) for page in pages]

    assert sum(span in body for body in bodies) == 1
    for body in bodies:
        assert body.count("`") % 2 == 0


def test_split_prefers_heading_and_list_boundaries():
    """切点应落在标题或列表项开头，而不是把结构从中间劈开。"""
    source = (
        "正文段落，内容较长用于把第一页填满。" * 3
        + "\n## 第二节标题\n"
        + "- 列表第一项内容\n- 列表第二项内容\n"
    )

    pages = text_render.split_structured_text(source, max_bytes=150)
    bodies = [_without_page_label(page) for page in pages]

    assert len(bodies) > 1
    # 标题与列表项都不允许出现「上一页留半截」的形态。
    for body in bodies:
        for line in body.split("\n"):
            if "第二节标题" in line:
                assert line.strip().startswith("##"), f"标题被劈开：{line!r}"
            if "列表第" in line:
                assert line.strip().startswith("-"), f"列表项被劈开：{line!r}"


def test_split_still_loses_no_characters_with_the_new_boundaries():
    """新增边界规则不得吞字：拼回去必须与原文完全一致。"""
    source = "**加粗**与`代码`混排。" * 12

    pages = text_render.split_structured_text(source, max_bytes=160)
    recovered = "".join(_without_page_label(page) for page in pages)

    assert recovered == source


def test_over_budget_content_is_truncated_with_a_notice_not_dropped():
    """超预算必须截断并提示，不能让整条回复丢失。

    旧行为是 `split_structured_text` 抛 `ValueError`，而适配器的
    `_render_message_batches` 不捕获它，异常一路穿出 `send_message`——
    用户看到的是机器人完全没反应。收到前 N 页 + 明确的「已截断」提示，
    比静默失败好得多。
    """
    pages, truncated = text_render.paginate_with_truncation_notice(
        "内容" * 5000, max_bytes=200, max_pages=3
    )

    assert truncated is True
    assert 0 < len(pages) <= 3
    assert all(len(page.encode("utf-8")) <= 200 for page in pages)
    assert "已截断" in pages[-1]


def test_content_within_budget_is_not_truncated():
    """预算够用时不得追加任何提示，观感必须与从前完全一致。"""
    pages, truncated = text_render.paginate_with_truncation_notice(
        "短内容", max_bytes=200
    )

    assert truncated is False
    assert pages == ["短内容"]
    assert "已截断" not in pages[0]


def test_total_byte_budget_also_truncates_instead_of_raising():
    """总字节上限同样走截断路径。"""
    pages, truncated = text_render.paginate_with_truncation_notice(
        "x" * 5000, max_bytes=200, max_total_bytes=900
    )

    assert truncated is True
    assert sum(len(page.encode("utf-8")) for page in pages) <= 900


def test_a_configuration_error_still_raises_rather_than_truncating():
    """上限本身非法是调用方的配置错误，必须抛出而不是静默降级。"""
    with pytest.raises(ValueError, match="必须"):
        text_render.paginate_with_truncation_notice("内容")

    with pytest.raises(ValueError, match="大于 0"):
        text_render.paginate_with_truncation_notice("内容", max_bytes=0)


def test_split_never_breaks_a_multibyte_emoji():
    """Emoji 不能被切成两半。

    需求 19.4 点名要验证 Emoji。此前只有一条围栏代码块用例顺带含 Emoji
    （`print('中文🙂')`），那验证的是代码块完整性，不是「Emoji 本身不被劈开」。
    UTF-8 下一个 Emoji 占 4 字节，按字节切分时它是最容易被拦腰截断的字符；
    切坏的结果是两页各出现一个替换字符，用户看到的是乱码而不是表情。
    """
    # 组合序列（带肤色/ZWJ）比单码点更容易踩坑，两种都覆盖。
    source = ("状态🙂正常。" * 12) + ("家庭👨‍👩‍👧‍👦成员。" * 8)

    pages = text_render.split_structured_text(source, max_bytes=160)
    bodies = [_without_page_label(page) for page in pages]

    assert len(bodies) > 1, "上限过大，这条用例没有真正触发分页"
    for body in bodies:
        # 能无损编解码即说明没有半个码点被留下。
        assert body.encode("utf-8").decode("utf-8") == body
        assert "�" not in body, f"出现替换字符，说明多字节字符被切坏：{body!r}"
    assert "".join(bodies) == source


def test_split_keeps_mixed_chinese_english_words_measurable():
    """中英混排：页面必须都在字节上限内，且拼回去与原文完全一致。

    中文 3 字节、英文 1 字节，混排时「按字符数估算字节」会系统性算错——
    这正是需求点名要验证中英混排的原因。
    """
    source = "Kirara 的 workflow 编排器 supports 多模型 fallback 与 streaming。" * 6

    pages = text_render.split_structured_text(source, max_bytes=180)
    bodies = [_without_page_label(page) for page in pages]

    assert len(bodies) > 1
    for page in pages:
        assert len(page.encode("utf-8")) <= 180
    assert "".join(bodies) == source


def test_split_preserves_an_empty_reply_without_raising():
    """空内容不得抛错：需求 19.4 把「空内容」列为必须验证的场景。"""
    assert text_render.split_structured_text("", max_bytes=100) == [""]


def test_split_handles_one_oversized_single_line():
    """超长单行没有任何可用边界，仍必须切开且不丢字符。"""
    source = "A" * 5000

    pages = text_render.split_structured_text(source, max_bytes=200)
    bodies = [_without_page_label(page) for page in pages]

    assert len(bodies) > 1
    assert all(len(page.encode("utf-8")) <= 200 for page in pages)
    assert "".join(bodies) == source
