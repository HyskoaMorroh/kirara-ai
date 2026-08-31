"""需求 6：代码要放进代码框，旁边有直接复制键。

Telegram 是四个渠道里**平台原生支持**这件事的一个：Bot API 的
`InlineKeyboardButton` 有 `copy_text` 字段，点一下就把指定文本放进用户剪贴板，
不需要回调、不需要机器人再发一条消息。此前适配器对它零调用——
一整个可用的能力被跳过，用户只能长按选中，而 Telegram 客户端里选中一段带缩进的
代码恰恰最容易连着前后正文一起选上。

两个设计约束写进用例：

1. **复制的是代码原文**，不是渲染后的 MarkdownV2。转义后的文本里
   `\\_` `\\*` 这类反斜杠会被一起复制走，粘进编辑器就是坏代码。
2. **按钮只挂在代码块那一条消息上。** 给每条普通正文都挂一个复制键，
   会把「复制这段代码」变成一排看不出区别的按钮。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter

from .test_adapter_outbox import make_adapter, recipient


CODE_REPLY = "先这样写：\n```python\nprint('hi')\n    indented = 1\n```\n就好了。"


def _units(adapter: TelegramAdapter, text: str):
    message = IMMessage(sender=recipient(), message_elements=[TextMessage(text)])
    _, units = asyncio.get_event_loop().run_until_complete(
        adapter._render_send_units(message, recipient())
    )
    return units


@pytest.mark.asyncio
async def test_code_block_unit_carries_a_copy_button(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        _, units = await adapter._render_send_units(
            IMMessage(sender=recipient(), message_elements=[TextMessage(CODE_REPLY)]),
            recipient(),
        )

        with_button = [unit for unit in units if unit.params.get("_copy_text")]
        # 回归点：修之前没有任何单元带这个字段。
        assert len(with_button) == 1
        assert with_button[0].params["_copy_text"] == "print('hi')\n    indented = 1"
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_copy_payload_is_raw_code_not_escaped_markdown(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        code = "value = a_b * c_d"
        _, units = await adapter._render_send_units(
            IMMessage(
                sender=recipient(),
                message_elements=[TextMessage("```py\n" + code + "\n```")],
            ),
            recipient(),
        )

        copy_payloads = [
            unit.params["_copy_text"] for unit in units if unit.params.get("_copy_text")
        ]
        assert copy_payloads == [code]
        # MarkdownV2 转义会把 `_` 变成 `\_`；复制走那份等于粘进编辑器就是坏代码。
        assert "\\_" not in copy_payloads[0]
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_plain_text_units_get_no_button(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        _, units = await adapter._render_send_units(
            IMMessage(
                sender=recipient(),
                message_elements=[TextMessage("只是普通回复，没有代码。")],
            ),
            recipient(),
        )

        # 给每条正文都挂复制键，会把「复制这段代码」变成一排看不出区别的按钮。
        assert all(not unit.params.get("_copy_text") for unit in units)
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_send_payload_builds_a_real_inline_keyboard(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        await adapter._send_outbox_payload(
            {
                "_action": "text",
                "_copy_text": "x = 1",
                "chat_id": "member-1",
                "text": "```\nx = 1\n```",
                "parse_mode": "MarkdownV2",
            }
        )

        kwargs = adapter.application.bot.send_message.await_args.kwargs
        markup = kwargs["reply_markup"]
        # `_copy_text` 是本项目的内部约定字段，绝不能原样传给 Bot API——
        # 未知参数会让整条发送被拒。
        assert "_copy_text" not in kwargs
        button = markup.inline_keyboard[0][0]
        assert button.copy_text.text == "x = 1"
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_send_payload_without_copy_text_has_no_markup(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        await adapter._send_outbox_payload(
            {
                "_action": "text",
                "chat_id": "member-1",
                "text": "普通回复",
                "parse_mode": "MarkdownV2",
            }
        )

        kwargs = adapter.application.bot.send_message.await_args.kwargs
        assert "reply_markup" not in kwargs
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_copy_text_survives_json_round_trip(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        _, units = await adapter._render_send_units(
            IMMessage(
                sender=recipient(),
                message_elements=[TextMessage("```py\nprint(1)\n```")],
            ),
            recipient(),
        )
        unit = next(unit for unit in units if unit.params.get("_copy_text"))

        # outbox 把 params 存成 JSON 再取回来发送。带对象的 markup 过不了这一跳，
        # 所以约定字段必须是纯字符串——这条用例钉住那个约定。
        restored = json.loads(json.dumps(unit.params, ensure_ascii=False))
        assert restored["_copy_text"] == "print(1)"
        await adapter._send_outbox_payload(restored)
        button = adapter.application.bot.send_message.await_args.kwargs[
            "reply_markup"
        ].inline_keyboard[0][0]
        assert button.copy_text.text == "print(1)"
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_oversized_code_is_not_offered_as_a_copy_button(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        # Bot API 对 copy_text 的长度有上限（256 字符）。超过时挂上去整条消息会被拒，
        # 也就是说「加个按钮」把一条本来能发出去的回复变成发不出去——
        # 那是负向调整，宁可退回没有按钮。
        long_code = "x = 1  # " + "y" * 400
        _, units = await adapter._render_send_units(
            IMMessage(
                sender=recipient(),
                message_elements=[TextMessage("```py\n" + long_code + "\n```")],
            ),
            recipient(),
        )

        assert all(not unit.params.get("_copy_text") for unit in units)
    finally:
        database.shutdown()


@pytest.mark.asyncio
async def test_multiple_code_blocks_each_get_their_own_button(tmp_path: Path):
    adapter, database = make_adapter(tmp_path)
    try:
        _, units = await adapter._render_send_units(
            IMMessage(
                sender=recipient(),
                message_elements=[TextMessage("一\n```a\n1\n```\n二\n```b\n2\n```")],
            ),
            recipient(),
        )

        payloads = [
            unit.params["_copy_text"] for unit in units if unit.params.get("_copy_text")
        ]
        assert payloads == ["1", "2"]
    finally:
        database.shutdown()
