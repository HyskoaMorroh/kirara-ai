"""OneBot must deliver code as its own message so it can actually be copied."""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.im.text_render import CODE_COPY_HINT
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter
from kirara_ai.plugins.im_onebot_adapter.config import OneBotConfig


def make_adapter(**overrides) -> OneBotAdapter:
    container = DependencyContainer()
    container.register(OneBotConfig, OneBotConfig(**overrides))
    return Inject(container).create(OneBotAdapter)()


def message(text: str) -> IMMessage:
    return IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[TextMessage(text)],
    )


def texts(batches) -> list[str]:
    return [
        "".join(segment.data.get("text", "") for segment in batch) for batch in batches
    ]


@pytest.mark.asyncio
async def test_code_is_sent_as_its_own_message_followed_by_a_copy_hint():
    adapter = make_adapter()

    batches = await adapter._render_message_batches(
        message("说明文字\n\n```python\nprint(1)\n```\n\n后续说明")
    )
    sent = texts(batches)

    assert "说明文字" in sent[0]
    # The code message must contain the code and nothing else.
    assert sent[1].strip().startswith("```python")
    assert "说明文字" not in sent[1]
    assert sent[2] == CODE_COPY_HINT
    assert "后续说明" in sent[3]


@pytest.mark.asyncio
async def test_the_copy_hint_is_not_emitted_for_a_reply_without_code():
    adapter = make_adapter()

    sent = texts(await adapter._render_message_batches(message("只有普通文字。")))

    assert CODE_COPY_HINT not in "".join(sent)


@pytest.mark.asyncio
async def test_disabling_isolation_restores_the_previous_mixed_layout():
    adapter = make_adapter(isolate_code_messages=False)

    sent = texts(
        await adapter._render_message_batches(message("说明\n\n```py\nprint(1)\n```"))
    )

    assert CODE_COPY_HINT not in "".join(sent)
    # Prose and code share one message again, exactly as before.
    assert any("说明" in item and "```py" in item for item in sent)


@pytest.mark.asyncio
async def test_code_indentation_survives_delivery():
    adapter = make_adapter()

    sent = texts(
        await adapter._render_message_batches(
            message("```python\ndef f():\n    return 1\n```")
        )
    )

    assert any("    return 1" in item for item in sent)


@pytest.mark.asyncio
async def test_two_code_blocks_each_get_their_own_message_and_hint():
    adapter = make_adapter()

    sent = texts(
        await adapter._render_message_batches(
            message("a\n\n```py\n1\n```\n\nb\n\n```sh\nls\n```")
        )
    )

    assert sent.count(CODE_COPY_HINT) == 2


@pytest.mark.asyncio
async def test_an_unclosed_fence_does_not_swallow_the_following_prose():
    adapter = make_adapter()

    sent = texts(
        await adapter._render_message_batches(message("说明\n\n```py\nx = 1\n未闭合"))
    )

    joined = "".join(sent)
    assert "未闭合" in joined
    assert CODE_COPY_HINT not in joined


@pytest.mark.asyncio
async def test_a_long_code_block_is_still_paginated_with_complete_fences():
    adapter = make_adapter()
    body = "\n".join(f"print({index})" for index in range(400))

    sent = texts(await adapter._render_message_batches(message(f"```python\n{body}\n```")))

    code_pages = [item for item in sent if "```python" in item]
    assert len(code_pages) > 1
    for page in code_pages:
        assert page.count("```") == 2


@pytest.mark.asyncio
async def test_media_elements_become_their_own_batches_around_paginated_text():
    """需求 19.4 点名要验证「图片/文件」在分段中的行为。

    图片、语音、视频、文件不能与文本挤进同一条消息：QQ 的一条消息里混排大段
    文字和媒体时，客户端的展示顺序不再可预测；而分页又会把文本切成多条。
    这里断言媒体各自独立成条，且文本页与媒体的**相对顺序**保持原样——
    顺序错乱会让「这张图说明的是上面那段话」这个关系失效。
    """
    from kirara_ai.im.message import ImageMessage, VoiceMessage

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    adapter = make_adapter()
    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[
            TextMessage("说明文字。" * 4),
            ImageMessage(data=png),
            TextMessage("后续说明。" * 4),
            VoiceMessage(data=b"RIFF0000WAVEfmt "),
        ],
    )

    batches = await adapter._render_message_batches(reply)

    kinds = [
        "text" if all(seg.type == "text" for seg in batch) else batch[0].type
        for batch in batches
    ]
    # 两段文本（可能各自分页）与两个媒体交替出现，媒体各自独立成条。
    assert "image" in kinds and "record" in kinds
    assert kinds.index("image") < kinds.index("record"), "媒体相对顺序被打乱"
    first_text = next(index for index, kind in enumerate(kinds) if kind == "text")
    assert first_text < kinds.index("image"), "首段文本应排在图片之前"
    for batch in batches:
        types = {segment.type for segment in batch}
        assert types == {"text"} or "text" not in types, (
            f"媒体与文本被塞进同一条消息：{types}"
        )


@pytest.mark.asyncio
async def test_a_media_only_reply_still_produces_a_batch():
    """纯媒体回复（没有任何文字）不得被当成空消息丢掉。"""
    from kirara_ai.im.message import ImageMessage

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
        b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    adapter = make_adapter()
    reply = IMMessage(
        sender=ChatSender.get_bot_sender(),
        message_elements=[ImageMessage(data=png)],
    )

    batches = await adapter._render_message_batches(reply)

    assert len(batches) == 1
    assert batches[0][0].type == "image"
