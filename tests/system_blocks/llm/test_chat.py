import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.resilience import FailoverExecutionError
from kirara_ai.llm.format.message import LLMChatMessage, LLMChatTextContent, LLMToolResultContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage
from kirara_ai.llm.format.tool import CallableWrapper, Function, TextContent, Tool, ToolCall, ToolInputSchema
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import CombinedDispatchRule, RuleGroup, SimpleDispatchRule
from kirara_ai.workflow.core.dispatch.rules.base import DispatchRule
from kirara_ai.workflow.core.execution.executor import WorkflowExecutor
from kirara_ai.workflow.implementations.blocks.llm.chat import (ChatCompletion, ChatCompletionWithTools,
                                                                ChatMessageConstructor, ChatResponseConverter,
                                                                FunctionCalling)


def get_tools() -> list[Tool]:
    async def mock_tool_invoke(tool_call: ToolCall) -> LLMToolResultContent:
        return LLMToolResultContent(
            id=tool_call.id,
            name=tool_call.function.name,
            content=[TextContent(text="晴天，温度25°C")]
        )
    
    return [
        Tool(
            type="function",
            name="get_weather",
            description="Get the current weather in a given location",
            parameters=ToolInputSchema(
                type="object",
                properties = {
                    "location": {
                        "type": "string",
                        "description": "The city and state, e.g. San Francisco, CA"
                    }
                },
                required=["location"],
            ),
            invokeFunc=CallableWrapper(mock_tool_invoke)
        )
    ]

def get_llm_tool_calls() -> list[ToolCall]:
    return [
        ToolCall(
            id = "call_e33147bcb72525ed",
            function = Function(
                name="get_weather",
                arguments={"location": "San Francisco, CA"}
            )
        )
    ]

# 创建模拟的 LLM 类
class MockLLM:
    def chat(self, request):
        return LLMChatResponse(
            message=Message(
                role="assistant",
                content=[LLMChatTextContent(text="这是 AI 的回复")]
            ),
            model="gpt-3.5-turbo",
            usage=Usage(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30
            )
        )

class MockLLMWithToolCalls:
    def __init__(self, with_tool_calls=True):
        self.with_tool_calls = with_tool_calls
        self.call_count = 0
    
    def chat(self, request):
        self.call_count += 1
        
        # 第一次调用返回工具调用
        if self.with_tool_calls and self.call_count == 1:
            return LLMChatResponse(
                message=Message(
                    role="assistant",
                    content=[LLMChatTextContent(text="我需要查询天气")],
                    tool_calls=get_llm_tool_calls()
                ),
                model="gpt-3.5-turbo",
                usage=Usage(
                    prompt_tokens=10,
                    completion_tokens=20, 
                    total_tokens=30
                )
            )
        # 后续调用返回最终回复
        else:
            return LLMChatResponse(
                message=Message(
                    role="assistant",
                    content=[LLMChatTextContent(text="旧金山今天是晴天，温度25°C")]
                ),
                model="gpt-3.5-turbo",
                usage=Usage(
                    prompt_tokens=10,
                    completion_tokens=20, 
                    total_tokens=30
                )
            )

# 创建模拟的 LLMManager 类
class MockLLMManager(LLMManager):
    def __init__(self):
        self.mock_llm = MockLLM()

    def get_llm_id_by_ability(self, ability):
        return "gpt-3.5-turbo"

    def get_llm(self, model_id):
        return self.mock_llm
    
class MockLLMManagerWithToolCalls(LLMManager):
    def __init__(self, with_tool_calls=True):
        self.mock_llm = MockLLMWithToolCalls(with_tool_calls)

    def get_llm_id_by_ability(self, ability):
        return "gpt-3.5-turbo"

    def get_llm(self, model_id):
        return self.mock_llm


class _HttpError(Exception):
    def __init__(self, status_code: int):
        super().__init__(f"upstream status {status_code}")
        self.status_code = status_code


class _ResilientAdapter:
    def __init__(self, provider_name, outcomes):
        self.backend_name = provider_name
        self.outcomes = list(outcomes)
        self.calls = 0

    def chat(self, request):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _resilient_manager(model_adapters):
    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(
            name=adapter.backend_name,
            adapter="fake",
            priority=index,
            models=list(model_adapters),
            circuit_failure_threshold=10,
        )
        for index, adapter in enumerate(
            adapter for adapters in model_adapters.values() for adapter in adapters
        )
    ]
    manager = object.__new__(LLMManager)
    manager.config = config
    manager.active_backends = model_adapters
    manager._resilience_breakers = {}
    manager._resilience_attempts = {}
    manager._resilience_initialized = False
    manager._initialize_resilience_state()
    return manager


def _response(text="这是 AI 的回复", model="model-a"):
    return LLMChatResponse(
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text=text)],
        ),
        model=model,
        usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
    )

@pytest.fixture
def container():
    """创建一个带有模拟 LLM 提供者的容器"""
    container = DependencyContainer()

    # 模拟 LLMManager
    mock_llm_manager = MockLLMManager()

    # 模拟 LLM
    # mock_llm = MockLLM()

    # 模拟响应
    mock_response = LLMChatResponse(
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="这是 AI 的回复")]
        ),
        model="gpt-3.5-turbo",
        usage=Usage(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30
        )
    )
    # mock_llm.chat.return_value = mock_response

    # 模拟 WorkflowExecutor
    mock_executor = MagicMock(spec=WorkflowExecutor)

    # 创建一个在新线程中运行的事件循环
    def start_background_loop(loop):
        asyncio.set_event_loop(loop)
        loop.run_forever()
    
    # 创建新的事件循环
    new_loop = asyncio.new_event_loop()
    
    # 在新线程中启动事件循环
    t = threading.Thread(target=start_background_loop, args=(new_loop,), daemon=True)
    t.start()
    
    # 注册到容器
    container.register(LLMManager, mock_llm_manager)
    container.register(WorkflowExecutor, mock_executor)
    container.register(asyncio.AbstractEventLoop, new_loop)

    return container


@patch('kirara_ai.workflow.implementations.blocks.llm.chat.ChatMessageConstructor.execute')
def test_chat_message_constructor(mock_execute):
    """测试聊天消息构造器"""
    # 模拟 execute 方法的返回值
    mock_execute.return_value = {
        "llm_msg": [Message(role="user", content=[LLMChatTextContent(text="你好，AI！")])]
    }

    # 创建块
    block = ChatMessageConstructor()

    # 模拟容器
    mock_container = MagicMock(spec=DependencyContainer)
    block.container = mock_container

    # 执行块 - 基本用法
    user_msg = IMMessage(
        sender=ChatSender.from_c2c_chat(
            user_id="test_user", display_name="Test User"),
        message_elements=[TextMessage("你好，AI！")]
    )

    result = block.execute(
        user_msg=user_msg,
        memory_content="",
        system_prompt_format="",
        user_prompt_format=""
    )

    # 验证结果
    assert "llm_msg" in result
    assert isinstance(result["llm_msg"], list)
    assert len(result["llm_msg"]) > 0
    assert result["llm_msg"][0].role == "user"
    assert result["llm_msg"][0].content[0].text == "你好，AI！"


def test_chat_completion(container):
    # 创建消息列表
    messages = [
        Message(role="system", content=[LLMChatTextContent(text="你是一个助手")]),
        Message(role="user", content=[LLMChatTextContent(text="你好，AI！")])
    ]

    # 创建块 - 默认参数
    block = ChatCompletion()
    block.container = container

    # 执行块
    result = block.execute(prompt=messages)

    # 验证结果
    assert "resp" in result
    assert isinstance(result["resp"], LLMChatResponse)
    assert result["resp"].message.content[0].text == "这是 AI 的回复"


def test_chat_completion_uses_deployment_default_after_configured_chain_is_unavailable():
    """Bundled model choices stay ordered, with a local model as the last resort."""
    manager = MagicMock(spec=LLMManager)
    manager.get_llm_id_by_ability.return_value = "local-text-model"
    requested_models = []

    def get_llm(model_id):
        requested_models.append(model_id)
        return MockLLM() if model_id == "local-text-model" else None

    manager.get_llm.side_effect = get_llm
    container = DependencyContainer()
    container.register(LLMManager, manager)
    block = ChatCompletion(
        model_name="unavailable-primary",
        fallback_model_1="unavailable-secondary",
        max_retries=1,
        # 该兜底现在是显式开关，默认关闭，需要时才打开
        use_deployment_default_model=True,
    )
    block.container = container
    messages = [Message(role="user", content=[LLMChatTextContent(text="测试")])]

    result = block.execute(prompt=messages)

    assert result["resp"].message.content[0].text == "这是 AI 的回复"
    assert requested_models == [
        "unavailable-primary",
        "unavailable-secondary",
        "local-text-model",
    ]


def test_chat_completion_does_not_silently_substitute_the_deployment_default_model():
    """未开启开关时，绝不能偷偷换成本机默认模型作答。"""
    manager = MagicMock(spec=LLMManager)
    manager.get_llm_id_by_ability.return_value = "local-text-model"
    requested_models = []

    def get_llm(model_id):
        requested_models.append(model_id)
        return MockLLM() if model_id == "local-text-model" else None

    manager.get_llm.side_effect = get_llm
    container = DependencyContainer()
    container.register(LLMManager, manager)
    block = ChatCompletion(
        model_name="unavailable-primary",
        fallback_model_1="unavailable-secondary",
        max_retries=1,
    )
    block.container = container
    messages = [Message(role="user", content=[LLMChatTextContent(text="测试")])]

    with pytest.raises(Exception):
        block.execute(prompt=messages)

    assert requested_models == ["unavailable-primary", "unavailable-secondary"]


def test_chat_completion_still_uses_the_default_model_when_nothing_is_configured():
    """一个模型都没选时仍然使用本机默认模型，否则工作流根本跑不起来。"""
    manager = MagicMock(spec=LLMManager)
    manager.get_llm_id_by_ability.return_value = "local-text-model"
    requested_models = []

    def get_llm(model_id):
        requested_models.append(model_id)
        return MockLLM()

    manager.get_llm.side_effect = get_llm
    container = DependencyContainer()
    container.register(LLMManager, manager)
    block = ChatCompletion(max_retries=1)
    block.container = container
    messages = [Message(role="user", content=[LLMChatTextContent(text="测试")])]

    result = block.execute(prompt=messages)

    assert result["resp"].message.content[0].text == "这是 AI 的回复"
    assert requested_models == ["local-text-model"]


def test_chat_completion_uses_resilient_provider_queue_before_model_fallback():
    primary = _ResilientAdapter("primary", [_HttpError(503), _HttpError(503)])
    secondary = _ResilientAdapter("secondary", [_response(model="model-a")])
    fallback = _ResilientAdapter("fallback", [_response(text="fallback model", model="model-b")])
    manager = _resilient_manager({
        "model-a": [primary, secondary],
        "model-b": [fallback],
    })
    manager.get_llm = MagicMock(side_effect=AssertionError("legacy get_llm path used"))
    container = DependencyContainer()
    container.register(LLMManager, manager)
    block = ChatCompletion(
        model_name="model-a",
        fallback_model_1="model-b",
        max_retries=2,
        retry_delay=0,
    )
    block.container = container

    result = block.execute(
        prompt=[Message(role="user", content=[LLMChatTextContent(text="测试")])]
    )

    assert result["resp"].model == "model-a"
    assert primary.calls == 2
    assert secondary.calls == 1
    assert fallback.calls == 0
    manager.get_llm.assert_not_called()


def test_chat_completion_stops_model_fallback_after_authentication_failure():
    primary = _ResilientAdapter("primary", [_HttpError(401)])
    fallback = _ResilientAdapter("fallback", [_response(model="model-b")])
    manager = _resilient_manager({
        "model-a": [primary],
        "model-b": [fallback],
    })
    container = DependencyContainer()
    container.register(LLMManager, manager)
    block = ChatCompletion(
        model_name="model-a",
        fallback_model_1="model-b",
        max_retries=3,
        retry_delay=0,
    )
    block.container = container

    with pytest.raises(FailoverExecutionError):
        block.execute(
            prompt=[Message(role="user", content=[LLMChatTextContent(text="测试")])]
        )

    assert primary.calls == 1
    assert fallback.calls == 0


def test_chat_completion_moves_to_next_model_after_retryable_provider_exhaustion():
    primary = _ResilientAdapter("primary", [_HttpError(503)])
    secondary = _ResilientAdapter("secondary", [_HttpError(429)])
    fallback = _ResilientAdapter("fallback", [_response(model="model-b")])
    manager = _resilient_manager({
        "model-a": [primary, secondary],
        "model-b": [fallback],
    })
    container = DependencyContainer()
    container.register(LLMManager, manager)
    block = ChatCompletion(
        model_name="model-a",
        fallback_model_1="model-b",
        max_retries=1,
        retry_delay=0,
    )
    block.container = container

    result = block.execute(
        prompt=[Message(role="user", content=[LLMChatTextContent(text="测试")])]
    )

    assert result["resp"].model == "model-b"
    assert primary.calls == 1
    assert secondary.calls == 1
    assert fallback.calls == 1


def test_chat_response_converter():
    """测试聊天响应转换器"""
    # 创建聊天响应
    chat_response = LLMChatResponse(
        message=Message(
            role="assistant",
            content=[LLMChatTextContent(text="这是 AI 的回复")]
        ),
        model="gpt-3.5-turbo",
        usage=Usage(
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30
        )
    )

    # 创建块
    block = ChatResponseConverter()

    # 模拟容器
    mock_container = MagicMock(spec=DependencyContainer)
    # 模拟 get_bot_sender 方法
    mock_bot_sender = ChatSender.from_c2c_chat(
        user_id="bot", display_name="Bot")
    mock_container.resolve = MagicMock(
        side_effect=lambda x: mock_bot_sender if x == ChatSender.get_bot_sender else None)
    block.container = mock_container

    # 执行块
    result = block.execute(resp=chat_response)

    # 验证结果
    assert "msg" in result
    assert isinstance(result["msg"], IMMessage)
    assert "这是 AI 的回复" in result["msg"].content
def test_chat_completion_with_tools(container):
    """测试工具调用块"""
    container.register(LLMManager, MockLLMManagerWithToolCalls(with_tool_calls=True))
    
    # 创建消息列表
    messages = [
        LLMChatMessage(role="system", content=[LLMChatTextContent(text="你是一个助手")]),
        LLMChatMessage(role="user", content=[LLMChatTextContent(text="旧金山今天天气如何？")])
    ]

    # 创建工具列表
    tools = get_tools()

    # 创建块
    block = ChatCompletionWithTools(model_name="gpt-3.5-turbo", max_iterations=3)
    block.container = container

    # 执行块
    result = block.execute(msg=messages, tools=tools)

    # 验证结果
    assert "resp" in result
    assert "iteration_msgs" in result
    assert isinstance(result["resp"], LLMChatResponse)
    assert isinstance(result["iteration_msgs"], list)
    assert len(result["iteration_msgs"]) >= 2  # 至少包含工具调用和最终回复
    
    # 验证工具调用过程
    assert result["iteration_msgs"][0].tool_calls is not None
    assert result["iteration_msgs"][0].tool_calls[0].function.name == "get_weather"
    
    # 验证最终回复
    assert "旧金山今天是晴天" in result["resp"].message.content[0].text

def test_chat_completion_with_tools_no_tool_calls(container):
    """测试工具调用块 - 无工具调用情况"""

    # 注册到容器 - 使用不会进行工具调用的模拟
    container.register(LLMManager, MockLLMManagerWithToolCalls(with_tool_calls=False))

    # 创建消息列表
    messages = [
        LLMChatMessage(role="system", content=[LLMChatTextContent(text="你是一个助手")]),
        LLMChatMessage(role="user", content=[LLMChatTextContent(text="你好，AI！")])
    ]

    # 创建工具列表
    tools = get_tools()

    # 创建块
    block = ChatCompletionWithTools(model_name="gpt-3.5-turbo", max_iterations=3)
    block.container = container

    # 执行块
    result = block.execute(msg=messages, tools=tools)

    # 验证结果 - 直接返回响应，没有工具调用
    assert "resp" in result
    assert "iteration_msgs" in result
    assert isinstance(result["resp"], LLMChatResponse)
    assert isinstance(result["iteration_msgs"], list)
    assert len(result["iteration_msgs"]) == 0  # 无消息，因为没有工具调用


def test_chat_completion_with_tools_uses_resilient_boundary_for_every_iteration(container):
    adapter = _ResilientAdapter(
        "primary",
        [
            LLMChatResponse(
                message=Message(
                    role="assistant",
                    content=[LLMChatTextContent(text="我需要查询天气")],
                    tool_calls=get_llm_tool_calls(),
                ),
                model="model-a",
            ),
            _response(text="旧金山今天是晴天", model="model-a"),
        ],
    )
    manager = _resilient_manager({"model-a": [adapter]})
    manager.get_llm = MagicMock(side_effect=AssertionError("legacy get_llm path used"))
    container.register(LLMManager, manager)
    block = ChatCompletionWithTools(model_name="model-a", max_iterations=3)
    block.container = container

    result = block.execute(
        msg=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="天气")])],
        tools=get_tools(),
    )

    assert result["resp"].message.content[0].text == "旧金山今天是晴天"
    assert adapter.calls == 2
    assert len(manager.get_resilience_status()[0]["recent_attempts"]) == 2
    manager.get_llm.assert_not_called()


# ---- function_calling：只联系模型、不执行工具的低层区块 ----
#
# FunctionCalling 与 ChatCompletionWithTools 是两个不同的区块：后者内部自动完成
# 「模型请求工具 → 执行 → 回传结果」的循环，前者只把请求发给模型，然后按模型是否
# 请求了工具，二选一地输出 tool_call 或 resp，工具的实际执行交给用户自己的节点。
# 这个二选一的输出契约是下游节点连线的依据，必须有测试守住。


class MockLLMFunctionCalling:
    """总是返回带 tool_calls 的响应，模拟模型决定调用工具。"""

    def chat(self, request):
        return LLMChatResponse(
            message=Message(
                role="assistant",
                content=[LLMChatTextContent(text="这是 AI 的回复")],
                tool_calls=get_llm_tool_calls()
            ),
            model="gpt-3.5-turbo",
            usage=Usage(
                prompt_tokens=10,
                completion_tokens=20,
                total_tokens=30
            )
        )


class MockLLMManagerFunctionCalling(LLMManager):
    def __init__(self):
        self.mock_llm = MockLLMFunctionCalling()

    def get_llm_id_by_ability(self, ability):
        return "gpt-3.5-turbo"

    def get_llm(self, model_id):
        return self.mock_llm


def test_chat_function_calling():
    """测试函数调用块：模型请求工具时走 tool_call，否则走 resp。"""
    chat_request = LLMChatRequest(
        model="gpt-3.5-turbo",
        tools=get_tools(),
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="今天天气如何？")])]
    )

    container = DependencyContainer()
    container.register(LLMManager, MockLLMManagerFunctionCalling())

    block = FunctionCalling(model_name="gpt-3.5-turbo")
    block.container = container

    # step 1：模型请求调用工具，只输出 tool_call
    result = block.execute(request_body=chat_request)

    assert "tool_call" in result
    assert "resp" not in result
    assert isinstance(result["tool_call"], LLMChatResponse)
    assert result["tool_call"].message.content[0].text == "这是 AI 的回复"
    assert result["tool_call"].message.tool_calls == get_llm_tool_calls()

    # step 2：换成不请求工具的模型，只输出 resp
    container.register(LLMManager, MockLLMManager())
    result = block.execute(request_body=chat_request)

    assert "resp" in result
    assert "tool_call" not in result
    assert isinstance(result["resp"], LLMChatResponse)
    assert result["resp"].message.content[0].text == "这是 AI 的回复"
    assert result["resp"].message.tool_calls is None


def test_chat_function_calling_requires_a_model_name():
    """预设里 model_name 故意留空，报错必须指名是哪个节点没选模型。"""
    block = FunctionCalling(model_name="")
    block.container = DependencyContainer()

    with pytest.raises(ValueError) as error:
        block.execute(request_body=LLMChatRequest(
            model="",
            tools=get_tools(),
            messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="今天天气如何？")])]
        ))

    assert "function_calling" in str(error.value)


def test_chat_function_calling_falls_back_to_the_next_model():
    """主模型不可用时应换用备用模型，而不是直接失败。"""

    class OnlyFallbackAvailableManager(LLMManager):
        def __init__(self):
            self.mock_llm = MockLLM()
            self.requested = []

        def get_llm_id_by_ability(self, ability):
            return "gpt-3.5-turbo"

        def get_llm(self, model_id):
            self.requested.append(model_id)
            # 主模型查不到，备用模型可用
            return None if model_id == "missing-primary" else self.mock_llm

    manager = OnlyFallbackAvailableManager()
    container = DependencyContainer()
    container.register(LLMManager, manager)

    block = FunctionCalling(
        model_name="missing-primary",
        fallback_model_1="gpt-3.5-turbo",
        max_retries=1,
        retry_delay=0,
    )
    block.container = container

    result = block.execute(request_body=LLMChatRequest(
        model="missing-primary",
        tools=get_tools(),
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="今天天气如何？")])]
    ))

    assert manager.requested == ["missing-primary", "gpt-3.5-turbo"]
    assert "resp" in result
    assert result["resp"].message.content[0].text == "这是 AI 的回复"


def test_chat_function_calling_uses_resilient_provider_queue():
    primary = _ResilientAdapter("primary", [_HttpError(503)])
    secondary = _ResilientAdapter("secondary", [_response(model="model-a")])
    manager = _resilient_manager({"model-a": [primary, secondary]})
    manager.get_llm = MagicMock(side_effect=AssertionError("legacy get_llm path used"))
    container = DependencyContainer()
    container.register(LLMManager, manager)
    block = FunctionCalling(model_name="model-a", max_retries=1, retry_delay=0)
    block.container = container

    result = block.execute(request_body=LLMChatRequest(
        model="model-a",
        tools=get_tools(),
        messages=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="今天天气如何？")])],
    ))

    assert result["resp"].model == "model-a"
    assert primary.calls == 1
    assert secondary.calls == 1
    manager.get_llm.assert_not_called()


# ---- 采样温度：类型放宽 + 调度规则 metadata 生效 ----

class RecordingLLM:
    """记录收到的请求，用于断言温度确实传到了消费方。"""

    def __init__(self):
        self.requests = []

    def chat(self, request):
        self.requests.append(request)
        return LLMChatResponse(
            message=Message(role="assistant", content=[LLMChatTextContent(text="这是 AI 的回复")]),
            model="gpt-3.5-turbo",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
        )


def _recording_container(llm: RecordingLLM) -> DependencyContainer:
    manager = MagicMock(spec=LLMManager)
    manager.get_llm_id_by_ability.return_value = "local-text-model"
    manager.get_llm.return_value = llm
    container = DependencyContainer()
    container.register(LLMManager, manager)
    return container


def _rule_with_metadata(metadata) -> CombinedDispatchRule:
    return CombinedDispatchRule(
        rule_id="chat_normal",
        name="群聊 AI 对话",
        workflow_id="chat:normal",
        rule_groups=[RuleGroup(operator="or", rules=[SimpleDispatchRule(type="fallback", config={})])],
        metadata=metadata,
    )


def test_llm_chat_request_keeps_float_temperature():
    """0.7 这类小数必须能原样保留，不能被截断成整数。"""
    req = LLMChatRequest(temperature=0.7, top_p=0.95)

    assert req.temperature == 0.7
    assert req.top_p == 0.95
    assert req.model_dump()["temperature"] == 0.7


def test_llm_chat_request_still_accepts_int_temperature():
    """向后兼容：旧配置里写整数 1 仍然可以校验通过。"""
    req = LLMChatRequest(temperature=1, top_p=1, frequency_penalty=0, presence_penalty=0, max_tokens=512)

    assert req.temperature == 1.0
    assert isinstance(req.temperature, float)
    assert req.top_p == 1.0
    assert req.frequency_penalty == 0.0
    assert req.presence_penalty == 0.0
    # max_tokens 是 token 数量，仍然保持整数
    assert req.max_tokens == 512
    assert isinstance(req.max_tokens, int)


def test_chat_completion_uses_dispatch_rule_metadata_temperature():
    """节点没配温度时，命中的调度规则 metadata.temperature 应当生效。"""
    llm = RecordingLLM()
    container = _recording_container(llm)
    container.register(DispatchRule, _rule_with_metadata({"category": "chat", "temperature": 0.7}))
    block = ChatCompletion(model_name="gpt-3.5-turbo", max_retries=1)
    block.container = container

    block.execute(prompt=[Message(role="user", content=[LLMChatTextContent(text="测试")])])

    assert llm.requests[0].temperature == 0.7


def test_chat_completion_block_config_overrides_rule_metadata():
    """节点上显式配置的温度优先于规则 metadata。"""
    llm = RecordingLLM()
    container = _recording_container(llm)
    container.register(DispatchRule, _rule_with_metadata({"temperature": 0.9}))
    block = ChatCompletion(model_name="gpt-3.5-turbo", max_retries=1, temperature=0.1)
    block.container = container

    block.execute(prompt=[Message(role="user", content=[LLMChatTextContent(text="测试")])])

    assert llm.requests[0].temperature == 0.1


def test_chat_completion_ignores_invalid_rule_metadata_temperature():
    """非法温度一律忽略，落回模型自身默认值，不能把坏值发给服务端。"""
    llm = RecordingLLM()
    container = _recording_container(llm)
    container.register(DispatchRule, _rule_with_metadata({"temperature": "很高"}))
    block = ChatCompletion(model_name="gpt-3.5-turbo", max_retries=1)
    block.container = container

    block.execute(prompt=[Message(role="user", content=[LLMChatTextContent(text="测试")])])

    assert llm.requests[0].temperature is None

    llm_out_of_range = RecordingLLM()
    container_out_of_range = _recording_container(llm_out_of_range)
    container_out_of_range.register(DispatchRule, _rule_with_metadata({"temperature": 9}))
    block_out_of_range = ChatCompletion(model_name="gpt-3.5-turbo", max_retries=1)
    block_out_of_range.container = container_out_of_range

    block_out_of_range.execute(prompt=[Message(role="user", content=[LLMChatTextContent(text="测试")])])

    assert llm_out_of_range.requests[0].temperature is None


def test_chat_completion_without_dispatch_rule_sends_no_temperature():
    """没有命中规则、也没有节点配置时，请求里不应出现 temperature。"""
    llm = RecordingLLM()
    container = _recording_container(llm)
    block = ChatCompletion(model_name="gpt-3.5-turbo", max_retries=1)
    block.container = container

    block.execute(prompt=[Message(role="user", content=[LLMChatTextContent(text="测试")])])

    assert llm.requests[0].temperature is None


def test_chat_completion_with_tools_honors_rule_metadata_temperature(container):
    """带工具调用的对话块同样读取规则 metadata 的温度。"""
    llm = RecordingLLM()
    manager = MagicMock(spec=LLMManager)
    manager.get_llm.return_value = llm
    container.register(LLMManager, manager)
    container.register(DispatchRule, _rule_with_metadata({"temperature": 0.9}))
    block = ChatCompletionWithTools(model_name="gpt-3.5-turbo", max_iterations=2)
    block.container = container

    block.execute(msg=[LLMChatMessage(role="user", content=[LLMChatTextContent(text="你好")])], tools=[])

    assert llm.requests[0].temperature == 0.9


def test_chat_completion_declares_temperature_config_for_webui():
    """WebUI 节点配置面板由 block 声明的 schema 驱动，这里断言字段被正确暴露。"""
    from kirara_ai.workflow.core.block.registry import BlockRegistry

    registry = BlockRegistry()
    registry.register("chat_completion", "internal", ChatCompletion)
    _, _, configs = registry.extract_block_info(ChatCompletion)

    assert "temperature" in configs
    assert configs["temperature"].type == "float"
    assert configs["temperature"].required is False
    assert configs["temperature"].label == "采样温度"
    assert "0.0~2.0" in (configs["temperature"].description or "")
