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
