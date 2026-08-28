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
