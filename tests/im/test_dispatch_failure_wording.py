"""同一个失败，四个渠道必须说同一套话。

发现过程
------
用户在 Telegram 发一句话，收到的是：

    Workflow execution failed, please try again later:
    No Agent is configured for this channel identity

两个问题叠在一起：那句话是全项目**唯一**暴露给用户的英文报错，而它的内容
（「没有为这个渠道身份配置 Agent」）对一个不知道 Agent 是什么的人零信息。

而同一个失败在另外三个渠道上呈现完全不同：

* 企业微信有一套按错误类型分派的中文说明（超时 / 认证 / 限流 / 网络）；
* OneBot 与 QQ 官方机器人**只记日志**——用户那侧完全静默。

静默是三者里最糟的：用户无法区分「机器人挂了」与「我的消息没发出去」，
只会反复重发，而每次重发都会再走一遍那条失败的链路。

这组测试锁住的边界
----------------
1. 四个渠道用同一个函数，因此说的是同一套话（不再各写一份互相漂移）。
2. 按「用户接下来该做什么」分类，而不是按异常继承关系——
   「认证失败」与「参数错误」都是 4xx，但一个去改凭据、一个去改请求。
3. **配置缺失不说「稍后再试」**：重试永远不会成功。
4. 原始异常文本必须保留（截断后）：运维拿它去搜日志。
5. 取消不是失败：正常停机时不该给每个在途会话都发一条错误。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from kirara_ai.im.dispatch_failure import describe_dispatch_failure
from kirara_ai.workflow.core.dispatch.exceptions import AgentConfigurationNotFound

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: 四个 IM 适配器。它们必须都用共享的失败描述。
_ADAPTERS = (
    "kirara_ai/plugins/im_telegram_adapter/adapter.py",
    "kirara_ai/plugins/im_wecom_adapter/adapter.py",
    "kirara_ai/plugins/im_onebot_adapter/adapter.py",
    "kirara_ai/plugins/im_qqbot_adapter/adapter.py",
)


class TestEveryChannelUsesTheSameWording:
    @pytest.mark.parametrize("relative", _ADAPTERS)
    def test_the_adapter_uses_the_shared_description(self, relative: str):
        source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        assert "describe_dispatch_failure" in source, (
            f"{relative} 没有使用共享的失败描述——它会漂移成第五种说法"
        )

    def test_no_adapter_still_emits_the_english_prefix(self):
        """那句英文是全项目唯一暴露给用户的英文报错。"""
        for relative in _ADAPTERS:
            source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
            assert "Workflow execution failed, please try again later" not in source, relative

    def test_wecom_no_longer_keeps_its_own_classification(self):
        """企业微信那套分类已经搬进共享模块，原地不该再留一份。

        留着会让「改一处文案」变成「两处真相」，而漂移之后没有症状——
        两条分支各自看都对。
        """
        source = (REPOSITORY_ROOT / _ADAPTERS[1]).read_text(encoding="utf-8")
        # 原实现的判据字符串。它们现在只应存在于共享模块里。
        assert 'if "524" in error_message' not in source
        assert "error_message.lower()" not in source


class TestItClassifiesByWhatTheUserShouldDoNext:
    def test_a_missing_agent_does_not_say_try_again_later(self):
        """配置缺失时重试永远不会成功。

        说「稍后再试」会让用户在一个必然失败的动作上反复尝试，
        而真正该做的是去建一个 Agent。
        """
        text = describe_dispatch_failure(
            AgentConfigurationNotFound("没有可用的 Agent：请在「Agent 管理」里新建一个")
        )

        assert "稍后" not in text
        assert "Agent" in text

    def test_a_timeout_says_try_again(self):
        text = describe_dispatch_failure(RuntimeError("HTTP 524 upstream timed out"))

        assert "超时" in text
        assert "稍后" in text

    def test_an_auth_failure_points_at_the_credential(self):
        """401/403 要指向凭据，而不是笼统的「失败」。"""
        text = describe_dispatch_failure(RuntimeError("401 Unauthorized"))

        assert "认证" in text
        assert "密钥" in text or "凭据" in text

    def test_rate_limiting_is_distinguished_from_a_timeout(self):
        """429 与超时的处置不同：前者降频，后者查上游。"""
        text = describe_dispatch_failure(RuntimeError("429 Too Many Requests"))

        assert "频繁" in text or "速率" in text
        assert "超时" not in text

    def test_a_network_error_points_at_connectivity(self):
        text = describe_dispatch_failure(OSError("connection refused"))

        assert "网络" in text

    def test_an_unknown_failure_still_says_something_useful(self):
        """没有匹配到分类时，至少说清「处理失败」并带上原始信息。"""
        text = describe_dispatch_failure(ValueError("something very specific broke"))

        assert "失败" in text
        assert "something very specific broke" in text


class TestTheOriginalDetailSurvives:
    def test_the_detail_is_included_for_diagnosis(self):
        """运维拿原始文本去搜日志。一句纯粹的「请稍后重试」会把
        可诊断的失败变成不可诊断的。
        """
        text = describe_dispatch_failure(RuntimeError("524 from provider-alpha"))

        assert "provider-alpha" in text

    def test_a_huge_detail_is_truncated(self):
        """上游有时把整个 HTML 错误页塞进异常字符串，那会撑爆一条 IM 消息。"""
        text = describe_dispatch_failure(RuntimeError("x" * 5000))

        assert len(text) < 1000

    def test_an_empty_message_does_not_produce_a_dangling_colon(self):
        """空异常消息不能得到「消息处理失败：」这种半句话。"""
        text = describe_dispatch_failure(ValueError())

        assert text.strip().endswith("ValueError") or "ValueError" in text
        assert not text.rstrip().endswith("：")


class TestCancellationIsNotAFailure:
    @pytest.mark.parametrize(
        "relative",
        (
            "kirara_ai/plugins/im_onebot_adapter/adapter.py",
            "kirara_ai/plugins/im_qqbot_adapter/adapter.py",
        ),
    )
    def test_cancellation_is_handled_before_the_generic_branch(self, relative: str):
        """正常停机时不该给每个在途会话都发一条「处理失败」。

        `asyncio.CancelledError` 继承 `BaseException`，所以它必须在
        `except BaseException` **之前**被接住——顺序反了就会给用户发错误。
        """
        source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        cancelled_at = source.find("except asyncio.CancelledError")
        generic_at = source.find("except BaseException as exc")

        assert cancelled_at != -1, f"{relative} 没有单独处理取消"
        assert generic_at != -1, f"{relative} 没有通用失败分支"
        assert cancelled_at < generic_at, (
            f"{relative} 的取消分支在通用分支之后，停机时会给用户发错误"
        )


class TestTheFailureReplyNeverMasksTheOriginalError:
    @pytest.mark.parametrize(
        "relative",
        (
            "kirara_ai/plugins/im_onebot_adapter/adapter.py",
            "kirara_ai/plugins/im_qqbot_adapter/adapter.py",
        ),
    )
    def test_the_send_failure_is_caught_and_the_original_reraised(self, relative: str):
        """发送提示本身失败时，不能吞掉原始异常——原因在那里面。"""
        source = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        tail = source[source.find("except BaseException as exc") :]

        assert "except Exception:" in tail, "发送失败没有被单独接住"
        assert "raise" in tail, "原始异常必须继续上抛"
