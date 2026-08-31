"""整流器必须对 OpenAI 兼容适配器也生效，而不只对 Claude 生效。

供应商编辑页上有四个整流开关，文档（`docs/PRACTICAL_PLAN_AND_TUTORIAL.md` 4.1）
把「发图片给不支持图片的模型 → 图片换成可见占位文本」写成一条产品行为。
但 `rectify_request` 只在 `claude_adapter.py` 里被调用过。

于是对一个 OpenAI 兼容供应商（本项目里最常见的形态：OpenAI 官方、DeepSeek、
Moonshot、OpenRouter、SiliconFlow、火山、腾讯云、阿里云、Mistral、MiniMax
——十个适配器全部继承同一个基类）来说：开关在界面上、默认开启、
`llm_manager` 也把配置写进了请求，而那个值从未参与任何决策。

这正是需求反复点名的「点了保存没报错，但那个值从未生效」。用户看到的是一次
硬失败（「请求失败」），而真正的原因是一张图，且那不是他能自己改的。

新增的第四类整流 `reasoning_effort_unsupported` 同理：我们刚把
`reasoning_effort` 接到 OpenAI 系与 Ollama 上，而大量兼容网关根本不认识这个
字段，会直接 400。这类失败换供应商也没用——同一个不合法请求发给备用上游
同样会被拒；正确处置是**去掉那一个字段再重试一次**。

四条边界与 Claude 路径逐字一致：
1. 只在上游**真的拒绝**之后动；
2. 只改命中白名单的那一处；
3. 每类只改一次，改完仍失败就抛原始错误；
4. 总开关或单项开关关掉时一个字节都不改。
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from kirara_ai.llm.format.message import (
    LLMChatImageContent,
    LLMChatMessage,
    LLMChatTextContent,
)
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.rectifier import (
    UNSUPPORTED_IMAGE_PLACEHOLDER,
    RectifierConfig,
)
from kirara_ai.plugins.llm_preset_adapters.openai_adapter import (
    OpenAIAdapter,
    OpenAIConfig,
)

OPENAI_OK = {
    "choices": [
        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ]
}


class _Sequence:
    """按顺序返回若干响应的 requests 替身，记录每一次请求体。"""

    def __init__(self, outcomes: list[tuple[int, str, dict | None]]):
        self.outcomes = list(outcomes)
        self.bodies: list[dict] = []
        self._current: tuple[int, str, dict | None] = (200, "", OPENAI_OK)

    def __call__(self, *_args, **kwargs):
        self.bodies.append(kwargs.get("json") or {})
        self._current = (
            self.outcomes.pop(0) if self.outcomes else (200, "", OPENAI_OK)
        )
        return self

    @property
    def status_code(self) -> int:
        return self._current[0]

    @property
    def text(self) -> str:
        return self._current[1]

    def json(self) -> dict:
        payload = self._current[2]
        if payload is None:
            raise ValueError("no json body")
        return payload

    def raise_for_status(self) -> None:
        if self._current[0] >= 400:
            raise RuntimeError(f"HTTP {self._current[0]}: {self._current[1]}")

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def iter_lines(self, *_args, **_kwargs):
        return iter(())


class _FakeMedia:
    """A media object whose base64 URL is awaited by the adapter."""

    async def get_base64_url(self) -> str:
        return "data:image/png;base64,AA=="


class _FakeMediaManager:
    """最小可用的媒体管理器替身。

    适配器会 `await media.get_base64_url()`，因此不能用裸 MagicMock——
    那会以 `TypeError: object MagicMock can't be used in 'await' expression`
    失败，而那个错误与本用例要验的整流行为无关。
    """

    def get_media(self, _media_id: str) -> _FakeMedia:
        return _FakeMedia()


def _adapter() -> OpenAIAdapter:
    adapter = OpenAIAdapter(
        OpenAIConfig(api_key="key", api_base="http://invalid.example")
    )
    adapter.backend_name = "compatible"
    adapter.tracer = MagicMock()
    adapter.media_manager = _FakeMediaManager()
    return adapter


def _image_request(**overrides) -> LLMChatRequest:
    return LLMChatRequest(
        messages=[
            LLMChatMessage(
                role="user",
                content=[
                    LLMChatTextContent(text="这张图里是什么"),
                    LLMChatImageContent(media_id="media-1"),
                ],
            )
        ],
        model="text-only-model",
        **overrides,
    )


def _text_request(**overrides) -> LLMChatRequest:
    return LLMChatRequest(
        messages=[
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])
        ],
        model="text-only-model",
        **overrides,
    )


class TestMediaFallbackReachesOpenAICompatible:
    def test_image_rejection_is_rectified_and_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        adapter = _adapter()
        session = _Sequence(
            [
                (400, '{"error":{"message":"This model does not support image input"}}', None),
                (200, "", OPENAI_OK),
            ]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        response = adapter.chat(_image_request(rectifier=RectifierConfig()))

        assert response.message.content[0].text == "ok"  # type: ignore[union-attr]
        assert len(session.bodies) == 2
        # 第二次请求里图片已换成可见占位文本：换成占位而不是删除，
        # 模型才能说「我没收到图片」而不是对着空内容编一个答案。
        retried = str(session.bodies[1])
        assert UNSUPPORTED_IMAGE_PLACEHOLDER in retried

    def test_a_disabled_switch_changes_nothing(self, monkeypatch: pytest.MonkeyPatch):
        adapter = _adapter()
        session = _Sequence(
            [(400, '{"error":{"message":"does not support image"}}', None)]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        with pytest.raises(Exception):
            adapter.chat(
                _image_request(
                    rectifier=RectifierConfig(request_media_fallback=False)
                )
            )

        # 关掉这一项必须逐字节保持旧行为：只发一次，抛原始错误。
        assert len(session.bodies) == 1

    def test_the_master_switch_off_changes_nothing(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        adapter = _adapter()
        session = _Sequence(
            [(400, '{"error":{"message":"does not support image"}}', None)]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        with pytest.raises(Exception):
            adapter.chat(_image_request(rectifier=RectifierConfig(enabled=False)))

        assert len(session.bodies) == 1

    def test_an_absent_config_uses_the_adapter_default(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        """`rectifier=None` 表示「按适配器默认处理」，默认是全开。

        这是 `LLMChatRequest.rectifier` 的文档语义（见该字段注释），
        也与 Claude 路径逐字一致：`rectify_request` 在 config 为 None 时
        用一份默认全开的 `RectifierConfig`。要关掉必须显式关——
        让「没传」等于「关闭」会让 `llm_manager` 忘记注入配置时静默丢掉
        整个特性，而那种失效无法自查。
        """
        adapter = _adapter()
        session = _Sequence(
            [
                (400, '{"error":{"message":"does not support image"}}', None),
                (200, "", OPENAI_OK),
            ]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        adapter.chat(_image_request())

        assert len(session.bodies) == 2
        assert UNSUPPORTED_IMAGE_PLACEHOLDER in str(session.bodies[1])


class TestUnsupportedReasoningEffortIsRectified:
    def test_unknown_reasoning_effort_field_is_dropped_and_retried(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        adapter = _adapter()
        session = _Sequence(
            [
                (
                    400,
                    '{"error":{"message":"Unrecognized request argument supplied: reasoning_effort"}}',
                    None,
                ),
                (200, "", OPENAI_OK),
            ]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        adapter.chat(
            _text_request(reasoning_effort="max", rectifier=RectifierConfig())
        )

        assert len(session.bodies) == 2
        assert session.bodies[0]["reasoning_effort"] == "max"
        # 换供应商帮不上忙：备用上游收到同一个不合法字段同样会拒。
        # 正确处置是去掉那一个字段再重试一次。
        assert "reasoning_effort" not in session.bodies[1]

    def test_other_fields_survive_the_rectification(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        adapter = _adapter()
        session = _Sequence(
            [
                (400, '{"error":{"message":"unsupported parameter: reasoning_effort"}}', None),
                (200, "", OPENAI_OK),
            ]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        adapter.chat(
            _text_request(
                reasoning_effort="high",
                temperature=0.3,
                max_tokens=128,
                rectifier=RectifierConfig(),
            )
        )

        retried = session.bodies[1]
        # 只改命中白名单的那一处，其余字段一个都不动。
        assert retried["temperature"] == 0.3
        assert retried["max_tokens"] == 128
        assert retried["model"] == "text-only-model"

    def test_an_unrelated_error_is_not_rectified(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        adapter = _adapter()
        session = _Sequence(
            [(401, '{"error":{"message":"invalid api key"}}', None)]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        with pytest.raises(Exception):
            adapter.chat(
                _text_request(reasoning_effort="max", rectifier=RectifierConfig())
            )

        # 鉴权错误不是参数错误：整流它等于把一个明确的失败变成一次无声的降级。
        assert len(session.bodies) == 1

    def test_a_request_without_the_field_is_not_rectified(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        adapter = _adapter()
        session = _Sequence(
            [(400, '{"error":{"message":"unsupported parameter: reasoning_effort"}}', None)]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        with pytest.raises(Exception):
            # 请求里根本没有这个字段时，删它不会改变任何东西——
            # 重试一次只是白花一次调用。
            adapter.chat(_text_request(rectifier=RectifierConfig()))

        assert len(session.bodies) == 1


class TestEachKindAppliesOnlyOnce:
    def test_a_second_identical_rejection_raises_the_original_error(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        adapter = _adapter()
        session = _Sequence(
            [
                (400, '{"error":{"message":"does not support image"}}', None),
                (400, '{"error":{"message":"does not support image"}}', None),
            ]
        )
        monkeypatch.setattr(adapter._session, "post", session)

        with pytest.raises(Exception):
            adapter.chat(_image_request(rectifier=RectifierConfig()))

        # 改完重试仍失败就抛原始错误。反复整流会把「参数错」变成「一直在转」，
        # 而日志里全是重试、没有一条说明原因。
        assert len(session.bodies) == 2
