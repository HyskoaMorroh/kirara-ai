"""OneBot V11 的纯文本排版与分页。

OneBot 本身只接收消息段，不提供跨客户端一致的富文本按钮。这里使用
客户端普遍支持的 Markdown 代码围栏和等宽框线表格，尽量让 QQ 侧的
普通文本、代码与表格在不同实现中都保持可读。
"""

from __future__ import annotations

import re
from typing import Iterable, List, Optional

from kirara_ai.im.text_render import convert_markdown_tables


DEFAULT_MAX_BYTES = 3800
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_TOTAL_BYTES = 1_000_000

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
    "leq": "≤",
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
_BRACED_PATTERN = re.compile(r"\\(?:text|mathrm|mathbf|mathit|operatorname|boxed)\{([^{}]*)\}")
_SPACE_COMMAND_PATTERN = re.compile(r"\\(?:,|;|:|!|quad|qquad)")
_MATH_PATTERN = re.compile(r"(?<!\\)(\${1,2})(.+?)(?<!\\)\1", re.DOTALL)


def _replace_frac_and_sqrt(text: str) -> str:
    """处理常见的单层 TeX 分组，避免把 LaTeX 原样暴露到 QQ。"""
    text = re.sub(r"\\frac\{([^{}]*)\}\{([^{}]*)\}", r"(\1)/(\2)", text)
    text = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", text)
    text = re.sub(r"\\overline\{([^{}]*)\}", r"\1̄", text)
    return text


def _clean_math_expression(text: str) -> str:
    """清洗已经确认位于数学定界符内的 TeX 表达式。"""
    text = _replace_frac_and_sqrt(text)
    text = _BRACED_PATTERN.sub(r"\1", text)
    text = _SPACE_COMMAND_PATTERN.sub(" ", text)
    text = _COMMAND_PATTERN.sub(
        lambda match: _LATEX_COMMANDS.get(match.group(1), match.group(1)), text
    )
    text = text.replace(r"\_", "_")
    # Escaped braces are literal TeX characters. Protect them while removing
    # grouping braces so ordinary JSON/dict text outside math is untouched.
    text = text.replace(r"\{", "\x00OPEN_BRACE\x00")
    text = text.replace(r"\}", "\x00CLOSE_BRACE\x00")
    text = text.replace("{", "").replace("}", "")
    return text.replace("\x00OPEN_BRACE\x00", "{").replace("\x00CLOSE_BRACE\x00", "}")


def _clean_latex(text: str) -> str:
    """只清洗明确的 TeX 数学片段，保留普通文本中的花括号。"""
    text = _MATH_PATTERN.sub(
        lambda match: _clean_math_expression(match.group(2)), text
    )
    text = text.replace(r"\$", "$")
    return text


def _split_fenced_sections(text: str) -> Iterable[tuple[bool, str]]:
    """按完整代码围栏切分文本，代码段原样返回。"""
    lines = text.splitlines(keepends=True)
    if not lines:
        yield False, text
        return

    buffer: List[str] = []
    in_fence = False
    for line in lines:
        is_fence = line.lstrip().startswith("```")
        if is_fence:
            if buffer:
                yield in_fence, "".join(buffer)
                buffer = []
            yield True, line
            in_fence = not in_fence
        else:
            buffer.append(line)
    if buffer:
        yield in_fence, "".join(buffer)


def render_onebot_text(text: str) -> str:
    """清洗普通文本并把 Markdown 表格转换成规整框线表格。

    代码围栏中的字符完全保持原样，避免清洗公式时破坏可执行示例。
    """
    sections = list(_split_fenced_sections(text))
    rendered: List[str] = []
    for is_fence, section in sections:
        if is_fence:
            rendered.append(section)
        else:
            rendered.append(_clean_latex(section))
    return convert_markdown_tables("".join(rendered), fenced=False)


def _byte_length(text: str) -> int:
    return len(text.encode("utf-8"))


def _split_text_preserving(text: str, max_bytes: int) -> List[str]:
    """按 Unicode 字符切分，保留原始文本的每个字符和换行。"""
    if not text:
        return [""]
    if max_bytes < 4:
        raise ValueError("max_bytes 太小，无法容纳 UTF-8 字符")

    chunks: List[str] = []
    remaining = text
    while remaining:
        if _byte_length(remaining) <= max_bytes:
            chunks.append(remaining)
            break

        used = 0
        cut = 0
        for index, char in enumerate(remaining):
            size = _byte_length(char)
            if used + size > max_bytes:
                break
            used += size
            cut = index + 1
        if cut == 0:
            raise ValueError("max_bytes 太小，无法容纳 UTF-8 字符")

        # 优先在自然换行、空格或中文标点后分页，但不丢弃边界字符。
        boundaries = []
        for marker in ("\n\n", "\n", "。", "！", "？", ". ", " "):
            position = remaining.rfind(marker, 0, cut)
            if position >= 0:
                boundaries.append(position + len(marker))
        if boundaries:
            cut = max(boundaries)
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def _split_regular_section(text: str, max_bytes: int) -> List[str]:
    return _split_text_preserving(text, max_bytes)


def _split_code_block(block: str, max_bytes: int) -> List[str]:
    lines = block.splitlines(keepends=True)
    if len(lines) < 2 or not lines[0].lstrip().startswith("```"):
        return _split_regular_section(block, max_bytes)

    opener = lines[0].rstrip("\r\n")
    has_closer = lines[-1].lstrip().startswith("```") and len(lines) > 1
    if not has_closer:
        return _split_regular_section(block, max_bytes)
    closer = lines[-1].rstrip("\r\n")
    content = "".join(lines[1:-1])
    prefix = opener + "\n"
    suffix = "\n" + closer
    available = max_bytes - _byte_length(prefix) - _byte_length(suffix)
    if available < 5:
        raise ValueError("max_bytes 太小，无法保留完整代码围栏")

    # A chunk without a trailing newline receives one before the closing
    # fence. Reserve that byte/character in the split budget so the final
    # message never exceeds the advertised UTF-8 limit.
    parts = _split_text_preserving(content, available - 1) if content else [""]
    result: List[str] = []
    for part in parts:
        if part and not part.endswith(("\n", "\r")):
            part += "\n"
        result.append(prefix + part + closer)
    return result


def _is_box_table_start(line: str) -> bool:
    return line.startswith("┌")


def _is_box_table_end(line: str) -> bool:
    return line.startswith("└")


def _split_box_table(lines: List[str], max_bytes: int) -> List[str]:
    if len(lines) < 4:
        return _split_regular_section("\n".join(lines), max_bytes)

    header = lines[:3]
    footer = lines[-1]
    data_rows = lines[3:-1]
    fixed = header + [footer]
    if _byte_length("\n".join(fixed)) > max_bytes:
        raise ValueError("max_bytes 太小，无法保留完整表格边框")

    pages: List[str] = []
    current = list(header)
    for row in data_rows:
        candidate = current + [row, footer]
        if _byte_length("\n".join(candidate)) <= max_bytes:
            current.append(row)
            continue
        if len(current) == len(header):
            pages.extend(
                _split_regular_section(
                    "表格行（单元格过长，已折行）：\n" + row,
                    max_bytes,
                )
            )
            continue
        pages.append("\n".join(current + [footer]))
        current = list(header)
        if _byte_length("\n".join(current + [row, footer])) <= max_bytes:
            current.append(row)
        else:
            pages.extend(
                _split_regular_section(
                    "表格行（单元格过长，已折行）：\n" + row,
                    max_bytes,
                )
            )
    if len(current) > len(header) or not pages:
        pages.append("\n".join(current + [footer]))
    return pages


def _split_body(text: str, max_bytes: int) -> List[str]:
    """按普通文本、代码块和框线表格切分正文。"""
    lines = text.splitlines(keepends=True)
    if not lines:
        return [text]

    chunks: List[str] = []
    regular: List[str] = []
    index = 0

    def flush_regular() -> None:
        nonlocal regular
        if regular:
            chunks.extend(_split_regular_section("".join(regular), max_bytes))
            regular = []

    while index < len(lines):
        line = lines[index]
        stripped = line.rstrip("\r\n")
        if line.lstrip().startswith("```"):
            flush_regular()
            block = [line]
            index += 1
            while index < len(lines):
                block.append(lines[index])
                if lines[index].lstrip().startswith("```"):
                    index += 1
                    break
                index += 1
            chunks.extend(_split_code_block("".join(block), max_bytes))
            continue
        if _is_box_table_start(stripped):
            flush_regular()
            table = [stripped]
            index += 1
            while index < len(lines):
                table.append(lines[index].rstrip("\r\n"))
                if _is_box_table_end(table[-1]):
                    index += 1
                    break
                index += 1
            chunks.extend(_split_box_table(table, max_bytes))
            continue
        regular.append(line)
        index += 1

    flush_regular()
    return chunks or [""]


def _page_label(page: int, total: int) -> str:
    return f"第 {page} 页 / 共 {total} 页\n"


def paginate_onebot_text(
    text: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_total_bytes: Optional[int] = DEFAULT_MAX_TOTAL_BYTES,
) -> List[str]:
    """将已排版文本分页，并只为多页回复添加中文页码。"""
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须大于 0")
    if max_pages <= 0:
        raise ValueError("max_pages 必须大于 0")
    if max_total_bytes is not None and max_total_bytes <= 0:
        raise ValueError("max_total_bytes 必须大于 0")
    source_bytes = _byte_length(text)
    if max_total_bytes is not None and source_bytes > max_total_bytes:
        raise ValueError(
            f"回复内容超过总字节上限（{max_total_bytes} bytes）"
        )
    if _byte_length(text) <= max_bytes:
        return [text]

    total = 1
    chunks: List[str] = []
    for _ in range(12):
        label_bytes = max(
            _byte_length(_page_label(page, total)) for page in range(1, total + 1)
        )
        available = max_bytes - label_bytes
        if available < 4:
            raise ValueError("max_bytes 太小，无法容纳页码和正文")
        chunks = _split_body(text, available)
        new_total = len(chunks)
        if new_total > max_pages:
            raise ValueError(f"回复内容超过页数上限（{max_pages} 页）")
        if new_total == total:
            break
        total = new_total
    else:
        raise ValueError("无法在指定字节上限内稳定计算页数")

    total = len(chunks)
    result = [_page_label(index, total) + chunk for index, chunk in enumerate(chunks, 1)]
    if any(_byte_length(page) > max_bytes for page in result):
        # The final digit count can grow when pagination stabilizes; recalculate
        # once with the exact total before reporting an invalid page.
        available = max_bytes - max(
            _byte_length(_page_label(page, total)) for page in range(1, total + 1)
        )
        chunks = _split_body(text, available)
        total = len(chunks)
        if total > max_pages:
            raise ValueError(f"回复内容超过页数上限（{max_pages} 页）")
        result = [_page_label(index, total) + chunk for index, chunk in enumerate(chunks, 1)]
    if max_total_bytes is not None and sum(_byte_length(page) for page in result) > max_total_bytes:
        raise ValueError(
            f"分页后的回复超过总字节上限（{max_total_bytes} bytes）"
        )
    return result
