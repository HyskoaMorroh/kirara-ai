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
    in_code_fence = False

    def flush():
        if not buffer:
            return
        rendered = render_box_table(buffer)
        buffer.clear()
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
    "to": "→",
    "rightarrow": "→",
    "leftarrow": "←",
    "le": "≤",
    "leq": "≤",
    "ge": "≥",
    "geq": "≥",
    "neq": "≠",
    "approx": "≈",
    "times": "×",
    "cdot": "·",
    "pm": "±",
    "infty": "∞",
    "exp": "exp",
    "ln": "ln",
    "log": "log",
    "sin": "sin",
    "cos": "cos",
    "tan": "tan",
    "sum": "Σ",
    "prod": "Π",
}
_COMMAND_PATTERN = re.compile(r"\\([A-Za-z]+)")
_BRACED_PATTERN = re.compile(
    r"\\(?:text|mathrm|mathbf|mathit|operatorname|boxed)\{([^{}]*)\}"
)
_SPACE_COMMAND_PATTERN = re.compile(r"\\(?:,|;|:|!|quad|qquad)")
_MATH_PATTERN = re.compile(r"(?<!\\)(\${1,2})(.+?)(?<!\\)\1", re.DOTALL)
_PAREN_MATH_PATTERN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_BRACKET_MATH_PATTERN = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def _clean_math_expression(text: str) -> str:
    text = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", text)
    text = re.sub(r"\\overline\{([^{}]*)\}", r"\1̄", text)
    text = _BRACED_PATTERN.sub(r"\1", text)
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


def _clean_latex(text: str) -> str:
    for pattern, group in (
        (_MATH_PATTERN, 2),
        (_PAREN_MATH_PATTERN, 1),
        (_BRACKET_MATH_PATTERN, 1),
    ):
        text = pattern.sub(
            lambda match, group=group: _clean_math_expression(match.group(group)),
            text,
        )
    return text.replace(r"\$", "$")


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


def render_plain_text(
    document: Union[TextDocument, str],
    *,
    fenced_tables: bool = False,
) -> str:
    """Degrade unsupported formulas and tables without altering fenced code."""
    source = document.source if isinstance(document, TextDocument) else document
    rendered: list[str] = []
    for is_fence, section in _split_fenced_sections(source):
        rendered.append(section if is_fence else _clean_latex(section))
    return convert_markdown_tables("".join(rendered), fenced=fenced_tables)


Measure = Callable[[str], int]


def _utf8_length(text: str) -> int:
    return len(text.encode("utf-8"))


def _cut_inside_atomic_link(text: str, cut: int, limit: int, measure: Measure) -> int:
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

        cut = _cut_inside_atomic_link(remaining, hard_cut, limit, measure)
        if cut == hard_cut:
            boundaries: list[int] = []
            for marker in ("\n\n", "\n", "。", "！", "？", ". ", " "):
                position = remaining.rfind(marker, 0, hard_cut)
                if position >= 0:
                    candidate = position + len(marker)
                    if _cut_inside_atomic_link(
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
