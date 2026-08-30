"""Skill 要能被模型调用，而不只是整篇塞进系统提示词（需求 10）。

需求 10 要求「跟 cc switch 实现的原理是一样的」。cc-switch / Claude Code 的 Skill
是**渐进披露**：前置元数据（name + description）常驻上下文作为一句廉价广告，
正文只在模型决定用它的那一轮才载入。

此前 `_build_messages` 把每个已绑定 Skill 的**全文**拼进每一次请求的 system 消息：

- 成本随「技能数 × 请求数」线性增长，且其中绝大部分与当轮问题无关；
- 模型无法「选用」一个技能，因为没有可调用的东西。

判据是**能不能广告**（前置元数据里有没有 `description`），不是一个新开关：
没有前置元数据的 Skill 无法广告，仍整篇注入——行为与此前逐字节一致，
不会因为升级让既有部署的技能突然失效。
"""

from __future__ import annotations

import pytest

from kirara_ai.agent_runtime.skills import (
    SKILL_TOOL_PREFIX,
    build_skill_tools,
    parse_skill_front_matter,
    skill_advertisement,
    skill_catalog_section,
)

SKILL_WITH_FRONT_MATTER = """---
name: PDF 表格提取
description: 从 PDF 里抽取表格并转成 CSV，处理跨页与合并单元格
---

# 步骤

1. 先用 pdfplumber 打开
2. 逐页调用 extract_tables
"""

SKILL_WITHOUT_FRONT_MATTER = """# 内部约定

回复公司邮件时一律使用「您」，签名用中文全名。
"""


class TestFrontMatterParsing:
    def test_reads_name_and_description(self):
        values = parse_skill_front_matter(SKILL_WITH_FRONT_MATTER)

        assert values["name"] == "PDF 表格提取"
        assert "CSV" in values["description"]

    def test_a_skill_without_front_matter_yields_empty_values(self):
        values = parse_skill_front_matter(SKILL_WITHOUT_FRONT_MATTER)

        assert values == {"name": "", "description": ""}

    def test_an_unclosed_delimiter_is_not_front_matter(self):
        """只有开头的 `---` 没有闭合时，那是正文的一部分，不是元数据。

        把它当元数据解析会把整篇正文吃进 description。
        """
        values = parse_skill_front_matter("---\nname: x\n\n# 正文开始")

        assert values == {"name": "", "description": ""}

    def test_malformed_content_does_not_raise(self):
        """这段解析在**每一轮对话**上运行，抛异常会让一次正常提问失败。

        安装期那份解析必须对非法内容报错（否则坏包进注册表），
        这里的要求正相反，因此两处故意不共用。
        """
        for text in ("", "---", "---\n---", "---\n: 没有键\n---\n正文"):
            assert parse_skill_front_matter(text) is not None

    def test_quotes_are_stripped(self):
        values = parse_skill_front_matter('---\nname: "带引号"\ndescription: \'也带\'\n---\n')

        assert values["name"] == "带引号"
        assert values["description"] == "也带"


class TestAdvertisement:
    def test_a_skill_with_a_description_can_be_advertised(self):
        advertisement = skill_advertisement("skill.pdf", SKILL_WITH_FRONT_MATTER)

        assert advertisement is not None
        assert advertisement["resource_id"] == "skill.pdf"
        assert advertisement["name"] == "PDF 表格提取"

    def test_a_skill_without_a_description_cannot(self):
        """`None` 是「无法广告」，调用方据此回落到整篇注入。

        那是这类 Skill 唯一还能产生效果的方式。
        """
        assert skill_advertisement("skill.plain", SKILL_WITHOUT_FRONT_MATTER) is None

    def test_a_name_only_skill_still_cannot_be_advertised(self):
        """只有 name 没有 description 时广告不成立：模型无从判断何时该用它。"""
        assert skill_advertisement("skill.x", "---\nname: 只有名字\n---\n正文") is None

    def test_a_missing_name_falls_back_to_the_resource_id(self):
        advertisement = skill_advertisement(
            "skill.abc", "---\ndescription: 有用途没名字\n---\n正文"
        )

        assert advertisement is not None
        assert advertisement["name"] == "skill.abc"

    def test_a_very_long_description_is_truncated(self):
        """广告的意义在于便宜；一段超长 description 会把省下的成本还回去。"""
        advertisement = skill_advertisement(
            "skill.long", f"---\ndescription: {'长' * 2000}\n---\n正文"
        )

        assert advertisement is not None
        assert len(advertisement["description"]) <= 400


class TestSkillTools:
    def test_one_tool_per_advertised_skill(self):
        tools = build_skill_tools(
            [
                {"resource_id": "skill.a", "name": "A", "description": "做 A"},
                {"resource_id": "skill.b", "name": "B", "description": "做 B"},
            ]
        )

        assert [tool.name for tool in tools] == [
            f"{SKILL_TOOL_PREFIX}skill.a",
            f"{SKILL_TOOL_PREFIX}skill.b",
        ]

    def test_the_tool_takes_no_arguments(self):
        """这个工具没有可调的旋钮：它只是「把正文取回来」。

        加一个自由文本参数只会诱导模型编造出一个我们并不使用的值，白花一轮 token。
        """
        tool = build_skill_tools([{"resource_id": "skill.a", "name": "A", "description": "做 A"}])[0]

        assert tool.parameters.required == []
        assert tool.parameters.properties == {}

    def test_the_description_says_it_returns_instructions_not_an_answer(self):
        """不说清这一点，模型会把技能正文当成答案直接转发给用户。"""
        tool = build_skill_tools([{"resource_id": "skill.a", "name": "A", "description": "做 A"}])[0]

        assert "指令" in tool.description
        assert "做 A" in tool.description

    def test_an_entry_without_a_resource_id_is_skipped(self):
        # 在工具列表里放一个名字为空的工具，等于让模型撞一次墙再重试。
        assert build_skill_tools([{"resource_id": "", "name": "x", "description": "y"}]) == []

    def test_no_advertisements_means_no_tools(self):
        assert build_skill_tools([]) == []


class TestCatalogSection:
    def test_the_catalog_lists_names_and_tool_names(self):
        section = skill_catalog_section(
            [{"resource_id": "skill.a", "name": "报表整理", "description": "把周报合并"}]
        )

        assert "报表整理" in section
        assert f"{SKILL_TOOL_PREFIX}skill.a" in section
        assert "把周报合并" in section

    def test_the_catalog_says_the_tool_must_be_called_first(self):
        """只给一行目录而不说要先调工具，模型会照着目录去猜正文内容。"""
        section = skill_catalog_section(
            [{"resource_id": "skill.a", "name": "A", "description": "做 A"}]
        )

        assert "载入" in section

    def test_the_catalog_does_not_contain_the_skill_body(self):
        """目录里出现正文就等于没有渐进披露——那是这次改动的全部意义。"""
        section = skill_catalog_section(
            [
                {
                    "resource_id": "skill.pdf",
                    "name": "PDF 表格提取",
                    "description": "从 PDF 里抽取表格",
                }
            ]
        )

        assert "pdfplumber" not in section
        assert "extract_tables" not in section

    def test_an_empty_catalog_is_an_empty_string(self):
        # 空目录必须是空串而不是一句「没有可用技能」：后者是每轮都付费的噪声。
        assert skill_catalog_section([]) == ""
