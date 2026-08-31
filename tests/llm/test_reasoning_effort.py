"""供应商级「最大强度思考」与「禁用自动升级」的真实语义（需求 8）。

需求 8 点名了参考实现的四个开关。它们在那里写的是 **编码 CLI
进程**的设置（commit 署名、实验性 agent teams、tool search、CLI 自动更新器），
照搬到 kirara 的供应商模型只会得到永远没人读的死配置——这正是本轮反复在修的
那类缺陷（`UsageSource.ESTIMATED` 曾经就是有定义、有测试、主链路零调用）。

其中**两个**在本项目里有真实且可验证的对应物，因此按语义实现而不是按字段名照抄：

1. **最大强度思考** → `reasoning_effort`。各家 API 都已提供推理强度控制
   （OpenAI `reasoning_effort`、Claude `thinking.budget_tokens`、
   Gemini `thinkingConfig.thinkingBudget`），而 `LLMChatRequest` 此前**一个都没有**：
   配了推理模型也只能跑默认强度。这是真实缺口，也正是「最大强度思考」要的东西。
2. **禁用自动升级** → `update.disable_auto_check`。启动时无条件 `create_task`
   去 pypi.org 查版本，离线或内网部署既查不到又要等超时，且没有任何开关。

另外两个（隐藏 AI 署名、Teammates 模式）在本项目里没有对应物，
且不能靠编一个字段假装支持——理由与替代做法写在
`docs/EXTENDING.md` 的对照表里，不在代码里留空壳。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.plugins.llm_preset_adapters.claude_adapter import ClaudeAdapter, ClaudeConfig
from kirara_ai.plugins.llm_preset_adapters.gemini_adapter import GeminiAdapter, GeminiConfig
from kirara_ai.plugins.llm_preset_adapters.openai_adapter import OpenAIAdapter, OpenAIConfig


def chat_request(**overrides) -> LLMChatRequest:
    return LLMChatRequest(
        messages=[
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="hello")])
        ],
        model="mock-model",
        **overrides,
    )


class _Capture:
    """记录最后一次请求体的 requests 替身。"""

    def __init__(self, payload: dict):
        self.payload = payload
        self.body: dict | None = None

    def __call__(self, *_args, **kwargs):
        self.body = kwargs.get("json")
        return self

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self.payload

    @property
    def text(self) -> str:
        return "<capture>"


OPENAI_OK = {
    "choices": [
        {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
    ]
}
CLAUDE_OK = {
    "role": "assistant",
    "stop_reason": "end_turn",
    "content": [{"type": "text", "text": "ok"}],
}
GEMINI_OK = {
    "candidates": [
        {"content": {"role": "model", "parts": [{"text": "ok"}]}, "finishReason": "STOP"}
    ]
}


def test_reasoning_effort_is_a_request_field():
    """请求模型必须能表达推理强度，否则「最大强度思考」无处落脚。"""
    request = chat_request(reasoning_effort="max")

    assert request.reasoning_effort == "max"


def test_reasoning_effort_defaults_to_unset_so_existing_behavior_is_unchanged():
    """未配置时保持 None：默认行为不得因为新增字段而改变。"""
    assert chat_request().reasoning_effort is None


def test_openai_sends_reasoning_effort_when_requested(monkeypatch: pytest.MonkeyPatch):
    adapter = OpenAIAdapter(OpenAIConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "openai"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    capture = _Capture(OPENAI_OK)
    monkeypatch.setattr(adapter._session, "post", capture)

    adapter.chat(chat_request(reasoning_effort="max"))

    assert capture.body is not None
    assert capture.body["reasoning_effort"] == "max"


def test_openai_omits_reasoning_effort_when_unset(monkeypatch: pytest.MonkeyPatch):
    """未配置时不得出现该键：部分兼容网关对未知字段直接 400。"""
    adapter = OpenAIAdapter(OpenAIConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "openai"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    capture = _Capture(OPENAI_OK)
    monkeypatch.setattr(adapter._session, "post", capture)

    adapter.chat(chat_request())

    assert capture.body is not None
    assert "reasoning_effort" not in capture.body


def test_claude_translates_effort_into_a_thinking_budget(monkeypatch: pytest.MonkeyPatch):
    """Claude 没有 `reasoning_effort`，它用 `thinking.budget_tokens`。

    直接把 `reasoning_effort: "max"` 塞给 Claude 会被拒；必须按各家真实字段翻译。
    """
    adapter = ClaudeAdapter(ClaudeConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "claude"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    capture = _Capture(CLAUDE_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.claude_adapter.requests.post", capture
    )

    adapter.chat(chat_request(reasoning_effort="max", max_tokens=8000))

    assert capture.body is not None
    thinking = capture.body.get("thinking")
    assert thinking is not None, "Claude 必须收到 thinking 配置而不是 reasoning_effort"
    assert thinking["type"] == "enabled"
    assert thinking["budget_tokens"] > 0
    # thinking 预算必须留出正文空间，否则上游直接拒绝整个请求。
    assert thinking["budget_tokens"] < capture.body["max_tokens"]
    assert "reasoning_effort" not in capture.body


def test_gemini_translates_effort_into_a_thinking_budget(monkeypatch: pytest.MonkeyPatch):
    """Gemini 用 `generationConfig.thinkingConfig.thinkingBudget`。

    `max` 映射到 `-1`——那是 Gemini 定义的「动态思考」哨兵值，表示把预算交给
    模型自行决定，正好对应「最大强度」；写一个我们猜的具体数字反而设了上限。
    """
    adapter = GeminiAdapter(GeminiConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "gemini"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    capture = _Capture(GEMINI_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.gemini_adapter.requests.post", capture
    )

    adapter.chat(chat_request(reasoning_effort="max"))

    assert capture.body is not None
    generation = capture.body.get("generationConfig") or {}
    assert generation.get("thinkingConfig") == {"thinkingBudget": -1}
    assert "reasoning_effort" not in capture.body


def test_gemini_lower_tiers_use_explicit_positive_budgets(
    monkeypatch: pytest.MonkeyPatch,
):
    """非 max 档位必须给出正数预算：那才是「限制强度」的意思。"""
    adapter = GeminiAdapter(GeminiConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "gemini"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    capture = _Capture(GEMINI_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.gemini_adapter.requests.post", capture
    )

    adapter.chat(chat_request(reasoning_effort="low"))

    generation = (capture.body or {}).get("generationConfig") or {}
    assert generation["thinkingConfig"]["thinkingBudget"] > 0


def test_gemini_omits_thinking_config_when_unset(monkeypatch: pytest.MonkeyPatch):
    """未配置时不得出现该键：不支持思考的模型收到它会直接报错。"""
    adapter = GeminiAdapter(GeminiConfig(api_key="key", api_base="http://invalid.example"))
    adapter.backend_name = "gemini"
    adapter.tracer = MagicMock()
    adapter.media_manager = MagicMock()
    capture = _Capture(GEMINI_OK)
    monkeypatch.setattr(
        "kirara_ai.plugins.llm_preset_adapters.gemini_adapter.requests.post", capture
    )

    adapter.chat(chat_request())

    generation = (capture.body or {}).get("generationConfig") or {}
    assert "thinkingConfig" not in generation


def test_backend_config_carries_reasoning_effort_with_validation():
    """供应商级配置项：只接受受支持的档位，拼错必须报错而不是静默忽略。"""
    backend = LLMBackendConfig(name="p", adapter="openai", reasoning_effort="max")
    assert backend.reasoning_effort == "max"

    assert LLMBackendConfig(name="p", adapter="openai").reasoning_effort is None

    with pytest.raises(ValueError):
        LLMBackendConfig(name="p", adapter="openai", reasoning_effort="maximum")


def test_auto_update_check_can_be_disabled():
    """`禁用自动升级`：启动时的版本探测必须可关。

    此前启动无条件 `create_task(check_update(...))` 去 pypi.org 查版本，
    离线或内网部署既查不到又要等超时，且没有任何开关。
    """
    assert GlobalConfig().update.disable_auto_check is False

    config = GlobalConfig()
    config.update.disable_auto_check = True
    assert config.update.disable_auto_check is True


@pytest.mark.asyncio
async def test_check_update_returns_without_touching_the_registry_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
):
    """关闭后不得发起任何注册表请求——否则「禁用」只是不打印结果。"""
    from kirara_ai import entry

    probe = AsyncMock(return_value=("9.9.9", "https://example.invalid/x.whl"))
    monkeypatch.setattr(entry, "get_latest_pypi_version", probe)

    config = GlobalConfig()
    config.update.disable_auto_check = True

    await entry.check_update(config)

    probe.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_update_still_probes_when_enabled(monkeypatch: pytest.MonkeyPatch):
    """对照组：默认仍然检查，行为不变。"""
    from kirara_ai import entry

    probe = AsyncMock(return_value=("9.9.9", "https://example.invalid/x.whl"))
    monkeypatch.setattr(entry, "get_latest_pypi_version", probe)

    await entry.check_update(GlobalConfig())

    probe.assert_awaited_once()
