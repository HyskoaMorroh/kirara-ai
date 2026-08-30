"""遗留工作流的 LLM 节点也必须下传取消信号与总预算。

Agent 路径已经接上，但工作流节点（`ChatCompletion` / 函数调用 / 工具循环）
是另一条调用链。只接一条就等于「用 Agent 的部署有总预算、用工作流的没有」——
同一个配置项在两条路径上语义不同，比没有这个配置更糟。

三个调用点分别是：模型回退循环、函数调用回退循环、多轮工具 while 循环。
最后一个最关键：它最容易把一轮对话拖成无限长。
"""

from __future__ import annotations

import threading

import pytest

from kirara_ai.config.global_config import AgentRuntimeConfig, GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.implementations.blocks.llm import chat as chat_module


def container_with_budget(seconds: float) -> DependencyContainer:
    container = DependencyContainer()
    config = GlobalConfig()
    config.agent_runtime = AgentRuntimeConfig(turn_deadline_seconds=seconds)
    container.register(GlobalConfig, config)
    return container


def test_no_budget_configured_yields_no_signal_and_no_deadline():
    """默认 0 表示不设预算：必须什么都不传，保持既有行为。"""
    event, budget = chat_module._turn_budget(container_with_budget(0))

    assert event is None
    assert budget is None


def test_a_configured_budget_yields_a_fresh_event_and_the_budget():
    event, budget = chat_module._turn_budget(container_with_budget(45))

    assert isinstance(event, threading.Event)
    assert not event.is_set(), "预算刚开始时不应已被取消"
    assert budget == 45.0


def test_a_container_without_config_degrades_to_no_budget():
    """容器里没有 GlobalConfig（单元测试与部分插件如此）时不得抛错。"""
    event, budget = chat_module._turn_budget(DependencyContainer())

    assert event is None and budget is None


def test_a_hostile_container_degrades_instead_of_breaking_the_reply():
    """读配置失败只应失去预算，不能让这条回复发不出去。"""

    class Hostile:
        def has(self, _key):
            raise RuntimeError("container exploded")

    event, budget = chat_module._turn_budget(Hostile())

    assert event is None and budget is None


def test_execute_resilient_chat_forwards_both_parameters():
    forwarded: dict = {}

    class Manager:
        def execute_chat(self, request, **options):
            forwarded.update(options)

            class Result:
                response = "ok"

            return Result()

    event = threading.Event()
    chat_module._execute_resilient_chat(
        Manager(),
        object(),
        cancellation_event=event,
        deadline_seconds=12.5,
    )

    assert forwarded["cancellation_event"] is event
    assert forwarded["deadline_seconds"] == 12.5


def test_execute_resilient_chat_omits_absent_parameters():
    """没有值就不塞 None：那会覆盖 manager 自己的默认语义。"""
    forwarded: dict = {}

    class Manager:
        def execute_chat(self, request, **options):
            forwarded.update(options)

            class Result:
                response = "ok"

            return Result()

    chat_module._execute_resilient_chat(Manager(), object())

    assert "cancellation_event" not in forwarded
    assert "deadline_seconds" not in forwarded


def test_all_three_call_sites_pass_a_deadline():
    """源码级断言：三个调用点都必须带上取消与截止时间。

    这里以源码为断言对象，因为三个调用点分别埋在三个大 execute() 里，
    单测挂载成本远高于收益；而「有没有传」正是这条需求的全部内容。
    """
    import inspect

    source = inspect.getsource(chat_module)
    call_count = source.count("_execute_resilient_chat(")
    # 一次定义 + 三个调用点
    assert call_count == 4, f"调用点数量变了（{call_count}），请同步检查预算下传"
    assert source.count("cancellation_event=turn_cancellation") == 2
    assert source.count("cancellation_event=loop_cancellation") == 1
    assert source.count("deadline_seconds=remaining()") == 2
    assert source.count("deadline_seconds=loop_remaining()") == 1
