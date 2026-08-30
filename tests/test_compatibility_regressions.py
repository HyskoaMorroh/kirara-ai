from datetime import timedelta

import pytest

from kirara_ai.config.global_config import GlobalConfig, WebConfig
from kirara_ai.im.message import IMMessage, MessageElement
from kirara_ai.im.sender import ChatSender
from kirara_ai.im.text_render import convert_markdown_tables
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.format.message import LLMToolCallContent
from kirara_ai.llm.format.request import LLMChatRequest, Tool, ToolParameters
from kirara_ai.llm.format.tool import Function, LLMToolResultContent, ToolCall
from kirara_ai.memory.composes.builtin_composes import DefaultMemoryDecomposer, MultiElementDecomposer
from kirara_ai.memory.composes.composer_strategy import IMMessageProcessor
from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text
from kirara_ai.plugins.llm_preset_adapters.claude_adapter import resolve_tool_calls as resolve_claude_tool_calls
from kirara_ai.plugins.llm_preset_adapters.gemini_adapter import resolve_function_call
from kirara_ai.plugins.llm_preset_adapters.ollama_adapter import resolve_tool_calls as resolve_ollama_tool_calls
from kirara_ai.web.app import WebServer
from kirara_ai.web.auth.services import AuthService


class UnknownMessageElement(MessageElement):
    def to_dict(self):
        return {"type": "unknown"}

    def to_plain(self):
        return "unknown message"


def test_legacy_tool_models_accept_optional_fields_and_arbitrary_results():
    function = Function()
    tool_call = ToolCall()
    tool_result = LLMToolResultContent(name="legacy_tool", content={"status": "ok"})
    tool_content = LLMToolCallContent(name="legacy_tool")

    assert function.name is None
    assert tool_call.id is None
    assert tool_call.function is None
    assert tool_result.id is None
    assert tool_result.content == {"status": "ok"}
    assert tool_content.id is None


def test_legacy_request_tool_model_coexists_with_executable_tools():
    legacy_tool = Tool(
        name="legacy_tool",
        description="legacy integration",
        parameters=ToolParameters(properties={}, required=[]),
    )
    request = LLMChatRequest(messages=[], tools=[legacy_tool])

    assert request.tools == [legacy_tool]


def test_legacy_adapter_tool_call_helpers_remain_available():
    claude_calls = resolve_claude_tool_calls(
        [{"type": "tool_use", "id": "call-1", "name": "lookup", "input": {"id": 1}}]
    )
    gemini_calls = resolve_function_call([LLMToolCallContent(name="lookup", parameters={"id": 1})])
    ollama_calls = resolve_ollama_tool_calls(
        {"message": {"tool_calls": [{"function": {"name": "lookup", "arguments": {"id": 1}}}]}}
    )

    assert claude_calls[0].function.name == "lookup"
    assert gemini_calls[0].model == "gemini"
    assert ollama_calls[0].model == "ollama"


def test_legacy_memory_helpers_and_unknown_elements_are_preserved():
    decomposer = DefaultMemoryDecomposer()
    multi_element_decomposer = MultiElementDecomposer()
    sender = ChatSender.from_c2c_chat("user-1", "User")
    message = IMMessage(sender=sender, message_elements=[UnknownMessageElement()])
    rendered = IMMessageProcessor(DependencyContainer()).process(message, {})

    assert decomposer.get_time_str(timedelta(minutes=5)) == "5分钟前"
    assert multi_element_decomposer.create_llm_chat_message("", "user", sender) is None
    assert rendered.count("unknown message") == 1


def test_placeholder_web_secret_is_replaced_before_auth_service_initialization():
    placeholder = "please-change-this-to-a-secure-secret-key"
    container = DependencyContainer()
    config = GlobalConfig(web=WebConfig(secret_key=placeholder, password_file="test_password.hash"))
    container.register(GlobalConfig, config)

    WebServer(container)
    auth_service = container.resolve(AuthService)

    assert config.web.secret_key != placeholder
    assert auth_service.secret_key == config.web.secret_key


def test_relative_web_password_file_is_anchored_to_data_path(tmp_path, monkeypatch):
    from kirara_ai.web import app as web_app

    data_path = tmp_path / "vps-data"
    monkeypatch.setattr(web_app, "DATA_PATH", str(data_path))
    container = DependencyContainer()
    config = GlobalConfig(
        web=WebConfig(
            secret_key="test-secret-key",
            password_file="./data/web/password.hash",
        )
    )
    container.register(GlobalConfig, config)

    WebServer(container)

    assert container.resolve(AuthService).password_file == (
        data_path / "web" / "password.hash"
    ).resolve()


def test_relative_web_password_file_cannot_escape_data_path(tmp_path, monkeypatch):
    from kirara_ai.web import app as web_app

    monkeypatch.setattr(web_app, "DATA_PATH", str(tmp_path / "vps-data"))

    with pytest.raises(ValueError, match="must remain inside DATA_PATH"):
        web_app.resolve_password_file_path("../password.hash")


def test_wecom_and_telegram_table_rendering_keep_readable_structure():
    source = "## **标题**\n\n| *列一* | **列二** |\n| --- | --- |\n| 内容 | `代码` |"
    wecom = markdown_to_plain_text(source)
    telegram = convert_markdown_tables(source, fenced=True)

    assert "##" not in wecom
    assert "**" not in wecom
    assert "┌" in wecom and "└" in wecom
    assert "```" in telegram
    assert "┌" in telegram and "└" in telegram


def test_wecom_degrades_math_like_the_other_channels():
    """企业微信也必须做数学降级，平台差异只应体现在渲染层。

    此前 WeCom 路径完全跳过 `_clean_latex`，于是同一段模型回复在 QQ 上是
    `T → 0`、在企业微信上是原始的 `$T \to 0$`。「有的平台处理了、有的没有」
    不是平台差异，是漏了一步。
    """
    from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

    rendered = markdown_to_plain_text(r"收敛到 $T \to 0$，面积 $a \times b$。")

    assert "→" in rendered
    assert "×" in rendered
    assert "$" not in rendered
    assert r"\to" not in rendered


def test_wecom_keeps_fenced_code_out_of_the_math_pass():
    """代码块内部的 LaTeX 字面量不得被降级。"""
    from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

    rendered = markdown_to_plain_text(
        r"说明 $x \to 0$" + "\n\n```python\n" + r"formula = r'$x \to 0$'" + "\n```"
    )

    assert "→" in rendered
    assert r"formula = r'$x \to 0$'" in rendered


def test_wecom_wide_tables_degrade_to_fields_too():
    """企业微信同样没有可靠等宽字体，宽表必须走纵向布局。"""
    from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

    header = "| " + " | ".join(f"列名称{index}" for index in range(1, 9)) + " |"
    separator = "|" + "---|" * 8
    row = "| " + " | ".join(f"数据内容{index}" for index in range(1, 9)) + " |"

    rendered = markdown_to_plain_text("\n".join([header, separator, row]))

    assert "┌" not in rendered
    assert "列名称1：数据内容1" in rendered


def test_wecom_narrow_tables_still_use_the_box_layout():
    """窄表观感不变：既有部署看到的仍是框线表。"""
    from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

    rendered = markdown_to_plain_text("| 参数 | 值 |\n| --- | --- |\n| T | 0 |")

    assert "┌" in rendered and "└" in rendered


def test_telegram_degrades_math_like_the_other_channels():
    """Telegram 也必须做数学降级：MarkdownV2 同样不渲染 LaTeX。

    需求 19.1 点名 QQ、Telegram、WeCom 三个平台，要求差异只体现在渲染层。
    不降级时同一段模型回复在 QQ 上是 `T → 0`、在 Telegram 上是原始 `$T \to 0$`
    ——那不是平台差异，是漏了一步。
    """
    from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter

    rendered = TelegramAdapter.render_text(r"收敛到 $T \to 0$。")

    assert "→" in rendered
    assert "to" not in rendered, "LaTeX 命令名不应作为裸单词残留"


def test_telegram_keeps_fenced_code_out_of_the_math_pass():
    """代码块内部的 LaTeX 字面量不得被降级。"""
    from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter

    rendered = TelegramAdapter.render_text(
        r"说明 $x \to 0$" + "\n\n```python\n" + r"formula = r'$x \to 0$'" + "\n```"
    )

    assert "→" in rendered
    assert "formula" in rendered


def test_telegram_wide_tables_degrade_like_the_other_channels():
    """宽表在 Telegram 上同样走纵向字段布局。"""
    from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter

    header = "| " + " | ".join(f"列名称{index}" for index in range(1, 9)) + " |"
    separator = "|" + "---|" * 8
    row = "| " + " | ".join(f"数据内容{index}" for index in range(1, 9)) + " |"

    rendered = TelegramAdapter.render_text("\n".join([header, separator, row]))

    assert "┌" not in rendered
    assert "列名称1" in rendered and "数据内容1" in rendered


def test_all_three_channels_degrade_the_same_formula():
    """同一段公式在三个渠道上必须得到同样的可读符号。

    这条用例的价值不在单个渠道，而在「三者一致」：需求 19.1 要求平台差异只落在
    渲染层，因此降级结果本身不该因渠道而异。
    """
    from kirara_ai.im.text_render import render_plain_text
    from kirara_ai.plugins.im_telegram_adapter.adapter import TelegramAdapter
    from kirara_ai.plugins.im_wecom_adapter.delegates import markdown_to_plain_text

    source = r"收敛到 $T \to 0$，面积 $a \times b$。"

    for rendered in (
        render_plain_text(source),
        markdown_to_plain_text(source),
        TelegramAdapter.render_text(source),
    ):
        assert "→" in rendered, f"缺少箭头降级：{rendered!r}"
        assert "×" in rendered, f"缺少乘号降级：{rendered!r}"
