"""被动回复只能发一条时，不能把丢掉的页数记成成功（需求 19.4）。

19.4 明确要求「保证顺序稳定、**全部发送**、失败可记录」。企业微信未开通主动回复
能力时，上游返回 `48001`，适配器退回被动回复 API——那个 API **只能回一条消息**。
此前的处理是：把第 1 页作为回复交出去，然后记一条
`send_succeeded(delivery_mode="passive_reply")`。

于是一条 4 页的回复变成：用户收到「第 1 页 / 共 4 页」，然后什么都没有；
而投递耗时看板上这一轮是**成功**。这是本轮审计里最确定的一处真实数据丢失——
不是「排版不好看」，是内容没了，且系统认为没问题。

## 判据

**能发多少是平台的限制，如实说出来是我们的责任。** 三件事：

1. **告诉用户还有几页。** 在那唯一一条消息的末尾追加一句说明，
   而不是让他盯着「共 4 页」等剩下三页。
2. **阶段不能记成完全成功。** 记 `send_succeeded` 但带上被丢弃的页数，
   让「上周二那批回复为什么用户说看不全」在事后可查。
3. **日志说清代价。** 现有那句「此模式下只能回复一条消息」是对的，
   但没说这次实际丢了几页。

刻意不做的：**不把剩余页塞进那一条**。被动回复 API 的长度上限与主动发送一致
（`split_long_message` 已经按它切过），硬拼回去只会让整条被上游拒收——
那时用户连第 1 页都收不到，比现在更糟。
"""

from __future__ import annotations

from kirara_ai.im.text_render import PAGE_LABEL_PATTERN
from kirara_ai.plugins.im_wecom_adapter.adapter import (
    truncated_passive_reply,
)


class TestTheNoticeIsAppended:
    def test_a_single_page_reply_is_unchanged(self):
        """一页装得下时没有任何损失，不该多出一句解释。"""
        assert truncated_passive_reply("完整回复", dropped_pages=0) == "完整回复"

    def test_a_multi_page_reply_says_how_many_pages_were_dropped(self):
        rendered = truncated_passive_reply("第一页正文", dropped_pages=3)

        assert "第一页正文" in rendered
        assert "3" in rendered

    def test_the_notice_explains_why(self):
        """不解释的话用户会以为是故障，然后去重发同一个问题。"""
        rendered = truncated_passive_reply("第一页正文", dropped_pages=3)

        assert "被动回复" in rendered or "主动回复" in rendered

    def test_the_notice_tells_the_user_what_to_do(self):
        rendered = truncated_passive_reply("第一页正文", dropped_pages=3)

        # 用户能自己做的事：把问题问得更窄，或分次问。
        assert "分次" in rendered or "缩小" in rendered

    def test_the_notice_is_appended_not_prepended(self):
        """正文必须先出现：把说明放在最前面会推走用户真正要读的内容。"""
        rendered = truncated_passive_reply("第一页正文", dropped_pages=2)

        assert rendered.startswith("第一页正文")

    def test_a_negative_count_is_treated_as_none(self):
        assert truncated_passive_reply("正文", dropped_pages=-1) == "正文"


class TestTheStageRecordsTheLoss:
    def test_the_adapter_records_dropped_pages(self):
        """`send_succeeded` 不能只说成功；丢了几页必须留在时间线上。"""
        import inspect

        from kirara_ai.plugins.im_wecom_adapter import adapter as adapter_module

        source = inspect.getsource(adapter_module.WecomAdapter.send_message)

        assert "dropped_pages" in source

    def test_the_adapter_appends_the_notice(self):
        import inspect

        from kirara_ai.plugins.im_wecom_adapter import adapter as adapter_module

        source = inspect.getsource(adapter_module.WecomAdapter.send_message)

        assert "truncated_passive_reply" in source

    def test_the_warning_says_how_many_pages_are_lost(self):
        import inspect

        from kirara_ai.plugins.im_wecom_adapter import adapter as adapter_module

        source = inspect.getsource(adapter_module.WecomAdapter.send_message)

        # 现有那句「只能回复一条消息」是对的，但没说这次丢了几页。
        assert "dropped" in source or "丢弃" in source


class TestTheRemainingPagesAreNotCrammedBack:
    def test_the_notice_does_not_carry_other_pages(self):
        """硬拼回去会让整条被上游拒收，那时连第 1 页都收不到。"""
        rendered = truncated_passive_reply("第一页正文", dropped_pages=3)

        # 只应出现追加的那句说明，不应出现第二个页码标记。
        assert len(PAGE_LABEL_PATTERN.findall(rendered)) == 0
