"""Teammates 模式：把其他 Agent 作为工具委派（需求 8）。

cc-switch 的 `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS` 打开的是 Claude Code CLI 的
多 agent 协作。本项目的等价物在 Agent 层而不是供应商层——供应商是「上游模型」，
不是「协作单元」。因此实现为 `AgentDefinition.teammate_agent_ids`：
打开后当前 Agent 会额外获得若干 `delegate_to_<agent_id>` 工具，
模型可以把子任务交给队友，队友用自己的模型链、Prompt、Skill 与 MCP 白名单执行。

五条边界，每条都有对应用例——多 Agent 委派最危险的失败形态是**无限递归**
（A 委派 B、B 委派 A，每一层都是一次真实的模型调用，账单和时延同时爆炸）：

1. **默认为空**：不配队友就完全没有委派工具，行为与此前一致。
2. **不能委派自己**：自委派是最短的无限递归。
3. **有递归深度上限**：队友再委派时深度递减，到 0 就不再暴露委派工具。
4. **队友必须启用且存在**：停用的队友不出现在工具列表里，而不是调用时才失败。
5. **委派受同一套授权约束**：非创建者拿不到工具（与既有 `allow_tools` 同源），
   因此「其他使用者」不能借委派绕过权限边界。
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime.core import AgentDefinition, build_teammate_tools


def agent(agent_id: str, *, teammates: tuple[str, ...] = ()) -> AgentDefinition:
    return AgentDefinition(
        agent_id=agent_id,
        model_priority=("model-a",),
        teammate_agent_ids=teammates,
    )


def test_no_teammates_by_default():
    """不配队友时完全没有委派工具，既有部署行为不变。"""
    assert agent("solo").teammate_agent_ids == ()
    assert build_teammate_tools(agent("solo"), {}, depth_remaining=2) == []


def test_self_delegation_is_rejected_at_definition_time():
    """自委派是最短的无限递归，必须在定义期就拒绝。"""
    with pytest.raises(ValueError, match="teammate"):
        agent("loop", teammates=("loop",))


def test_duplicate_teammates_are_collapsed():
    """重复声明不该产生两个同名工具——同名工具会让模型的选择变成未定义行为。"""
    definition = agent("lead", teammates=("helper", "helper"))

    assert definition.teammate_agent_ids == ("helper",)


def test_tools_are_built_only_for_enabled_existing_teammates():
    """停用或不存在的队友不出现在工具列表里。

    在工具列表里放一个必定失败的工具，等于让模型去撞墙一次再重试，
    白花一轮 token。
    """
    registry = {
        "helper": agent("helper"),
        "retired": AgentDefinition(
            agent_id="retired", model_priority=("model-a",), enabled=False
        ),
    }

    tools = build_teammate_tools(
        agent("lead", teammates=("helper", "retired", "ghost")),
        registry,
        depth_remaining=2,
    )

    assert [tool.name for tool in tools] == ["delegate_to_helper"]


def test_no_tools_when_the_recursion_budget_is_exhausted():
    """深度用尽时不再暴露委派工具，而不是暴露了再拒绝。

    暴露一个必定被拒的工具会让模型反复尝试；直接不给它，模型自然会自己作答。
    """
    registry = {"helper": agent("helper")}

    assert (
        build_teammate_tools(
            agent("lead", teammates=("helper",)), registry, depth_remaining=0
        )
        == []
    )


def test_tool_schema_asks_for_a_task_string():
    """委派工具的入参必须是一段任务描述，且是必填。

    可选参数会让模型用空任务调用队友，队友只能凭上下文猜——
    而它拿不到主 Agent 的对话历史。
    """
    registry = {"helper": agent("helper")}

    tool = build_teammate_tools(
        agent("lead", teammates=("helper",)), registry, depth_remaining=1
    )[0]

    assert tool.parameters.required == ["task"]
    assert "task" in tool.parameters.properties
    assert tool.parameters.properties["task"]["type"] == "string"


def test_tool_description_names_the_teammate():
    """描述里要写清队友是谁，否则模型无法判断该把任务给谁。"""
    registry = {"helper": AgentDefinition(
        agent_id="helper",
        model_priority=("model-a",),
        display_name="资料检索助手",
    )}

    tool = build_teammate_tools(
        agent("lead", teammates=("helper",)), registry, depth_remaining=1
    )[0]

    assert "资料检索助手" in tool.description


def test_teammate_ids_are_normalized_like_other_identifiers():
    """空白与空串不得进入列表：它们会生成 `delegate_to_` 这种残缺工具名。"""
    definition = AgentDefinition(
        agent_id="lead",
        model_priority=("model-a",),
        teammate_agent_ids=("  helper  ", "", "   "),
    )

    assert definition.teammate_agent_ids == ("helper",)


def test_policy_signature_covers_teammates():
    """队友列表变化必须让待确认操作失效。

    否则一个「等待确认」的操作会在队友集合被改掉之后仍然按旧集合执行——
    确认的是一件事，执行的是另一件事。
    """
    from kirara_ai.agent_runtime.executor import AgentRuntimeExecutor

    lead = agent("lead", teammates=("helper",))
    changed = agent("lead", teammates=("other",))

    assert AgentRuntimeExecutor._agent_policy_signature(
        lead
    ) != AgentRuntimeExecutor._agent_policy_signature(changed)
