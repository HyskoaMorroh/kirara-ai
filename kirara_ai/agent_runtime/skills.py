"""Skill 必须能被模型**调用**，而不只是整篇塞进系统提示词（需求 10）。

cc-switch / Claude Code 的 Skill 机制是**渐进披露**：前置元数据（name +
description）常驻上下文，作为一句廉价的「我能做什么」广告；正文只在模型决定用它
的那一轮才加载。需求 10 明确要求「跟 cc switch 实现的原理是一样的」。

本项目此前只有一半：`_build_messages` 把每个已绑定 Skill 的**全文**拼进每一次请求
的 system 消息。两处后果：

1. **成本随「技能数 × 请求数」线性增长**，而其中绝大部分与当轮问题无关。
   十个 Skill 各 2000 token，就是每轮 20000 token 的固定开销。
2. **模型无法「选用」一个技能**，因为没有可调用的东西。于是「装了技能之后 AI
   会话真的因此改变行为」这件事，只能靠把全文硬塞进上下文来实现——那不是同一个
   机制，也拿不到同一种效果（模型看到一堵墙时反而更容易忽略其中的具体指令）。

判据是**能不能广告**，而不是一个开关：

- Skill 前置元数据里有 `description` → 系统提示词里只放一行目录，正文由
  `skill_<id>` 工具按需取回。
- 没有前置元数据（纯文本 Skill、旧资源）→ 无法广告，仍然整篇注入。
  行为与此前逐字节一致，不会因为升级而让既有部署的技能突然「消失」。

这条规则是单一代码路径上的条件判断，不是两套并存的模式。
"""

from __future__ import annotations

from typing import Any, Mapping, Optional, Sequence

#: 工具名前缀。与 `delegate_to_` 同一形态：模型侧不需要区分「这是技能还是工具」。
SKILL_TOOL_PREFIX = "skill_"

#: 目录行与工具描述里 description 的截断长度。
#:
#: 广告的意义在于「便宜」：一段 800 字的 description 会把渐进披露省下的成本
#: 又还回去。截断只影响广告，正文一个字都不少。
_ADVERTISEMENT_LIMIT = 400


def parse_skill_front_matter(text: str) -> dict[str, str]:
    """从 Skill 正文头部读出 `name` / `description`。

    与 `resource_sources` 里安装期那份解析**故意不共用**：安装期必须对非法内容
    报错（否则会把坏包装进注册表），而这里在**每一轮对话**上运行，
    任何异常都会让一次正常提问失败。因此这里只有「解析出来」与「解析不出来」
    两种结果，不抛异常。

    返回值里缺失的键为空串，调用方以 `description` 是否为空判断能否广告。
    """
    values = {"name": "", "description": ""}
    if not text.startswith("---"):
        return values
    lines = text.splitlines()
    end = None
    for index, line in enumerate(lines[1:], 1):
        if line.strip() == "---":
            end = index
            break
    if end is None:
        # 只有开头的 `---` 没有闭合：这不是前置元数据，是正文的一部分。
        return values
    for line in lines[1:end]:
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip()
        if key in values:
            values[key] = value.strip().strip("'\"")[:1000]
    return values


def skill_readiness_note(
    dependency_ids: Sequence[str], dependency_service: Any
) -> str:
    """技能所需的服务器命令是否真的装好了；已就绪或未知时返回空串。

    需求 10 要的是「安装在 VPS 里的插件**起作用**」。而技能正文与依赖状态此前
    互不知情：`agent-browser` 的 SKILL.md 通篇是 `agent-browser ...` 命令，
    CLI 装没装记录在 `SystemDependencyService` 里，只投影给安装界面看。
    没装时会发生这样一轮——模型照着技能写出命令、命令不存在、模型无从得知，
    于是把「我已经打开了浏览器」当成事实继续往下答。
    **没有报错，只有一个自信的假答案**，而用户看不出区别。

    三态严格区分，因为它们的代价不同：

    - **已就绪** → 一个字都不加。每句多余的话都是每轮都要付费的噪音。
    - **确认缺失** → 点名那个命令，并要求模型不要假装执行过。
    - **未知**（服务未接线、探测抛错、依赖表里没登记、``ready`` 为 ``None``）
      → 同样什么都不说。把「不知道」说成「缺失」会劝退一个本来能用的技能，
      而那是我们凭猜测造成的损失。

    任何异常都吞掉：这段代码运行在每一轮对话上，让一次正常提问因为探测后端
    出问题而失败，比不给这句提示糟得多。
    """
    if not dependency_ids or dependency_service is None:
        return ""
    getter = getattr(dependency_service, "get_dependency", None)
    if not callable(getter):
        return ""
    missing: list[str] = []
    for dependency_id in dependency_ids:
        try:
            state = getter(dependency_id)
        except Exception:
            # 未登记的 id、探测后端异常——都是「不知道」，不是「缺失」。
            continue
        if not isinstance(state, Mapping):
            continue
        # `ready is False` 才算确认缺失；`None` 是「还没探测过」。
        if state.get("ready") is False:
            missing.append(str(state.get("dependency_id") or dependency_id))
    if not missing:
        return ""
    names = "、".join(missing)
    return (
        f"注意：本技能依赖的服务器组件当前不可用（{names}）。"
        "不要假装已经执行过其中的命令；如确实需要，请说明该组件尚未安装。"
    )


def skill_advertisement(
    resource_id: str, content: str, *, readiness_note: str = ""
) -> Optional[dict[str, str]]:
    """把一个 Skill 的正文压成一句广告；无法广告时返回 ``None``。

    ``None`` 的含义是「这个 Skill 没有可用的 description」，调用方据此回落到
    整篇注入——那是它唯一还能产生效果的方式。

    ``readiness_note`` 由 {@link skill_readiness_note} 产出，空串表示「无需多言」。
    """
    metadata = parse_skill_front_matter(content)
    description = metadata["description"].strip()
    if not description:
        return None
    return {
        "resource_id": resource_id,
        # name 缺失时用 resource_id：模型至少要有个能对上工具名的称呼。
        "name": metadata["name"].strip() or resource_id,
        "description": description[:_ADVERTISEMENT_LIMIT],
        "readiness_note": readiness_note,
    }


def build_skill_tools(advertisements: Sequence[Mapping[str, str]]) -> list[Any]:
    """为可广告的 Skill 生成调用工具。

    返回 `kirara_ai.llm.format.request.Tool`，与 MCP 工具、队友委派同一形态。

    **不带参数**：这个工具做的是「把这篇技能的正文取回来」，没有可调的旋钮。
    给它加一个自由文本参数只会诱导模型编造出一个我们并不使用的值，
    白花一轮 token。
    """
    from kirara_ai.llm.format.request import Tool, ToolParameters

    tools: list[Any] = []
    for advertisement in advertisements:
        resource_id = str(advertisement.get("resource_id", "")).strip()
        if not resource_id:
            continue
        label = str(advertisement.get("name") or resource_id)
        description = str(advertisement.get("description") or "")
        # 就绪提示必须进**工具描述**：那是模型决定要不要调用时唯一会读的地方。
        # 只写在目录行里不够——模型可能直接照着目录去调，然后拿到一份它无法
        # 执行的说明，却以为自己执行了。就绪时这里是空串，输出与没有这个
        # 特性时逐字节一致。
        readiness = str(advertisement.get("readiness_note") or "").strip()
        suffix = f" {readiness}" if readiness else ""
        tools.append(
            Tool(
                name=f"{SKILL_TOOL_PREFIX}{resource_id}",
                description=(
                    f"载入技能「{label}」的完整操作说明后再作答。{description} "
                    "当本轮任务与该说明相关时调用；返回的是要你遵循的指令文本，"
                    f"不是给用户看的答案。{suffix}"
                ),
                parameters=ToolParameters(properties={}, required=[]),
            )
        )
    return tools


def skill_catalog_section(advertisements: Sequence[Mapping[str, str]]) -> str:
    """系统提示词里的技能目录。

    只有名字与用途，没有正文——这正是渐进披露省下成本的地方。
    同时说清「要用就得先调工具」，否则模型会照着一行目录去猜正文内容。
    """
    if not advertisements:
        return ""
    lines = ["可用技能（需要时先调用对应的 skill_ 工具载入完整说明，再据此作答）："]
    for advertisement in advertisements:
        resource_id = str(advertisement.get("resource_id", ""))
        label = str(advertisement.get("name") or resource_id)
        description = str(advertisement.get("description") or "")
        readiness = str(advertisement.get("readiness_note") or "").strip()
        suffix = f" {readiness}" if readiness else ""
        lines.append(
            f"- {label}（{SKILL_TOOL_PREFIX}{resource_id}）：{description}{suffix}"
        )
    return "\n".join(lines)
