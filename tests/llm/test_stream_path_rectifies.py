"""流式路径也必须整流（需求 8）。

`rectify_request` 修的是「上游因参数约束拒绝、而这个约束不在用户能改的地方」这类
硬失败：不支持的图片、上游不认识的 thinking 字段、超出范围的 budget、
不支持的 reasoning_effort。

非流式路径（`chat`）已经在整流。但 `stream_chat` 里 `raise_for_status()` 失败后
直接 `raise error`——**同一个请求换成流式就不整流了**。

这个落差在产品上的后果：`reply_stream_mode` 配成 `aggregate` 或 `incremental` 之后
（文档推荐这么配，因为流式超时与首字节前的故障转移才生效），供应商编辑页上的
四个整流开关对这条路径**从未参与任何决策**。用户看到「请求失败」，
而真正的原因是一张图或一个上游不认识的字段——两者都不是他能自己改的。

而十个 OpenAI 兼容适配器全部继承 `OpenAIAdapterChatBase`，所以这一个缺口覆盖
绝大多数部署。

判据：**同一个请求走哪条路径，容错行为必须一致。** 两条路径各写一套的后果不是
重复代码，而是两份会各自漂移的行为——而漂移的那一侧没有任何症状能让人察觉。
"""

from __future__ import annotations

import re
from pathlib import Path

_ADAPTER = (
    Path(__file__).resolve().parents[2]
    / "kirara_ai"
    / "plugins"
    / "llm_preset_adapters"
    / "openai_adapter.py"
)


def _method_body(source: str, name: str) -> str:
    """取出一个方法的正文（到下一个同级 def 为止）。"""
    start = source.index(f"    def {name}(")
    remainder = source[start + 1 :]
    match = re.search(r"\n    def [a-zA-Z_]+\(", remainder)
    end = start + 1 + (match.start() if match else len(remainder))
    return source[start:end]


def test_the_probe_finds_both_paths():
    """自检：两个方法都能被取出，否则下面的断言是空跑。"""
    source = _ADAPTER.read_text(encoding="utf-8")

    chat = _method_body(source, "chat")
    stream = _method_body(source, "stream_chat")

    assert "raise_for_status" in chat
    assert "raise_for_status" in stream
    # 两段必须真的不同，否则说明切分逻辑把整个文件都取了。
    assert chat != stream


def test_the_non_stream_path_rectifies():
    """基线：非流式路径本来就整流，它是这条断言的参照。"""
    source = _ADAPTER.read_text(encoding="utf-8")

    assert "rectify_request(" in _method_body(source, "chat")


def test_the_stream_path_also_rectifies():
    """流式路径必须同样整流，否则四个整流开关对它从不参与决策。"""
    source = _ADAPTER.read_text(encoding="utf-8")

    stream = _method_body(source, "stream_chat")

    assert "rectify_request(" in stream, (
        "stream_chat 没有调用 rectify_request——"
        "`reply_stream_mode` 配成 aggregate/incremental 后，"
        "供应商页上的四个整流开关对这条路径完全不生效"
    )


def test_the_stream_path_bounds_its_retries():
    """整流重试必须有界：每类最多一次，改完仍失败就抛原始错误。

    无界重试会把一次必然失败变成一个循环——而流式路径上每次重试都要重新
    建立连接、重新等首字节。
    """
    source = _ADAPTER.read_text(encoding="utf-8")

    stream = _method_body(source, "stream_chat")

    assert "already_applied" in stream, "流式整流没有传 already_applied，同一类会反复重试"


def test_the_stream_path_reuses_the_shared_rectifier_config():
    """用请求自带的 `rectifier` 配置，而不是在流式路径另造一套。"""
    source = _ADAPTER.read_text(encoding="utf-8")

    stream = _method_body(source, "stream_chat")

    assert "req.rectifier" in stream
