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

#: Gemini 预算违规时改用的 `thinkingConfig.thinkingBudget`。
#:
#: 不能沿用 `RECTIFIED_THINKING_BUDGET`（32000）：Gemini 的预算上限按模型而定，
#: 2.5 Flash 是 24576。取一个各代都接受的中间值，并且下面还会保证
#: `maxOutputTokens` 严格大于它——预算吃掉整个输出上限时，改完仍然拿不到回复。
RECTIFIED_GEMINI_THINKING_BUDGET = 8192

#: Gemini 预算整流后 `maxOutputTokens` 的下限。必须大于上面的预算。
RECTIFIED_GEMINI_MAX_OUTPUT_TOKENS = 16384

#: `thinking` 签名失效的特征串。三者需同时具备才算命中，避免把无关的
#: `signature` 字样（例如鉴权签名错误）误判成 thinking 签名问题。
_SIGNATURE_MARKERS = ("signature", "thinking")

#: `budget_tokens` 违规的特征串。任一命中即可。
#:
#: 三种写法对应三家的字段路径：Anthropic 是 `thinking.budget_tokens`，
#: Gemini 报错时按 `generation_config.thinking_config.thinking_budget` 与
#: `thinkingBudget` 两种拼法出现。同一件事（预算越界）在不同上游只是措辞不同，
#: 判定放在一处比在每家复制一遍更不容易漏。
_BUDGET_MARKERS = ("budget_tokens", "thinking_budget", "thinkingbudget")

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

#: 上游明确表示「这个模型不支持思考」的特征串。
#:
#: 与预算违规是两件事：预算违规改一个数字就能过，不支持思考则必须把整个
#: 思考配置去掉。Gemini 与 Ollama 都会给出这类错误，而它们的思考配置位置
#: 各不相同（`generationConfig.thinkingConfig` / 顶层 `think`）。
_THINKING_UNSUPPORTED_MARKERS = ("thinking", "think")

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
    #: 上游明确表示「这个模型不支持思考」时删掉整个思考配置再重试一次。
    #:
    #: 与预算整流分开是因为动作不同：预算越界改一个数字，不支持思考要把配置
    #: 整个去掉。合成一条会让「只需降档」的请求彻底失去思考能力。
    request_thinking_unsupported: bool = True
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
    """`budget_tokens` 约束类错误才命中。

    三家的措辞不同（`thinking.budget_tokens` / `thinking_config.thinking_budget`
    / `thinkingBudget`），但都指同一件事：预算越界。任一命中即可——
    要求全部同时出现等于只认 Anthropic 一家。
    """
    if not config.allows("thinking_budget"):
        return False
    text = _error_text(error)
    return any(marker in text for marker in _BUDGET_MARKERS)


def should_rectify_thinking_unsupported(error: Any, config: RectifierConfig) -> bool:
    """上游明确表示「这个模型不支持思考」时才命中。

    与预算违规分开的理由：预算违规改一个数字就能过，而不支持思考必须把整个
    思考配置去掉。用同一条规则处理会让「预算越界」也被删掉思考能力，
    那是一次不必要的降级。

    要求「思考类词」与「不支持类词」同时出现，且**不能**同时带预算类词：
    带预算词的那条更具体，应该由预算整流处理。
    """
    if not config.allows("thinking_unsupported"):
        return False
    text = _error_text(error)
    if any(marker in text for marker in _BUDGET_MARKERS):
        return False
    if not any(marker in text for marker in _THINKING_UNSUPPORTED_MARKERS):
        return False
    return any(marker in text for marker in _UNRECOGNIZED_MARKERS)


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


def detect_payload_shape(body: Mapping[str, Any]) -> str:
    """判断请求体属于哪一种载荷形状。

    整流规则必须按形状分派。此前四条规则全部按 `messages[*].content` 书写
    （OpenAI / Claude 的形状），于是 Gemini（`contents[*].parts`）与 Ollama
    （`content` 是纯字符串、图片在并列的 `images` 数组）两家要么空转、
    要么被写进上游不认识的字段。后者比不整流更糟：`rectify_thinking_budget`
    会往 Gemini 请求体注入 Anthropic 的 `thinking` 与 `max_tokens` 两个顶层键，
    Gemini 对未知字段直接 400——一次「整流」把可重试的错误变成必然失败。

    认不出来时返回 ``"messages"``：那是唯一有完整规则集的形状，而落到那套
    规则上最坏也只是空转（`applied` 为假 → 抛原始错误），不会改错字段。
    """
    if isinstance(body.get("contents"), list):
        return "gemini"
    # Ollama 与 OpenAI 都用 `messages`，区分点是并列的 `options` 采样块
    # 与顶层 `think`。两者任一出现就按 Ollama 处理。
    if isinstance(body.get("messages"), list) and (
        isinstance(body.get("options"), Mapping) or "think" in body
    ):
        return "ollama"
    return "messages"


def rectify_gemini_media(body: dict[str, Any]) -> RectifyRecord:
    """把 Gemini 的图片 part 换成可见占位文本。

    Gemini 的图片在 `contents[*].parts[*].inline_data`（也接受 `inlineData`
    驼峰拼法），不在 `messages[*].content`。两种拼法都要认——只认一种会让
    另一种静默漏过，而「命中错误特征但没改成」的结果是抛原始错误，
    表现上与整流器不存在完全一样。
    """
    record = RectifyRecord(kind="media_fallback")
    replaced = 0
    contents = body.get("contents")
    if not isinstance(contents, list):
        return record
    for content in contents:
        if not isinstance(content, dict):
            continue
        parts = content.get("parts")
        if not isinstance(parts, list):
            continue
        for index, part in enumerate(parts):
            if not isinstance(part, dict):
                continue
            if "inline_data" in part or "inlineData" in part:
                parts[index] = {"text": UNSUPPORTED_IMAGE_PLACEHOLDER}
                replaced += 1
    record.applied = replaced > 0
    record.details = {"replaced_images": replaced}
    return record


def rectify_gemini_thinking_unsupported(body: dict[str, Any]) -> RectifyRecord:
    """去掉 Gemini 的 `generationConfig.thinkingConfig`，其余采样项保留。

    **不写入** `thinking` / `max_tokens`：那是 Anthropic 的顶层字段，
    Gemini 收到未知顶层键直接返回 400 INVALID_ARGUMENT。
    """
    record = RectifyRecord(kind="thinking_unsupported")
    generation = body.get("generationConfig")
    if not isinstance(generation, dict) or "thinkingConfig" not in generation:
        return record
    removed = generation.pop("thinkingConfig")
    record.applied = True
    record.details = {"removed_thinking_config": removed}
    return record


def rectify_gemini_thinking_budget(body: dict[str, Any]) -> RectifyRecord:
    """把 Gemini 的思考预算收进一个各代都接受的区间。

    改的是 `generationConfig.thinkingConfig.thinkingBudget`，并保证
    `maxOutputTokens` 严格大于它——预算吃掉整个输出上限时，改完仍然拿不到回复。
    """
    record = RectifyRecord(kind="thinking_budget")
    generation = body.get("generationConfig")
    if not isinstance(generation, dict):
        return record
    thinking = generation.get("thinkingConfig")
    if not isinstance(thinking, dict):
        return record

    before = {
        "thinkingBudget": thinking.get("thinkingBudget"),
        "maxOutputTokens": generation.get("maxOutputTokens"),
    }
    thinking["thinkingBudget"] = RECTIFIED_GEMINI_THINKING_BUDGET
    max_output = generation.get("maxOutputTokens")
    if not isinstance(max_output, int) or max_output <= RECTIFIED_GEMINI_THINKING_BUDGET:
        generation["maxOutputTokens"] = RECTIFIED_GEMINI_MAX_OUTPUT_TOKENS
    after = {
        "thinkingBudget": RECTIFIED_GEMINI_THINKING_BUDGET,
        "maxOutputTokens": generation.get("maxOutputTokens"),
    }
    record.applied = before != after
    record.details = {"before": before, "after": after}
    return record


def rectify_ollama_media(body: dict[str, Any]) -> RectifyRecord:
    """删掉 Ollama 的 `images` 数组并在文本里留下占位标记。

    只删数组会让「这张图里是什么」对着无图的上下文提问，模型于是编一个答案。
    Ollama 的 `content` 是纯字符串（不是块列表），所以占位标记只能追加进文本。
    """
    record = RectifyRecord(kind="media_fallback")
    replaced = 0
    messages = body.get("messages")
    if not isinstance(messages, list):
        return record
    for message in messages:
        if not isinstance(message, dict):
            continue
        images = message.get("images")
        if not isinstance(images, list) or not images:
            continue
        replaced += len(images)
        message.pop("images", None)
        text = message.get("content")
        placeholder = UNSUPPORTED_IMAGE_PLACEHOLDER * 1
        message["content"] = (
            f"{text}\n{placeholder}" if isinstance(text, str) and text else placeholder
        )
    record.applied = replaced > 0
    record.details = {"replaced_images": replaced}
    return record


def rectify_ollama_thinking_unsupported(body: dict[str, Any]) -> RectifyRecord:
    """去掉 Ollama 的顶层 `think`，采样参数一个都不动。

    Ollama 的思考开关是顶层 `think` 而不是 `options` 里的一项；
    删 `options.num_predict` 会顺手砍掉输出长度，那不是这次失败的原因。
    """
    record = RectifyRecord(kind="thinking_unsupported")
    if "think" not in body:
        return record
    removed = body.pop("think")
    record.applied = True
    record.details = {"removed_think": removed}
    return record


#: 每种载荷形状的整流规则表。
#:
#: 键是整流种类（与 `RectifierConfig.request_*` 开关一一对应，也是
#: `already_applied` 去重的依据）；缺失的种类表示那家没有对应位置，
#: 此时该规则不参与——而不是空转出一个 `applied=False` 让调用方以为试过了。
_SHAPE_RULES: dict[str, dict[str, Any]] = {}


def _register_shape_rules() -> None:
    """建立形状 → 规则的映射。

    写成函数而不是字面量：规则函数都定义在上面，字面量放在文件更靠前的位置
    会引用到还没定义的名字。
    """
    _SHAPE_RULES["messages"] = {
        "thinking_signature": rectify_thinking_signature,
        "thinking_budget": rectify_thinking_budget,
        "media_fallback": rectify_media,
        "reasoning_effort_unsupported": rectify_reasoning_effort,
    }
    _SHAPE_RULES["gemini"] = {
        # Gemini 不回传 thinking block，没有签名可失效，因此没有
        # `thinking_signature`；它也不发 `reasoning_effort`（发的是
        # thinkingConfig），因此没有那一条。
        "thinking_budget": rectify_gemini_thinking_budget,
        "thinking_unsupported": rectify_gemini_thinking_unsupported,
        "media_fallback": rectify_gemini_media,
    }
    _SHAPE_RULES["ollama"] = {
        # Ollama 的思考只有开关没有预算，因此没有 `thinking_budget`。
        "thinking_unsupported": rectify_ollama_thinking_unsupported,
        "media_fallback": rectify_ollama_media,
    }


_register_shape_rules()


#: 各整流种类的命中判定。与形状无关：判定看的是上游错误文本，
#: 而错误文本不取决于我们发的是哪种形状。
_DETECTORS: dict[str, Any] = {
    "thinking_signature": should_rectify_thinking_signature,
    "thinking_budget": should_rectify_thinking_budget,
    "thinking_unsupported": should_rectify_thinking_unsupported,
    "media_fallback": should_rectify_media,
    "reasoning_effort_unsupported": should_rectify_reasoning_effort,
}

#: 同一次错误命中多条特征时的应用顺序。
#:
#: 「改一个值」排在「删一个字段」之前：能靠改值过关时不该先砍掉能力。
#: `thinking_unsupported` 排在 `thinking_budget` 之后同理——预算越界改数字，
#: 只有上游说「压根不支持」才删配置。
_RULE_ORDER = (
    "thinking_signature",
    "thinking_budget",
    "media_fallback",
    "thinking_unsupported",
    "reasoning_effort_unsupported",
)


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

    规则按**载荷形状**分派（见 `detect_payload_shape`）：同一个开关在每家都改
    它自己的字段，而不是把 Anthropic 的字段名写进别家的请求体。某家没有对应
    位置的规则在那家不出现——不出现与「出现但改不动」是两件事，后者会让
    调用方以为已经试过。
    """
    config = config or RectifierConfig()
    if not config.enabled:
        return None, None

    rules = _SHAPE_RULES[detect_payload_shape(body)]
    for kind in _RULE_ORDER:
        apply = rules.get(kind)
        if apply is None:
            continue
        if kind in already_applied:
            continue
        if not _DETECTORS[kind](error, config):
            continue
        candidate = copy.deepcopy(dict(body))
        record = apply(candidate)
        if not record.applied:
            # 命中了错误特征但没有可改之处：重试同一个请求只会得到同一个错误。
            continue
        return candidate, record
    return None, None
