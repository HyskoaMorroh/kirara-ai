"""QQ 的回复不能只是把裸 Markdown 标记原样发出去（需求 6(a)(b)(e)）。

第 6 条原文要求「**参照 telegram、wecom 等其他 APP 的格式**，让 QQ 回复更美观符合
大众审美」。实测方向正好相反——同一段回复：

    QQ / OneBot                     WeCom
    ## 二、结论                     ━━ 二、结论 ━━
    **重点**在于 `T` 的取值。       「重点」在于 『T』 的取值。
    - 第一条                        • 第一条
    > 引用一句                      ┃ 引用一句
    见 [文档](https://…)。          见 文档 (https://…)。

QQ 上六种标记全部原样保留。原因在 `render_plain_text`：它接收一个
`TextDocument`（`parse_text_document` 已经把块结构解析好了）却只取
`document.source`，**把 `blocks` 全部丢掉**，随后只做数学降级与表格转换。
`render_onebot_text` 恰好是 `render_plain_text(parse_text_document(text))`——
解析了，然后扔了。

WeCom 早就有一整套块/行内符号表（`delegates.py` 的 `_BLOCK_RENDERERS` 与
`_INLINE_RULES`）。项目自己写下的约定是「平台差异只放在渲染层，不允许各平台
各写一套 Markdown 解析」，而 QQ 这边连渲染层都没有。

## 判据

QQ 与企业微信共享同一套块渲染框架，各自只提供**符号表**。QQ 的符号取值可以与
企业微信不同（那是两个平台的观感取舍），但「有没有渲染」不能不同。

四条边界：

1. **围栏代码原样不动。** 代码块内的 `**`、`#`、`|` 都是内容。
2. **表格仍走共享 `render_table`**（含宽表降级），不因为新增块渲染而绕开它。
3. **数学降级仍在**，且必须发生在行内标记替换之前——否则 `$a_1$` 的下划线会先被
   当成强调标记吃掉。
4. **既有调用方不受影响**：`render_plain_text` 传字符串时（没有块结构可用）
   行为逐字不变，Telegram 走的是它自己的 `markdownify` 链，也不受影响。
"""

from __future__ import annotations

from kirara_ai.im.text_render import render_plain_text, render_rich_text
from kirara_ai.plugins.im_onebot_adapter.render import render_onebot_text

SOURCE = """## 二、结论

**重点**在于 `T` 的取值。

- 第一条
- 第二条

> 引用一句

见 [文档](https://example.invalid/doc)。"""


class TestNoBareMarkdownReachesQQ:
    def test_headings_are_rendered(self):
        rendered = render_onebot_text(SOURCE)

        assert "##" not in rendered
        assert "二、结论" in rendered

    def test_bold_markers_are_rendered(self):
        rendered = render_onebot_text(SOURCE)

        assert "**" not in rendered
        assert "重点" in rendered

    def test_inline_code_markers_are_rendered(self):
        rendered = render_onebot_text(SOURCE)

        assert "`T`" not in rendered
        assert "T" in rendered

    def test_list_markers_are_rendered(self):
        rendered = render_onebot_text(SOURCE)

        assert "- 第一条" not in rendered
        assert "第一条" in rendered

    def test_quote_markers_are_rendered(self):
        rendered = render_onebot_text(SOURCE)

        assert "> 引用" not in rendered
        assert "引用一句" in rendered

    def test_link_syntax_is_rendered(self):
        rendered = render_onebot_text(SOURCE)

        assert "[文档](" not in rendered
        # URL 必须保留：把它删掉等于让用户看到一个点不开的词。
        assert "https://example.invalid/doc" in rendered
        assert "文档" in rendered


class TestFencedCodeIsUntouched:
    def test_markers_inside_code_are_content(self):
        source = "说明\n\n```python\n# 注释\nx = a ** 2\n```\n\n结束"

        rendered = render_onebot_text(source)

        assert "# 注释" in rendered
        assert "a ** 2" in rendered

    def test_the_fence_survives(self):
        """代码单独成条依赖围栏识别；渲染掉围栏会让复制路径失效。"""
        rendered = render_onebot_text("```python\nprint(1)\n```")

        assert "```" in rendered

    def test_a_table_pipe_inside_code_is_not_converted(self):
        rendered = render_onebot_text("```sh\ncat a | grep b\n```")

        assert "cat a | grep b" in rendered
        assert "┌" not in rendered

    def test_an_unclosed_fence_is_not_given_a_closing_one(self):
        """补一个闭合会把截断回复的剩余正文变成「代码」，并跟上「长按可整段复制」。

        解析器把未闭合围栏也收成代码块（否则后面的内容会散成正文），
        因此块级渲染很容易顺手补上闭合——那正是这条要拦住的事。
        复制走的会是半句话，而用户以为自己拿到了代码。
        """
        rendered = render_onebot_text("说明\n\n```py\nx = 1\n未闭合")

        assert "未闭合" in rendered
        # 只有开围栏那一个，没有被补出来的第二个。
        assert rendered.count("```") == 1

    def test_an_unclosed_fence_does_not_become_a_copyable_code_message(self):
        """端到端：`split_for_copyable_code` 不该把它判成可复制代码。"""
        from kirara_ai.im.text_render import split_for_copyable_code

        parts = split_for_copyable_code(render_onebot_text("说明\n\n```py\nx = 1\n未闭合"))

        assert not any(part.is_code for part in parts)
        assert "未闭合" in "".join(part.text for part in parts)

    def test_a_closed_fence_still_becomes_a_copyable_code_message(self):
        from kirara_ai.im.text_render import split_for_copyable_code

        parts = split_for_copyable_code(
            render_onebot_text("说明\n\n```py\nx = 1\n```\n\n后续")
        )

        assert any(part.is_code for part in parts)


class TestSharedBehaviourIsKept:
    def test_math_is_still_degraded(self):
        rendered = render_onebot_text(r"温度 $T \to 0$ 时收敛。")

        assert "T → 0" in rendered
        assert "$" not in rendered

    def test_math_runs_before_inline_markers(self):
        """`$a_1$` 的下划线不能先被当成强调标记吃掉。"""
        rendered = render_onebot_text(r"系数 $a_1$ 与 $a_2$。")

        assert "a_1" in rendered
        assert "a_2" in rendered

    def test_narrow_tables_still_become_box_tables(self):
        rendered = render_onebot_text("| 参数 | 值 |\n| --- | --- |\n| T | 0 |")

        assert "┌" in rendered and "└" in rendered

    def test_wide_tables_still_degrade(self):
        header = "| " + " | ".join(f"列名称{index}" for index in range(1, 9)) + " |"
        separator = "|" + "---|" * 8
        row = "| " + " | ".join(f"数据内容{index}" for index in range(1, 9)) + " |"

        rendered = render_onebot_text("\n".join([header, separator, row]))

        assert "┌" not in rendered
        assert "列名称1：数据内容1" in rendered


class TestTheSharedEntryPoint:
    def test_render_rich_text_is_exported(self):
        assert callable(render_rich_text)

    def test_a_plain_string_keeps_the_old_behaviour(self):
        """`render_plain_text` 拿到字符串时没有块结构可用，行为必须逐字不变。"""
        source = "## 标题\n\n**粗体**"

        assert render_plain_text(source) == source

    def test_the_symbol_table_is_per_platform(self):
        """QQ 与企业微信的符号可以不同，但都必须**有**符号。"""
        from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

        qq = render_onebot_text(SOURCE)
        wecom = markdown_to_plain_text(SOURCE)

        for rendered in (qq, wecom):
            assert "##" not in rendered
            assert "**" not in rendered

    def test_wecom_also_leaves_an_unclosed_fence_unclosed(self):
        """同一个判据：补一个闭合标记等于宣称代码到这里结束，而上游其实被截断了。"""
        from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

        rendered = markdown_to_plain_text("说明\n\n```py\nx = 1\n未闭合")

        assert "未闭合" in rendered
        assert "［/代码］" not in rendered

    def test_wecom_still_closes_a_closed_fence(self):
        from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

        rendered = markdown_to_plain_text("说明\n\n```py\nx = 1\n```")

        assert "［/代码］" in rendered
