"""OneBot V11 text rendering backed by the shared IM text pipeline."""

from __future__ import annotations

from typing import List, Optional, Tuple

from kirara_ai.im.text_render import (
    paginate_with_truncation_notice,
    parse_text_document,
    render_plain_text,
    split_structured_text,
)


DEFAULT_MAX_BYTES = 3800
DEFAULT_MAX_PAGES = 100
DEFAULT_MAX_TOTAL_BYTES = 1_000_000


def render_onebot_text(text: str) -> str:
    """Render portable Markdown into OneBot-compatible plain text."""
    return render_plain_text(parse_text_document(text))


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
