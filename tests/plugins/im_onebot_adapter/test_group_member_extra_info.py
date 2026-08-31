"""群成员的角色与头衔不能在融入时丢掉（需求 7、12）。

被融入的 OneBot 适配器项目在 `_convert_group_member_info` 里写了 `extra_info`：

    extra_info={
        'role': info.get('role'),
        'title': info.get('title'),
        'join_time': info.get('join_time'),
        'last_sent_time': info.get('last_sent_time'),
    }

本项目把两个转换函数合并成一个 `_profile_from_info`，而这四个字段在合并时**被丢掉**了。
`UserProfile.extra_info` 字段一直存在（`kirara_ai/im/profile.py:28`），但全仓零写入点。

这不是「少几个可选字段」。`role` 区分群主 / 管理员 / 普通成员，而那正是
「这条指令要不要执行」的判据：一个工作流想只让管理员触发某个动作时，
它需要的就是这一位信息。丢了它，那种工作流只能靠硬编码 QQ 号，
而硬编码的名单换个群就失效。

需求 12 明确要求「不能降低本项目原有的功能细节品质」，而融入的来源项目有这一项。

## 四条边界

1. **陌生人查询不编造群字段。** `get_stranger_info` 不返回 role/title，
   给一个全是 `None` 的字典等于宣称「这个人在这个群里没有角色」，
   而事实是「这次查询与群无关」。
2. **上游没给的键不出现。** OneBot 实现之间字段有差异；把缺失的键填成 `None`
   会让消费方无法区分「上游没报」与「上游报了空值」。
3. **不引入账号身份之外的新数据。** 这四个字段都来自上游对该群成员的公开描述，
   与已经返回的昵称、等级同一性质。
4. **全部缺失时 `extra_info` 为 `None`**，而不是空字典：后者读起来像
   「查过了，什么都没有」。
"""

from __future__ import annotations

from kirara_ai.im.profile import Gender
from kirara_ai.plugins.im_onebot_adapter.adapter import OneBotAdapter

GROUP_MEMBER = {
    "user_id": 10001,
    "nickname": "原昵称",
    "card": "群名片",
    "sex": "male",
    "age": 20,
    "level": 12,
    "avatar": "https://example.invalid/a.png",
    "role": "admin",
    "title": "技术顾问",
    "join_time": 1_600_000_000,
    "last_sent_time": 1_700_000_000,
}

STRANGER = {
    "user_id": 10002,
    "nickname": "陌生人",
    "sex": "female",
    "age": 30,
}


class TestGroupMemberDetailSurvives:
    def test_the_role_is_kept(self):
        profile = OneBotAdapter._profile_from_info(GROUP_MEMBER, "10001")

        assert profile.extra_info is not None
        assert profile.extra_info["role"] == "admin"

    def test_the_title_is_kept(self):
        profile = OneBotAdapter._profile_from_info(GROUP_MEMBER, "10001")

        assert profile.extra_info is not None
        assert profile.extra_info["title"] == "技术顾问"

    def test_the_timestamps_are_kept(self):
        profile = OneBotAdapter._profile_from_info(GROUP_MEMBER, "10001")

        assert profile.extra_info is not None
        assert profile.extra_info["join_time"] == 1_600_000_000
        assert profile.extra_info["last_sent_time"] == 1_700_000_000

    def test_the_existing_fields_are_unchanged(self):
        """既有映射一个都不能动：`card` 优先于 `nickname` 是原有行为。"""
        profile = OneBotAdapter._profile_from_info(GROUP_MEMBER, "10001")

        assert profile.user_id == "10001"
        assert profile.display_name == "群名片"
        assert profile.username == "群名片"
        assert profile.full_name == "原昵称"
        assert profile.gender is Gender.MALE
        assert profile.age == 20
        assert profile.level == 12
        assert profile.avatar_url == "https://example.invalid/a.png"


class TestStrangerInfoInventsNothing:
    def test_no_group_fields_are_fabricated(self):
        """`get_stranger_info` 与群无关；给一个全 None 的字典是在回答一个没问的问题。"""
        profile = OneBotAdapter._profile_from_info(STRANGER, "10002")

        assert profile.extra_info is None

    def test_the_stranger_fields_are_still_mapped(self):
        profile = OneBotAdapter._profile_from_info(STRANGER, "10002")

        assert profile.display_name == "陌生人"
        assert profile.gender is Gender.FEMALE
        assert profile.age == 30


class TestPartialUpstreamData:
    def test_only_the_keys_the_upstream_reported_appear(self):
        """OneBot 实现之间字段有差异；填 None 会让消费方分不清「没报」与「空值」。"""
        profile = OneBotAdapter._profile_from_info(
            {"user_id": 3, "nickname": "只有角色", "role": "owner"}, "3"
        )

        assert profile.extra_info == {"role": "owner"}

    def test_an_empty_string_title_is_treated_as_absent(self):
        """空头衔就是没有头衔；上游普遍用空串而不是省略这个键。"""
        profile = OneBotAdapter._profile_from_info(
            {"user_id": 4, "nickname": "无头衔", "role": "member", "title": ""}, "4"
        )

        assert profile.extra_info == {"role": "member"}
