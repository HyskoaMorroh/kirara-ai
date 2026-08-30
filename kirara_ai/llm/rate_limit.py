"""上游限额余量：从响应头里读「离上限还有多远」（需求 9）。

cc-switch 的额度面板回答「这个上游还剩多少可用」，读的是各家订阅计划的专有接口。
本项目不能照搬那些接口，但同一个用户意图有确切落点：**上游在每个响应里就带着
限额余量**。此前这些头被完整丢弃——适配器里 `response.headers` 零次读取。

丢掉它们的后果不是少一个图表，而是**限流只能事后发现**：请求开始报 429 才知道
撞了上限，而那时排队与重试已经在发生。余量是唯一能在撞上之前给出信号的东西。

覆盖的两套命名（其余家多为这两套的变体）：

* OpenAI 风格：``x-ratelimit-{limit,remaining,reset}-{requests,tokens}``，
  reset 是 ``12s`` / ``6m0s`` 这样的时长串；
* Anthropic 风格：``anthropic-ratelimit-{requests,tokens}-{limit,remaining}``。

三条边界写进实现，而不是留给调用方自觉：

* **缺头是 ``None``，不是 0。** 很多兼容端点根本不返回这些头。0 表示「余量用完」，
  是最该报警的状态；把「没上报」显示成 0 会造出一个不存在的紧急情况。
* **不猜上限。** 只有 remaining 没有 limit 时不反推百分比：百分比需要分母，
  编一个分母会得到一个看起来精确的错数字。
* **解析失败不影响请求。** 限额头是上游给的，不能假定可解析。一个解析异常会让
  整条本已成功的请求失败——那比少一个数字严重得多。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional

#: OpenAI 风格头名 → 快照字段。
_OPENAI_HEADERS = {
    "x-ratelimit-limit-requests": "limit_requests",
    "x-ratelimit-remaining-requests": "remaining_requests",
    "x-ratelimit-limit-tokens": "limit_tokens",
    "x-ratelimit-remaining-tokens": "remaining_tokens",
}

#: Anthropic 风格头名 → 快照字段。字段顺序与 OpenAI 相反（资源在前、量在后）。
_ANTHROPIC_HEADERS = {
    "anthropic-ratelimit-requests-limit": "limit_requests",
    "anthropic-ratelimit-requests-remaining": "remaining_requests",
    "anthropic-ratelimit-tokens-limit": "limit_tokens",
    "anthropic-ratelimit-tokens-remaining": "remaining_tokens",
}

#: 重置时间头名 → 快照字段。
_RESET_HEADERS = {
    "x-ratelimit-reset-requests": "reset_requests_seconds",
    "x-ratelimit-reset-tokens": "reset_tokens_seconds",
}

#: 时长串的单位换算。``ms`` 必须排在 ``m`` 之前，否则 ``250ms`` 会被读成 250 分钟。
_DURATION_UNITS = (
    ("ms", 0.001),
    ("h", 3600.0),
    ("m", 60.0),
    ("s", 1.0),
)

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ms|[hms])")


def _parse_int(raw: Any) -> Optional[int]:
    """宽松解析整数；不可解析时返回 ``None`` 而不是抛错。

    上游给的值不能假定可解析，而一个解析异常会让整条本已成功的请求失败。
    """
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _parse_duration_seconds(raw: Any) -> Optional[float]:
    """解析 ``12s`` / ``6m0s`` / ``250ms`` / ``2h`` 这类时长串，返回秒。

    也接受裸数字（按秒），因为部分实现直接给整数秒。
    """
    if raw is None:
        return None
    text = str(raw).strip().lower()
    if not text:
        return None
    try:
        # 裸数字按秒。放在正则之前：`re` 分支要求带单位。
        return float(text)
    except ValueError:
        pass
    total = 0.0
    matched = False
    for value, unit in _DURATION_PART.findall(text):
        factor = dict(_DURATION_UNITS).get(unit)
        if factor is None:
            continue
        try:
            total += float(value) * factor
        except ValueError:
            continue
        matched = True
    return total if matched else None


@dataclass(frozen=True)
class RateLimitSnapshot:
    """一次响应里上游报告的限额余量。

    每个字段都可以是 ``None``——那表示**上游没报这一项**，与「这一项是 0」
    是两件不同的事，且后者才是要报警的。
    """

    limit_requests: Optional[int] = None
    remaining_requests: Optional[int] = None
    limit_tokens: Optional[int] = None
    remaining_tokens: Optional[int] = None
    reset_requests_seconds: Optional[float] = None
    reset_tokens_seconds: Optional[float] = None
    retry_after_seconds: Optional[float] = None

    @staticmethod
    def _headroom(limit: Optional[int], remaining: Optional[int]) -> Optional[float]:
        """余量比例。缺任一半或上限为 0 时返回 ``None``。

        只有 remaining 时不反推：百分比需要分母，编一个分母会得到一个
        看起来精确的错数字。
        """
        if limit is None or remaining is None or limit <= 0:
            return None
        return max(0.0, min(1.0, remaining / limit))

    @property
    def request_headroom(self) -> Optional[float]:
        return self._headroom(self.limit_requests, self.remaining_requests)

    @property
    def token_headroom(self) -> Optional[float]:
        """与 `request_headroom` 分开。

        两者会分别见底，且处置不同：请求数见底要降频，Token 见底要缩短上下文。
        合成一个数就分不开，而分不开时任何处置都是猜。
        """
        return self._headroom(self.limit_tokens, self.remaining_tokens)

    def __bool__(self) -> bool:
        """全 None 的快照不该被当成「上游报了限额」。"""
        return any(
            value is not None
            for value in (
                self.limit_requests,
                self.remaining_requests,
                self.limit_tokens,
                self.remaining_tokens,
                self.reset_requests_seconds,
                self.reset_tokens_seconds,
                self.retry_after_seconds,
            )
        )

    def to_dict(self) -> dict[str, Any]:
        """序列化。派生值缺依据时也是 ``None``，不能填 0——0 会被读成「已用尽」。"""
        return {
            "limit_requests": self.limit_requests,
            "remaining_requests": self.remaining_requests,
            "limit_tokens": self.limit_tokens,
            "remaining_tokens": self.remaining_tokens,
            "reset_requests_seconds": self.reset_requests_seconds,
            "reset_tokens_seconds": self.reset_tokens_seconds,
            "retry_after_seconds": self.retry_after_seconds,
            "request_headroom": self.request_headroom,
            "token_headroom": self.token_headroom,
        }


def parse_rate_limit_headers(
    headers: Optional[Mapping[str, Any]],
) -> Optional[RateLimitSnapshot]:
    """从响应头读出限额快照；一项都没有时返回 ``None``。

    返回 ``None`` 而不是一个全 ``None`` 的快照，是为了让调用方能区分
    「上游不报限额」与「上游报了但余量为 0」。后者才该触发降频。
    """
    if not headers:
        return None
    # HTTP 头大小写不敏感，而 `dict` 是敏感的；`requests` 的 `headers` 恰好
    # 不敏感，但这个函数也要能吃普通 dict（测试与其他传输层）。
    lowered = {str(key).lower(): value for key, value in headers.items()}

    fields: dict[str, Any] = {}
    for source in (_OPENAI_HEADERS, _ANTHROPIC_HEADERS):
        for header, field in source.items():
            if header not in lowered:
                continue
            parsed = _parse_int(lowered[header])
            # 两套命名同时出现时（少见但可能，例如中转站转发原头），
            # 先到的那套不被后到的 None 覆盖。
            if parsed is not None or field not in fields:
                fields.setdefault(field, parsed)

    for header, field in _RESET_HEADERS.items():
        if header in lowered:
            fields[field] = _parse_duration_seconds(lowered[header])

    if "retry-after" in lowered:
        fields["retry_after_seconds"] = _parse_duration_seconds(lowered["retry-after"])

    if not fields:
        return None
    snapshot = RateLimitSnapshot(**fields)
    # 头存在但值全部不可解析时，仍返回快照：调用方由此知道「上游报了，
    # 但我们读不懂」，这与「上游根本不报」是不同的诊断。
    return snapshot
