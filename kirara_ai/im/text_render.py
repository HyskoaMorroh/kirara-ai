"""
IM 消息文本排版工具。

集中提供「显示宽度计算 / 等宽表格渲染 / Markdown 表格预渲染」等能力，
供各 IM 适配器（企业微信、Telegram 等）共用，避免各自实现导致排版不一致。
"""

import re
from typing import List

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
