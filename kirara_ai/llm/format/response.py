from enum import Enum
from typing import List, Optional

from pydantic import BaseModel

from kirara_ai.llm.format.message import LLMChatMessage
from kirara_ai.llm.format.tool import Function, ModelTypes, ToolCall

# 3.2.0 及更早版本在本模块内定义了 Function / ToolCall / ModelTypes，
# 现已统一到 kirara_ai.llm.format.tool，这里保留再导出以兼容按旧路径导入的插件。
__all__ = [
    "Function",
    "ToolCall",
    "ModelTypes",
    "Message",
    "Usage",
    "UsageSource",
    "LLMChatResponse",
]


class Message(LLMChatMessage):
    tool_calls: Optional[List[ToolCall]] = None
    finish_reason: Optional[str] = None

class UsageSource(str, Enum):
    """Where a usage record's numbers came from.

    需求把用量分成四类：真实 Token、供应商返回的 Token、估算 Token、未知。
    这里只有三个成员，是因为前两类在本项目的数据链路上**无法区分**——
    我们唯一能拿到的「真实」就是供应商在响应里回报的那份，没有第二个独立信源
    可以用来交叉验证。为这两者各留一个成员，只会得到一个永远没有生产者的枚举值
    （`ESTIMATED` 之前正是这样：有定义、有测试、主链路上零调用）。

    因此 ``PROVIDER`` 同时承担「真实」与「供应商返回」两个语义，并在文档里写明
    这一点；如果将来接入了独立的计量来源（例如网关侧的旁路计数），
    再新增 ``MEASURED`` 成员并把它与 ``PROVIDER`` 分开才有意义。
    """

    #: 供应商在响应里回报的用量。这是本项目能获得的最高可信度，
    #: 因此也充当需求里的「真实 Token」。
    PROVIDER = "provider"
    #: 供应商未回报，由本地估算器按脚本感知的字符类别估出。不作为账单依据。
    ESTIMATED = "estimated"
    #: 既没有供应商用量，也没有可估算的内容。绝不写成 0——
    #: 「0」是一个断言，「未知」不是，前者更糟。
    UNKNOWN = "unknown"


class Usage(BaseModel):
    completion_tokens: Optional[int] = None
    prompt_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    cached_tokens: Optional[int] = None
    cache_write_tokens: Optional[int] = None
    source: UsageSource = UsageSource.UNKNOWN

class LLMChatResponse(BaseModel):
    model: Optional[str] = None
    usage: Optional[Usage] = None
    message: Message
