"""需求 19.2：不得出现「转义残片」。

原文点名禁止的四类残留：成片的 `$...$`、`\\to`、**未闭合反引号**、转义残片。
前两类已经处理。后两类还在：

1. `_COMMAND_PATTERN` 是白名单 —— 未收录的命令保留 `match.group(0)`，
   于是 `\\foo` 原样送到 QQ。用户看到的是一个反斜杠加一串字母，
   那正是「转义残片」的定义。
2. 模型输出本身就不配对的反引号没有任何清理。已有的两处防守是
   「未闭合围栏不当代码」与「分页不劈开行内代码」，都不解决「本来就少一个」。
   一个落单的 `` ` `` 在 QQ 里是可见的垃圾字符；更糟的是它会把后面一大段
   正文都变成「行内代码待闭合」的观感。

刻意不做的事：不猜测未知命令的语义。`\\foo` 到底该显示成什么无法可靠推断，
因此只**去掉反斜杠**保留命令名 —— 那至少是一个可读的单词，而不是残片。
"""

from __future__ import annotations

import pytest

from kirara_ai.im.text_render import degrade_math, render_plain_text


def test_an_unknown_latex_command_loses_its_backslash():
    """未收录的命令不能带着反斜杠出现在回复里。

    保留 `\\foo` 是「转义残片」；去掉反斜杠得到 `foo`——不一定准确，
    但它是一个可读的词，而残片什么信息都不传达。
    """
    assert "\\foo" not in degrade_math(r"结果是 \foo 之后收敛")
    assert "foo" in degrade_math(r"结果是 \foo 之后收敛")


def test_known_commands_still_map_to_their_symbols():
    """白名单里的命令行为不变。"""
    rendered = degrade_math(r"当 $T \to 0$ 时 $a \times b \le c$")

    assert "→" in rendered
    assert "×" in rendered
    assert "≤" in rendered
    assert "\\to" not in rendered


def test_escaped_characters_are_not_treated_as_commands():
    """`\\$`、`\\_`、`\\{` 是转义字符而不是命令，必须还原成字面量。"""
    rendered = degrade_math(r"价格 \$5 与 file\_name 以及 \{key\}")

    assert "$5" in rendered
    assert "file_name" in rendered
    assert "{key}" in rendered
    assert "\\" not in rendered


def test_an_unpaired_backtick_is_removed():
    """落单的反引号必须清掉。

    留着它一方面是可见的垃圾字符，另一方面会让后面一大段正文在客户端上
    呈现为「行内代码待闭合」的观感。
    """
    rendered = render_plain_text("这里有一个 `未闭合 的反引号，后面还有正文")

    assert "`" not in rendered
    # 正文一个字都不能少——清理的是标记，不是内容。
    assert "未闭合 的反引号，后面还有正文" in rendered


def test_paired_inline_code_is_preserved():
    """配对的行内代码是有效标记，不能被这条清理波及。"""
    rendered = render_plain_text("执行 `pip install kirara` 即可")

    assert "`pip install kirara`" in rendered


def test_only_the_unpaired_backtick_is_removed_when_others_pair_up():
    rendered = render_plain_text("先 `a` 再 `b` 然后 ` 收尾")

    assert rendered.count("`") == 4
    assert "`a`" in rendered and "`b`" in rendered


def test_fenced_code_backticks_are_never_touched():
    """围栏代码里的反引号是内容，动它等于改坏用户要执行的东西。"""
    source = "说明：\n```python\nprint('`')\n```\n结束"

    rendered = render_plain_text(source)

    assert "```python" in rendered
    assert "print('`')" in rendered


def test_an_unclosed_fence_keeps_its_backticks():
    """未闭合围栏按普通文本处理，但它的三个反引号是**成对的开头标记**。

    把它们当「落单反引号」清掉会让一段本该显示成代码的内容失去边界，
    用户看到的是被抹平缩进的裸文本。
    """
    source = "看这段：\n```python\nprint(1)"

    rendered = render_plain_text(source)

    assert "```" in rendered


@pytest.mark.parametrize(
    "source",
    [
        "``",
        "````",
        "a `` b",
    ],
)
def test_even_numbers_of_backticks_are_left_alone(source: str):
    """偶数个反引号有可能是有意的（空行内代码、转义），不去猜。"""
    assert render_plain_text(source).count("`") == source.count("`")
