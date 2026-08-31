"""流式用量必须按字段合并，不能被后一个分片整体覆盖。

流式响应把用量拆在多个分片里回报是常见形态：OpenAI 兼容端点里
`prompt_tokens` 往往只在第一个带 usage 的分片出现（提示词在请求时就已确定），
而 `completion_tokens` / `total_tokens` 要等生成结束才有值。

聚合时若写成 `usage = chunk.usage`，最后一个带 usage 的分片会整体替换先前那份。
表现是**账单里的输入 Token 变成「未上报」**，而这条请求明明报过输入用量：

* 成本一栏因为缺输入维度而偏低；
* `usage_source` 仍是 `provider`（上游确实报了用量），所以界面上不会有任何
  「这条数据不完整」的迹象——它看起来是一条正常的、便宜的请求。

字段级合并的口径：后到的非 None 值优先（它更新），后到的 None 不覆盖已有值
（None 是「这个分片没提」，不是「这个值是空」）。这与本项目在别处反复坚持的
「`None` 与 `0` 是两件事」同一条规则：把「没提」当成「归零」会造出假数字。
"""

from __future__ import annotations

from kirara_ai.agent_runtime.executor import merge_stream_usage
from kirara_ai.llm.format.response import Usage, UsageSource


def test_later_chunk_without_prompt_tokens_keeps_the_earlier_value():
    first = Usage(prompt_tokens=1200, source=UsageSource.PROVIDER)
    final = Usage(completion_tokens=340, total_tokens=1540, source=UsageSource.PROVIDER)

    merged = merge_stream_usage(merge_stream_usage(None, first), final)

    assert merged is not None
    # 这一行是回归点：覆盖式实现会把它变成 None。
    assert merged.prompt_tokens == 1200
    assert merged.completion_tokens == 340
    assert merged.total_tokens == 1540


def test_later_value_wins_when_both_report_the_same_dimension():
    first = Usage(completion_tokens=10, source=UsageSource.PROVIDER)
    final = Usage(completion_tokens=340, source=UsageSource.PROVIDER)

    merged = merge_stream_usage(merge_stream_usage(None, first), final)

    assert merged is not None
    # 增量计数的上游会在每个分片回报「到目前为止」的值，后到的那份才是最终值。
    assert merged.completion_tokens == 340


def test_zero_is_a_real_value_and_overwrites_a_previous_number():
    first = Usage(cached_tokens=64, source=UsageSource.PROVIDER)
    final = Usage(cached_tokens=0, source=UsageSource.PROVIDER)

    merged = merge_stream_usage(merge_stream_usage(None, first), final)

    assert merged is not None
    # 0 是「报了，确实没有」；把它当成缺失会保留一个过时的非零值。
    assert merged.cached_tokens == 0


def test_cache_dimensions_merge_independently():
    first = Usage(prompt_tokens=900, cached_tokens=800, source=UsageSource.PROVIDER)
    final = Usage(
        completion_tokens=120,
        cache_write_tokens=100,
        total_tokens=1020,
        source=UsageSource.PROVIDER,
    )

    merged = merge_stream_usage(merge_stream_usage(None, first), final)

    assert merged is not None
    assert (merged.prompt_tokens, merged.cached_tokens) == (900, 800)
    assert (merged.completion_tokens, merged.cache_write_tokens) == (120, 100)
    assert merged.total_tokens == 1020


def test_provider_source_is_not_downgraded_by_a_later_unknown_chunk():
    first = Usage(prompt_tokens=500, source=UsageSource.PROVIDER)
    # 一个只带 finish_reason 的收尾分片可能携带默认构造的 usage。
    final = Usage(completion_tokens=42)

    merged = merge_stream_usage(merge_stream_usage(None, first), final)

    assert merged is not None
    assert merged.prompt_tokens == 500
    # 上游报过用量这件事不该被一个没写 source 的分片抹掉：
    # 降级成 unknown 会让这条请求在统计里从「有据可依」掉进「不明」。
    assert merged.source is UsageSource.PROVIDER


def test_first_usage_is_taken_as_is():
    only = Usage(prompt_tokens=7, source=UsageSource.PROVIDER)

    assert merge_stream_usage(None, only) is not None
    assert merge_stream_usage(None, only).prompt_tokens == 7


def test_none_incoming_leaves_the_accumulator_untouched():
    accumulated = Usage(prompt_tokens=7, source=UsageSource.PROVIDER)

    assert merge_stream_usage(accumulated, None) is accumulated
    assert merge_stream_usage(None, None) is None
