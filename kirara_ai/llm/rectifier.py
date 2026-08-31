"""请求整流器：上游因参数约束拒绝时，按白名单修一次再重试（需求 8）。

需求 8 最后一句点名「如果整流器能够一起融入进来到本项目最优」。参考实现里
它是两个专用模块（thinking 整流与 thinking 预算整流）：
Anthropic 拒绝请求时，按错误文本判断是哪一类参数违规，改掉那一处再重试一次。

它修的不是「我们猜错了」，而是**同一个 API 在不同模型上的约束不同**：

- `thinking.budget_tokens` 有下限（1024）且必须小于 `max_tokens`。上下文里带
  `reasoning_effort` 时预算按比例换算，某些模型的 `max_tokens` 上限较低，
  换算结果就会踩到「budget 必须小于 max_tokens」这条线。
- 多轮对话里回传上一轮的 `thinking` block 时带着 `signature`；换了模型或
  换了供应商之后签名不再有效，上游报 `Invalid 'signature' in 'thinking' block`。
- 不支持图片的模型收到图片块会直接拒绝整个请求。

三者的共同点是**改一处就能成功，而不改就必然失败**。没有整流器时的表现是一次
硬失败：用户看到「请求失败」，而真正的原因（预算与上限的关系、一个过期签名、
一张图）既不在错误里说清，也不是用户能自己改的。

## 边界

这里**不做模糊自动改写**。整流只在三条同时成立时发生：

1. 上游确实拒绝了这次请求（有真实错误响应），不是我们预判的；
2. 错误文本命中某一条整流器的**特征串白名单**；
3. 该整流器与总开关都启用。

且**每类整流最多应用一次**：修完重试一次仍失败就把原始错误抛出去。反复整流会
把「参数错」变成「一直在转」，而后者更难查——日志里全是重试，没有一条说明原因。

整流会记录改了什么（`RectifyRecord`），因为静默改写请求是最难排查的一类行为：
用户发的和上游收到的不是同一个请求，而没有任何地方说明这件事。
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

#: Claude 要求的 thinking 预算下限。低于它等于没开思考。
MIN_THINKING_BUDGET = 1024

#: 整流后采用的 thinking 预算。与参考实现的预算上限取值一致。
RECTIFIED_THINKING_BUDGET = 32000

#: 整流后 `max_tokens` 的取值，必须大于预算。
RECTIFIED_MAX_TOKENS = 64000

#: 图片被拒时的占位文本。保留一个可见标记而不是静默丢弃——
#: 用户问「这张图里是什么」时，模型至少能说「我没收到图片」而不是编一个答案。
UNSUPPORTED_IMAGE_PLACEHOLDER = "[Unsupported Image]"

#: `thinking` 签名失效的特征串。三者需同时具备才算命中，避免把无关的
#: `signature` 字样（例如鉴权签名错误）误判成 thinking 签名问题。
_SIGNATURE_MARKERS = ("signature", "thinking")

#: `budget_tokens` 违规的特征串。
_BUDGET_MARKERS = ("budget_tokens",)

#: 上游不认识 `reasoning_effort` 时的特征串。
#:
#: 大量 OpenAI 兼容网关只实现了 chat/completions 的核心字段，收到这个键直接 400。
#: 这类失败**换供应商也没用**：同一个不合法请求发给备用上游同样会被拒，
#: 正确处置是去掉那一个字段再重试一次。
_REASONING_EFFORT_MARKERS = ("reasoning_effort",)

#: 与 `_REASONING_EFFORT_MARKERS` 同时要求出现的「不认识 / 不支持」类词。
#:
#: 只匹配字段名会把「reasoning_effort 取值非法」（例如上游只认 low/medium/high
#: 而我们发了 max）也当成「字段不支持」，然后把整个字段删掉——那会让一个
#: 只需降档的请求彻底失去思考能力。
_UNRECOGNIZED_MARKERS = (
    "unrecognized",
    "unsupported",
    "not supported",
    "does not support",
    "unknown",
    "unexpected",
    "extra inputs",
    "additional propert",
)

#: 图片不受支持的特征串。任一命中即可：各家措辞差别很大。
_IMAGE_MARKERS = (
    "image",
    "multimodal",
    "vision",
)

#: 图片错误还需要一个「不支持 / 无效」类的词，否则任何提到 image 的错误都会命中。
_UNSUPPORTED_MARKERS = (
    "not support",
    "unsupported",
    "does not support",
    "invalid",
    "cannot process",
    "no support",
)


@dataclass(frozen=True)
class RectifierConfig:
    """整流器开关。默认全开，与参考实现一致。

    分成总开关 + 三个子开关，而不是一个布尔：三类整流的风险不同。
    签名整流丢掉的是上一轮的思考过程（本来也已失效），预算整流改的是数值，
    而图片降级**会改变模型看到的内容**——它最该能被单独关掉。
    """

    enabled: bool = True
    request_thinking_signature: bool = True
    request_thinking_budget: bool = True
    request_media_fallback: bool = True
    #: 上游不认识 `reasoning_effort` 时删掉该字段再重试一次。
    #:
    #: 与前三项同为「改一处就能成功、不改就必然失败」，而且换供应商帮不上忙：
    #: 备用上游收到同一个不合法字段同样会拒。
    request_reasoning_effort_unsupported: bool = True

    def allows(self, kind: str) -> bool:
        if not self.enabled:
            return False
        return bool(getattr(self, f"request_{kind}", False))


@dataclass
class RectifyRecord:
    """一次整流改了什么。

    静默改写请求是最难排查的一类行为：用户发出的与上游收到的不是同一个请求，
    而没有任何地方说明这件事。这条记录进 trace，让「为什么第二次就成功了」
    有一个可查的答案。
    """

    kind: str
    applied: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "applied": self.applied, "details": dict(self.details)}


def _error_text(error: Any) -> str:
    """把任意错误对象压成可匹配的小写文本。

    上游错误可能是异常、响应体字符串或 dict。取不到就返回空串——
    匹配不到特征串时不整流，这正是我们要的默认行为。
    """
    if error is None:
        return ""
    if isinstance(error, Mapping):
        return str(error).casefold()
    if isinstance(error, BaseException):
        parts = [str(error)]
        response = getattr(error, "response", None)
        text = getattr(response, "text", None)
        if isinstance(text, str):
            parts.append(text)
        return " ".join(parts).casefold()
    return str(error).casefold()


def should_rectify_thinking_signature(error: Any, config: RectifierConfig) -> bool:
    """`Invalid 'signature' in 'thinking' block` 这一类错误才命中。

    要求 `signature` 与 `thinking` **同时**出现：只看 `signature` 会把鉴权签名
    错误也当成 thinking 问题，然后去删一堆与失败无关的字段。
    """
    if not config.allows("thinking_signature"):
        return False
    text = _error_text(error)
    return all(marker in text for marker in _SIGNATURE_MARKERS)


def should_rectify_thinking_budget(error: Any, config: RectifierConfig) -> bool:
    """`budget_tokens` 约束类错误才命中。"""
    if not config.allows("thinking_budget"):
        return False
    text = _error_text(error)
    return all(marker in text for marker in _BUDGET_MARKERS)


def should_rectify_media(error: Any, config: RectifierConfig) -> bool:
    """上游明确表示不支持图片时才命中。

    需要「图片类词」与「不支持类词」同时出现。只看前者会让任何提到 image 的
    错误都触发降级，那会把用户的图片在一次无关的失败里悄悄删掉。
    """
    if not config.allows("media_fallback"):
        return False
    text = _error_text(error)
    if not any(marker in text for marker in _IMAGE_MARKERS):
        return False
    return any(marker in text for marker in _UNSUPPORTED_MARKERS)


def rectify_thinking_signature(body: dict[str, Any]) -> RectifyRecord:
    """移除失效的 thinking 签名，最小侵入。

    只删三样东西：`messages[*].content` 里的 `thinking` / `redacted_thinking`
    块，以及非 thinking 块上遗留的 `signature` 字段。**不动**文本、图片与工具块——
    那些与签名校验无关，删它们等于把一次参数修复变成一次内容删改。
    """
    record = RectifyRecord(kind="thinking_signature")
    removed_thinking = 0
    removed_redacted = 0
    removed_signature = 0
    messages = body.get("messages")
    if not isinstance(messages, list):
        return record
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        kept: list[Any] = []
        for block in content:
            if not isinstance(block, dict):
                kept.append(block)
                continue
            block_type = block.get("type")
            if block_type == "thinking":
                removed_thinking += 1
                continue
            if block_type == "redacted_thinking":
                removed_redacted += 1
                continue
            if "signature" in block:
                # 非 thinking 块上的 signature 是上一轮残留，留着会继续触发校验。
                block.pop("signature", None)
                removed_signature += 1
            kept.append(block)
        message["content"] = kept
    record.applied = bool(removed_thinking or removed_redacted or removed_signature)
    record.details = {
        "removed_thinking_blocks": removed_thinking,
        "removed_redacted_thinking_blocks": removed_redacted,
        "removed_signature_fields": removed_signature,
    }
    return record


def rectify_thinking_budget(body: dict[str, Any]) -> RectifyRecord:
    """把 thinking 预算与 `max_tokens` 调到一组合法值。

    `adaptive` 类型不改：那是让上游自己决定预算，我们无从判断该给多少，
    改成一个固定数字反而是替上游做了它没要求的决定。
    """
    record = RectifyRecord(kind="thinking_budget")
    thinking = body.get("thinking")
    if isinstance(thinking, Mapping) and thinking.get("type") == "adaptive":
        record.details = {"skipped": "adaptive"}
        return record

    before = {
        "max_tokens": body.get("max_tokens"),
        "thinking_type": thinking.get("type") if isinstance(thinking, Mapping) else None,
        "budget_tokens": (
            thinking.get("budget_tokens") if isinstance(thinking, Mapping) else None
        ),
    }
    body["thinking"] = {"type": "enabled", "budget_tokens": RECTIFIED_THINKING_BUDGET}
    max_tokens = body.get("max_tokens")
    if not isinstance(max_tokens, int) or max_tokens <= RECTIFIED_THINKING_BUDGET:
        # `max_tokens` 必须严格大于预算，否则换一个预算也仍然违规。
        body["max_tokens"] = RECTIFIED_MAX_TOKENS
    after = {
        "max_tokens": body.get("max_tokens"),
        "thinking_type": "enabled",
        "budget_tokens": RECTIFIED_THINKING_BUDGET,
    }
    record.applied = before != after
    record.details = {"before": before, "after": after}
    return record


def rectify_media(body: dict[str, Any]) -> RectifyRecord:
    """把图片块换成可见占位文本，让对话不中断。

    换成占位而不是静默删除：用户问「这张图里是什么」时，模型至少能说
    「我没有收到图片」，而不是对着一段空内容编一个答案。
    """
    record = RectifyRecord(kind="media_fallback")
    replaced = 0
    messages = body.get("messages")
    if not isinstance(messages, list):
        return record
    for message in messages:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for index, block in enumerate(content):
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"image", "image_url"}:
                content[index] = {"type": "text", "text": UNSUPPORTED_IMAGE_PLACEHOLDER}
                replaced += 1
    record.applied = replaced > 0
    record.details = {"replaced_images": replaced}
    return record


def should_rectify_reasoning_effort(error: Any, config: RectifierConfig) -> bool:
    """上游明确表示不认识 `reasoning_effort` 这个字段时才命中。

    要求「字段名」与「不认识/不支持类词」**同时**出现。只匹配字段名会把
    「取值非法」（上游只认 low/medium/high 而我们发了 max）也判成
    「字段不支持」，然后把整个字段删掉——那会让一个只需降档的请求
    彻底失去思考能力。
    """
    if not config.allows("reasoning_effort_unsupported"):
        return False
    text = _error_text(error)
    if not any(marker in text for marker in _REASONING_EFFORT_MARKERS):
        return False
    return any(marker in text for marker in _UNRECOGNIZED_MARKERS)


def rectify_reasoning_effort(body: dict[str, Any]) -> RectifyRecord:
    """删掉上游不认识的 `reasoning_effort` 字段，其余字段一个都不动。

    没有这个字段时 `applied` 为假：重试一个逐字节相同的请求只会得到同一个
    错误，白花一次调用。
    """
    record = RectifyRecord(kind="reasoning_effort_unsupported")
    if "reasoning_effort" not in body:
        return record
    removed = body.pop("reasoning_effort")
    record.applied = True
    record.details = {"removed_reasoning_effort": removed}
    return record


def rectify_request(
    body: Mapping[str, Any],
    error: Any,
    config: Optional[RectifierConfig] = None,
    *,
    already_applied: frozenset[str] = frozenset(),
) -> tuple[Optional[dict[str, Any]], Optional[RectifyRecord]]:
    """按错误选一条整流并返回改好的请求体；不该整流时返回 ``(None, None)``。

    ``already_applied`` 是本次请求已经用过的整流种类。同一类**只应用一次**：
    修完重试仍失败就把原始错误抛出去。反复整流会把「参数错」变成「一直在转」，
    而后者更难查——日志里全是重试，没有一条说明原因。

    **不修改入参**：返回的是深拷贝。原始请求体要留着，一是重试失败时要抛原始
    错误，二是 trace 里得能看出「用户发的」与「上游收到的」差在哪。
    """
    config = config or RectifierConfig()
    if not config.enabled:
        return None, None

    checks = (
        ("thinking_signature", should_rectify_thinking_signature, rectify_thinking_signature),
        ("thinking_budget", should_rectify_thinking_budget, rectify_thinking_budget),
        ("media_fallback", should_rectify_media, rectify_media),
        # 排在最后：前三类都是「改一个值」，这一类是「删一个字段」。
        # 顺序只在一次错误同时命中多条特征时才有意义，那时优先改值而不是删字段。
        (
            "reasoning_effort_unsupported",
            should_rectify_reasoning_effort,
            rectify_reasoning_effort,
        ),
    )
    for kind, should, apply in checks:
        if kind in already_applied:
            continue
        if not should(error, config):
            continue
        candidate = copy.deepcopy(dict(body))
        record = apply(candidate)
        if not record.applied:
            # 命中了错误特征但没有可改之处：重试同一个请求只会得到同一个错误。
            continue
        return candidate, record
    return None, None
