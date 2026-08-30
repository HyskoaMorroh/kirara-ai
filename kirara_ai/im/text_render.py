"""
IM 消息文本排版工具。

集中提供「显示宽度计算 / 等宽表格渲染 / Markdown 表格预渲染」等能力，
供各 IM 适配器（企业微信、Telegram 等）共用，避免各自实现导致排版不一致。
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, List, Optional, Union

# 需要按两列宽度显示的字符区间（CJK 表意文字、全角标点、假名、韩文等）
_WIDE_RANGES = (
    (0x1100, 0x115F),
    (0x2E80, 0xA4CF),
    (0xAC00, 0xD7A3),
    (0xF900, 0xFAFF),
    (0xFE30, 0xFE4F),
    (0xFF00, 0xFF60),
    (0xFFE0, 0xFFE6),
    (0x20000, 0x3FFFD),
)

# Markdown 表格分隔行，例如 |---|:--:|---|
TABLE_SEPARATOR_PATTERN = re.compile(r'^\|?[\s:\-|]{3,}\|?$')

# 单元格内的行内 Markdown 标记。表格会被渲染为等宽框线文本，
# 保留 **/*/`/~~ 这些标记只会让列宽计算失真、观感变差，因此统一去掉。
_INLINE_MARKDOWN_PATTERNS = (
    (re.compile(r'\*\*\*(.+?)\*\*\*'), r'\1'),
    (re.compile(r'\*\*(.+?)\*\*'), r'\1'),
    (re.compile(r'(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])'), r'\1'),
    (re.compile(r'___(.+?)___'), r'\1'),
    (re.compile(r'__(.+?)__'), r'\1'),
    (re.compile(r'(?<![\w_])_(?!\s)([^_]+?)(?<!\s)_(?![\w_])'), r'\1'),
    (re.compile(r'~~(.+?)~~'), r'\1'),
    (re.compile(r'`([^`]+)`'), r'\1'),
    (re.compile(r'!\[([^\]]*)\]\([^)]*\)'), r'\1'),
    (re.compile(r'\[([^\]]+)\]\(([^)]+)\)'), r'\1 (\2)'),
)


def strip_inline_markdown(text: str) -> str:
    """去掉单元格内的行内 Markdown 标记，只保留可读文本。"""
    for pattern, replacement in _INLINE_MARKDOWN_PATTERNS:
        text = pattern.sub(replacement, text)
    return text.strip()


def display_width(text: str) -> int:
    """计算字符串在等宽字体下的显示宽度，中日韩全角字符按 2 列计算。"""
    width = 0
    for char in text:
        code = ord(char)
        if any(start <= code <= end for start, end in _WIDE_RANGES):
            width += 2
        else:
            width += 1
    return width


def pad_cell(text: str, width: int) -> str:
    """按显示宽度在右侧补空格，使表格单元格对齐。"""
    return text + " " * max(0, width - display_width(text))


def is_table_separator(line: str) -> bool:
    """判断是否为 Markdown 表格的分隔行（只用于标记表头，不参与渲染）。"""
    stripped = line.strip()
    return bool(
        "|" in stripped
        and "-" in stripped
        and TABLE_SEPARATOR_PATTERN.match(stripped)
    )


def is_table_row(line: str) -> bool:
    """
    判断是否为 Markdown 表格数据行。

    仅当整行以竖线开头/结尾，或行内出现两个及以上竖线时才认定为表格，
    避免把「a | b」这类正文里的普通竖线误判成表格。
    """
    stripped = line.strip()
    if "|" not in stripped or is_table_separator(stripped):
        return False
    if stripped.startswith("|") and stripped.endswith("|") and len(stripped) > 1:
        return True
    return stripped.count("|") >= 2


def parse_table_row(line: str) -> List[str]:
    """解析一行 Markdown 表格，返回各单元格文本（已去除首尾竖线、空白与行内标记）。"""
    return [
        strip_inline_markdown(cell.strip())
        for cell in line.strip().strip("|").split("|")
    ]


#: 框线表格允许的最大显示宽度（以等宽字符计）。
#:
#: 超过这个宽度的表格在 QQ 这类没有等宽字体、也不能横向滚动的客户端上
#: 会按窗口宽度随机折行：框线错位之后，读者根本分不清哪个值属于哪一列。
#: 60 是常见移动端聊天气泡一行能容纳的中文字符数（30 个汉字）的两倍显示宽度。
MAX_TABLE_DISPLAY_WIDTH = 60


def render_box_table(rows: List[List[str]]) -> List[str]:
    """
    将表格数据渲染为带完整边框线的等宽文本表格。

    第一行视为表头，用 ├─┼─┤ 与数据区分隔，列宽按显示宽度对齐，
    保证中英混排时竖线不会错位。
    """
    if not rows:
        return []

    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    widths = [
        max(display_width(row[index]) for row in normalized)
        for index in range(column_count)
    ]

    def border(left: str, middle: str, right: str) -> str:
        return left + middle.join("─" * (width + 2) for width in widths) + right

    def data_line(row: List[str]) -> str:
        cells = (pad_cell(row[index], widths[index]) for index in range(column_count))
        return "│ " + " │ ".join(cells) + " │"

    lines = [border("┌", "┬", "┐"), data_line(normalized[0])]
    if len(normalized) > 1:
        lines.append(border("├", "┼", "┤"))
        lines.extend(data_line(row) for row in normalized[1:])
    lines.append(border("└", "┴", "┘"))
    return lines


def box_table_display_width(rows: List[List[str]]) -> int:
    """Predict the widest line `render_box_table` would emit for these rows."""
    if not rows:
        return 0
    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    widths = [
        max(display_width(row[index]) for row in normalized)
        for index in range(column_count)
    ]
    # 每列占「内容 + 左右各一个空格」，列之间与两端各有一根竖线。
    return sum(width + 2 for width in widths) + column_count + 1


def render_field_table(rows: List[List[str]], has_header: bool) -> List[str]:
    """Render a table as per-row `字段：值` groups instead of a box.

    这是宽表的降级形态。横向排不下时，纵向逐字段列出至少能保证
    「哪个值属于哪一列」不丢失——而错位的框线连这一点都做不到。
    有表头时用表头名作字段名；没有表头（缺少 `---` 分隔行）时只能逐值列出，
    但绝不丢内容。
    """
    if not rows:
        return []

    column_count = max(len(row) for row in rows)
    normalized = [row + [""] * (column_count - len(row)) for row in rows]
    header = normalized[0] if has_header else None
    body = normalized[1:] if has_header else normalized

    lines: List[str] = []
    for index, row in enumerate(body):
        if index:
            lines.append("")
        for column, value in enumerate(row):
            if header is not None:
                name = header[column] or f"第 {column + 1} 列"
                lines.append(f"{name}：{value}")
            else:
                lines.append(f"· {value}")
    return lines


def render_table(rows: List[List[str]], has_header: bool = True) -> List[str]:
    """Render a table with the layout its width can actually support."""
    if not rows:
        return []
    if box_table_display_width(rows) <= MAX_TABLE_DISPLAY_WIDTH:
        return render_box_table(rows)
    return render_field_table(rows, has_header)


def convert_markdown_tables(text: str, fenced: bool = False) -> str:
    """
    将文本中的 Markdown 表格替换为等宽框线表格。

    围栏代码块（```）内的内容原样保留，不会被误当成表格处理。

    :param text: 原始文本
    :param fenced: 是否用 ``` 围栏包裹渲染结果。Telegram 等平台需要围栏才会用
                   等宽字体显示，否则框线依然会错位。
    """
    lines = text.split("\n")
    result: List[str] = []
    buffer: List[List[str]] = []
    # 是否见过 `---` 分隔行。没有它就无法断言第一行是表头，
    # 降级时也就不能拿它当字段名。
    saw_separator = False
    in_code_fence = False

    def flush():
        nonlocal saw_separator
        if not buffer:
            saw_separator = False
            return
        rendered = render_table(buffer, has_header=saw_separator)
        buffer.clear()
        saw_separator = False
        result.append("")
        if fenced:
            result.append("```")
            result.extend(rendered)
            result.append("```")
        else:
            result.extend(rendered)
        result.append("")

    for line in lines:
        # 进入/退出围栏代码块，围栏内不做表格识别
        if line.lstrip().startswith("```"):
            flush()
            in_code_fence = not in_code_fence
            result.append(line)
            continue
        if in_code_fence:
            result.append(line)
            continue
        if is_table_separator(line):
            saw_separator = True
            continue
        if is_table_row(line):
            buffer.append(parse_table_row(line))
            continue
        flush()
        result.append(line)
    flush()

    return "\n".join(result)


#: 代码消息旁边的提示文案。
#:
#: QQ 的 OneBot 消息模型没有交互按钮，因此无法渲染真正的「复制」按钮；
#: 画一个点不动的按钮比不画更糟（1.txt 19.3 明确禁止显示不可用按钮）。
#: 等价路径是让代码单独成为一条消息——整条消息就是代码本体，
#: 任意 QQ 客户端长按全选复制即可拿到干净的代码，不会混入正文或页码。
CODE_COPY_HINT = "↑ 代码已单独成条，长按可整段复制"


@dataclass(frozen=True)
class CopyablePart:
    """一段待发送内容；``is_code`` 为真时整条消息只有代码。"""

    text: str
    is_code: bool = False
    code: Optional[str] = None
    language: Optional[str] = None


_FENCE_OPEN_PATTERN = re.compile(r"^\s*```([\w+#.-]*)\s*$")
_FENCE_CLOSE_PATTERN = re.compile(r"^\s*```\s*$")


def split_for_copyable_code(text: str) -> List[CopyablePart]:
    """把正文与围栏代码块拆成可分别发送的片段。

    代码块单独成条，正文合并为相邻片段。未闭合的围栏不当作代码——
    截断的回复不能把后续正文一起吞进代码块里。空代码块直接丢弃，
    避免发出一条只有围栏的空消息。
    """
    lines = text.splitlines()
    parts: List[CopyablePart] = []
    prose: List[str] = []
    index = 0

    def flush_prose() -> None:
        if not prose:
            return
        body = "\n".join(prose).strip()
        prose.clear()
        if body:
            parts.append(CopyablePart(text=body))

    while index < len(lines):
        opening = _FENCE_OPEN_PATTERN.match(lines[index])
        if opening is None:
            prose.append(lines[index])
            index += 1
            continue

        closing_index = next(
            (
                cursor
                for cursor in range(index + 1, len(lines))
                if _FENCE_CLOSE_PATTERN.match(lines[cursor])
            ),
            None,
        )
        if closing_index is None:
            # 未闭合：按普通文本处理，保留原样，不吞掉后面的内容。
            prose.extend(lines[index:])
            break

        flush_prose()
        language = opening.group(1) or None
        code = "\n".join(lines[index + 1 : closing_index])
        if code.strip():
            fence = f"```{language}" if language else "```"
            parts.append(
                CopyablePart(
                    text=f"{fence}\n{code}\n```",
                    is_code=True,
                    code=code,
                    language=language,
                )
            )
        index = closing_index + 1

    flush_prose()
    return parts


class TextBlockKind(str, Enum):
    """Portable block types used by IM adapters before platform rendering."""

    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST = "list"
    QUOTE = "quote"
    TABLE = "table"
    CODE = "code"


@dataclass(frozen=True)
class TextLink:
    label: str
    url: str


@dataclass(frozen=True)
class TextBlock:
    kind: TextBlockKind
    text: str
    links: tuple[TextLink, ...] = ()
    level: Optional[int] = None
    rows: tuple[tuple[str, ...], ...] = ()
    language: Optional[str] = None


@dataclass(frozen=True)
class TextDocument:
    source: str
    blocks: tuple[TextBlock, ...]


_LINK_PATTERN = re.compile(r"!?\[([^\]\n]+)\]\(([^)\n]+)\)")
_LIST_PATTERN = re.compile(r"^\s*(?:[-*+] |\d+[.)] )")
_HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+)$")


def _links_in(text: str) -> tuple[TextLink, ...]:
    return tuple(
        TextLink(label=match.group(1), url=match.group(2))
        for match in _LINK_PATTERN.finditer(text)
    )


def parse_text_document(text: str) -> TextDocument:
    """Parse the Markdown structures that need consistent IM rendering."""
    lines = text.splitlines()
    blocks: list[TextBlock] = []
    index = 0

    def starts_block(line: str) -> bool:
        stripped = line.strip()
        return bool(
            line.lstrip().startswith("```")
            or _HEADING_PATTERN.match(line)
            or _LIST_PATTERN.match(line)
            or line.lstrip().startswith(">")
            or is_table_row(stripped)
        )

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        if line.lstrip().startswith("```"):
            opener = line.lstrip()[3:].strip()
            index += 1
            body: list[str] = []
            while index < len(lines) and not lines[index].lstrip().startswith("```"):
                body.append(lines[index])
                index += 1
            if index < len(lines):
                index += 1
            blocks.append(
                TextBlock(
                    TextBlockKind.CODE,
                    "\n".join(body),
                    language=opener or None,
                )
            )
            continue

        heading = _HEADING_PATTERN.match(line)
        if heading:
            content = heading.group(2)
            blocks.append(
                TextBlock(
                    TextBlockKind.HEADING,
                    content,
                    links=_links_in(content),
                    level=len(heading.group(1)),
                )
            )
            index += 1
            continue

        if is_table_row(line.strip()):
            table_lines: list[str] = []
            rows: list[tuple[str, ...]] = []
            while index < len(lines):
                candidate = lines[index]
                stripped = candidate.strip()
                if not (is_table_row(stripped) or is_table_separator(stripped)):
                    break
                table_lines.append(candidate)
                if is_table_row(stripped):
                    rows.append(tuple(parse_table_row(stripped)))
                index += 1
            source = "\n".join(table_lines)
            blocks.append(
                TextBlock(
                    TextBlockKind.TABLE,
                    source,
                    links=_links_in(source),
                    rows=tuple(rows),
                )
            )
            continue

        if _LIST_PATTERN.match(line):
            values: list[str] = []
            while index < len(lines) and _LIST_PATTERN.match(lines[index]):
                values.append(lines[index])
                index += 1
            source = "\n".join(values)
            blocks.append(
                TextBlock(TextBlockKind.LIST, source, links=_links_in(source))
            )
            continue

        if line.lstrip().startswith(">"):
            values = []
            while index < len(lines) and lines[index].lstrip().startswith(">"):
                values.append(lines[index])
                index += 1
            source = "\n".join(values)
            blocks.append(
                TextBlock(TextBlockKind.QUOTE, source, links=_links_in(source))
            )
            continue

        values = [line]
        index += 1
        while index < len(lines) and lines[index].strip() and not starts_block(lines[index]):
            values.append(lines[index])
            index += 1
        source = "\n".join(values)
        blocks.append(
            TextBlock(TextBlockKind.PARAGRAPH, source, links=_links_in(source))
        )

    return TextDocument(source=text, blocks=tuple(blocks))


_LATEX_COMMANDS = {
    "alpha": "α",
    "beta": "β",
    "gamma": "γ",
    "delta": "δ",
    "Delta": "Δ",
    "epsilon": "ε",
    "lambda": "λ",
    "mu": "μ",
    "pi": "π",
    "sigma": "σ",
    "omega": "ω",
    "Omega": "Ω",
    "theta": "θ",
    "Theta": "Θ",
    "phi": "φ",
    "Phi": "Φ",
    "psi": "ψ",
    "rho": "ρ",
    "tau": "τ",
    "nabla": "∇",
    "partial": "∂",
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "Rightarrow": "⇒",
    "Leftarrow": "⇐",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "neq": "≠",
    "approx": "≈",
    "equiv": "≡",
    "propto": "∝",
    "sim": "∼",
    "times": "×",
    "div": "÷",
    "cdot": "·",
    "pm": "±",
    "mp": "∓",
    "infty": "∞",
    "int": "∫",
    "iint": "∬",
    "oint": "∮",
    "in": "∈",
    "notin": "∉",
    "subset": "⊂",
    "subseteq": "⊆",
    "cup": "∪",
    "cap": "∩",
    "emptyset": "∅",
    "forall": "∀",
    "exists": "∃",
    "land": "∧",
    "lor": "∨",
    "neg": "¬",
    "angle": "∠",
    "perp": "⊥",
    "parallel": "∥",
    "degree": "°",
    "ldots": "…",
    "cdots": "⋯",
    "exp": "exp",
    "ln": "ln",
    "log": "log",
    "lim": "lim",
    "max": "max",
    "min": "min",
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "sum": "Σ",
    "prod": "Π",
}
_COMMAND_PATTERN = re.compile(r"\\([A-Za-z]+)")
_BRACED_PATTERN = re.compile(
    r"\\(?:text|mathrm|mathbf|mathit|mathbb|mathcal|operatorname|boxed)\{([^{}]*)\}"
)
_SPACE_COMMAND_PATTERN = re.compile(r"\\(?:,|;|:|!|quad|qquad)")
#: `\begin{env}` / `\end{env}` 只是排版容器。整段丢掉环境名比留下 `begincases`
#: 这种拼接词好：后者既不是数学也不是中文，纯属噪声。
_ENVIRONMENT_PATTERN = re.compile(r"\\(?:begin|end)\{[^{}]*\}")
#: `\left(` / `\right]` 只影响定界符大小，去掉命令保留符号本身即可。
#: `(?![A-Za-z])` 不可省：否则 `\right` 会吃掉 `\rightarrow` 的前缀，
#: 把箭头变成 `arrow`；`\left` 对 `\leftarrow` 同理。
_SIZED_DELIMITER_PATTERN = re.compile(
    r"\\(?:left|right|big|Big|bigg|Bigg)(?![A-Za-z])\s*"
)

#: LaTeX 的换行命令。保留为字面 `\\` 会被当成转义残片，正是 19.2 禁止的形态。
_LINE_BREAK_PATTERN = re.compile(r"\\\\")

#: 一段 `$...$` 只有在内容确实带 LaTeX 特征时才按公式处理。
#:
#: 旧规则只看 `$` 是否配对，于是 "price $5 and $7" 里两个货币符号恰好配对，
#: 中间内容被当作公式剥离——金额的单位被静默删掉，只剩数字。判据改成内容里
#: 必须出现反斜杠命令、上下标或分式，货币写法（`$5`、`$1,200`）不会命中。
_MATH_EVIDENCE_PATTERN = re.compile(r"\\[A-Za-z]|\\\\|[\^_]|\{|\}")
_MATH_PATTERN = re.compile(r"(?<!\\)(\${1,2})(.+?)(?<!\\)\1", re.DOTALL)
_PAREN_MATH_PATTERN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_BRACKET_MATH_PATTERN = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def _looks_like_math(expression: str) -> bool:
    """Whether a `$...$` span really carries LaTeX rather than currency amounts."""
    return bool(_MATH_EVIDENCE_PATTERN.search(expression))



def _clean_math_expression(text: str) -> str:
    # 嵌套分式要反复求解，否则 \frac{\frac{a}{b}}{c} 只有内层被处理，
    # 外层残留成 frac(...)。内层没有花括号时正则不再匹配，循环必然终止。
    for _ in range(4):
        replaced = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", text)
        if replaced == text:
            break
        text = replaced
    text = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", text)
    text = re.sub(r"\\overline\{([^{}]*)\}", r"\1̄", text)
    text = _BRACED_PATTERN.sub(r"\1", text)
    text = _ENVIRONMENT_PATTERN.sub(" ", text)
    text = _LINE_BREAK_PATTERN.sub("\n", text)
    text = _SIZED_DELIMITER_PATTERN.sub("", text)
    text = _SPACE_COMMAND_PATTERN.sub(" ", text)
    text = _COMMAND_PATTERN.sub(
        lambda match: _LATEX_COMMANDS.get(match.group(1), match.group(1)), text
    )
    text = text.replace(r"\_", "_")
    text = text.replace(r"\{", "\x00OPEN_BRACE\x00")
    text = text.replace(r"\}", "\x00CLOSE_BRACE\x00")
    text = text.replace("{", "").replace("}", "")
    return text.replace("\x00OPEN_BRACE\x00", "{").replace(
        "\x00CLOSE_BRACE\x00", "}"
    )


#: LaTeX 里对字面量的转义。留着反斜杠就是 19.2 点名禁止的「转义残片」：
#: 用户看到的是 `\$5`、`file\_name`，而那两个反斜杠不传达任何信息。
_ESCAPED_LITERAL_PATTERN = re.compile(r"\\([$_{}%&#~])")


def _clean_latex(text: str) -> str:
    def _math_span(match: "re.Match[str]", group: int) -> str:
        expression = match.group(group)
        # 货币金额也能凑出配对的 `$`；没有 LaTeX 特征就原样保留整段，
        # 包括定界符本身，否则 "$5" 会变成 "5"。
        if not _looks_like_math(expression):
            return match.group(0)
        return _clean_math_expression(expression)

    for pattern, group in (
        (_MATH_PATTERN, 2),
        (_PAREN_MATH_PATTERN, 1),
        (_BRACKET_MATH_PATTERN, 1),
    ):
        text = pattern.sub(
            lambda match, group=group: _math_span(match, group),
            text,
        )
    # 定界符之外的裸命令同样要降级：模型经常直接写 `\to`、`\times`，
    # 只处理定界符内部会让这些命令原样出现在 QQ 里。
    text = _ENVIRONMENT_PATTERN.sub(" ", text)
    text = _SIZED_DELIMITER_PATTERN.sub("", text)
    # 未收录的命令**去掉反斜杠、保留命令名**。
    #
    # 此前这里保留 `match.group(0)`，于是 `\foo` 原样送到 QQ——那正是
    # 19.2 点名禁止的「转义残片」。命令名不一定准确表达原意，但它是一个可读的
    # 单词；残片什么信息都不传达，还会让人以为回复被截断或编码坏了。
    # 刻意不猜未知命令的语义：`\foo` 该显示成什么无法可靠推断。
    text = _COMMAND_PATTERN.sub(
        lambda match: _LATEX_COMMANDS.get(match.group(1), match.group(1)), text
    )
    text = _ESCAPED_LITERAL_PATTERN.sub(r"\1", text)
    return text


def _split_fenced_sections(text: str) -> Iterable[tuple[bool, str]]:
    lines = text.splitlines(keepends=True)
    if not lines:
        yield False, text
        return
    buffer: list[str] = []
    in_fence = False
    for line in lines:
        if line.lstrip().startswith("```"):
            if buffer:
                yield in_fence, "".join(buffer)
                buffer = []
            yield True, line
            in_fence = not in_fence
        else:
            buffer.append(line)
    if buffer:
        yield in_fence, "".join(buffer)


def _drop_unpaired_backticks(text: str) -> str:
    """Remove a trailing, unmatched single-backtick inline-code marker.

    需求 19.2 点名禁止「未闭合反引号」。已有的两处防守（未闭合围栏不当代码、
    分页不劈开行内代码）解决的都是「我们不要弄坏它」，不解决「模型输出本身就
    少一个」。一个落单的 `` ` `` 在 QQ 里是可见的垃圾字符，更糟的是它会让
    后面一大段正文呈现为「行内代码待闭合」的观感。

    刻意收窄到**单个**反引号：
    - 只删最后一段落单的，前面配对的行内代码一个都不动——删多了是丢格式，
      而不是清噪声。
    - 长度 ≥2 的反引号串（```` `` ````、`` ```` ``）不处理。它们在 Markdown 里
      有合法用途（在行内代码里包含反引号本身），落单时到底是噪声还是有意写法
      无法可靠判断，猜错就是改内容。

    围栏行由调用方在分段时排除，不会进到这里。
    """
    runs = [match for match in re.finditer(r"`+", text)]
    if not runs:
        return text
    single_runs = [match for match in runs if len(match.group(0)) == 1]
    # 只有单反引号参与配对判断：多反引号串各自成对或本就不是行内代码标记。
    if not single_runs or len(single_runs) % 2 == 0:
        return text
    last = single_runs[-1]
    return text[: last.start()] + text[last.end() :]


def degrade_math(text: str) -> str:
    """Degrade LaTeX to readable plain text, leaving fenced code untouched.

    这是 ``render_plain_text`` 的数学降级那一半，单独导出供**已经**自带
    Markdown 处理流程的适配器复用（WeCom 有一套自己的标题/强调/表格规则，
    它需要的只是这一步）。此前 WeCom 路径完全不做数学降级，于是同一个模型的
    同一段回复，QQ 上是 `T → 0`、WeCom 上是原始的 `$T \\to 0$`——
    平台差异应该只体现在渲染层，不该表现为「有的平台处理了、有的没有」。
    """
    rendered: list[str] = []
    non_fence_indexes: list[int] = []
    for is_fence, section in _split_fenced_sections(text):
        if is_fence:
            rendered.append(section)
        else:
            non_fence_indexes.append(len(rendered))
            rendered.append(_clean_latex(section))
    # 落单反引号只在**围栏之外**清理，且按整段（而非逐个片段）判断配对：
    # 一段行内代码不会跨越围栏，但完全可能跨越 `_split_fenced_sections`
    # 切出的相邻片段，逐片判断会把配对的两半各自当成落单的。
    if non_fence_indexes:
        joined = "".join(rendered[index] for index in non_fence_indexes)
        cleaned = _drop_unpaired_backticks(joined)
        if cleaned != joined:
            # 只有真的删掉了才重排：这条路径罕见，不该在常见情况下改变结构。
            for position, index in enumerate(non_fence_indexes):
                rendered[index] = cleaned if position == 0 else ""
    return "".join(rendered)


def render_plain_text(
    document: Union[TextDocument, str],
    *,
    fenced_tables: bool = False,
) -> str:
    """Degrade unsupported formulas and tables without altering fenced code."""
    source = document.source if isinstance(document, TextDocument) else document
    return convert_markdown_tables(degrade_math(source), fenced=fenced_tables)


Measure = Callable[[str], int]


def _utf8_length(text: str) -> int:
    return len(text.encode("utf-8"))


#: 不可在中间切断的行内片段。
#:
#: 把 `*强调*`、`` `代码` `` 或一条链接劈成两页，得到的是两个都不成立的残片：
#: 第一页尾巴上挂着一个没有闭合的标记，第二页开头是一个凭空出现的标记。
#: 1.txt 19.4 明确禁止拆坏 Markdown 标记。链接单独用 `_LINK_PATTERN`
#: 处理（它还要参与 alt/文本提取），这里补上其余成对标记。
_ATOMIC_SPAN_PATTERNS: tuple["re.Pattern[str]", ...] = (
    _LINK_PATTERN,
    re.compile(r"\*\*\*[^*\n]+\*\*\*"),
    re.compile(r"\*\*[^*\n]+\*\*"),
    re.compile(r"(?<!\*)\*[^*\n]+\*(?!\*)"),
    re.compile(r"___[^_\n]+___"),
    re.compile(r"__[^_\n]+__"),
    re.compile(r"~~[^~\n]+~~"),
    re.compile(r"``[^`\n]+``"),
    re.compile(r"(?<!`)`[^`\n]+`(?!`)"),
)

#: 优先作为分页边界的行首结构。
#:
#: 只按段落与句号切分会把标题、列表项、表格行从中间劈开——读者在下一页看到的是
#: 一段没有标题的正文，或半个列表项。这些模式让切点落在结构的起始处。
_STRUCTURAL_LINE_PATTERN = re.compile(
    r"^(?:#{1,6} |[-*+] |\d+[.)] |> |\|)",
    re.MULTILINE,
)


def _cut_inside_atomic_span(
    text: str, cut: int, limit: int, measure: Measure
) -> int:
    """Move a cut point out of any atomic inline span it lands inside.

    片段整体放得下时把切点推到它前面（让它整块进下一页）；放不下时只能退到
    它后面——否则这一页永远装不进任何内容，分页会不收敛。
    """
    for pattern in _ATOMIC_SPAN_PATTERNS:
        for match in pattern.finditer(text):
            if match.start() < cut < match.end():
                if measure(match.group(0)) <= limit:
                    return match.start() if match.start() else match.end()
    return cut


def _cut_inside_atomic_link(text: str, cut: int, limit: int, measure: Measure) -> int:
    """Backwards-compatible alias kept for callers that only cared about links."""
    for match in _LINK_PATTERN.finditer(text):
        if match.start() < cut < match.end() and measure(match.group(0)) <= limit:
            return match.start() if match.start() else match.end()
    return cut


def _split_text_preserving(text: str, limit: int, measure: Measure) -> list[str]:
    """Split on Unicode boundaries while retaining every source character."""
    if not text:
        return [""]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if measure(remaining) <= limit:
            chunks.append(remaining)
            break

        used = 0
        hard_cut = 0
        for index, char in enumerate(remaining):
            size = measure(char)
            if used + size > limit:
                break
            used += size
            hard_cut = index + 1
        if hard_cut == 0:
            raise ValueError("消息上限太小，无法容纳一个完整字符")

        cut = _cut_inside_atomic_span(remaining, hard_cut, limit, measure)
        if cut == hard_cut:
            boundaries: list[int] = []
            # 结构行的起点优先：标题、列表项、引用与表格行都应整块进入下一页。
            for match in _STRUCTURAL_LINE_PATTERN.finditer(remaining, 0, hard_cut):
                candidate = match.start()
                if candidate <= 0:
                    continue
                if _cut_inside_atomic_span(
                    remaining, candidate, limit, measure
                ) == candidate:
                    boundaries.append(candidate)
            if not boundaries:
                for marker in ("\n\n", "\n", "。", "！", "？", ". ", " "):
                    position = remaining.rfind(marker, 0, hard_cut)
                    if position >= 0:
                        candidate = position + len(marker)
                        if _cut_inside_atomic_span(
                            remaining, candidate, limit, measure
                        ) == candidate:
                            boundaries.append(candidate)
            if boundaries:
                cut = max(boundaries)
        if cut <= 0:
            cut = hard_cut
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def _code_line_kind(line: str, code_style: str) -> tuple[bool, bool]:
    stripped = line.rstrip("\r\n")
    if code_style == "markdown":
        fence = line.lstrip().startswith("```")
        return fence, fence
    if code_style == "wecom":
        return stripped.startswith("［代码"), stripped == "［/代码］"
    raise ValueError(f"不支持的代码围栏样式：{code_style}")


def _split_code_block(
    block: str,
    limit: int,
    measure: Measure,
    code_style: str,
) -> list[str]:
    lines = block.splitlines(keepends=True)
    if len(lines) < 2:
        return _split_text_preserving(block, limit, measure)
    is_open, _ = _code_line_kind(lines[0], code_style)
    _, is_close = _code_line_kind(lines[-1], code_style)
    if not is_open or not is_close:
        return _split_text_preserving(block, limit, measure)

    opener = lines[0].rstrip("\r\n")
    closer = lines[-1].rstrip("\r\n")
    content = "".join(lines[1:-1])
    prefix = opener + "\n"
    available = limit - measure(prefix) - measure(closer) - measure("\n")
    if available <= 0:
        raise ValueError("消息上限太小，无法保留完整代码围栏")
    parts = _split_text_preserving(content, available, measure) if content else [""]
    result: list[str] = []
    for part in parts:
        result.append(prefix + part + "\n" + closer)
    return result


def _split_box_table(lines: list[str], limit: int, measure: Measure) -> list[str]:
    if len(lines) < 4:
        return _split_text_preserving("\n".join(lines), limit, measure)
    header = lines[:3]
    footer = lines[-1]
    data_rows = lines[3:-1]
    if measure("\n".join(header + [footer])) > limit:
        raise ValueError("消息上限太小，无法保留完整表格边框")

    pages: list[str] = []
    current = list(header)
    for row in data_rows:
        if measure("\n".join(current + [row, footer])) <= limit:
            current.append(row)
            continue
        if len(current) > len(header):
            pages.append("\n".join(current + [footer]))
            current = list(header)
        if measure("\n".join(current + [row, footer])) <= limit:
            current.append(row)
        else:
            pages.extend(
                _split_text_preserving(
                    "表格行（单元格过长，已折行）：\n" + row,
                    limit,
                    measure,
                )
            )
    if len(current) > len(header) or not pages:
        pages.append("\n".join(current + [footer]))
    return pages


def _split_structured_body(
    text: str,
    limit: int,
    measure: Measure,
    code_style: str,
) -> list[str]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return [text]
    chunks: list[str] = []
    regular: list[str] = []
    index = 0

    def flush_regular() -> None:
        nonlocal regular
        if regular:
            chunks.extend(_split_text_preserving("".join(regular), limit, measure))
            regular = []

    while index < len(lines):
        line = lines[index]
        is_open, _ = _code_line_kind(line, code_style)
        if is_open:
            flush_regular()
            block = [line]
            index += 1
            while index < len(lines):
                block.append(lines[index])
                _, is_close = _code_line_kind(lines[index], code_style)
                index += 1
                if is_close:
                    break
            chunks.extend(
                _split_code_block("".join(block), limit, measure, code_style)
            )
            continue

        stripped = line.rstrip("\r\n")
        if stripped.startswith("┌"):
            flush_regular()
            table = [stripped]
            index += 1
            while index < len(lines):
                table.append(lines[index].rstrip("\r\n"))
                index += 1
                if table[-1].startswith("└"):
                    break
            chunks.extend(_split_box_table(table, limit, measure))
            continue

        regular.append(line)
        index += 1
    flush_regular()
    return chunks or [""]


#: 全渠道统一的页码格式。
#:
#: QQ、Telegram、WeCom 必须使用同一种写法：同一个机器人在不同 APP 上给出两套
#: 页码（例如 ``第 1 页 / 共 3 页`` 与 ``[1/3]``）会让用户以为是两个不同的服务。
#: 该正则同时供测试与调用方识别、剥离页码，避免各处再写一份字面量。
PAGE_LABEL_PATTERN = re.compile(r"^第 \d+ 页 / 共 \d+ 页\n?", re.MULTILINE)


def _page_label(page: int, total: int) -> str:
    return f"第 {page} 页 / 共 {total} 页\n"


#: 内容被截断时追加的提示。
#:
#: 超出页数或总字节预算时，旧行为是让 ``ValueError`` 一路穿出 ``send_message``，
#: 于是用户**什么都收不到**。收到前 N 页并被明确告知「已截断」远好过静默失败：
#: 前者能读到大部分内容并知道还有更多，后者只看到机器人没反应。
TRUNCATION_NOTICE = "\n\n（内容过长，已截断；请缩小提问范围或分次获取剩余部分。）"


def paginate_with_truncation_notice(
    text: str,
    *,
    max_bytes: Optional[int] = None,
    max_length: Optional[int] = None,
    max_pages: int = 100,
    max_total_bytes: Optional[int] = 1_000_000,
    code_style: str = "markdown",
) -> tuple[list[str], bool]:
    """Paginate, falling back to a truncated reply instead of raising.

    返回 ``(页面列表, 是否发生截断)``。参数校验类错误（上限本身非法、上限太小
    连一个字符都放不下）仍然抛出——那是调用方的配置错误，不该悄悄降级。
    只有「内容超出预算」这一类才截断。
    """
    def _paginate(source: str) -> list[str]:
        return split_structured_text(
            source,
            max_bytes,
            max_length=max_length,
            max_pages=max_pages,
            max_total_bytes=max_total_bytes,
            code_style=code_style,
        )

    try:
        return _paginate(text), False
    except ValueError as error:
        if "上限" not in str(error) or "太小" in str(error):
            raise
        if "必须" in str(error):
            raise

    measure: Measure = _utf8_length if max_bytes is not None else len
    limit = max_bytes if max_bytes is not None else max_length
    assert limit is not None

    # 逐步收缩到预算之内。二分比线性收缩快，且每一步都是一次真实分页，
    # 因此最终结果一定满足页数与字节两个上限。
    low, high = 0, len(text)
    best: Optional[list[str]] = None
    while low < high:
        middle = (low + high + 1) // 2
        candidate = text[:middle] + TRUNCATION_NOTICE
        try:
            pages = _paginate(candidate)
        except ValueError:
            high = middle - 1
            continue
        best = pages
        low = middle
    if best is not None:
        return best, True

    # 连一条只有提示语的消息都放不下：只能给出被硬切到上限的提示本身。
    notice = TRUNCATION_NOTICE.strip()
    while notice and measure(notice) > limit:
        notice = notice[:-1]
    return ([notice] if notice else []), True


def split_structured_text(
    text: str,
    max_bytes: Optional[int] = None,
    *,
    max_length: Optional[int] = None,
    max_pages: int = 100,
    max_total_bytes: Optional[int] = 1_000_000,
    code_style: str = "markdown",
) -> list[str]:
    """Paginate text with complete Unicode, links, code fences and tables.

    ``max_bytes`` selects UTF-8 byte accounting for QQ and WeCom.
    ``max_length`` selects Unicode character accounting for Telegram.
    """
    if (max_bytes is None) == (max_length is None):
        raise ValueError("必须且只能指定 max_bytes 或 max_length")
    limit = max_bytes if max_bytes is not None else max_length
    assert limit is not None
    if limit <= 0:
        raise ValueError("消息上限必须大于 0")
    if max_pages <= 0:
        raise ValueError("max_pages 必须大于 0")
    if max_total_bytes is not None and max_total_bytes <= 0:
        raise ValueError("max_total_bytes 必须大于 0")
    if max_total_bytes is not None and _utf8_length(text) > max_total_bytes:
        raise ValueError(f"回复内容超过总字节上限（{max_total_bytes} bytes）")

    measure: Measure = _utf8_length if max_bytes is not None else len
    if measure(text) <= limit:
        return [text]

    total = 1
    chunks: list[str] = []
    for _ in range(12):
        label_size = max(
            measure(_page_label(page, total)) for page in range(1, total + 1)
        )
        available = limit - label_size
        if available <= 0:
            raise ValueError("消息上限太小，无法容纳页码和正文")
        chunks = _split_structured_body(text, available, measure, code_style)
        new_total = len(chunks)
        if new_total > max_pages:
            raise ValueError(f"回复内容超过页数上限（{max_pages} 页）")
        if new_total == total:
            break
        total = new_total
    else:
        raise ValueError("无法在指定消息上限内稳定计算页数")

    total = len(chunks)
    result = [
        _page_label(index, total) + chunk
        for index, chunk in enumerate(chunks, start=1)
    ]
    if any(measure(page) > limit for page in result):
        label_size = max(
            measure(_page_label(page, total)) for page in range(1, total + 1)
        )
        chunks = _split_structured_body(
            text, limit - label_size, measure, code_style
        )
        total = len(chunks)
        if total > max_pages:
            raise ValueError(f"回复内容超过页数上限（{max_pages} 页）")
        result = [
            _page_label(index, total) + chunk
            for index, chunk in enumerate(chunks, start=1)
        ]
    if max_total_bytes is not None and sum(
        _utf8_length(page) for page in result
    ) > max_total_bytes:
        raise ValueError(
            f"分页后的回复超过总字节上限（{max_total_bytes} bytes）"
        )
    return result
