"""每个适配器都必须真的调整流器，且重试有界（需求 8）。

整流规则按**载荷形状**分派（`detect_payload_shape`）：`messages[*].content`
块列表是 OpenAI / Claude 的形状，Gemini 用 `contents[*].parts`，
Ollama 用 `messages` 但 `content` 是纯字符串、图片在并列的 `images` 数组。
形状与规则的对应关系由 `tests/llm/test_rectifier_payload_shapes.py` 守住。

这份测试守的是另一件事：**四家适配器的两条路径都真的调用了整流器**。
只在一条路径上整流的后果是同一个请求换个 `reply_stream_mode` 就失去容错；
一家都不调用的后果更严重——供应商编辑页上那几个整流开关对它
从未参与任何决策，而界面上没有任何地方说明。

历史：Gemini 与 Ollama 曾经完全未接线，README 里为此写过一段「整流器不覆盖」
的免责说明。现在两家各按自己的形状接上了，那段说明已随之删除。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADAPTER_DIR = (
    Path(__file__).resolve().parents[2] / "kirara_ai" / "plugins" / "llm_preset_adapters"
)

#: 自己构造请求体、因此必须自己接整流的适配器。
#:
#: 十个 OpenAI 兼容适配器全部继承 `openai_adapter.py` 的基类，接在那里即全覆盖。
_RECTIFYING_ADAPTERS = (
    "openai_adapter.py",
    "claude_adapter.py",
    "gemini_adapter.py",
    "ollama_adapter.py",
)


def _source(name: str) -> str:
    return (_ADAPTER_DIR / name).read_text(encoding="utf-8")


def _method_body(source: str, name: str) -> str:
    start = source.index(f"    def {name}(")
    remainder = source[start + 1 :]
    match = re.search(r"\n    def [a-zA-Z_]+\(", remainder)
    end = start + 1 + (match.start() if match else len(remainder))
    return source[start:end]


def test_the_probe_finds_the_adapters():
    """自检：适配器文件确实存在，不是拿空字符串在断言。"""
    for name in _RECTIFYING_ADAPTERS:
        assert (_ADAPTER_DIR / name).is_file(), f"{name} 不存在"
        assert "def chat(" in _source(name)


@pytest.mark.parametrize("name", _RECTIFYING_ADAPTERS)
def test_wired_adapters_rectify_on_both_paths(name: str):
    """已接线的适配器：非流式与流式两条路径都要整流。

    只在一条路径上整流的后果是同一个请求换个 `reply_stream_mode` 就失去容错，
    而那个开关在文档里是被推荐打开的。
    """
    source = _source(name)

    for method in ("chat", "stream_chat"):
        body = _method_body(source, method)
        assert "rectify_request(" in body, (
            f"{name} 的 {method} 没有整流——"
            "供应商页上的整流开关对这条路径不参与决策"
        )


@pytest.mark.parametrize("name", _RECTIFYING_ADAPTERS)
def test_wired_adapters_bound_their_rectify_retries(name: str):
    """每类整流最多一次。无界重试把「参数错」变成「一直在转」，后者更难查。"""
    source = _source(name)

    for method in ("chat", "stream_chat"):
        body = _method_body(source, method)
        assert "already_applied" in body, f"{name} 的 {method} 整流重试无界"


def test_no_adapter_is_left_without_rectification():
    """不允许再出现「有整流开关但对某家无效」的状态。

    此前 Gemini 与 Ollama 完全未接线，README 里为此写了一段免责说明。
    两家接上之后那段说明必须消失——留着会把一个已经修好的边界说成仍然存在，
    而下一个读它的人会据此放弃排查真正的原因。
    """
    readme = (Path(__file__).resolve().parents[2] / "README.md").read_text(encoding="utf-8")

    assert "整流器不覆盖" not in readme, (
        "README 仍写着整流器不覆盖某些供应商，但四家适配器都已接线——"
        "过期的免责说明比没有说明更糟"
    )
    for name in _RECTIFYING_ADAPTERS:
        assert "rectify_request(" in _source(name), f"{name} 未接整流器"
