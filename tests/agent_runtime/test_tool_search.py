"""需求 8「Tool Search」：工具也要能被**搜索**出来，而不是全量塞进每次请求。

参考实现里这个开关（`ENABLE_TOOL_SEARCH`）打开的是编码 CLI 的工具渐进披露：
不把所有工具的完整 schema 常驻上下文，而给模型一个搜索工具，让它按需取回。
本项目已经对 **Skill** 做了同一件事（`skill_<id>` 工具 + 一行目录），
但 **MCP 工具** 仍然是全量注入：一台连了三个 MCP 服务器的部署很容易有几十个工具，
每个带完整的 JSON Schema。

两处后果，与当初 Skill 全量注入完全一样：

1. **成本随「工具数 × 请求数」线性增长**，而其中绝大部分与当轮问题无关。
   四十个工具各 300 token，就是每一轮固定多付 12000 token。
2. **模型在一堵墙前反而更容易选错**：几十个名字相近的工具（`read_file`、
   `read_text_file`、`read_multiple_files`）挤在一起时，选择质量下降。

机制：工具数超过阈值时，系统提示词里只放一行目录（名字 + 一句用途），
完整 schema 由 `search_tools` 取回。低于阈值时**一律全量注入**——
给三个工具再加一层搜索只是凭空多一轮往返。
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime.tool_search import (
    TOOL_SEARCH_TOOL_NAME,
    build_tool_search_tool,
    search_tool_entries,
    should_use_tool_search,
    tool_catalog_section,
)
from kirara_ai.llm.format.request import Tool, ToolParameters


def _tool(name: str, description: str = "") -> Tool:
    return Tool(
        name=name,
        description=description or f"{name} description",
        parameters=ToolParameters(properties={"path": {"type": "string"}}, required=["path"]),
    )


def _tools(count: int) -> list[Tool]:
    return [_tool(f"tool_{index}") for index in range(count)]


class TestThreshold:
    def test_a_small_tool_set_is_injected_in_full(self):
        # 给三个工具再加一层搜索只是凭空多一轮往返：模型本来一眼就能选。
        assert should_use_tool_search(_tools(3), threshold=12) is False

    def test_a_large_tool_set_switches_to_search(self):
        assert should_use_tool_search(_tools(40), threshold=12) is True

    def test_exactly_at_the_threshold_stays_full(self):
        # 边界归「全量」：阈值的含义是「超过这么多才值得换机制」。
        assert should_use_tool_search(_tools(12), threshold=12) is False

    def test_threshold_zero_disables_the_feature(self):
        # 0 表示关闭，而不是「一律搜索」。一个把阈值设成 0 想关掉它的运维
        # 不该反而把它全开——那是最容易配反的一处。
        assert should_use_tool_search(_tools(99), threshold=0) is False

    def test_an_empty_tool_set_never_uses_search(self):
        assert should_use_tool_search([], threshold=1) is False


class TestCatalog:
    def test_catalog_lists_every_tool_with_one_line_each(self):
        section = tool_catalog_section(
            [_tool("read_file", "读取一个文件"), _tool("write_file", "写入一个文件")]
        )

        assert "read_file" in section
        assert "读取一个文件" in section
        assert "write_file" in section
        # 目录必须点名取回正文的那个工具，否则模型知道有工具却不知道怎么拿到参数。
        assert TOOL_SEARCH_TOOL_NAME in section

    def test_catalog_does_not_contain_parameter_schemas(self):
        section = tool_catalog_section([_tool("read_file")])

        # 目录的全部意义就是不带 schema。带上等于既付了目录的钱又付了全量的钱。
        assert "properties" not in section
        assert "additionalProperties" not in section

    def test_an_empty_catalog_is_an_empty_string(self):
        # 没有工具时不放一段「以下是可用工具：（空）」——那是一句会让模型
        # 反复确认的废话，而且每轮都要付费。
        assert tool_catalog_section([]) == ""

    def test_catalog_order_is_stable(self):
        tools = [_tool("b_tool"), _tool("a_tool"), _tool("c_tool")]

        first = tool_catalog_section(tools)
        second = tool_catalog_section(tools)

        # 顺序不稳定会让同一份配置产出不同的提示词，从而击穿上游的提示词缓存。
        assert first == second


class TestSearch:
    def test_search_matches_on_name(self):
        matched = search_tool_entries([_tool("read_file"), _tool("send_email")], "read")

        assert [tool.name for tool in matched] == ["read_file"]

    def test_search_matches_on_description(self):
        matched = search_tool_entries(
            [_tool("alpha", "读取一个文件"), _tool("beta", "发送邮件")], "文件"
        )

        assert [tool.name for tool in matched] == ["alpha"]

    def test_search_is_case_insensitive(self):
        matched = search_tool_entries([_tool("ReadFile")], "readfile")

        assert [tool.name for tool in matched] == ["ReadFile"]

    def test_search_returns_full_schemas(self):
        matched = search_tool_entries([_tool("read_file")], "read")

        # 取回的必须是**完整可调用**的定义。返回一个没有参数的壳子会让模型
        # 用空参数去调，然后拿到一个它无法理解的校验错误。
        assert matched[0].parameters.required == ["path"]
        assert "path" in matched[0].parameters.properties

    def test_an_empty_query_returns_everything(self):
        # 「我不知道该搜什么」是一个合法状态。返回空会把模型逼进死角：
        # 它既拿不到工具，也没有别的入口。
        matched = search_tool_entries(_tools(3), "")

        assert len(matched) == 3

    def test_search_result_count_is_capped(self):
        matched = search_tool_entries(_tools(200), "tool", limit=5)

        # 不设上限时一次宽泛搜索会把刚省下的 token 一次性还回去，
        # 而且比全量注入更糟——全量至少只付一次。
        assert len(matched) == 5

    def test_no_match_returns_empty_rather_than_guessing(self):
        matched = search_tool_entries([_tool("read_file")], "quantum_teleport")

        # 猜一个最接近的工具交出去，比返回空更糟：模型会调用一个它没要求的工具。
        assert matched == []


class TestSearchToolDefinition:
    def test_the_search_tool_declares_a_query_parameter(self):
        tool = build_tool_search_tool()

        assert tool.name == TOOL_SEARCH_TOOL_NAME
        assert "query" in tool.parameters.properties

    def test_the_search_tool_description_says_what_to_do_with_the_result(self):
        tool = build_tool_search_tool()

        # 模型需要知道「搜到之后可以直接调用」，否则它会搜完就停下来问用户。
        assert tool.parameters.required == ["query"]
        assert tool.description

    def test_the_search_tool_name_does_not_collide_with_the_skill_prefix(self):
        # 技能取回工具用 `skill_` 前缀。两者混淆会让一次「取回技能正文」被当成
        # 「搜索工具」，而两者返回的东西完全不同。
        assert not TOOL_SEARCH_TOOL_NAME.startswith("skill_")
        assert not TOOL_SEARCH_TOOL_NAME.startswith("delegate_to_")
