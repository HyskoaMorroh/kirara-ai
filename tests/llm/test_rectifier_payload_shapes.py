"""整流器必须按各家请求体形状整流，而不是只认一种形状（需求 8）。

供应商编辑页上有四个整流开关。此前 `rectify_request` 的四条规则全部按
`messages[*].content`（OpenAI / Claude 的形状）书写，而 Gemini 用
`contents[*].parts`、Ollama 的 `content` 是纯字符串加一个并列的 `images` 数组。
两家适配器连 `rectify_request` 都没有 import——**那四个开关对它们从未参与决策**。

「直接把现有函数接上去」是错的，而且比不接更糟：`rectify_thinking_budget`
不做键存在性校验就写入 `body["thinking"]` 与 `body["max_tokens"]`，
这两个是 Anthropic 的顶层字段。Gemini 对未知顶层字段直接返回 400
INVALID_ARGUMENT，真正的预算位（`generationConfig.thinkingConfig.thinkingBudget`）
反而没被改——一次「整流」把可重试的错误变成必然失败。

所以形状要显式识别，规则按形状分派：同一个开关在每家都有真实语义，
不适用的规则在那家不出现，而不是空转或写错字段。
"""

from __future__ import annotations

import pytest

from kirara_ai.llm.rectifier import (
    RECTIFIED_GEMINI_THINKING_BUDGET,
    UNSUPPORTED_IMAGE_PLACEHOLDER,
    RectifierConfig,
    detect_payload_shape,
    rectify_request,
)

IMAGE_ERROR = "This model does not support image input"
GEMINI_THINKING_UNSUPPORTED = (
    "Unable to submit request because thinking is not supported by this model"
)
GEMINI_BUDGET_ERROR = (
    "Invalid value at 'generation_config.thinking_config.thinking_budget': "
    "thinkingBudget must be between 512 and 24576"
)
OLLAMA_THINK_ERROR = '"qwen2.5:7b" does not support thinking'


def gemini_body(**overrides) -> dict:
    body = {
        "contents": [
            {
                "role": "user",
                "parts": [
                    {"text": "这张图里是什么"},
                    {"inline_data": {"mime_type": "image/png", "data": "AAAA"}},
                ],
            }
        ],
        "generationConfig": {
            "maxOutputTokens": 2048,
            "thinkingConfig": {"thinkingBudget": -1},
        },
    }
    body.update(overrides)
    return body


def ollama_body(**overrides) -> dict:
    body = {
        "model": "qwen2.5:7b",
        "messages": [
            {"role": "user", "content": "这张图里是什么", "images": ["AAAA"]},
        ],
        "think": "high",
        "options": {"temperature": 0.7, "num_predict": 2048},
    }
    body.update(overrides)
    return body


class TestShapeDetection:
    """形状识别错了，后面每一条规则都会改错字段。"""

    def test_gemini_is_recognized_by_contents(self):
        assert detect_payload_shape(gemini_body()) == "gemini"

    def test_ollama_is_recognized_by_its_options_block(self):
        """Ollama 与 OpenAI 都用 `messages`，区分点是并列的 `options`。"""
        assert detect_payload_shape(ollama_body()) == "ollama"

    def test_openai_and_claude_keep_the_original_shape_name(self):
        """既有两家不得因为新增识别而改判——它们的规则一个字都不能变。"""
        assert detect_payload_shape({"messages": [], "max_tokens": 16}) == "messages"
        assert (
            detect_payload_shape(
                {"messages": [], "thinking": {"type": "enabled", "budget_tokens": 2048}}
            )
            == "messages"
        )

    def test_an_empty_body_falls_back_to_the_messages_shape(self):
        """认不出来时按既有形状处理：那是唯一有完整规则集的一种。"""
        assert detect_payload_shape({}) == "messages"


class TestGemini:
    def test_images_become_a_visible_placeholder_in_parts(self):
        """图片降级要落在 `parts` 上，而不是找一个不存在的 `messages`。"""
        rectified, record = rectify_request(gemini_body(), IMAGE_ERROR)

        assert record is not None and record.applied
        assert rectified is not None
        parts = rectified["contents"][0]["parts"]
        assert parts[0] == {"text": "这张图里是什么"}
        # 换成可见占位而非静默删除：用户问「这张图里是什么」时，
        # 模型至少能说「我没有收到图片」，而不是对着空内容编一个答案。
        assert parts[1] == {"text": UNSUPPORTED_IMAGE_PLACEHOLDER}

    def test_camel_case_inline_data_is_also_replaced(self):
        """Gemini 两种拼法都合法，只认一种会让另一种静默漏过。"""
        body = gemini_body(
            contents=[
                {"role": "user", "parts": [{"inlineData": {"mimeType": "image/png", "data": "AA"}}]}
            ]
        )

        rectified, record = rectify_request(body, IMAGE_ERROR)

        assert record is not None and record.applied
        assert rectified is not None
        assert rectified["contents"][0]["parts"][0] == {
            "text": UNSUPPORTED_IMAGE_PLACEHOLDER
        }

    def test_a_text_only_gemini_body_reports_no_change(self):
        """无可改之处时不重试：同一个请求只会得到同一个错误。"""
        body = gemini_body(contents=[{"role": "user", "parts": [{"text": "hi"}]}])

        assert rectify_request(body, IMAGE_ERROR) == (None, None)

    def test_unsupported_thinking_removes_only_the_thinking_config(self):
        """删掉不被支持的 `thinkingConfig`，`generationConfig` 其余项保留。"""
        rectified, record = rectify_request(gemini_body(), GEMINI_THINKING_UNSUPPORTED)

        assert record is not None and record.applied
        assert rectified is not None
        generation = rectified["generationConfig"]
        assert "thinkingConfig" not in generation
        assert generation["maxOutputTokens"] == 2048

    def test_removing_thinking_never_injects_anthropic_fields(self):
        """整流不得引入上游不认识的顶层键——Gemini 对未知字段直接 400。

        这正是「照搬现有函数」会犯的错：`rectify_thinking_budget` 会写入
        `thinking` 与 `max_tokens`，把一个可重试的错误变成必然失败。
        """
        rectified, _ = rectify_request(gemini_body(), GEMINI_THINKING_UNSUPPORTED)

        assert rectified is not None
        assert "thinking" not in rectified
        assert "max_tokens" not in rectified
        assert "reasoning_effort" not in rectified

    def test_an_out_of_range_budget_is_clamped_in_place(self):
        """预算违规改的是 `thinkingConfig.thinkingBudget`，不是 Anthropic 的位置。"""
        rectified, record = rectify_request(gemini_body(), GEMINI_BUDGET_ERROR)

        assert record is not None and record.applied
        assert rectified is not None
        thinking = rectified["generationConfig"]["thinkingConfig"]
        assert thinking["thinkingBudget"] == RECTIFIED_GEMINI_THINKING_BUDGET
        assert "thinking" not in rectified

    def test_the_clamped_budget_leaves_room_for_output(self):
        """预算不能吃掉整个输出上限，否则改完仍然拿不到回复。"""
        body = gemini_body(
            generationConfig={"maxOutputTokens": 256, "thinkingConfig": {"thinkingBudget": 99999}}
        )

        rectified, _ = rectify_request(body, GEMINI_BUDGET_ERROR)

        assert rectified is not None
        generation = rectified["generationConfig"]
        assert generation["maxOutputTokens"] > generation["thinkingConfig"]["thinkingBudget"]

    def test_a_budget_error_without_any_thinking_config_yields_nothing(self):
        body = gemini_body(generationConfig={"maxOutputTokens": 2048})

        assert rectify_request(body, GEMINI_BUDGET_ERROR) == (None, None)

    def test_the_media_switch_still_gates_gemini(self):
        """子开关必须对每家都有效，否则它只是对某些供应商的开关。"""
        config = RectifierConfig(request_media_fallback=False)

        assert rectify_request(gemini_body(), IMAGE_ERROR, config) == (None, None)

    def test_the_master_switch_still_gates_gemini(self):
        config = RectifierConfig(enabled=False)

        assert rectify_request(gemini_body(), IMAGE_ERROR, config) == (None, None)

    def test_the_same_kind_is_only_applied_once_for_gemini(self):
        """每类只改一次：改完仍失败抛原始错误，不把「参数错」变成「一直在转」。"""
        first, record = rectify_request(gemini_body(), IMAGE_ERROR)
        assert first is not None and record is not None

        again = rectify_request(
            gemini_body(), IMAGE_ERROR, already_applied=frozenset({record.kind})
        )

        assert again == (None, None)

    def test_the_original_gemini_body_is_never_mutated(self):
        body = gemini_body()

        rectify_request(body, IMAGE_ERROR)

        assert body["contents"][0]["parts"][1]["inline_data"]["data"] == "AAAA"


class TestOllama:
    def test_images_are_dropped_and_announced_in_the_text(self):
        """Ollama 的图片是并列的 `images` 数组，内容是纯字符串。

        只删数组会让「这张图里是什么」对着无图的上下文提问；占位标记要进
        文本里，模型才能说「我没有收到图片」。
        """
        rectified, record = rectify_request(ollama_body(), IMAGE_ERROR)

        assert record is not None and record.applied
        assert rectified is not None
        message = rectified["messages"][0]
        assert "images" not in message
        assert message["content"].startswith("这张图里是什么")
        assert UNSUPPORTED_IMAGE_PLACEHOLDER in message["content"]

    def test_a_message_without_images_is_left_alone(self):
        body = ollama_body(messages=[{"role": "user", "content": "hi"}])

        assert rectify_request(body, IMAGE_ERROR) == (None, None)

    def test_unsupported_thinking_removes_the_top_level_think(self):
        """Ollama 的思考开关是顶层 `think`，不是 options 里的一项。"""
        rectified, record = rectify_request(ollama_body(), OLLAMA_THINK_ERROR)

        assert record is not None and record.applied
        assert rectified is not None
        assert "think" not in rectified
        # 其余采样参数一个都不能动。
        assert rectified["options"]["num_predict"] == 2048

    def test_removing_think_never_injects_anthropic_fields(self):
        rectified, _ = rectify_request(ollama_body(), OLLAMA_THINK_ERROR)

        assert rectified is not None
        assert "thinking" not in rectified
        assert "max_tokens" not in rectified

    def test_a_think_error_without_think_configured_yields_nothing(self):
        body = ollama_body()
        body.pop("think")

        assert rectify_request(body, OLLAMA_THINK_ERROR) == (None, None)

    def test_the_original_ollama_body_is_never_mutated(self):
        body = ollama_body()

        rectify_request(body, IMAGE_ERROR)

        assert body["messages"][0]["images"] == ["AAAA"]


class TestExistingShapeIsUnchanged:
    """既有两家的行为一个字都不能变——这是回归护栏，不是新能力。"""

    def test_openai_image_blocks_still_become_placeholders(self):
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "看图"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA"}},
                    ],
                }
            ]
        }

        rectified, record = rectify_request(body, IMAGE_ERROR)

        assert record is not None and record.applied
        assert rectified is not None
        assert rectified["messages"][0]["content"][1] == {
            "type": "text",
            "text": UNSUPPORTED_IMAGE_PLACEHOLDER,
        }

    def test_anthropic_budget_rectification_still_writes_the_anthropic_fields(self):
        body = {
            "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
            "max_tokens": 100,
            "thinking": {"type": "enabled", "budget_tokens": 8},
        }

        rectified, record = rectify_request(
            body,
            "max_tokens must be greater than thinking.budget_tokens; "
            "budget_tokens must be at least 1024",
        )

        assert record is not None and record.applied
        assert rectified is not None
        assert rectified["thinking"]["type"] == "enabled"
        assert rectified["max_tokens"] > rectified["thinking"]["budget_tokens"]


class TestAdapterWiring:
    """两家适配器的两条路径都要真的调整流器。"""

    @pytest.mark.parametrize("name", ["gemini_adapter.py", "ollama_adapter.py"])
    @pytest.mark.parametrize("method", ["chat", "stream_chat"])
    def test_the_adapter_calls_the_rectifier_on_both_paths(self, name: str, method: str):
        import re
        from pathlib import Path

        source = (
            Path(__file__).resolve().parents[2]
            / "kirara_ai"
            / "plugins"
            / "llm_preset_adapters"
            / name
        ).read_text(encoding="utf-8")
        start = source.index(f"    def {method}(")
        remainder = source[start + 1 :]
        match = re.search(r"\n    def [a-zA-Z_]+\(", remainder)
        end = start + 1 + (match.start() if match else len(remainder))
        body = source[start:end]

        assert "rectify_request(" in body, (
            f"{name} 的 {method} 没有整流——供应商页上的整流开关对这条路径不参与决策"
        )
        assert "already_applied" in body, f"{name} 的 {method} 整流重试无界"
