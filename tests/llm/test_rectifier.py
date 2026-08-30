"""整流器：上游因参数约束拒绝时，按白名单修一次再重试（需求 8）。

需求 8 末句点名整流器。cc-switch 里它是两个模块（thinking 签名、thinking 预算），
配置里另有图片降级。三者的共同点是**改一处就能成功，不改就必然失败**：

- `budget_tokens` 有下限且必须小于 `max_tokens`；
- 多轮对话回传的 `thinking` 签名在换模型/换供应商后失效；
- 不支持图片的模型收到图片块会拒绝整个请求。

没有整流器时是一次硬失败：用户看到「请求失败」，而真正原因（预算与上限的关系、
一个过期签名、一张图）既不在错误里说清，也不是用户能自己改的。

这里的每一条断言都在守同一件事：**整流必须是事实驱动的、白名单的、一次性的**。
模糊自动改写会掩盖真正的参数错误，那比硬失败更糟——它把一个能查的错误
变成一个查不到的错误。
"""

from __future__ import annotations

import pytest

from kirara_ai.llm.rectifier import (
    MIN_THINKING_BUDGET,
    RECTIFIED_MAX_TOKENS,
    RECTIFIED_THINKING_BUDGET,
    UNSUPPORTED_IMAGE_PLACEHOLDER,
    RectifierConfig,
    rectify_media,
    rectify_request,
    rectify_thinking_budget,
    rectify_thinking_signature,
    should_rectify_media,
    should_rectify_thinking_budget,
    should_rectify_thinking_signature,
)

SIGNATURE_ERROR = "Invalid 'signature' in 'thinking' block at messages.1.content.0"
BUDGET_ERROR = (
    "max_tokens must be greater than thinking.budget_tokens; "
    "budget_tokens must be at least 1024"
)
IMAGE_ERROR = "This model does not support image input"


class TestDetection:
    def test_signature_error_is_detected(self):
        assert should_rectify_thinking_signature(SIGNATURE_ERROR, RectifierConfig())

    def test_an_auth_signature_error_is_not_a_thinking_problem(self):
        """只看 `signature` 会把鉴权签名错误也当成 thinking 问题。

        那会去删一堆与失败无关的字段，而真正的原因（密钥不对）被掩盖。
        """
        assert not should_rectify_thinking_signature(
            "Signature verification failed: invalid credential", RectifierConfig()
        )

    def test_budget_error_is_detected(self):
        assert should_rectify_thinking_budget(BUDGET_ERROR, RectifierConfig())

    def test_image_error_needs_both_an_image_word_and_an_unsupported_word(self):
        assert should_rectify_media(IMAGE_ERROR, RectifierConfig())
        # 只提到 image 但不是「不支持」：例如图片下载超时，降级会把图片悄悄删掉。
        assert not should_rectify_media(
            "image download timed out after 30s", RectifierConfig()
        )

    def test_unrelated_errors_never_trigger_anything(self):
        config = RectifierConfig()
        for error in ("rate limit exceeded", "internal server error", "", None):
            assert not should_rectify_thinking_signature(error, config)
            assert not should_rectify_thinking_budget(error, config)
            assert not should_rectify_media(error, config)

    def test_the_master_switch_disables_all_three(self):
        config = RectifierConfig(enabled=False)
        assert not should_rectify_thinking_signature(SIGNATURE_ERROR, config)
        assert not should_rectify_thinking_budget(BUDGET_ERROR, config)
        assert not should_rectify_media(IMAGE_ERROR, config)

    def test_each_sub_switch_is_independent(self):
        """图片降级最该能被单独关掉：它**会改变模型看到的内容**。"""
        config = RectifierConfig(request_media_fallback=False)
        assert should_rectify_thinking_signature(SIGNATURE_ERROR, config)
        assert not should_rectify_media(IMAGE_ERROR, config)

    def test_an_exception_carrying_a_response_body_is_matched(self):
        """上游错误常常只在响应体里，异常本身的 str 不含特征串。"""

        class _Response:
            text = SIGNATURE_ERROR

        class _Error(Exception):
            response = _Response()

        assert should_rectify_thinking_signature(_Error("400 Client Error"), RectifierConfig())


class TestSignatureRectify:
    def test_thinking_blocks_are_removed(self):
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "thinking", "thinking": "...", "signature": "abc"},
                        {"type": "text", "text": "answer"},
                    ],
                }
            ]
        }

        record = rectify_thinking_signature(body)

        assert record.applied
        assert body["messages"][0]["content"] == [{"type": "text", "text": "answer"}]

    def test_redacted_thinking_blocks_are_removed_too(self):
        body = {
            "messages": [
                {"role": "assistant", "content": [{"type": "redacted_thinking", "data": "x"}]}
            ]
        }

        record = rectify_thinking_signature(body)

        assert record.applied
        assert record.details["removed_redacted_thinking_blocks"] == 1

    def test_a_stray_signature_on_a_text_block_is_stripped_but_the_block_stays(self):
        """留着上一轮残留的 signature 会继续触发校验；但文本本身与签名无关。"""
        body = {
            "messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "keep me", "signature": "old"}],
                }
            ]
        }

        record = rectify_thinking_signature(body)

        assert record.applied
        assert body["messages"][0]["content"] == [{"type": "text", "text": "keep me"}]

    def test_images_and_tool_blocks_are_untouched(self):
        """删它们等于把一次参数修复变成一次内容删改。"""
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"data": "..."}},
                        {"type": "tool_use", "id": "t1", "name": "search"},
                    ],
                }
            ]
        }

        record = rectify_thinking_signature(body)

        assert not record.applied
        assert len(body["messages"][0]["content"]) == 2

    def test_a_body_without_messages_is_not_an_error(self):
        assert rectify_thinking_signature({}).applied is False


class TestBudgetRectify:
    def test_budget_and_max_tokens_are_made_consistent(self):
        body = {"max_tokens": 2000, "thinking": {"type": "enabled", "budget_tokens": 500}}

        record = rectify_thinking_budget(body)

        assert record.applied
        assert body["thinking"] == {
            "type": "enabled",
            "budget_tokens": RECTIFIED_THINKING_BUDGET,
        }
        # max_tokens 必须严格大于预算，否则换个预算也仍然违规。
        assert body["max_tokens"] == RECTIFIED_MAX_TOKENS
        assert body["max_tokens"] > body["thinking"]["budget_tokens"]

    def test_a_large_enough_max_tokens_is_preserved(self):
        """用户配了更大的上限就不要替他改小。"""
        body = {"max_tokens": 100000, "thinking": {"type": "enabled", "budget_tokens": 8}}

        rectify_thinking_budget(body)

        assert body["max_tokens"] == 100000

    def test_adaptive_thinking_is_left_alone(self):
        """`adaptive` 是让上游自己定预算；改成固定值是替它做了没要求的决定。"""
        body = {"max_tokens": 100, "thinking": {"type": "adaptive"}}

        record = rectify_thinking_budget(body)

        assert not record.applied
        assert body["thinking"] == {"type": "adaptive"}

    def test_a_missing_thinking_object_is_created(self):
        body = {"max_tokens": 100}

        record = rectify_thinking_budget(body)

        assert record.applied
        assert body["thinking"]["budget_tokens"] == RECTIFIED_THINKING_BUDGET

    def test_the_rectified_budget_clears_the_documented_minimum(self):
        body = {"max_tokens": 100}
        rectify_thinking_budget(body)
        assert body["thinking"]["budget_tokens"] >= MIN_THINKING_BUDGET

    def test_an_already_valid_body_reports_no_change(self):
        """已经合法时不该报告「整流过」——那会让 trace 里多出一条假记录。"""
        body = {
            "max_tokens": RECTIFIED_MAX_TOKENS,
            "thinking": {"type": "enabled", "budget_tokens": RECTIFIED_THINKING_BUDGET},
        }

        assert rectify_thinking_budget(body).applied is False


class TestMediaRectify:
    def test_images_become_a_visible_placeholder(self):
        """换成占位而不是静默删除：否则模型会对着空内容编一个答案。"""
        body = {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "what is this?"},
                        {"type": "image", "source": {"data": "..."}},
                    ],
                }
            ]
        }

        record = rectify_media(body)

        assert record.applied
        assert record.details["replaced_images"] == 1
        assert body["messages"][0]["content"][1] == {
            "type": "text",
            "text": UNSUPPORTED_IMAGE_PLACEHOLDER,
        }

    def test_openai_style_image_url_blocks_are_handled(self):
        body = {"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]}

        assert rectify_media(body).applied

    def test_text_only_bodies_report_no_change(self):
        body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}

        assert rectify_media(body).applied is False


class TestRectifyRequest:
    def test_it_returns_a_copy_and_never_mutates_the_original(self):
        """原始请求体必须留着：重试失败要抛原始错误，trace 要能看出差异。"""
        body = {"max_tokens": 100, "thinking": {"type": "enabled", "budget_tokens": 8}}

        rectified, record = rectify_request(body, BUDGET_ERROR)

        assert record is not None and record.applied
        assert rectified is not None
        assert body["max_tokens"] == 100, "入参被就地修改了"
        assert rectified["max_tokens"] == RECTIFIED_MAX_TOKENS

    def test_an_unrelated_error_yields_nothing(self):
        assert rectify_request({"max_tokens": 1}, "rate limited") == (None, None)

    def test_the_same_kind_is_only_applied_once(self):
        """修完重试仍失败就抛原始错误。

        反复整流会把「参数错」变成「一直在转」，而后者更难查：
        日志里全是重试，没有一条说明原因。
        """
        body = {"max_tokens": 100, "thinking": {"type": "enabled", "budget_tokens": 8}}

        first, record = rectify_request(body, BUDGET_ERROR)
        assert first is not None and record is not None

        again = rectify_request(body, BUDGET_ERROR, already_applied=frozenset({record.kind}))

        assert again == (None, None)

    def test_a_matching_error_with_nothing_to_change_yields_nothing(self):
        """命中错误特征但无可改之处时，重试同一个请求只会得到同一个错误。"""
        body = {"messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]}

        assert rectify_request(body, SIGNATURE_ERROR) == (None, None)

    def test_the_master_switch_short_circuits(self):
        body = {"max_tokens": 100}

        assert rectify_request(body, BUDGET_ERROR, RectifierConfig(enabled=False)) == (
            None,
            None,
        )

    def test_the_record_says_what_changed(self):
        """静默改写请求最难排查：用户发的与上游收到的不是同一个请求。"""
        body = {"max_tokens": 100, "thinking": {"type": "enabled", "budget_tokens": 8}}

        _, record = rectify_request(body, BUDGET_ERROR)

        assert record is not None
        payload = record.to_dict()
        assert payload["kind"] == "thinking_budget"
        assert payload["applied"] is True
        assert payload["details"]["before"]["max_tokens"] == 100
        assert payload["details"]["after"]["max_tokens"] == RECTIFIED_MAX_TOKENS
