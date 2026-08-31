"""工具的渐进披露：让模型**搜索**工具，而不是把全部 schema 塞进每次请求。

需求 8 点名了「Tool Search」。参考实现里那个开关打开的是编码 CLI 的工具渐进披露：
不把所有工具的完整定义常驻上下文，而给模型一个搜索工具，让它按需取回。

本项目已经对 **Skill** 做过同一件事（一行目录 + ``skill_<id>`` 工具，见
``skills.py``），但 **MCP 工具** 此前仍是全量注入。一台连了三个 MCP 服务器的部署
很容易有几十个工具，每个带完整的 JSON Schema，于是：

1. **成本随「工具数 × 请求数」线性增长**，其中绝大部分与当轮问题无关。
   四十个工具各 300 token，就是每一轮固定多付 12000 token。
2. **模型在一堵墙前更容易选错。** 几十个名字相近的工具
   （``read_file`` / ``read_text_file`` / ``read_multiple_files``）挤在一起时，
   选择质量下降——这一点比成本更难发现，因为它表现为「AI 变笨了」。

机制与 Skill 完全对称，因此运维只需要理解一套心智模型：

* 工具数**超过**阈值时，系统提示词里只放一行目录（名字 + 一句用途），
  完整 schema 由 ``search_tools`` 取回；
* 低于或等于阈值时**一律全量注入**——给三个工具再加一层搜索只是凭空多一轮往返。

阈值而不是布尔开关，是因为这件事的收益完全取决于工具数量：同一个开关在
「三个工具」和「四十个工具」上一个是纯损失、一个是纯收益。
"""

from __future__ import annotations

from typing import Iterable, Sequence

from kirara_ai.llm.format.request import Tool, ToolParameters

#: 搜索工具的名字。刻意不带 ``skill_`` / ``delegate_to_`` 前缀：三者返回的东西
#: 完全不同，名字撞上会让一次「取回技能正文」被当成「搜索工具」。
TOOL_SEARCH_TOOL_NAME = "search_tools"

#: 默认阈值。超过这么多个工具才切换到搜索模式。
#:
#: 取 12：常见部署里一到两个 MCP 服务器合计不超过十来个工具，那种规模下全量注入
#: 的开销可以接受，而多一轮搜索往返的代价是确定的。三个以上服务器才会明显超过它。
DEFAULT_TOOL_SEARCH_THRESHOLD = 12

#: 一次搜索最多返回多少个工具。
#:
#: 不设上限时一次宽泛搜索（例如空查询）会把刚省下的 token 一次性还回去，
#: 而且比全量注入更糟——全量至少只付一次，搜索是「先付目录、再付全量」。
DEFAULT_TOOL_SEARCH_LIMIT = 8


def should_use_tool_search(
    tools: Sequence[Tool], *, threshold: int = DEFAULT_TOOL_SEARCH_THRESHOLD
) -> bool:
    """工具数是否多到值得改用搜索。

    ``threshold <= 0`` 表示**关闭**这个特性，而不是「一律搜索」。
    一个把阈值设成 0 想关掉它的运维不该反而把它全开——那是最容易配反的一处。
    """
    if threshold <= 0:
        return False
    return len(tools) > threshold


def tool_catalog_section(tools: Sequence[Tool]) -> str:
    """把工具列表压成一段一行一个的目录，**不含**参数 schema。

    不含 schema 是这段文字存在的全部理由。带上 schema 等于既付了目录的钱、
    又付了全量的钱，比原来更贵。

    没有工具时返回空串：一段「以下是可用工具：（空）」是一句会让模型反复确认的
    废话，而且每一轮都要付费。
    """
    if not tools:
        return ""
    lines = [
        "可用工具目录（仅名称与用途）。需要某个工具的完整参数时，"
        f"先调用 `{TOOL_SEARCH_TOOL_NAME}` 按关键词取回它的定义，再调用它："
    ]
    for tool in tools:
        description = (tool.description or tool.name).strip().splitlines()
        summary = description[0] if description else tool.name
        lines.append(f"- `{tool.name}`：{summary}")
    return "\n".join(lines)


def search_tool_entries(
    tools: Iterable[Tool],
    query: str,
    *,
    limit: int = DEFAULT_TOOL_SEARCH_LIMIT,
) -> list[Tool]:
    """按关键词在名称与用途里搜索工具，返回**完整可调用**的定义。

    返回完整定义而不是名字列表：交出一个没有参数的壳子会让模型用空参数去调，
    然后拿到一个它无法理解的校验错误。

    空查询返回全部（受 ``limit`` 约束）：「我不知道该搜什么」是一个合法状态，
    返回空会把模型逼进死角——它既拿不到工具，也没有别的入口。

    无命中时返回空列表，**不猜**最接近的那个：猜一个交出去比返回空更糟，
    模型会调用一个它没有要求的工具。
    """
    candidates = list(tools)
    keyword = (query or "").strip().lower()
    if not keyword:
        return candidates[: max(0, limit)]
    matched = [
        tool
        for tool in candidates
        if keyword in tool.name.lower() or keyword in (tool.description or "").lower()
    ]
    return matched[: max(0, limit)]


def build_tool_search_tool() -> Tool:
    """构造暴露给模型的搜索工具本身。"""
    return Tool(
        name=TOOL_SEARCH_TOOL_NAME,
        description=(
            "按关键词搜索可用工具，返回匹配工具的完整参数定义。"
            "拿到定义后可以直接调用那个工具，不需要再询问用户。"
            "关键词可以是工具名的一部分或它用途里的词；留空则返回前若干个工具。"
        ),
        parameters=ToolParameters(
            properties={
                "query": {
                    "type": "string",
                    "description": "工具名或用途里的关键词；留空返回前若干个工具。",
                }
            },
            required=["query"],
            additionalProperties=False,
        ),
    )


__all__ = [
    "DEFAULT_TOOL_SEARCH_LIMIT",
    "DEFAULT_TOOL_SEARCH_THRESHOLD",
    "TOOL_SEARCH_TOOL_NAME",
    "build_tool_search_tool",
    "search_tool_entries",
    "should_use_tool_search",
    "tool_catalog_section",
]
