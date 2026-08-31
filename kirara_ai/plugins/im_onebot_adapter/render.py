"""OneBot V11 text rendering backed by the shared IM text pipeline."""

from __future__ import annotations

import re
from typing import Any, List, Optional, Tuple

from kirara_ai.im.text_render import (
    TextBlock,
    TextBlockKind,
    is_table_separator,
    paginate_with_truncation_notice,
    parse_text_document,
    render_rich_text,
    render_table,
    split_structured_text,
)


DEFAULT_MAX_BYTES = 3800
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_TOTAL_BYTES = 1_000_000


#: OneBot（QQ）侧的行内标记符号表。
#:
#: QQ 的消息是纯文本，没有任何富文本渲染：`**粗体**` 原样显示成五个字符外加两个星号。
#: 此前这一层完全不存在——`render_plain_text` 只做数学与表格，标题、强调、列表、
#: 引用、链接的标记一路送到用户眼前，而企业微信早就有一整套符号表。
#: 需求 6 要求「参照 telegram、wecom 的格式让 QQ 更美观」，方向恰好是反的。
#:
#: 符号取值与企业微信刻意不完全相同：那是两个平台各自的观感取舍
#: （企业微信用 `「」`，QQ 上更常见的强调形态是书名号式的 `【】`）。
#: 「有没有渲染」才是不能不同的那一层。
_INLINE_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    # 粗体：**文本** → 【文本】。QQ 群聊里这种括号形态最接近「重点」的语感。
    (re.compile(r"\*\*(.+?)\*\*"), r"【\1】"),
    # 斜体/强调：去掉标记。QQ 没有斜体，留着星号只是噪声。
    # 负向断言防止吃掉 `2 * 3` 这类乘法与已被上一条处理过的粗体残留。
    (re.compile(r"(?<![\w*])\*(?!\s)([^*\n]+?)(?<!\s)\*(?![\w*])"), r"\1"),
    (re.compile(r"(?<![\w_])_(?!\s)([^_\n]+?)(?<!\s)_(?![\w_])"), r"\1"),
    # 删除线：~~文本~~ → 文本。QQ 无删除线，保留内容比保留标记重要。
    (re.compile(r"~~(.+?)~~"), r"\1"),
    # 行内代码：`代码` → 「代码」。用直角引号而不是保留反引号：
    # 反引号在 QQ 上就是两个可见字符，而它要表达的是「这是一个标识符」。
    (re.compile(r"`([^`\n]+)`"), r"「\1」"),
    # 链接：[文本](url) → 文本（url）。URL 必须留着——删掉等于给出一个点不开的词。
    (re.compile(r"\[([^\]]+)\]\(([^\)]+)\)"), r"\1（\2）"),
)


def _render_heading(block: TextBlock) -> str:
    """标题按层级换成分隔线写法，与企业微信同一思路、符号更轻。

    QQ 气泡比企业微信窄（见 `MAX_TABLE_DISPLAY_WIDTH` 的推导），因此不用
    `━━━ 标题 ━━━` 那种两侧长横线——它在窄气泡里会把标题挤到折行。
    """
    content = _inline(block.text)
    level = block.level or 1
    if level >= 3:
        return f"· {content}"
    if level == 2:
        return f"▎{content}"
    return f"■ {content}"


def _render_code(block: TextBlock) -> str:
    """代码块保留 Markdown 围栏原样。

    与企业微信不同：那边换成 `［代码］` 是因为它不识别围栏。QQ 侧围栏必须原样留着,
    因为「代码单独成条」这条路径靠 `split_for_copyable_code` 识别围栏来切分
    （`adapter.py` 的 `_text_pages`），渲染掉围栏会让整条复制路径失效。

    **未闭合的围栏不补闭合。** 解析器把它也收成代码块（否则后面的内容会散成正文），
    但补一个闭合会把一条被截断的回复里剩下的正文变成「代码」，随后跟上一句
    「长按可整段复制」——而那不是代码，复制走的是半句话。
    """
    fence = f"```{block.language}" if block.language else "```"
    body = block.text.rstrip()
    if not block.closed:
        return f"{fence}\n{body}"
    return f"{fence}\n{body}\n```"


def _render_table_block(block: TextBlock) -> str:
    """表格走共享渲染：宽表自动降级成纵向字段布局。"""
    rows = [list(row) for row in block.rows]
    if not rows:
        return _inline(block.text)
    # 有 `---` 分隔行才能断言第一行是表头；没有它，降级时不能拿它当字段名。
    has_header = any(is_table_separator(line) for line in block.text.split("\n"))
    return "\n".join(render_table(rows, has_header=has_header))


def _render_list(block: TextBlock) -> str:
    """无序列表项换成 `•`，保留原有层级缩进；有序列表保留原本的序号。"""
    rendered = _inline(block.text)
    return re.sub(r"^(\s*)[-*+]\s+", r"\1• ", rendered, flags=re.MULTILINE)


def _render_quote(block: TextBlock) -> str:
    """引用块换成 `┃` 前缀——`>` 在 QQ 上不表示引用，只是一个大于号。"""
    rendered = _inline(block.text)
    return re.sub(r"^\s{0,3}>\s?", "┃ ", rendered, flags=re.MULTILINE)


_BLOCK_RENDERERS: dict[TextBlockKind, Any] = {
    TextBlockKind.HEADING: _render_heading,
    TextBlockKind.CODE: _render_code,
    TextBlockKind.TABLE: _render_table_block,
    TextBlockKind.LIST: _render_list,
    TextBlockKind.QUOTE: _render_quote,
}


def _inline(text: str) -> str:
    """块渲染器内部复用的行内替换；数学降级在前，标记替换在后。

    顺序不能反：`$a_1$` 里的下划线在数学降级之前会被强调规则当成标记吃掉。
    """
    from kirara_ai.im.text_render import degrade_math

    rendered = degrade_math(text)
    for pattern, replacement in _INLINE_RULES:
        rendered = pattern.sub(replacement, rendered)
    return rendered


def render_onebot_text(text: str) -> str:
    """Render portable Markdown into OneBot-compatible plain text."""
    return render_rich_text(
        parse_text_document(text),
        inline_rules=_INLINE_RULES,
        block_renderers=_BLOCK_RENDERERS,
    )


def paginate_onebot_text(
    text: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_total_bytes: Optional[int] = DEFAULT_MAX_TOTAL_BYTES,
) -> List[str]:
    """Paginate OneBot text using UTF-8 byte accounting."""
    return split_structured_text(
        text,
        max_bytes=max_bytes,
        max_pages=max_pages,
        max_total_bytes=max_total_bytes,
    )


def paginate_onebot_text_or_truncate(
    text: str,
    max_bytes: int = DEFAULT_MAX_BYTES,
    *,
    max_pages: int = DEFAULT_MAX_PAGES,
    max_total_bytes: Optional[int] = DEFAULT_MAX_TOTAL_BYTES,
) -> Tuple[List[str], bool]:
    """Paginate, truncating with a notice rather than losing the whole reply.

    ``paginate_onebot_text`` 保持原样（抛错）供仍然需要严格上限的调用方使用；
    发送路径用这个变体：超预算时用户应当收到前 N 页 + 明确的截断提示，
    而不是什么都收不到。
    """
    return paginate_with_truncation_notice(
        text,
        max_bytes=max_bytes,
        max_pages=max_pages,
        max_total_bytes=max_total_bytes,
    )
