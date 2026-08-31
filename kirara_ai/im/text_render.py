"""
IM 消息文本排版工具。

集中提供「显示宽度计算 / 等宽表格渲染 / Markdown 表格预渲染」等能力，
供各 IM 适配器（企业微信、Telegram 等）共用，避免各自实现导致排版不一致。
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import (Callable, Iterable, List, Mapping, Optional, Sequence,
                    Tuple, Union)

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


#: 围栏代码块的起始行。
#:
#: CommonMark 允许两种围栏字符（`` ` `` 与 `~`）以及**三个以上**的重复：
#: 模型展示 Markdown 时会用四个反引号包住三个反引号的内层块，这是标准写法。
#: 此前全部判定都写成 ``startswith("```")``，于是：
#:
#: - ``~~~`` 围栏完全不被当成代码，里面的 ``**``、``$x$``、表格行会被当成
#:   正文改写，代码内容被静默破坏；
#: - 四反引号围栏被算成「进入」，内层三反引号又把状态翻转回「退出」，
#:   之后的正文被当成代码、代码被当成正文。
#:
#: 语言标识只在**起始**行出现，且不含围栏字符本身（CommonMark 的规定）。
_FENCE_START_PATTERN = re.compile(r"^(?P<fence>`{3,}|~{3,})[ \t]*(?P<language>[^`\n]*?)[ \t]*$")


def fence_marker(line: str) -> Optional[str]:
    """返回该行的围栏标记（例如 ``` 或 ~~~~），不是围栏行时返回 ``None``。"""
    match = _FENCE_START_PATTERN.match(line.lstrip())
    return match.group("fence") if match else None


def fence_language(line: str) -> Optional[str]:
    """返回围栏起始行声明的语言，未声明时返回 ``None``。"""
    match = _FENCE_START_PATTERN.match(line.lstrip())
    if match is None:
        return None
    return match.group("language") or None


def closes_fence(line: str, opening: str) -> bool:
    """判断该行是否闭合 ``opening`` 打开的围栏。

    CommonMark 要求闭合围栏与起始围栏**同字符**且**不短于**它，并且不带
    语言标识。这条规则正是嵌套围栏能成立的原因：四反引号打开的块里，
    三反引号只是内容。
    """
    match = _FENCE_START_PATTERN.match(line.lstrip())
    if match is None:
        return False
    fence = match.group("fence")
    return (
        fence[0] == opening[0]
        and len(fence) >= len(opening)
        and not match.group("language")
    )


#: 框线表格允许的最大显示宽度（以等宽字符计）。
#:
#: 超过这个宽度的表格在 QQ 这类没有等宽字体、也不能横向滚动的客户端上
#: 会按窗口宽度随机折行：框线错位之后，读者根本分不清哪个值属于哪一列。
#:
#: 取值按**手机气泡一行的实际容量**：375pt 宽的手机上 QQ 气泡的正文区约 280pt，
#: 默认字号下一个汉字约 16pt，一行放得下 17–18 个汉字，即 35–37 显示列。
#: 取 38 留一点余量。
#:
#: 此前是 60（注释里的依据是「30 个汉字的两倍显示宽度」，而 30 个汉字偏大）。
#: 差别不是学术性的：一张 4 列中文参数表是 48 列、一张 3 列长键名配置表是 57 列，
#: 两者在 60 之下都画框线，而它们都放不进手机一行——折行之后竖线错位，
#: 而 `render_field_table` 的判据正是「错位的框线连『哪个值属于哪一列』都保证不了」。
#:
#: 还有一层无法靠计算修正的风险：U+2500–257F（制表符）的 East_Asian_Width 是
#: **Ambiguous**，西文字体里占 1 列、中日韩字体里占 2 列，而 `display_width` 按 1 计。
#: 边框行全由制表符组成、数据行是混排，因此在把 Ambiguous 当全角的客户端上
#: 两者膨胀幅度不同（实测边框行 48→96、数据行 48→53），对齐彻底失效。
#: 这让「窄表才画框线」从美观取舍变成正确性要求：越窄，出问题的概率越低。
MAX_TABLE_DISPLAY_WIDTH = 38


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

    围栏代码块（``` 或 ~~~，允许三个以上）内的内容原样保留，
    不会被误当成表格处理。

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
    # 当前打开的围栏标记；``None`` 表示不在围栏内。记标记而不是布尔值，
    # 是因为闭合围栏必须与起始围栏同字符且不更短——四反引号块里的
    # 三反引号是**内容**，不能把状态翻回来。
    open_fence: Optional[str] = None

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
        if open_fence is None:
            marker = fence_marker(line)
            if marker is not None:
                flush()
                open_fence = marker
                result.append(line)
                continue
        else:
            result.append(line)
            if closes_fence(line, open_fence):
                open_fence = None
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


def code_copy_hint(message_count: int) -> str:
    """代码块的复制指引；跨多条消息时一并说明它被拆成了几条。

    代码消息本身**不能**带页码：长按复制会把页码一起复制走，粘进编辑器就是坏
    代码，而代码单独成条的全部目的正是让它可以整段复制。但一段被拆成 5 条的代码
    同样要回答「我收齐了吗」，因此把条数放进紧随其后的这句指引里——它不参与复制。
    """
    if message_count <= 1:
        return CODE_COPY_HINT
    return f"{CODE_COPY_HINT}（这段代码共 {message_count} 条，请按顺序拼接）"


#: 平台原生「复制」按钮能携带的最大文本长度。
#:
#: Telegram Bot API 的 ``CopyTextButton.text`` 上限是 256 字符；超过时整条
#: sendMessage 会被拒绝。也就是说「顺手加个按钮」会把一条本来能发出去的回复
#: 变成发不出去——那是负向调整。超限时退回没有按钮：代码正文照常送达，
#: 用户仍可手动选中，只是少了一个便利。
MAX_COPY_BUTTON_TEXT_LENGTH = 256


def copyable_button_text(code: Optional[str]) -> Optional[str]:
    """判断一段代码能否作为原生复制按钮的载荷，能则返回它本身。

    返回 ``None`` 表示「这一段不挂按钮」，三种情况：没有代码、代码是空白、
    代码超过平台上限。三者都不该退化成「挂一个空按钮」或「挂一个会让整条消息
    被拒的按钮」——前者点了没反应，后者让整条回复发不出去。

    调用方必须传**代码原文**，不是渲染/转义之后的文本：转义产生的反斜杠会被
    一起复制走，粘进编辑器就是坏代码。
    """
    if not code:
        return None
    if not code.strip():
        return None
    if len(code) > MAX_COPY_BUTTON_TEXT_LENGTH:
        return None
    return code


def oversized_code_copy_hint(code_length: int) -> Optional[str]:
    """超过按钮载荷上限的代码该怎么复制；未超限返回 ``None``。

    256 字符只够十来行代码，因此「超限」是常态而不是边缘情况。此前超限的处理是
    **什么都不做**：那条代码消息一个按钮也没有，而它旁边一条更短的代码有一个显眼的
    「复制代码」。两条消息看起来能力不同，实际都能复制——Telegram 客户端在
    Markdown 代码块右上角自带复制图标。缺的不是复制途径，是**用户不知道有**。

    因此给一句指引。它不是按钮的等价物，但它把「这条不能复制」纠正成
    「这条这样复制」——后者是事实，前者不是。

    文案刻意不含任何 MarkdownV2 需要转义的字符：这一句会与正文走同一个
    ``parse_mode``，一个未转义的 ``_`` 会让整条消息被平台拒收，
    于是一句「提示」把一条本来能发出去的回复变成发不出去。
    """
    if code_length <= MAX_COPY_BUTTON_TEXT_LENGTH:
        return None
    return (
        f"这段代码有 {code_length} 个字符，超过按钮可携带的 "
        f"{MAX_COPY_BUTTON_TEXT_LENGTH} 字符上限；"
        "请长按上方代码块，或点它右上角的复制图标整段复制"
    )


@dataclass(frozen=True)
class CopyablePart:
    """一段待发送内容；``is_code`` 为真时整条消息只有代码。"""

    text: str
    is_code: bool = False
    code: Optional[str] = None
    language: Optional[str] = None


#: 三反引号围栏的起始/闭合行。保留为兼容入口：围栏识别已收敛到
#: :func:`fence_marker` / :func:`closes_fence`（支持 ``~~~`` 与四个以上反引号），
#: 这两个常量仍被外部插件按旧路径导入。
_FENCE_OPEN_PATTERN = re.compile(r"^\s*```([\w+#.-]*)\s*$")
_FENCE_CLOSE_PATTERN = re.compile(r"^\s*```\s*$")


def split_for_copyable_code(text: str) -> List[CopyablePart]:
    """把正文与围栏代码块拆成可分别发送的片段。

    代码块单独成条，正文合并为相邻片段。未闭合的围栏不当作代码——
    截断的回复不能把后续正文一起吞进代码块里。空代码块直接丢弃，
    避免发出一条只有围栏的空消息。

    围栏按 CommonMark 识别（``` 或 ~~~，三个以上），闭合围栏必须同字符
    且不更短：四反引号块里的三反引号属于代码内容，整块必须成为**一条**
    可复制的代码消息，而不是被内层围栏切成三段。
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
        opening = fence_marker(lines[index])
        if opening is None:
            prose.append(lines[index])
            index += 1
            continue

        closing_index = next(
            (
                cursor
                for cursor in range(index + 1, len(lines))
                if closes_fence(lines[cursor], opening)
            ),
            None,
        )
        if closing_index is None:
            # 未闭合：按普通文本处理，保留原样，不吞掉后面的内容。
            prose.extend(lines[index:])
            break

        flush_prose()
        language = fence_language(lines[index])
        code = "\n".join(lines[index + 1 : closing_index])
        if code.strip():
            fence = f"{opening}{language}" if language else opening
            parts.append(
                CopyablePart(
                    text=f"{fence}\n{code}\n{opening}",
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
    #: 代码块的围栏在源文本里有没有闭合。
    #:
    #: 只对 ``CODE`` 有意义。解析器把未闭合的围栏也收成一个代码块（否则后面的内容
    #: 会散成正文），但**渲染时不能替它补上闭合**：下游的
    #: ``split_for_copyable_code`` 按围栏配对判断「这一段是不是可复制的代码」，
    #: 补一个闭合会把一条被截断的回复里剩下的正文变成「代码」，
    #: 然后跟上一句「长按可整段复制」——而那不是代码。
    #:
    #: 默认 ``True``：既有构造点全部是闭合围栏，加这个字段不改变它们。
    closed: bool = True
    #: 这个代码块在源文本里**本来有没有围栏**。
    #:
    #: 只对 ``CODE`` 有意义。模型经常直接把代码贴在正文里（现场报障那段 QQ 回复
    #: 就是一百行无围栏 Python），解析器把它也收成代码块，但渲染层需要知道差别：
    #: 无围栏的块要**补**上围栏，才能进入「代码单独成条 + 可复制」的路径；
    #: 而原本带围栏的块只是原样保留。
    #:
    #: 默认 ``True``：既有构造点都来自真实围栏，加这个字段不改变它们。
    fenced: bool = True


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


#: 一行在「没有围栏的代码」探测里的角色。
#:
#: ``code`` 是单独就足以断言是代码的行；``neutral`` 是代码里常见但单独不足以
#: 断言的行（注释、缩进续行、只有收尾符号的行、空行）；``prose`` 是自然语言。
_LINE_CODE = "code"
_LINE_NEUTRAL = "neutral"
_LINE_PROSE = "prose"

#: CJK 与全角标点。**一个 ``code`` 行不允许含 CJK**——这一条挡掉了整类误判：
#: 中文技术散文里随处可见 `T = 100`、`Geman & Geman (1984)` 这种形状，
#: 若不设这条限制，一段说明文字会被整段包进代码框并挂上「长按可整段复制」。
#: 代码里的中文注释与中文字符串仍然成立：它们落到 ``neutral``，
#: 由已经成立的代码run 吸收，而不是自己去开一个 run。
_CJK_PATTERN = re.compile(
    r"[　-〿぀-ヿ㐀-䶿一-鿿"
    r"豈-﫿＀-￯]"
)

#: 缩进多少列起算「代码块的续行」。取 4 与 CommonMark 的缩进代码块一致。
_CODE_INDENT_COLUMNS = 4

#: 一个 run 里最多容忍几个连续空行。Python 的类内方法之间常有两个空行，
#: 取 2 才不会把一个类切成两段。
_MAX_BLANK_LINES_INSIDE_CODE = 2

#: 一个 run 至少要有几个 ``code`` 行才成立。
_MIN_CODE_LINES = 2

_CODE_DEFINITION = re.compile(
    r"^\s*(?:async\s+def|def|class|function|interface|struct|impl|fn|"
    r"(?:public|private|protected|static|final)\s+[\w<>\[\]]+)\s+[\w$]"
)
_CODE_IMPORT = re.compile(
    r"^\s*(?:import\s+\S|from\s+\S+\s+import\s|#include\s|using\s+\w|"
    r"require\s*\(|package\s+\w)"
)
_CODE_ASSIGNMENT = re.compile(
    r"^\s*(?:const |let |var |final |static )?[A-Za-z_$][\w$.]*"
    r"(?:\[[^\]\n]*\])?(?:\s*:\s*[\w\[\]., |'\"<>]+)?\s*"
    r"(?:\+|-|\*\*|\*|//|/|%|\||&|\^|>>|<<)?=\s*\S"
)
_CODE_DECORATOR = re.compile(r"^\s*@[A-Za-z_][\w.]*(?:\(.*\))?\s*$")
#: 赋值语句的**右侧**看起来像自然语言时不算代码。
#:
#: `_CODE_ASSIGNMENT` 单看形状会把英文正文里的等式句判成代码——
#: `Cost = benefit minus risk.` 三个裸词加一个句号，形状与 `x = y` 无异。
#: 判据是「右侧是两个以上纯字母词、且没有任何代码标点」：真实代码的右侧几乎总带
#: 括号、引号、数字、点号或运算符，而英文句子恰恰都没有。
_PROSE_ASSIGNMENT_TAIL = re.compile(
    r"=\s*[A-Za-z]+(?:\s+[A-Za-z]+){2,}\s*[.!?。！？]?\s*$"
)
#: 语句关键字只在**缩进之后**算代码：真实代码里的 `return` 一定在函数体内，
#: 而顶格的 `return ...` 更可能是一句在讲 return 的话。
_CODE_CONTROL = re.compile(
    r"^\s+(?:return|raise|yield|pass|break|continue|assert|del|await|throw)\b"
)
_CODE_BLOCK_KEYWORD = re.compile(
    r"^\s*(?:if|elif|else|for|while|try|except|finally|with|switch|case|do|match)"
    r"\b.*[:{]\s*$"
)
_CODE_SHELL = re.compile(
    r"^\s*(?:\$ |#!/|sudo |docker |docker-compose |npm |pnpm |yarn |pip3? |"
    r"apt |apt-get |yum |dnf |git |curl |wget |cd |ls |mkdir |cp |mv |rm |"
    r"chmod |chown |systemctl |kubectl |uv |cargo |go )\S"
)
_CODE_SQL = re.compile(
    r"^\s*(?:SELECT|INSERT INTO|UPDATE|DELETE FROM|CREATE TABLE|ALTER TABLE|"
    r"DROP TABLE|WITH)\b",
    re.IGNORECASE,
)
#: SQL 语句的**续行**关键字。只算 ``neutral``——它们不足以独立断言是代码，
#: 但必须能被已成立的 run 吸收，否则 ``SELECT ... / FROM ... / WHERE ...;``
#: 会在第二行断开，整条语句凑不够强行数而被判成正文。
#:
#: 刻意**只匹配全大写**：SQL 写在代码里惯用大写，而英文正文里不会出现独立的
#: 全大写 ``FROM`` / ``ON``（"On the other hand" 是 ``On``）。放开大小写会把
#: 以 ``On`` / ``Set`` 开头的英文句子变成可被代码段吞掉的中性行。
_CODE_SQL_CONTINUATION = re.compile(
    r"^\s*(?:FROM|WHERE|GROUP BY|ORDER BY|HAVING|LIMIT|OFFSET|VALUES|SET|"
    r"(?:INNER |LEFT |RIGHT |FULL |CROSS )?JOIN|ON|AND|OR|UNION(?: ALL)?)\b"
)
#: 语句形态的函数调用。标识符与左括号之间**不允许空格**：留了空格就会把
#: `Note (1984)` 这类英文正文算成调用。
_CODE_CALL = re.compile(r"^\s*[A-Za-z_$][\w$.]*\([^\n]*\)\s*;?\s*$")
_CODE_TERMINATED = re.compile(r"[;{}]\s*$")
_CODE_COMMENT = re.compile(r"^\s*(?:#|//|/\*|\*/|\*\s)")
_CODE_PUNCT_ONLY = re.compile(r"^\s*[\)\]\}>,;:]+\s*$")

_CODE_STRONG_PATTERNS = (
    _CODE_DEFINITION,
    _CODE_IMPORT,
    _CODE_DECORATOR,
    _CODE_SHELL,
    _CODE_SQL,
    _CODE_BLOCK_KEYWORD,
    _CODE_CALL,
    _CODE_ASSIGNMENT,
    _CODE_CONTROL,
)


def _leading_indent(line: str) -> int:
    """该行的缩进列数；制表符按 4 列算。"""
    columns = 0
    for character in line:
        if character == " ":
            columns += 1
        elif character == "\t":
            columns += _CODE_INDENT_COLUMNS
        else:
            break
    return columns


def _code_line_role(line: str) -> str:
    """判定一行在代码探测里的角色。"""
    stripped = line.strip()
    if not stripped:
        return _LINE_NEUTRAL
    if _CJK_PATTERN.search(line):
        # 含中文的行只能被已成立的 run 吸收，不能自己作为断言依据。
        if _CODE_COMMENT.match(line) or _leading_indent(line) >= _CODE_INDENT_COLUMNS:
            return _LINE_NEUTRAL
        return _LINE_PROSE
    for pattern in _CODE_STRONG_PATTERNS:
        if pattern.match(line):
            if pattern is _CODE_ASSIGNMENT and _PROSE_ASSIGNMENT_TAIL.search(stripped):
                # `Cost = benefit minus risk.` 是一句话，不是一条赋值。
                return _LINE_PROSE
            return _LINE_CODE
    if _CODE_TERMINATED.search(stripped):
        return _LINE_CODE
    if _CODE_COMMENT.match(line) or _CODE_PUNCT_ONLY.match(stripped):
        return _LINE_NEUTRAL
    if _CODE_SQL_CONTINUATION.match(line):
        return _LINE_NEUTRAL
    if _leading_indent(line) >= _CODE_INDENT_COLUMNS:
        return _LINE_NEUTRAL
    return _LINE_PROSE


def _guess_code_language(body: str) -> Optional[str]:
    """猜出代码块的语言标识。

    19.3 要求「明确的语言标识」。猜不出时返回 ``None``——写一个错的语言标识
    比不写更糟：Telegram 会按它高亮，标错的高亮让读者以为代码有语法错误。
    """
    if re.search(r"(?:=>|\bconsole\.log\b|\b(?:const|let)\s+\w+\s*=)", body):
        return "javascript"
    if re.search(
        r"^\s*(?:from\s+\S+\s+import|import\s+\w+|def\s+\w+\s*\(|"
        r"class\s+\w+\s*[:\(]|@\w+\s*$)",
        body,
        re.MULTILINE,
    ) or "self." in body:
        return "python"
    if re.search(
        r"^\s*(?:\$ |#!/bin/(?:ba)?sh|docker |npm |pip3? |git |sudo |apt |yum )",
        body,
        re.MULTILINE,
    ):
        return "bash"
    if re.search(
        r"^\s*(?:SELECT|INSERT INTO|CREATE TABLE)\b", body, re.IGNORECASE | re.MULTILINE
    ):
        return "sql"
    return None


def _fenced_line_indices(lines: Sequence[str]) -> set[int]:
    """围栏代码块占据的行下标（含起始与闭合行）。

    :func:`_detect_code_spans` 必须跳过它们：围栏里的内容已经是代码，
    在里面再认一段「无围栏代码」会让 :func:`fence_unfenced_code` 往一个
    已有围栏里塞第二层围栏，内层的三反引号又会把外层提前闭合。
    """
    inside: set[int] = set()
    index = 0
    while index < len(lines):
        opening = fence_marker(lines[index])
        if opening is None:
            index += 1
            continue
        inside.add(index)
        index += 1
        while index < len(lines):
            inside.add(index)
            if closes_fence(lines[index], opening):
                index += 1
                break
            index += 1
    return inside


def _detect_code_spans(lines: Sequence[str]) -> dict[int, int]:
    """找出没有围栏的代码段，返回 ``{起始行下标: 结束行下标(不含)}``。

    模型在对话里贴代码时**经常不加围栏**——现场报障那段 QQ 回复里整整一百行
    Python 一个反引号都没有。此前这些行走的是正文规则，后果不是「少了个代码框」
    而是代码被改坏：顶格的 `# ---- TSP 应用示例 ----` 被 ATX 标题规则吃成
    `■ ---- TSP 应用示例 ----`，`_private_` 掉下划线，`` `SELECT 1` `` 变成
    `「SELECT 1」`，`mask = a | b | c` 被画成框线表格。19.3 要求「代码必须保持
    原始缩进和换行、有明确的语言标识和代码边界」，这些都不成立。

    判据刻意保守，因为反向误判更严重：把一段中文说明当成代码，会给用户一段
    带围栏的说明文字，还会把它送进「长按可整段复制」的路径。因此一个 run 必须
    **以一个不含 CJK 的强代码行开头**，包含至少 :data:`_MIN_CODE_LINES` 个强行,
    且中途不能出现任何自然语言行。中文注释与中文字符串落在 ``neutral``，
    由已经成立的 run 吸收——它们从不自己开一个 run。
    """
    fenced = _fenced_line_indices(lines)
    roles = [
        _LINE_PROSE if index in fenced else _code_line_role(line)
        for index, line in enumerate(lines)
    ]
    spans: dict[int, int] = {}
    index = 0
    while index < len(lines):
        if roles[index] is not _LINE_CODE:
            index += 1
            continue
        strong = 0
        definition_seen = False
        indented_body = 0
        last_content = index
        cursor = index
        blank_run = 0
        while cursor < len(lines):
            role = roles[cursor]
            if role is _LINE_PROSE:
                break
            if not lines[cursor].strip():
                blank_run += 1
                if blank_run > _MAX_BLANK_LINES_INSIDE_CODE:
                    break
                cursor += 1
                continue
            blank_run = 0
            last_content = cursor
            if role is _LINE_CODE:
                strong += 1
                if _CODE_DEFINITION.match(lines[cursor]) or _CODE_IMPORT.match(
                    lines[cursor]
                ):
                    definition_seen = True
            elif _leading_indent(lines[cursor]) >= _CODE_INDENT_COLUMNS:
                indented_body += 1
            cursor += 1
        # 一个 `def`/`import` 加上缩进的（中文）函数体同样是代码，哪怕强行只有一个。
        accepted = strong >= _MIN_CODE_LINES or (
            strong >= 1 and definition_seen and indented_body >= 1
        )
        if accepted and last_content > index:
            spans[index] = last_content + 1
            index = last_content + 1
            continue
        index += 1
    return spans


def fence_unfenced_code(text: str) -> str:
    """给正文里没有围栏的代码段补上 Markdown 围栏。

    :func:`parse_text_document` 已经能把这些段落识别成 ``CODE`` 块，走块渲染的
    平台（QQ、企业微信）因此自动受益。Telegram 不走块渲染——它的管线是
    ``markdownify(convert_markdown_tables(degrade_math(text)))``，把源文本整体交给
    MarkdownV2 转换器。于是同一段无围栏 Python 在 Telegram 上后果更重：
    markdownify 会把每一行的前导空格当成排版空白吃掉，一段有缩进的函数体变成
    全部顶格的一堆行——代码的语义（缩进即块结构）直接消失，而 QQ 侧至少还留着缩进。

    19.1 要求「平台差异只放在渲染/发送层」。把「哪些行是代码」这个**判断**
    收在这里，三个平台就共用同一个答案；各平台再按自己的符号表渲染。
    补出的围栏同时让 :func:`split_for_copyable_code` 能把这段代码切成一条
    可复制的消息——19.3 要求的复制路径正是靠围栏配对识别的。
    """
    lines = text.splitlines()
    spans = _detect_code_spans(lines)
    if not spans:
        return text
    output: list[str] = []
    index = 0
    while index < len(lines):
        end = spans.get(index)
        if end is None:
            output.append(lines[index])
            index += 1
            continue
        body = [line for line in lines[index:end]]
        while body and not body[-1].strip():
            body.pop()
        language = _guess_code_language("\n".join(body))
        # 围栏前后各留一个空行：紧贴正文时 CommonMark 仍然识别，但 markdownify
        # 之类的转换器会把它当成同一段的续行。
        if output and output[-1].strip():
            output.append("")
        output.append(f"```{language}" if language else "```")
        output.extend(body)
        output.append("```")
        if end < len(lines) and lines[end : end + 1] != [""]:
            output.append("")
        index = end
    return "\n".join(output)


def parse_text_document(text: str) -> TextDocument:
    """Parse the Markdown structures that need consistent IM rendering."""
    lines = text.splitlines()
    blocks: list[TextBlock] = []
    index = 0
    # 没有围栏的代码段先探测出来。必须在进入逐行分派**之前**做：一旦让
    # `# 注释` 走到标题分支、`a | b | c` 走到表格分支，代码就已经被改写了。
    code_spans = _detect_code_spans(lines)

    def starts_block(line: str) -> bool:
        stripped = line.strip()
        return bool(
            fence_marker(line) is not None
            or _HEADING_PATTERN.match(line)
            or _LIST_PATTERN.match(line)
            or line.lstrip().startswith(">")
            or is_table_row(stripped)
        )

    def opens_block(position: int) -> bool:
        """该行是否开启一个新块——含无围栏代码段的起始行。

        段落累积必须认得这个边界：`_detect_code_spans` 找到的起始行在
        ``starts_block`` 眼里只是普通一行（`import numpy as np` 既不是标题
        也不是列表），于是前一个段落会把它吞进去，整段代码再也走不到代码分支。
        """
        return position in code_spans or starts_block(lines[position])

    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue

        # 无围栏代码段优先：它整段是代码，里面任何一行都不该再被正文规则解释。
        end = code_spans.get(index)
        if end is not None:
            body = "\n".join(lines[index:end]).rstrip()
            blocks.append(
                TextBlock(
                    TextBlockKind.CODE,
                    body,
                    language=_guess_code_language(body),
                    closed=True,
                    fenced=False,
                )
            )
            index = end
            continue

        opening = fence_marker(line)
        if opening is not None:
            language = fence_language(line)
            index += 1
            body: list[str] = []
            while index < len(lines) and not closes_fence(lines[index], opening):
                body.append(lines[index])
                index += 1
            # 走到文本末尾说明围栏没有闭合。记下来：渲染层不能替它补一个闭合，
            # 否则一条被截断的回复里剩下的正文会变成「代码」并跟上一句
            # 「长按可整段复制」。
            closed = index < len(lines)
            if closed:
                index += 1
            blocks.append(
                TextBlock(
                    TextBlockKind.CODE,
                    "\n".join(body),
                    language=language,
                    closed=closed,
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
        while index < len(lines) and lines[index].strip() and not opens_block(index):
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

#: 分式的三种写法。`\dfrac` / `\tfrac` 只是 `\frac` 的显示尺寸变体，语义相同。
#:
#: 少了后两个，同一段公式里写 `\frac` 得到 `(a)/(b)`、写 `\dfrac` 得到
#: `dfracab`——处理结果取决于作者选了哪个同义写法，这本身就是缺陷。
_FRACTION_PATTERN = re.compile(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}")

#: 重音命令 → Unicode 组合记号。与已处理的 `\overline` 属于同一类：
#: 缺了处理就只剩命令名（`\hat{x}` 变成 `hatx`）。
#:
#: 组合记号跟在基字符**之后**，这是 Unicode 的规则；顺序反了渲染出来是两个字符。
_ACCENT_MARKS = {
    "overline": "\u0304",
    "bar": "\u0304",
    "hat": "\u0302",
    "tilde": "\u0303",
    "dot": "\u0307",
    "ddot": "\u0308",
    "vec": "\u20d7",
}

#: 数学环境里的列分隔符。它是排版指令，不是内容。
#:
#: `_ENVIRONMENT_PATTERN` 删掉了 `\begin{cases}` 这类环境名，但 `&` 留着，
#: 于是每一行都拖着一个孤零零的符号——现场报障贴出的
#: `1, & \Delta E \le 0 \\` 正是这个形态。只在数学片段内部替换，
#: 正文里的 `&`（`Tom & Jerry`）是内容，必须原样保留。
#:
#: 两侧只吃**空格与制表符**，不吃换行：`\\` 已经被换成换行，吃掉它会把
#: 多行的 cases / matrix 压成一行，行与行的边界就此消失。
_ALIGNMENT_SEPARATOR_PATTERN = re.compile(r"[ \t]*(?<!\\)&[ \t]*")

#: 一段 `$...$` 带 LaTeX 特征时一定是公式。
#:
#: 旧规则只看 `$` 是否配对，于是 "price $5 and $7" 里两个货币符号恰好配对，
#: 中间内容被当作公式剥离——金额的单位被静默删掉，只剩数字。
#: 反斜杠命令、上下标、分式出现即可确认是公式。
_MATH_EVIDENCE_PATTERN = re.compile(r"\\[A-Za-z]|\\\\|[\^_]|\{|\}")

#: 一段 `$...$` **必须**是金额而不是公式的形态。
#:
#: 只按 LaTeX 特征收纳是不够的：模型高频输出 `$x = 5$`、`$a + b = c$` 这类
#: 纯符号公式，它们不带任何反斜杠命令，于是原样带着 `$` 送到 QQ——正是 19.2
#: 点名禁止的「成片的 `$...$`」。判据因此反过来：**先确认不是金额**，
#: 再按公式处理。
#:
#: 判据落在**开定界符的下一个字符**上：`$` 紧跟数字时它是货币符号，
#: 于是 "price $5 and $7 total" 里那两个 `$` 只是恰好配对的两个货币符号，
#: 中间的 " and " 不是公式内容。公式几乎总以变量、括号或运算符开头
#: （`$x = 5$`、`$(a+b)^2$`），而不是以金额数字开头。
_CURRENCY_LEAD_PATTERN = re.compile(r"^\d+(?:,\d{3})*(?:\.\d+)?")

#: 以数字开头但仍然是公式的形态。
#:
#: `$2x + 1$`、`$3 = a$` 这类确实以金额数字开头，但出现了运算符或
#: 紧贴的系数写法（`2x`），金额不会长这样。
#:
#: 「数字 + 空格 + 字母」**不算**系数：`$5 and $7` 里被捕获的内容是 `5 and `，
#: 那是两个货币符号之间的正文，不是公式。系数写法一定紧贴（`2x` 而非 `2 x`）。
_NUMERIC_MATH_PATTERN = re.compile(r"[=<>+/^~]|\d[A-Za-z]")

#: 一段 `$...$` 里出现即说明它是**正文**而不是公式的标点。
#:
#: 公式不会包含中文句读。它们出现在被捕获的内容里，只能说明那两个 `$` 恰好
#: 配成了一对，而中间是一句话——正文里出现一个落单的 `$`（金额、货币符号、
#: 或者模型漏掉一个定界符）就是这个形态。此时把中间当公式剥掉，会把一整句话
#: 的标点连同定界符一起搅乱，随后每个真公式的配对都错位一格。
#:
#: 只在**没有** LaTeX 特征时才参考它：`$$P(\text{接受}) = 1$$` 这类公式带着
#: 中文却确实是公式，而它一定带反斜杠命令。
_PROSE_PUNCTUATION_PATTERN = re.compile(r"[。，；！？、]")

_MATH_PATTERN = re.compile(r"(?<!\\)(\${1,2})(.+?)(?<!\\)\1", re.DOTALL)
_PAREN_MATH_PATTERN = re.compile(r"\\\((.+?)\\\)", re.DOTALL)
_BRACKET_MATH_PATTERN = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)


def _looks_like_currency(expression: str) -> bool:
    """Whether a `$...$` span opens with a currency amount rather than a formula."""
    stripped = expression.lstrip()
    if not _CURRENCY_LEAD_PATTERN.match(stripped):
        return False
    # 以数字开头，但带运算符或系数写法时仍是公式。
    return not _NUMERIC_MATH_PATTERN.search(stripped)


def _looks_like_math(expression: str) -> bool:
    """Whether a `$...$` span carries a formula rather than currency amounts.

    带 LaTeX 特征的一律是公式；否则只要开头不是金额、且中间不是一句话，
    就按公式处理——`$x = 5$`、`$a + b = c$` 这类纯符号公式必须剥掉定界符，
    而 `$5 and $7` 这类恰好配对的货币符号必须原样保留。
    """
    if _MATH_EVIDENCE_PATTERN.search(expression):
        return True
    if not expression.strip():
        return False
    if _PROSE_PUNCTUATION_PATTERN.search(expression):
        # 中文句读只出现在正文里。捕获到它说明这两个 `$` 是一个落单的定界符
        # （金额、或模型漏写的一个 `$`）与后面某个真公式的开定界符凑成的对，
        # 中间夹着一整句话。按公式处理会把那句话的定界符搅乱，并让后续每个
        # 公式的配对都错位一格——现场那段回复的后半段就是这样全是 `$` 残片。
        return False
    return not _looks_like_currency(expression)



def _clean_math_expression(text: str) -> str:
    # 嵌套分式要反复求解，否则 \frac{\frac{a}{b}}{c} 只有内层被处理，
    # 外层残留成 frac(...)。内层没有花括号时正则不再匹配，循环必然终止。
    #
    # `\dfrac` / `\tfrac` 只是 `\frac` 的显示尺寸变体，语义完全相同。少了它们，
    # 同一段公式里写 `\frac` 得到 `(a)/(b)`、写 `\dfrac` 得到 `dfracab`——
    # 处理结果不该取决于作者选了哪个同义写法。
    for _ in range(4):
        replaced = _FRACTION_PATTERN.sub(r"(\1)/(\2)", text)
        if replaced == text:
            break
        text = replaced
    text = re.sub(r"\\sqrt\{([^{}]*)\}", r"√(\1)", text)
    # 重音符号：与已处理的 `\overline` 属于同一类，缺了就只剩命令名。
    for command, mark in _ACCENT_MARKS.items():
        text = re.sub(
            rf"\\{command}\{{([^{{}}]*)\}}", lambda match, mark=mark: match.group(1) + mark, text
        )
    text = _BRACED_PATTERN.sub(r"\1", text)
    text = _ENVIRONMENT_PATTERN.sub(" ", text)
    text = _LINE_BREAK_PATTERN.sub("\n", text)
    # 列分隔符是排版指令而不是内容。环境名已经被删掉了，留着 `&` 会让每一行
    # 都拖着一个孤零零的符号——现场报障里 `\begin{cases}` 那段正是这个形态。
    text = _ALIGNMENT_SEPARATOR_PATTERN.sub(" ", text)
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


def _substitute_dollar_math(text: str) -> str:
    """Replace every `$...$` span that really is a formula, leaving prose alone.

    不能用 ``re.sub``：``$`` 的配对是从左到右贪心的，而正文里完全可能出现一个
    **落单**的 ``$``（金额、货币符号，或者模型漏写了一个定界符）。它会与后面第一个
    真公式的开定界符配成一对；``re.sub`` 即使原样返回这一段，扫描位置也已经越过
    了那个闭定界符，于是从这里开始每个公式的配对都错位一格——现场那段回复的
    后半段就是这样全是 ``$`` 残片。

    因此拒绝一段时只跳过**开**定界符，让闭定界符有机会与后面的内容重新配对。
    """
    pieces: list[str] = []
    position = 0
    while True:
        match = _MATH_PATTERN.search(text, position)
        if match is None:
            pieces.append(text[position:])
            return "".join(pieces)
        expression = match.group(2)
        if _looks_like_math(expression):
            pieces.append(text[position : match.start()])
            pieces.append(_clean_math_expression(expression))
            position = match.end()
            continue
        # 这一对不是公式：连定界符一起原样保留，但只前进到开定界符之后。
        resume = match.start() + len(match.group(1))
        pieces.append(text[position:resume])
        position = resume


def _clean_latex(text: str) -> str:
    def _math_span(match: "re.Match[str]", group: int) -> str:
        expression = match.group(group)
        if not _looks_like_math(expression):
            return match.group(0)
        return _clean_math_expression(expression)

    # `$...$` 单独处理：它的定界符与正文共用一个字符，配对可能失败并需要重试。
    # `\(...\)` 与 `\[...\]` 的定界符不会出现在正文里，没有这个问题。
    text = _substitute_dollar_math(text)
    for pattern, group in (
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
    open_fence: Optional[str] = None
    for line in lines:
        if open_fence is None:
            marker = fence_marker(line)
            if marker is not None:
                if buffer:
                    yield False, "".join(buffer)
                    buffer = []
                yield True, line
                open_fence = marker
                continue
            buffer.append(line)
            continue
        # 围栏内：内容与闭合行都属于代码，一律原样让出。
        if closes_fence(line, open_fence):
            if buffer:
                yield True, "".join(buffer)
                buffer = []
            yield True, line
            open_fence = None
            continue
        buffer.append(line)
    if buffer:
        yield open_fence is not None, "".join(buffer)


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


#: 平台的行内标记符号表：`(正则, 替换)`，按顺序应用。
InlineRules = Tuple[Tuple["re.Pattern[str]", str], ...]

#: 平台的块渲染器表：块类型 → 一个把 `TextBlock` 变成文本的函数。
BlockRenderers = Mapping[TextBlockKind, Callable[[TextBlock], str]]


def render_rich_text(
    document: Union[TextDocument, str],
    *,
    inline_rules: InlineRules,
    block_renderers: BlockRenderers,
) -> str:
    """按平台符号表渲染块结构；未登记的块类型只做行内替换。

    这是 `render_plain_text` 的**块级**对应物。后者只取 `document.source`，
    于是标题、强调、列表、引用、链接的标记原样送到用户眼前——企业微信早就有一套
    符号表（`━━` / `「」` / `┃` / `•` / `『』`），而 QQ 什么都没接，
    同一段回复在两个平台上一个可读、一个满是 `##` 与 `**`。

    平台差异只体现在传进来的两张表上；解析（`parse_text_document`）与结构处理
    （表格降级、数学降级）由共享实现完成。项目的约定是「不允许各平台各写一套
    Markdown 解析」，而这个函数是那条约定在块级上的落点。

    块之间留一个空行：表格与代码块因此自然带上前后空行。三个以上连续换行压成两个,
    否则源文本里的空行会在渲染后叠加。
    """
    if isinstance(document, str):
        document = parse_text_document(document)

    def _inline(text: str) -> str:
        rendered = degrade_math(text)
        for pattern, replacement in inline_rules:
            rendered = pattern.sub(replacement, rendered)
        return rendered

    rendered_blocks: list[str] = []
    for block in document.blocks:
        renderer = block_renderers.get(block.kind)
        rendered_blocks.append(renderer(block) if renderer else _inline(block.text))
    joined = "\n\n".join(part for part in rendered_blocks if part.strip())
    return re.sub(r"\n{3,}", "\n\n", joined).strip()


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


def _code_fence_open(line: str, code_style: str) -> Optional[str]:
    """返回该行打开的代码围栏标记，不是起始行时返回 ``None``。

    Markdown 侧返回真实的围栏串（``` 或 ~~~~ 等），因为闭合围栏必须同字符
    且不更短——分页时把四反引号块的内层三反引号当成闭合，会切出两个都不
    成立的残块。WeCom 侧只有一种标记，返回哨兵值即可。
    """
    if code_style == "markdown":
        return fence_marker(line)
    if code_style == "wecom":
        return "［代码" if line.rstrip("\r\n").startswith("［代码") else None
    raise ValueError(f"不支持的代码围栏样式：{code_style}")


def _code_fence_closes(line: str, opening: str, code_style: str) -> bool:
    """判断该行是否闭合 ``opening`` 打开的代码围栏。"""
    if code_style == "markdown":
        return closes_fence(line, opening)
    if code_style == "wecom":
        return line.rstrip("\r\n") == "［/代码］"
    raise ValueError(f"不支持的代码围栏样式：{code_style}")


def _code_line_kind(line: str, code_style: str) -> tuple[bool, bool]:
    """该行是否是代码围栏的起始行 / 闭合行。

    保留为兼容入口：成对判断已收敛到 :func:`_code_fence_open` 与
    :func:`_code_fence_closes`，因为「能否闭合」取决于起始围栏，
    单看一行无法回答。
    """
    opening = _code_fence_open(line, code_style)
    if code_style == "markdown":
        # Markdown 围栏行既可能是起始也可能是闭合，取决于上下文。
        return opening is not None, opening is not None
    return opening is not None, _code_fence_closes(line, "［代码", code_style)


def _split_code_block(
    block: str,
    limit: int,
    measure: Measure,
    code_style: str,
) -> list[str]:
    lines = block.splitlines(keepends=True)
    if len(lines) < 2:
        return _split_text_preserving(block, limit, measure)
    opening = _code_fence_open(lines[0], code_style)
    if opening is None or not _code_fence_closes(lines[-1], opening, code_style):
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


def _backfill_chunks(chunks: list[str], limit: int, measure: Measure) -> list[str]:
    """把相邻的、合起来还装得下的小块并进同一页。

    `_split_structured_body` 在每个代码围栏与每个框线表处 flush，然后把那个块的
    分页结果直接 extend 进结果列表——**从不回填**。于是一段「标题 + 正文 + 代码 +
    表格」× N 的技术回答，每个块各占一页而每页只有几十字节。实测 5.8 KB 的回复
    变成 40 条消息（页利用率 2.5%），8.7 KB 的直接撞满 100 页上限被截断——
    而它离「单页上限 × 页数上限」差两个数量级。

    19.4 要求「按平台安全长度拆分」与「全部发送、内容不得丢失」，上面两个后果
    正好各违反一条：每页 96 字节不是「安全长度」的意思，而截断就是丢内容。

    块边界因此从**强制**切点降为**优先**切点：装得下就合并，装不下才切。
    合并的是「已经完整的块」（围栏成对、表格带边框），不是把两个块的内部拼在一起,
    所以结构完整性不受影响；顺序也不动——只合并相邻项，不重排。
    """
    if len(chunks) <= 1:
        return chunks
    merged: list[str] = []
    for chunk in chunks:
        if not merged:
            merged.append(chunk)
            continue
        candidate = f"{merged[-1]}\n\n{chunk}"
        if measure(candidate) <= limit:
            merged[-1] = candidate
            continue
        merged.append(chunk)
    return merged


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
        opening = _code_fence_open(line, code_style)
        if opening is not None:
            flush_regular()
            block = [line]
            index += 1
            while index < len(lines):
                block.append(lines[index])
                closes = _code_fence_closes(lines[index], opening, code_style)
                index += 1
                if closes:
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
    if not chunks:
        return [""]
    # 块边界是优先切点而不是强制切点：相邻的小块合起来还装得下时应留在同一页，
    # 否则「块多」本身就会把一条几 KB 的回复拆成上百条消息并撞上页数上限。
    return _backfill_chunks(chunks, limit, measure)


#: 全渠道统一的页码格式。
#:
#: QQ、Telegram、WeCom 必须使用同一种写法：同一个机器人在不同 APP 上给出两套
#: 页码（例如 ``第 1 页 / 共 3 页`` 与 ``[1/3]``）会让用户以为是两个不同的服务。
#: 该正则同时供测试与调用方识别、剥离页码，避免各处再写一份字面量。
PAGE_LABEL_PATTERN = re.compile(r"^第 \d+ 页 / 共 \d+ 页\n?", re.MULTILINE)

#: 一条页码最多占多少字节。
#:
#: 页数上限是 100，所以两个数字各至多 3 位。重新编号的调用方需要预留这么多
#: 空间：按「当前这一段的页码长度」预留会在总页数进位时（`共 9 页` → `共 10 页`）
#: 让某一页刚好超出平台上限，而那一页会被上游拒收。
MAX_PAGE_LABEL_BYTES = len("第 100 页 / 共 100 页\n".encode("utf-8"))


def page_label(page: int, total: int) -> str:
    """全渠道统一的页码文本，含结尾换行。

    公开导出供需要**重新编号**的调用方使用（OneBot 把代码块拆成独立消息之后，
    页码必须跨这些消息连成一个序列）。格式只在这里定义一次：写成两份时，
    同一个机器人迟早在同一条回复里给出两种页码。
    """
    return f"第 {page} 页 / 共 {total} 页\n"


def _page_label(page: int, total: int) -> str:
    return page_label(page, total)


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
