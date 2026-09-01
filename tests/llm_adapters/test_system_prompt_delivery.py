"""系统提示词必须真的到达 Claude 与 Gemini（需求 7）。

第 7 条原文点名的报错：**`Object of type LLMChatTextContent is not JSON
serializable`**。这不是一个偶发异常——它在 Claude 适配器上是**必然**的：

    system_messages = [msg for msg in req.messages if msg.role == "system"]
    system_message = system_messages[0].content      # list[LLMChatTextContent]
    data = {..., "system": system_message}
    requests.post(api_url, json=data)                # json.dumps 在这里抛错

`messages` 那一项经过 `convert_llm_chat_message_to_claude_message()` 转换成纯 dict，
而 `system` 这一项**没有**：它把 Pydantic 对象原样塞进请求体。只要这一轮带系统
提示词——本项目的 Agent 运行时**总是**带（人格、技能目录、工具说明都在里面）——
Claude 就调不通。流式与非流式两条路径各有一份同样的代码。

Gemini 是同一个缺口的另一种形态，但**不报错**，因此更难发现：
`convert_llm_chat_message_to_gemini_message` 把 `system` 角色和 `user` 一起交给
`convert_non_tool_message`，而后者把非 assistant 的一律映射成 `"role": "user"`。
于是系统提示词变成了对话里的第一条用户消息——请求成功、模型有回复、
而人格与规则的权重完全不同（Gemini 有专门的 `systemInstruction` 字段）。
「静默降级成一条普通用户消息」比抛异常更糟：没有任何迹象表明它发生了。

这一组测试只断言**请求体形状**，不打真实上游：它要钉住的正是「我们发出去的
JSON 长什么样」，而那与网络无关。
"""

from __future__ import annotations

import json

import pytest

from kirara_ai.llm.format.message import (LLMChatImageContent,
                                          LLMChatMessage,
                                          LLMChatTextContent)
from kirara_ai.llm.format.request import LLMChatRequest


def _system_and_user() -> list[LLMChatMessage]:
    return [
        LLMChatMessage(
            role="system",
            content=[LLMChatTextContent(text="你是一位严谨的研究助理。")],
        ),
        LLMChatMessage(role="user", content=[LLMChatTextContent(text="讲讲退火")]),
    ]


class _Captured(Exception):
    """Abort the adapter right after the payload is built."""

    def __init__(self, payload: dict) -> None:
        super().__init__("payload captured")
        self.payload = payload


def _wire(adapter, backend_name: str):
    """Attach the collaborators every adapter reads before issuing a request.

    与既有适配器测试同一套装配（见 `test_gemini_adapter.py`）：`@trace_llm_chat`
    在进入方法体之前就读 `self.tracer`，`media_manager` 在图片部件上会被读到。
    不装这两样时失败发生在追踪装饰器里，而那与被测的请求体形状无关。
    """
    from unittest.mock import MagicMock

    from kirara_ai.media.manager import MediaManager

    adapter.media_manager = MediaManager()
    adapter.backend_name = backend_name
    adapter.tracer = MagicMock()
    return adapter


def _capture_claude_payload(monkeypatch, *, stream: bool) -> dict:
    """Build the Claude request body without contacting the upstream."""
    from kirara_ai.plugins.llm_preset_adapters import claude_adapter

    def fake_post(url, **kwargs):
        raise _Captured(kwargs.get("json"))

    monkeypatch.setattr(claude_adapter.requests, "post", fake_post)
    adapter = _wire(claude_adapter.ClaudeAdapter(
        claude_adapter.ClaudeConfig(api_key="test-key")
    ), "claude")
    request = LLMChatRequest(
        model="claude-sonnet-4",
        messages=_system_and_user(),
        max_tokens=256,
    )
    with pytest.raises(_Captured) as captured:
        if stream:
            # `stream_chat` 是生成器：必须真的取一次才会执行到 post。
            next(iter(adapter.stream_chat(request)))
        else:
            adapter.chat(request)
    return captured.value.payload


@pytest.mark.parametrize("stream", [False, True], ids=["chat", "stream_chat"])
def test_claude_system_prompt_is_json_serializable(monkeypatch, stream):
    """请求体必须能被 `json.dumps` 序列化。

    这就是需求 7 报的那个错。`requests` 内部对 `json=` 参数调用 `json.dumps`，
    因此「能序列化」不是一个额外要求，而是这条请求能否发出去的前提。
    """
    payload = _capture_claude_payload(monkeypatch, stream=stream)
    # 直接序列化整个请求体：任何一处漏转换的 Pydantic 对象都会在这里暴露。
    json.dumps(payload)


@pytest.mark.parametrize("stream", [False, True], ids=["chat", "stream_chat"])
def test_claude_system_prompt_arrives_as_text(monkeypatch, stream):
    """系统提示词必须以文本到达，而不是被丢掉或换成对象的 repr。

    只断言「能序列化」不够：把 `content` 换成 `str(...)` 也能过，
    而那样上游收到的是 ``[LLMChatTextContent(type='text', text='...')]``——
    请求成功、模型收到一段乱码当人格。
    """
    payload = _capture_claude_payload(monkeypatch, stream=stream)
    system = payload["system"]
    serialized = json.dumps(system, ensure_ascii=False)
    assert "你是一位严谨的研究助理。" in serialized
    assert "LLMChatTextContent" not in serialized


@pytest.mark.parametrize("stream", [False, True], ids=["chat", "stream_chat"])
def test_claude_system_prompt_never_enters_the_messages_array(monkeypatch, stream):
    """Claude 的 system 是顶层字段，不是一条 message。

    同时出现在两处会让人格被算两遍 token，且 Claude 明确不接受
    `role: "system"` 的 message。
    """
    payload = _capture_claude_payload(monkeypatch, stream=stream)
    roles = [message["role"] for message in payload["messages"]]
    assert "system" not in roles


def test_claude_without_a_system_message_omits_the_field(monkeypatch):
    """没有系统提示词时不能发一个 `"system": null` 或空串。

    Claude 对空 system 的处理与「没有 system」不同；而适配器本来就会剔除
    `None` 字段，这条钉住那个行为不被后续改动破坏。
    """
    from kirara_ai.plugins.llm_preset_adapters import claude_adapter

    def fake_post(url, **kwargs):
        raise _Captured(kwargs.get("json"))

    monkeypatch.setattr(claude_adapter.requests, "post", fake_post)
    adapter = _wire(claude_adapter.ClaudeAdapter(
        claude_adapter.ClaudeConfig(api_key="test-key")
    ), "claude")
    request = LLMChatRequest(
        model="claude-sonnet-4",
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="嗨")])],
        max_tokens=64,
    )
    with pytest.raises(_Captured) as captured:
        adapter.chat(request)
    assert "system" not in captured.value.payload


def test_claude_multipart_system_prompt_keeps_every_part(monkeypatch):
    """多段系统提示词必须全部到达。

    Agent 运行时会把人格、技能目录、工具说明拼成多个部件；只取第一段等于
    静默丢掉技能与工具说明，而模型仍然会正常回答——那是最难发现的一类缺陷。
    """
    from kirara_ai.plugins.llm_preset_adapters import claude_adapter

    def fake_post(url, **kwargs):
        raise _Captured(kwargs.get("json"))

    monkeypatch.setattr(claude_adapter.requests, "post", fake_post)
    adapter = _wire(claude_adapter.ClaudeAdapter(
        claude_adapter.ClaudeConfig(api_key="test-key")
    ), "claude")
    request = LLMChatRequest(
        model="claude-sonnet-4",
        messages=[
            LLMChatMessage(
                role="system",
                content=[
                    LLMChatTextContent(text="人格：严谨。"),
                    LLMChatTextContent(text="可用技能：agent-browser。"),
                ],
            ),
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="嗨")]),
        ],
        max_tokens=64,
    )
    with pytest.raises(_Captured) as captured:
        adapter.chat(request)
    serialized = json.dumps(captured.value.payload["system"], ensure_ascii=False)
    assert "人格：严谨。" in serialized
    assert "可用技能：agent-browser。" in serialized


def test_claude_ignores_a_non_text_system_part(monkeypatch):
    """系统提示词里混进图片时不能让整条请求炸掉。

    Claude 的顶层 `system` 只接受文本。丢弃那一段并保留文本，比抛出
    「无法序列化」更接近调用方的意图——它想设定人格，不是发图。
    """
    from kirara_ai.plugins.llm_preset_adapters import claude_adapter

    def fake_post(url, **kwargs):
        raise _Captured(kwargs.get("json"))

    monkeypatch.setattr(claude_adapter.requests, "post", fake_post)
    adapter = _wire(claude_adapter.ClaudeAdapter(
        claude_adapter.ClaudeConfig(api_key="test-key")
    ), "claude")
    request = LLMChatRequest(
        model="claude-sonnet-4",
        messages=[
            LLMChatMessage(
                role="system",
                content=[
                    LLMChatTextContent(text="人格：严谨。"),
                    LLMChatImageContent(media_id="not-a-real-media"),
                ],
            ),
            LLMChatMessage(role="user", content=[LLMChatTextContent(text="嗨")]),
        ],
        max_tokens=64,
    )
    with pytest.raises(_Captured) as captured:
        adapter.chat(request)
    payload = captured.value.payload
    json.dumps(payload)
    assert "人格：严谨。" in json.dumps(payload["system"], ensure_ascii=False)


def _capture_gemini_payload(monkeypatch, messages: list[LLMChatMessage]) -> dict:
    from kirara_ai.plugins.llm_preset_adapters import gemini_adapter

    adapter = _wire(gemini_adapter.GeminiAdapter(
        gemini_adapter.GeminiConfig(api_key="test-key")
    ), "gemini")

    def fake_post_with_retry(url, **kwargs):
        raise _Captured(kwargs.get("json"))

    monkeypatch.setattr(adapter, "_post_with_retry", fake_post_with_retry)
    request = LLMChatRequest(
        model="gemini-2.5-flash", messages=messages, max_tokens=256
    )
    with pytest.raises(_Captured) as captured:
        adapter.chat(request)
    return captured.value.payload


def test_gemini_system_prompt_uses_system_instruction(monkeypatch):
    """Gemini 有专门的 `systemInstruction`，系统提示词必须走那里。

    此前它被 `convert_non_tool_message` 映射成 `"role": "user"`——请求成功、
    模型有回复，而人格与规则的权重完全不同。**不报错的降级比报错更难发现**：
    没有任何迹象表明系统提示词已经不是系统提示词了。
    """
    payload = _capture_gemini_payload(monkeypatch, _system_and_user())

    assert "systemInstruction" in payload, "系统提示词没有走 systemInstruction"
    serialized = json.dumps(payload["systemInstruction"], ensure_ascii=False)
    assert "你是一位严谨的研究助理。" in serialized
    assert "LLMChatTextContent" not in serialized


def test_gemini_system_prompt_is_not_also_a_user_turn(monkeypatch):
    """走了 systemInstruction 之后，它不能同时留在 contents 里。

    留着会让同一段文字被算两遍 token，而且第一条用户消息不再是用户真正说的话。
    """
    payload = _capture_gemini_payload(monkeypatch, _system_and_user())

    contents = json.dumps(payload["contents"], ensure_ascii=False)
    assert "你是一位严谨的研究助理。" not in contents
    assert json.loads(json.dumps(payload["contents"]))[0]["parts"][0]["text"] == "讲讲退火"


def test_gemini_without_a_system_message_omits_the_field(monkeypatch):
    payload = _capture_gemini_payload(
        monkeypatch,
        [LLMChatMessage(role="user", content=[LLMChatTextContent(text="嗨")])],
    )
    assert "systemInstruction" not in payload


def test_gemini_payload_is_json_serializable(monkeypatch):
    payload = _capture_gemini_payload(monkeypatch, _system_and_user())
    json.dumps(payload)


def test_gemini_streaming_uses_the_same_system_instruction(monkeypatch):
    """流式与非流式必须同一口径。

    只修一条会让「同一个 Agent 在流式和非流式下人格权重不同」——而那个差别
    没有任何地方会报出来：两条路径都成功返回，只是其中一条的系统提示词
    退化成了普通用户消息。
    """
    from kirara_ai.plugins.llm_preset_adapters import gemini_adapter

    adapter = _wire(
        gemini_adapter.GeminiAdapter(gemini_adapter.GeminiConfig(api_key="test-key")),
        "gemini",
    )

    def fake_post(url, **kwargs):
        raise _Captured(kwargs.get("json"))

    monkeypatch.setattr(gemini_adapter.requests, "post", fake_post)
    request = LLMChatRequest(
        model="gemini-2.5-flash", messages=_system_and_user(), max_tokens=256
    )
    with pytest.raises(_Captured) as captured:
        next(iter(adapter.stream_chat(request)))

    payload = captured.value.payload
    json.dumps(payload)
    assert "systemInstruction" in payload
    serialized = json.dumps(payload["systemInstruction"], ensure_ascii=False)
    assert "你是一位严谨的研究助理。" in serialized
    # 同样不能留在 contents 里。
    assert "你是一位严谨的研究助理。" not in json.dumps(
        payload["contents"], ensure_ascii=False
    )
