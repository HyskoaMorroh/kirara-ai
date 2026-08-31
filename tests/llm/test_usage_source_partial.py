"""用量来源必须区分「全部维度都由上游回报」与「上游只报了一部分」。

需求 22.1 逐项点名四类：真实 Token、供应商返回的 Token、估算 Token、
未知 Token。当前枚举只有三个成员，理由写在 `UsageSource` 的 docstring 里：
「真实」与「供应商返回」在本项目的数据链路上没有第二个独立信源可以交叉验证，
硬拆会得到一个永远没有生产者的枚举值。

那个论证对「交叉验证」这一层成立，但它漏掉了一个**真实存在且可区分**的情形：

多数 OpenAI 兼容端点只回报 ``prompt_tokens`` / ``completion_tokens``，
不报 ``cached_tokens`` / ``cache_write_tokens``。这类响应目前与
「四个维度全部回报」一样被标成 ``provider``——于是账单页面上两种可信度
完全不同的数字看起来一模一样：

- 四维齐全：总额就是上游认定的消耗；
- 只报两维：缺失维度按 0 计价，总额是我们**补出来的**。

缓存读取的单价通常只有输入 Token 的 1/5 到 1/10，缓存写入往往更贵。
一份「缺失维度按 0」的账单在缓存密集的部署上可以系统性偏低，而页面上
没有任何迹象表明它被补过。这正是需求要求区分四类的实际后果。

因此新增 ``PROVIDER_PARTIAL``：**上游确实回报了用量，但不是全部维度**。
它与其余三个成员的处置各不相同：

- ``provider`` → 账单可直接采信；
- ``provider_partial`` → 可采信，但要知道缺失维度按 0 计；
- ``estimated`` → 不可作为账单依据；
- ``unknown`` → 连估算都做不到。

三条边界：
- **判据是「维度是否齐全」，不是「值是否为 0」。** 上游明确报了 0 是一个事实，
  没报是一个空缺；把前者也标成 partial 会让绝大多数请求挂上一个没有意义的标记。
- **旧数据仍是 ``provider``。** 这个成员是新增的，不回填历史记录——
  历史账单不能被后来的口径改写。
- **总额照常算得出来。** partial 不是错误，不该让成本变成 None：
  一个算不出来的账单比一个偏低的账单更没用。
"""

from __future__ import annotations

from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.response import (
    LLMChatResponse,
    Message,
    Usage,
    UsageSource,
)
from kirara_ai.tracing.decorator import mark_provider_usage


def _response(usage: Usage | None) -> LLMChatResponse:
    return LLMChatResponse(
        model="test-model",
        message=Message(role="assistant", content=[LLMChatTextContent(text="hi")]),
        usage=usage,
    )


class TestFourSourcesAreRepresentable:
    def test_partial_is_a_declared_member(self):
        # 没有这个成员，「上游只报了一部分」就无法表达，
        # 而它是四类里唯一一个当前被合并掉的。
        assert UsageSource.PROVIDER_PARTIAL.value == "provider_partial"

    def test_all_four_named_categories_exist(self):
        values = {member.value for member in UsageSource}
        assert {"provider", "provider_partial", "estimated", "unknown"} <= values


class TestCompleteUsageStaysProvider:
    def test_all_four_dimensions_reported_is_provider(self):
        usage = Usage(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cached_tokens=5,
            cache_write_tokens=2,
        )

        marked = mark_provider_usage(_response(usage))

        assert marked.usage is not None
        assert marked.usage.source is UsageSource.PROVIDER

    def test_explicit_zero_is_still_complete(self):
        # 上游明确报了 0 是一个事实，与「没报」是两件事。把前者也标成 partial
        # 会让绝大多数请求挂上一个没有意义的标记。
        usage = Usage(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cached_tokens=0,
            cache_write_tokens=0,
        )

        marked = mark_provider_usage(_response(usage))

        assert marked.usage is not None
        assert marked.usage.source is UsageSource.PROVIDER


class TestMissingDimensionsBecomePartial:
    def test_missing_cache_write_is_partial(self):
        # 多数 OpenAI 兼容端点的实际形态：报了缓存读取，没报缓存写入。
        usage = Usage(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
            cached_tokens=5,
        )

        marked = mark_provider_usage(_response(usage))

        assert marked.usage is not None
        assert marked.usage.source is UsageSource.PROVIDER_PARTIAL

    def test_missing_both_cache_dimensions_is_partial(self):
        usage = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30)

        marked = mark_provider_usage(_response(usage))

        assert marked.usage is not None
        assert marked.usage.source is UsageSource.PROVIDER_PARTIAL

    def test_missing_completion_tokens_is_partial(self):
        usage = Usage(prompt_tokens=10, total_tokens=10, cached_tokens=0, cache_write_tokens=0)

        marked = mark_provider_usage(_response(usage))

        assert marked.usage is not None
        assert marked.usage.source is UsageSource.PROVIDER_PARTIAL


class TestExistingBehaviourIsUnchanged:
    def test_an_already_marked_source_is_not_overwritten(self):
        usage = Usage(prompt_tokens=10, source=UsageSource.ESTIMATED)

        marked = mark_provider_usage(_response(usage))

        # 估算值不能被改写成「上游回报」：那会把一个不可计费的数字
        # 提升为账单依据。
        assert marked.usage is not None
        assert marked.usage.source is UsageSource.ESTIMATED

    def test_no_usage_at_all_stays_none(self):
        marked = mark_provider_usage(_response(None))

        assert marked.usage is None

    def test_a_usage_with_no_numbers_at_all_stays_unknown(self):
        marked = mark_provider_usage(_response(Usage()))

        # 一个字段都没有的 usage 不是「上游报了一部分」，它是「什么都没报」。
        assert marked.usage is not None
        assert marked.usage.source is UsageSource.UNKNOWN


class TestCostStillComputes:
    def test_partial_usage_is_not_treated_as_uncosted(self):
        from kirara_ai.tracing.models import LLMRequestTrace

        # partial 不是错误：一个算不出来的账单比一个偏低的账单更没用。
        # 这里只断言枚举值能落库，成本投影本身由定价测试覆盖。
        trace = LLMRequestTrace()
        trace.usage_source = UsageSource.PROVIDER_PARTIAL.value
        assert trace.usage_source == "provider_partial"
