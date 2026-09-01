"""超时默认值必须容得下真实的推理模型（需求 8，用户已确认的数值）。

原默认值来自「快速失败」的取向：首字节 15s、静默 30s、非流式 60s。那套数字对
一个 chat completion 是合理的，但本项目现在明确支持**最大强度思考**
（`reasoning_effort="max"`）——Claude 的 extended thinking 与 OpenAI 的
reasoning 模型在首个 token 之前就可能思考几十秒，而 60s 的非流式总预算连一次
长推理都装不下。

后果不是「慢」，而是**看起来像坏了**：请求在模型正常工作时被本地掐断，
熔断器记一次失败，连续三次之后整个供应商被跳过——一个配置默认值把可用的上游
判成了故障。用户确认后的数值：首字节 60s、静默 120s、非流式 600s。

判据：**超时是「判定对方已经不响应」的门槛，不是「我不想再等」的偏好。**
宁可让用户按需调紧，不能让默认值把正常的长推理判成故障。

同时钉住三者之间的关系：`stream_first_byte + stream_idle` 必须仍然落在
`stream_total` 之内（这条约束在 `check_resilience_budget` 里，改默认值时最容易
一起改坏），且非流式预算要能容下 `max_retries` 的退避总和。
"""

from __future__ import annotations

import pytest

from kirara_ai.config.global_config import LLMBackendConfig


def _defaults() -> LLMBackendConfig:
    return LLMBackendConfig(name="defaults", adapter="openai")


def test_stream_first_byte_default_allows_a_long_thinking_prelude():
    assert _defaults().stream_first_byte_timeout_seconds == pytest.approx(60.0)


def test_stream_idle_default_allows_a_pause_between_reasoning_blocks():
    assert _defaults().stream_idle_timeout_seconds == pytest.approx(120.0)


def test_non_stream_default_allows_a_full_max_effort_completion():
    assert _defaults().non_stream_timeout_seconds == pytest.approx(600.0)


def test_stream_total_default_covers_the_first_byte_and_idle_budgets():
    """总预算必须 ≥ 首字节 + 静默，否则那条 validator 一配置就报错。

    默认值之间自相矛盾的配置模型，会让「显式写一次 stream_total」这种正常操作
    莫名失败——而错误信息指向的是用户没写过的两个字段。
    """
    backend = _defaults()
    activity = (
        backend.stream_first_byte_timeout_seconds + backend.stream_idle_timeout_seconds
    )
    assert backend.stream_total_timeout_seconds >= activity
    assert backend.effective_stream_total_timeout() >= activity


def test_legacy_request_timeout_default_matches_the_new_stream_total():
    """没写新键的旧配置走 `request_timeout_seconds`，两者必须同步放宽。

    只放宽新键会让**升级前写过配置的用户**留在 60s 上，而界面显示的是新默认值——
    同一个部署里，面板说 600 而运行时按 60 掐断。
    """
    backend = _defaults()
    assert backend.effective_stream_total_timeout() == pytest.approx(
        backend.stream_total_timeout_seconds
    )
    assert backend.effective_non_stream_timeout() == pytest.approx(
        backend.non_stream_timeout_seconds
    )


def test_default_budget_survives_the_maximum_retry_backoff():
    """默认值下把 `max_retries` 拉到上限也不该触发预算校验失败。"""
    backend = LLMBackendConfig(
        name="max-retries",
        adapter="openai",
        max_retries=10,
        non_stream_timeout_seconds=600.0,
    )
    assert backend.max_retries == 10


def test_the_manager_fallback_uses_the_same_generous_numbers():
    """`llm_manager` 在拿不到 backend 时的兜底值必须与配置默认值一致。

    这两处一旦漂移，就出现「配置里 600、代码里 60」——而走到兜底分支的请求
    （backend 查不到时）会按一个界面上根本看不到的数字被掐断。断言查的是
    **行为**而不是源码里的字面常数：兜底本就该从配置派生，届时源码里不再有
    那个数字，查文本会把正确的实现判成失败。
    """
    from kirara_ai.config.global_config import backend_field_default
    from kirara_ai.llm.llm_manager import LLMManager

    backend = _defaults()
    assert LLMManager._stream_timeout(None) == pytest.approx(
        backend.stream_total_timeout_seconds
    )
    assert LLMManager._non_stream_timeout(None) == pytest.approx(
        backend.non_stream_timeout_seconds
    )
    for field in (
        "stream_first_byte_timeout_seconds",
        "stream_idle_timeout_seconds",
        "non_stream_timeout_seconds",
        "circuit_failure_threshold",
        "circuit_recovery_success_threshold",
        "retry_backoff_max_seconds",
    ):
        assert backend_field_default(field) == getattr(backend, field), (
            f"{field} 的派生兜底与配置默认值不一致"
        )
