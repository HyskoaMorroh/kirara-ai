from typing import Any, List, Literal, Optional, Union

from pydantic import BaseModel

from kirara_ai.llm.format.message import LLMChatMessage
from kirara_ai.llm.rectifier import RectifierConfig
from .tool import Tool as ExecutableTool

class ToolParameters(BaseModel):
    """
    规范化工具参数的格式

    Attributes:
        type (Literal["object"]): 参数的类型
        properties (dict): 工具属性，参考 openai api 的规范
        required (list[str]): 必填参数的名称列表
        additionalProperties (Optional[bool]): 是否允许额外的键值对
    """
    type: Literal["object"] = "object"
    properties: dict
    required: list[str]
    additionalProperties: Optional[bool] = False


class Tool(BaseModel):
    """
    这是传递给 llm 的工具信息

    Attributes:
        type (Optional[Literal["function"]]): 工具的类型
        name (str): 工具的名称
        description (str): 工具的描述
        parameters (ToolParameters): 工具的参数
        strict (Optional[bool]): 是否严格调用, openai api专属
    """
    type: Optional[Literal["function"]] = "function"
    name: str
    description: str
    parameters: ToolParameters
    strict: Optional[bool] = False

class ResponseFormat(BaseModel):
    type: Optional[str] = None


#: 受支持的推理强度档位。
#:
#: 各家 API 的字段名与取值都不同（OpenAI 是 `reasoning_effort` 字符串枚举，
#: Claude 是 `thinking.budget_tokens` 整数，Gemini 是
#: `thinkingConfig.thinkingBudget` 整数），因此这里只定义一个与厂商无关的档位，
#: 由各适配器翻译成自家字段。直接把某一家的字段名当通用参数透传，
#: 会让另外两家的请求被上游拒绝。
ReasoningEffort = Literal["low", "medium", "high", "max"]

class LLMChatRequest(BaseModel):
    """
    Attributes:
        tool_choice (Union[dict, Literal["auto", "any", "none"]]):
            "
            注意由于大模型对于这个接口实现不同，本次暂不实现tool_choice的功能。
            tool_choice这个参数告诉llmMessage应该如何选择调用的工具。
            "
    """

    messages: List[LLMChatMessage] = []
    model: Optional[str] = None
    # 采样类参数在各家 API（OpenAI / Anthropic / Gemini）里都是浮点数，
    # 原先标注为 int 会让 0.7 这类取值无法表达。pydantic 会把 int 自动
    # 转成 float，因此旧配置里写 1 仍然可以正常校验。
    frequency_penalty: Optional[float] = None
    # max_tokens 是 token 个数，本来就应该是整数，保持 int 不变。
    max_tokens: Optional[int] = None
    presence_penalty: Optional[float] = None
    response_format: Optional[ResponseFormat] = None
    stop: Optional[Any] = None
    stream: Optional[bool] = None
    stream_options: Optional[Any] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    # 规范tool传递
    tools: Optional[list[Union[Tool, ExecutableTool]]] = None
    # tool_choice各家目前标准不尽相同，暂不向用户提供更改这个值的选项
    tool_choice: Optional[Any] = None
    logprobs: Optional[bool] = None
    top_logprobs: Optional[Any] = None
    #: 推理强度。`None` 表示不指定，保持上游默认——新增字段不得改变既有行为。
    #: 由各适配器翻译成自家字段，见 `ReasoningEffort` 的说明。
    reasoning_effort: Optional[ReasoningEffort] = None
    #: 请求整流开关（需求 8）。`None` 表示按适配器默认（全开）处理。
    #:
    #: 之所以挂在请求上而不是让适配器读供应商配置：适配器只拿到凭据配置
    #: （例如 `ClaudeConfig`），读不到 `LLMBackendConfig`。而整流开关是
    #: **每供应商**的——队列里 P1 是自建 Anthropic 网关、P2 是不支持思考的
    #: 兼容接口时，两者必须各按自己的配置走。与 `reasoning_effort` 同一条通道。
    rectifier: Optional[RectifierConfig] = None
