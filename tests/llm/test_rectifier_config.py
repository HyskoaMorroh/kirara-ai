"""整流器开关必须可配（需求 8）。

参考实现把整流器做成总开关 + 三个子开关，理由是三类整流的风险不同：
签名整流丢掉的是**已经失效**的上一轮思考，预算整流改的是数值，
而图片降级**会改变模型看到的内容**——它最该能被单独关掉。

写死成「永远开」等于替用户决定了「可以改写我的请求」，
而这恰恰是需求 8 里唯一带「如果…最优」措辞的一项，说明它该是可选的。
"""

from __future__ import annotations

import pytest

from kirara_ai.config.global_config import LLMBackendConfig


def _backend(**overrides) -> LLMBackendConfig:
    payload = {"name": "p", "adapter": "claude"}
    payload.update(overrides)
    return LLMBackendConfig(**payload)


def test_rectifier_defaults_to_enabled():
    """默认开启：这些错误不修就必然失败，且原因不是用户能自己改的。"""
    config = _backend()

    assert config.rectifier_enabled is True
    assert config.rectify_thinking_signature is True
    assert config.rectify_thinking_budget is True
    assert config.rectify_media_fallback is True


def test_the_master_switch_can_be_turned_off():
    assert _backend(rectifier_enabled=False).rectifier_enabled is False


def test_media_fallback_can_be_turned_off_alone():
    """图片降级会改变模型看到的内容，必须能单独关掉而不牵连其他两项。"""
    config = _backend(rectify_media_fallback=False)

    assert config.rectify_media_fallback is False
    assert config.rectify_thinking_signature is True
    assert config.rectify_thinking_budget is True


def test_the_config_maps_onto_the_runtime_rectifier_config():
    """配置与运行时那份 `RectifierConfig` 必须是同一组语义。

    两处各写一份开关名，迟早会出现「界面关了但运行时还在改」——
    而那种不一致没有任何症状，只表现为请求被静默改写。
    """
    from kirara_ai.llm.rectifier import RectifierConfig

    config = _backend(rectify_media_fallback=False)
    runtime = config.build_rectifier_config()

    assert isinstance(runtime, RectifierConfig)
    assert runtime.enabled is True
    assert runtime.request_media_fallback is False
    assert runtime.allows("thinking_budget") is True
    assert runtime.allows("media_fallback") is False


def test_the_master_switch_disables_every_kind_at_runtime():
    runtime = _backend(rectifier_enabled=False).build_rectifier_config()

    for kind in ("thinking_signature", "thinking_budget", "media_fallback"):
        assert runtime.allows(kind) is False


def test_the_request_carries_the_per_provider_rectifier_config():
    """整流开关配在**每个供应商**上，因此必须随请求下发。

    同一个模型可由多个供应商提供：队列里 P1 是自建 Anthropic 网关（要整流）、
    P2 是不支持思考的兼容接口（图片降级都该关）时，两者必须各按自己的配置走。
    适配器只拿到凭据配置（`ClaudeConfig`），读不到 `LLMBackendConfig`，
    所以走 `reasoning_effort` 同一条 per-provider 通道。
    """
    from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
    from kirara_ai.llm.format.request import LLMChatRequest
    from kirara_ai.llm.llm_manager import LLMManager

    request = LLMChatRequest(
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
        model="claude-sonnet-5",
    )
    backend = _backend(rectify_media_fallback=False)

    applied = LLMManager._request_for_provider(None, request, backend)

    assert applied.rectifier is not None
    assert applied.rectifier.request_media_fallback is False
    # 不就地改写调用方的对象：P1 的设置泄漏到 P2 会让一次本可成功的转移变两连败。
    assert request.rectifier is None


def test_an_explicit_request_level_rectifier_wins():
    """调用方显式给出的优先，否则「这一次不要改我的请求」无法表达。"""
    from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent
    from kirara_ai.llm.format.request import LLMChatRequest
    from kirara_ai.llm.llm_manager import LLMManager
    from kirara_ai.llm.rectifier import RectifierConfig

    explicit = RectifierConfig(enabled=False)
    request = LLMChatRequest(
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="hi")])],
        model="claude-sonnet-5",
        rectifier=explicit,
    )

    applied = LLMManager._request_for_provider(None, request, _backend())

    assert applied.rectifier is explicit
