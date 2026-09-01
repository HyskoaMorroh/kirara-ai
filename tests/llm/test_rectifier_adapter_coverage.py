"""整流器与请求体形状的耦合必须显式（需求 8）。

`rectify_request` 的四条规则里，`media_fallback` 直接按 `body["messages"][*]["content"]`
遍历——那是 OpenAI / Ollama 的形状。Gemini 用 `contents` + `parts`，
Claude 用 `messages` 但块结构不同。

这不是"整流器写得不好"，而是**它当前只覆盖两种载荷形状**这件事没有被记录。
后果分两种：

- 已接线的适配器（OpenAI 兼容 10 个 + Claude）：`should_rectify_media` 命中错误特征，
  但 `rectify_media` 找不到可改之处 → `record.applied` 为 False → 返回 `(None, None)`
  → 抛原始错误。行为是对的（`rectify_request` 里那句注释写了这条：
  "命中了错误特征但没有可改之处：重试同一个请求只会得到同一个错误"）。
- **未接线的适配器（Gemini / Ollama）：整流器一次都不会被调用。**
  供应商编辑页上的四个整流开关对它们完全无效，而界面上没有任何地方说明这一点。

这份测试锁住两件事：已接线的适配器不能悄悄退出整流；未接线的适配器要么接上，
要么这个事实被显式记录下来——不能让"开关存在但对某些供应商无效"成为一个
只有读源码才能发现的事。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ADAPTER_DIR = (
    Path(__file__).resolve().parents[2] / "kirara_ai" / "plugins" / "llm_preset_adapters"
)

#: 载荷形状与 `rectify_media` 的遍历方式兼容的适配器——它们必须接整流。
#: 判据是请求体用 `messages` + 列表 `content`（OpenAI 与 Ollama 都是这个形状）。
_MESSAGES_SHAPED = ("openai_adapter.py", "claude_adapter.py", "ollama_adapter.py")


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
    for name in _MESSAGES_SHAPED:
        assert (_ADAPTER_DIR / name).is_file(), f"{name} 不存在"
        assert "def chat(" in _source(name)


@pytest.mark.parametrize("name", ["openai_adapter.py", "claude_adapter.py"])
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


@pytest.mark.parametrize("name", ["openai_adapter.py", "claude_adapter.py"])
def test_wired_adapters_bound_their_rectify_retries(name: str):
    """每类整流最多一次。无界重试把「参数错」变成「一直在转」，后者更难查。"""
    source = _source(name)

    for method in ("chat", "stream_chat"):
        body = _method_body(source, method)
        assert "already_applied" in body, f"{name} 的 {method} 整流重试无界"


def test_adapters_without_rectification_are_documented():
    """整流器不覆盖的适配器必须在文档里写明，而不是只能读源码发现。

    Gemini 用 `contents` + `parts`，与 `rectify_media` 遍历的
    `messages[*].content` 形状不同。整流开关对它无效是事实，
    但「开关存在却对某些供应商无效」不能是一个隐藏事实。
    """
    gemini = _source("gemini_adapter.py")
    if "rectify_request(" in gemini:
        return  # 已接线，无需文档说明

    # 原先写的是「README 里同时出现 Gemini 与 整流 两个词」——那两个词在一份
    # 六百行的 README 里几乎必然各自出现，断言因此空过。要查的是**这个边界本身**
    # 被写下来了，所以找一个只有在刻意记录它时才会出现的标记串。
    readme = (
        Path(__file__).resolve().parents[2] / "README.md"
    ).read_text(encoding="utf-8")

    assert "整流器不覆盖" in readme, (
        "Gemini 未接整流器，但 README 里没有记录这个边界——"
        "「开关存在却对某些供应商无效」不能是一个只能读源码发现的事实"
    )
