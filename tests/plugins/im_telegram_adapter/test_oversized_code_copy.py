"""超过 256 字符的代码在 Telegram 上也要能复制（需求 6(d)）。

Telegram 的 `CopyTextButton` 载荷上限是 256 字符，`copyable_button_text` 超限返回
`None`——于是那条代码消息**一个复制按钮都没有**。而 256 字符是很小的量：一个 12 行
的 Python 函数就超了。也就是说需求里「代码要统一放到代码框里旁边有直接复制键」
这一条，在真实长度的代码上于 Telegram 恰好不成立，而项目文档把它列为已实现。

## 已有的降级不够

现有降级是「不挂按钮」，理由是「挂上去会让整条 sendMessage 被平台拒绝」。那个理由
成立，但结论下得太早：Telegram 上代码本来就走 MarkdownV2 的 ``` 围栏，客户端在
代码块右上角自带一个复制图标。所以真正的问题不是「没有复制途径」，而是
**用户不知道有**——一条 300 字符的代码消息什么提示都没有，而它旁边那条 200 字符的
有一个显眼的「复制代码」按钮。两条消息看起来能力不同，实际都能复制。

## 判据

超限时给出一句指引，说明这段代码怎么复制。它不是按钮的等价物，但它把
「这条不能复制」纠正成「这条这样复制」——后者是事实，前者不是。

三条边界：

1. **短代码行为不变**：能挂按钮的仍然挂按钮，不要额外加一句话。
2. **指引不进代码消息**：那条消息整体是可复制的代码，往里加中文会污染复制结果。
3. **每个代码块只加一句**：一段被拆成多片的代码不要每片都跟一句。
"""

from __future__ import annotations

from kirara_ai.im.text_render import (
    MAX_COPY_BUTTON_TEXT_LENGTH,
    copyable_button_text,
    oversized_code_copy_hint,
)


class TestTheCapItself:
    def test_short_code_still_gets_a_button(self):
        assert copyable_button_text("print(1)") == "print(1)"

    def test_oversized_code_gets_no_button(self):
        """既有行为不变：挂上去会让整条 sendMessage 被平台拒绝。"""
        code = "x = 1\n" * 200
        assert len(code) > MAX_COPY_BUTTON_TEXT_LENGTH
        assert copyable_button_text(code) is None

    def test_the_cap_is_small_enough_to_hit_in_practice(self):
        """256 字符只够十来行代码——这不是一个边缘情况。"""
        realistic = "\n".join(
            f"    value_{index} = compute(index={index})" for index in range(12)
        )
        assert len(realistic) > MAX_COPY_BUTTON_TEXT_LENGTH


class TestTheHintFillsTheGap:
    def test_a_hint_exists_for_oversized_code(self):
        assert oversized_code_copy_hint(600) is not None

    def test_short_code_gets_no_hint(self):
        """能挂按钮时不要再补一句话：那是噪声。"""
        assert oversized_code_copy_hint(100) is None

    def test_the_boundary_matches_the_button_cap(self):
        assert oversized_code_copy_hint(MAX_COPY_BUTTON_TEXT_LENGTH) is None
        assert oversized_code_copy_hint(MAX_COPY_BUTTON_TEXT_LENGTH + 1) is not None

    def test_the_hint_says_how_to_copy(self):
        hint = oversized_code_copy_hint(600)
        assert hint is not None
        # 必须给出可执行动作，而不是「本条不支持复制」。
        assert "复制" in hint

    def test_the_hint_explains_why_there_is_no_button(self):
        """不解释的话，用户会以为是故障——旁边那条短代码明明有按钮。"""
        hint = oversized_code_copy_hint(600)
        assert hint is not None
        assert str(MAX_COPY_BUTTON_TEXT_LENGTH) in hint

    def test_the_hint_carries_no_markdown_that_would_need_escaping(self):
        """它会走 MarkdownV2；带 `_` 或 `*` 会让整条消息被平台拒。"""
        hint = oversized_code_copy_hint(600)
        assert hint is not None
        for char in "_*[]()~`>#+-=|{}.!":
            assert char not in hint, f"文案里的 {char!r} 在 MarkdownV2 下需要转义"


class TestTheTelegramAdapterUsesIt:
    def test_the_adapter_emits_the_hint(self):
        import inspect

        from kirara_ai.plugins.im_telegram_adapter import adapter as adapter_module

        source = inspect.getsource(adapter_module.TelegramAdapter._render_send_units)

        assert "oversized_code_copy_hint" in source

    def test_the_hint_is_a_separate_unit_from_the_code(self):
        """指引不能进代码消息：那条消息整体是可复制的代码。"""
        import inspect

        from kirara_ai.plugins.im_telegram_adapter import adapter as adapter_module

        source = inspect.getsource(adapter_module.TelegramAdapter._render_send_units)
        # 指引作为独立的发送单元追加，而不是拼进 chunk。
        assert "_TelegramSendUnit" in source
        hint_position = source.index("oversized_code_copy_hint")
        assert "units.append" in source[hint_position:]
