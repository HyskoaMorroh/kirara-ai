"""公式降级不能留下命令名残片（需求 6：QQ 回复里有「$\\to 等乱码」）。

`degrade_math` 对未收录的命令**去掉反斜杠、保留命令名**。对 `\\foo` 这类真正
未知的命令那是合理的兜底，但对高频出现的排版命令它会产出比原文更糟的结果：

    $\\exp\\left(-\\dfrac{\\Delta E}{k_B T}\\right)$  ->  exp(-dfracΔ Ek_B T)

`\\dfrac` 只是 `\\frac` 的「显示尺寸」变体，语义完全相同，而 `\\frac` 早就被
正确处理成 `(a)/(b)`。同一段公式里，写 `\\frac` 得到可读结果，写 `\\dfrac`
得到 `dfrac` 加上被吞掉花括号的一串符号——用户看到的正是那种「乱码」。
`\\hat`/`\\vec`/`\\bar` 同理：`\\overline` 有处理，它们没有。

行列环境更彻底：`\\begin{cases}` 的环境名被删掉了，但**列分隔符 `&` 与行结束
`\\\\` 没人管**，于是每一行都拖着一个孤零零的 `&`。现场报障里贴出来的
`\\begin{cases} 1, & \\Delta E \\le 0 \\\\ ... \\end{cases}` 正是这个形态。

## 判据：同义命令必须与它的标准写法给出同一个结果

一个命令的处理结果不该取决于作者选了哪个同义写法。这不是「多支持几个命令」的
枚举题——`\\dfrac` 与 `\\frac` 在数学上是同一个东西，它们给出不同结果本身就是
缺陷。同样地，`&` 在数学环境里是对齐用的排版符号，不是内容；留着它等于把
LaTeX 的排版指令当正文显示。
"""

from __future__ import annotations

import pytest

from kirara_ai.im.text_render import degrade_math, render_plain_text


class TestFractionVariants:
    """`\\dfrac` / `\\tfrac` 与 `\\frac` 是同一个东西。"""

    @pytest.mark.parametrize("command", ["frac", "dfrac", "tfrac"])
    def test_every_fraction_spelling_renders_the_same(self, command):
        rendered = degrade_math(rf"$\{command}{{a}}{{b}}$")

        assert rendered == "(a)/(b)", f"\\{command} 与 \\frac 给出了不同结果"

    def test_the_reported_expression_has_no_command_name_left(self):
        rendered = degrade_math(r"$\exp\left(-\dfrac{\Delta E}{k_B T}\right)$")

        assert "dfrac" not in rendered
        assert rendered == "exp(-(Δ E)/(k_B T))"


class TestAccents:
    """`\\hat` / `\\vec` / `\\bar` 与已处理的 `\\overline` 属于同一类。"""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (r"$\hat{x}$", "x̂"),
            # 组合记号而非预组合字符：与既有的 `\overline` 同一形态，
            # 渲染结果相同，但不需要为每个基字符查一张预组合表。
            (r"$\bar{y}$", "ȳ"),
            (r"$\tilde{z}$", "z̃"),
            (r"$\vec{v}$", "v⃗"),
        ],
    )
    def test_accents_become_combining_marks(self, source, expected):
        assert degrade_math(source) == expected

    def test_no_command_name_survives(self):
        rendered = degrade_math(r"$\hat{x}$ 与 $\vec{v}$ 与 $\bar{y}$")

        for name in ("hat", "vec", "bar"):
            assert name not in rendered

    def test_overline_keeps_its_existing_output(self):
        """既有行为不变：`\\overline` 一直产出基字符 + 组合长音符。"""
        assert degrade_math(r"$\overline{\Delta E}$") == degrade_math(r"$\bar{\Delta E}$")


class TestMathEnvironments:
    """`&` 与 `\\\\` 是排版指令，不是内容。"""

    def test_alignment_separators_are_removed(self):
        source = (
            "$$\n"
            r"\begin{cases}" "\n"
            r"1, & \Delta E \le 0 \\" "\n"
            r"\exp(-\Delta E / T), & \Delta E > 0" "\n"
            r"\end{cases}" "\n"
            "$$"
        )

        rendered = degrade_math(source)

        assert "&" not in rendered, "列分隔符被当成正文显示了"
        assert "1, Δ E ≤ 0" in rendered
        assert "exp(-Δ E / T), Δ E > 0" in rendered

    def test_aligned_environments_keep_their_content(self):
        source = "$$\n" + r"\begin{aligned} a &= b + c \\ &= d \end{aligned}" + "\n$$"

        rendered = degrade_math(source)

        assert "&" not in rendered
        assert "a = b + c" in rendered
        assert "= d" in rendered

    def test_row_breaks_survive_the_separator_removal(self):
        """`\\\\` 换来的换行不能被 `&` 两侧的空白吃掉。

        吃掉之后多行的 cases / matrix 会压成一行，行与行的边界就此消失——
        那比留着 `&` 更糟：读者看到的是一串首尾相接的算式。
        """
        source = "$$\n" + r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}" + "\n$$"

        rendered = degrade_math(source)

        assert "a b" in rendered
        assert "c d" in rendered
        # 两行必须还在两行上。
        body = [line.strip() for line in rendered.splitlines() if line.strip()]
        assert body == ["a b", "c d"]

    def test_a_matrix_does_not_leak_separators(self):
        source = "$$\n" + r"\begin{pmatrix} a & b \\ c & d \end{pmatrix}" + "\n$$"

        rendered = degrade_math(source)

        assert "&" not in rendered

    def test_an_ampersand_outside_math_is_untouched(self):
        """正文里的 `&` 是内容。只有数学环境里的才是排版指令。"""
        assert degrade_math("Tom & Jerry") == "Tom & Jerry"


class TestCurrencyDoesNotPoisonLaterFormulas:
    """一个金额不能让它后面的每个公式都留下 `$`。

    `_looks_like_currency` 判断的是**单个** `$...$` 片段，但 `$` 的配对是从左到右
    贪心的：正文里出现一个孤立的 `$200`，它会与后面第一个公式的开定界符配成一对，
    于是从那里开始，每个公式的定界符都错位一格。

    现场那段回复里既有 `$T$`、`$P(\\Delta E)$` 这类公式，也完全可能出现金额或
    孤立的 `$`——一旦出现，后半段全是 19.2 点名禁止的 `$` 残片。
    """

    def test_a_leading_amount_does_not_shift_later_pairings(self):
        rendered = render_plain_text(
            r"成本 $200 起。温度 $T$ 控制接受概率 $P(\Delta E)$，当 $T \to 0$ 时收敛。"
        )

        # 金额本身保留（含 `$`），公式全部剥掉定界符。
        assert "$200" in rendered
        assert "温度 T " in rendered
        assert "P(Δ E)" in rendered
        assert "T → 0" in rendered
        assert rendered.count("$") == 1, f"公式定界符残留：{rendered}"

    def test_an_odd_stray_dollar_does_not_shift_later_pairings(self):
        rendered = render_plain_text(r"阈值 $T 之下。温度 $T$ 与 $T \to 0$。")

        # 落单的那个 `$` 不该把后面两个公式的配对全部推移一位。
        assert "温度 T " in rendered
        assert "T → 0" in rendered

    def test_a_pure_currency_pair_is_still_left_alone(self):
        """既有行为不变：两个恰好配对的货币符号之间不是公式。"""
        assert render_plain_text("price $5 and $7 total") == "price $5 and $7 total"
