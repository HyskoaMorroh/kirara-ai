"""围栏识别与平台渲染一致性契约。

这个文件针对三类「看起来实现了、实际不起作用」的缺陷：

1. **围栏只认三个反引号。** `_FENCE_OPEN_PATTERN`、`_split_fenced_sections`、
   `convert_markdown_tables`、`parse_text_document` 与 `_code_line_kind` 全部
   硬编码 ``startswith("```")``。于是：

   - ``~~~`` 围栏（CommonMark 的合法写法）完全不被当成代码：里面的 ``**``、
     ``$x$``、表格行会被当成正文改写，代码内容被静默破坏。
   - 四个反引号的围栏（模型展示 Markdown 时的标准写法）被当成一次「进入围栏」
     加一次「退出围栏」，嵌套的三反引号块因此把围栏状态翻转回来，
     后续正文被错判成代码、代码被错判成正文。

2. **WeCom 自带一条独立的正则 Markdown 链。** 19.1 要求平台差异只在渲染层，
   不允许各平台各写一套 Markdown 解析。`markdown_to_plain_text` 的围栏摘取
   正则 ``` ```([\\w+-]*)\\n(.*?)``` ``` 在四反引号与嵌套围栏上会把代码切碎，
   剩下的落单反引号再被行内代码规则包成 ``『』``——同一段模型回复在 QQ 上正常、
   在企业微信上是乱码。

3. **纯符号 `$...$` 公式不降级。** `_MATH_EVIDENCE_PATTERN` 要求反斜杠命令、
   上下标或花括号，于是 ``$x = 5$`` 这类模型高频输出原样带着 ``$`` 送到 QQ，
   正是 19.2 点名禁止的形态。货币写法（``$5``、``$1,200``）必须继续保持原样。
"""

from __future__ import annotations

import pytest

from kirara_ai.im import text_render
from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

BACKTICK_3 = "`" * 3
BACKTICK_4 = "`" * 4


# --- 1. 围栏识别 ---------------------------------------------------------------


def test_tilde_fence_is_recognized_as_code_by_the_parser():
    """``~~~`` 是 CommonMark 合法围栏，必须解析成 CODE 块。"""
    document = text_render.parse_text_document("~~~python\nprint(1)\n~~~")

    assert [block.kind for block in document.blocks] == [
        text_render.TextBlockKind.CODE
    ]
    assert document.blocks[0].language == "python"
    assert document.blocks[0].text == "print(1)"


def test_tilde_fence_content_is_not_math_degraded():
    """围栏内的 LaTeX 字面量不得被改写，无论围栏用哪种字符。"""
    source = "~~~text\nT $\\to$ 0\n~~~"

    assert text_render.degrade_math(source) == source


def test_tilde_fence_content_is_not_table_converted():
    """围栏内的表格行不得被渲染成框线表。"""
    source = "~~~text\n| a | b |\n|---|---|\n| 1 | 2 |\n~~~"

    assert text_render.convert_markdown_tables(source) == source


def test_tilde_fence_code_is_isolated_into_its_own_message():
    """代码单独成条的能力同样必须覆盖 ``~~~`` 围栏。"""
    parts = text_render.split_for_copyable_code(
        "intro\n~~~python\nprint(1)\n~~~\ntail"
    )

    assert [part.is_code for part in parts] == [False, True, False]
    assert parts[1].code == "print(1)"
    assert parts[1].language == "python"


def test_tilde_fence_survives_pagination_with_its_own_markers():
    """分页时 ``~~~`` 围栏必须逐页补齐，和三反引号一致。"""
    body = "\n".join(f"line {index} = {index}" for index in range(40))
    pages = text_render.split_structured_text(
        f"~~~python\n{body}\n~~~", max_bytes=220
    )

    assert len(pages) > 1
    for page in pages:
        stripped = text_render.PAGE_LABEL_PATTERN.sub("", page)
        assert stripped.startswith("~~~python")
        assert stripped.rstrip().endswith("~~~")


def test_four_backtick_fence_keeps_a_nested_fence_intact():
    """四反引号围栏里的三反引号块属于代码内容，不能翻转围栏状态。"""
    source = (
        f"{BACKTICK_4}markdown\n{BACKTICK_3}python\nx = a ** b\n{BACKTICK_3}\n"
        f"{BACKTICK_4}"
    )

    document = text_render.parse_text_document(source)

    assert [block.kind for block in document.blocks] == [
        text_render.TextBlockKind.CODE
    ]
    assert document.blocks[0].language == "markdown"
    assert document.blocks[0].text == f"{BACKTICK_3}python\nx = a ** b\n{BACKTICK_3}"


def test_four_backtick_fence_is_one_copyable_code_message():
    """嵌套围栏必须整块成为一条代码消息，而不是被切成三段。"""
    source = (
        f"{BACKTICK_4}markdown\n{BACKTICK_3}python\nx = 1\n{BACKTICK_3}\n"
        f"{BACKTICK_4}"
    )

    parts = text_render.split_for_copyable_code(source)

    assert [part.is_code for part in parts] == [True]
    assert parts[0].code == f"{BACKTICK_3}python\nx = 1\n{BACKTICK_3}"


# --- 2. WeCom 与共享实现的一致性 ----------------------------------------------


def test_wecom_keeps_a_four_backtick_code_block_readable():
    """企业微信路径不得把四反引号围栏切碎成行内代码。"""
    source = f"{BACKTICK_4}\ncode with ** stars **\n{BACKTICK_4}"

    rendered = markdown_to_plain_text(source)

    assert "『" not in rendered, rendered
    assert "」" not in rendered, rendered
    assert "code with ** stars **" in rendered
    assert rendered.count("［代码") == 1
    assert rendered.count("［/代码］") == 1


def test_wecom_keeps_a_nested_fence_body_verbatim():
    """嵌套围栏的代码体不得漏到围栏标记之外。"""
    source = (
        f"{BACKTICK_4}markdown\n{BACKTICK_3}python\nx=1\n{BACKTICK_3}\n"
        f"{BACKTICK_4}"
    )

    rendered = markdown_to_plain_text(source)

    assert rendered.count("［代码") == 1
    assert rendered.count("［/代码］") == 1
    assert "`" not in rendered.replace(BACKTICK_3, ""), rendered
    body_start = rendered.index("］") + 1
    body_end = rendered.index("［/代码］")
    assert "x=1" in rendered[body_start:body_end]


def test_wecom_recognizes_a_tilde_fence_as_code():
    """``~~~`` 围栏在企业微信侧同样必须按代码处理。"""
    source = "~~~python\nx = a ** b\n~~~"

    rendered = markdown_to_plain_text(source)

    assert "［代码 python］" in rendered
    assert "x = a ** b" in rendered
    assert "~~~" not in rendered


def test_wecom_keeps_list_indentation_inside_an_indented_fence():
    """列表项里的缩进围栏，闭合标记必须保留原缩进。"""
    source = "- 步骤：\n  ~~~bash\n  ls -al\n  ~~~\n- 下一步"

    rendered = markdown_to_plain_text(source)

    assert "  ls -al" in rendered
    assert "• 步骤：" in rendered
    assert "• 下一步" in rendered


# --- 3. 纯符号公式降级 --------------------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        "$x = 5$",
        "结果是 $a + b = c$ 而不是别的",
        "$E = mc2$",
        "$a/b$",
    ],
)
def test_plain_symbol_math_delimiters_are_stripped(source: str):
    """没有反斜杠命令的公式同样不能带着 ``$`` 送到 QQ。"""
    rendered = text_render.render_plain_text(source)

    assert "$" not in rendered, rendered


def test_plain_symbol_math_keeps_its_content():
    """剥离定界符时变量与运算符必须完整保留。"""
    rendered = text_render.render_plain_text("已知 $y = k x + b$。")

    assert "y = k x + b" in rendered


@pytest.mark.parametrize(
    "source",
    [
        "price $5 and $7 total",
        "预算 $1,200 与 $980",
        "涨到 $5.50 后停止",
    ],
)
def test_currency_amounts_still_survive(source: str):
    """货币金额必须原样保留，包括 ``$`` 本身。"""
    assert text_render.render_plain_text(source) == source


def test_currency_and_a_real_formula_can_coexist():
    """一段文本里既有金额又有公式时，两者都要被正确处理。"""
    rendered = text_render.render_plain_text(r"成本 $30，收敛到 $x \to 0$")

    assert "$30" in rendered
    assert "→" in rendered
    assert r"\to" not in rendered
