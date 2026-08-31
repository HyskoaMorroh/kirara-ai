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

    「真实」与「供应商返回」在本项目里没有第二个独立信源可以交叉验证——
    我们唯一能拿到的「真实」就是供应商在响应里回报的那份。按「可信度是否经过
    独立核对」去拆这两者，只会得到一个永远没有生产者的枚举值
    （``ESTIMATED`` 之前正是这样：有定义、有测试、主链路上零调用）。

    但有一个**真实存在且可区分**的差别：多数 OpenAI 兼容端点只回报
    ``prompt_tokens`` / ``completion_tokens``，不报缓存两维。这类响应与
    「四维齐全」的可信度完全不同——缺失维度按 0 计价，总额是补出来的，
    而缓存读取的单价通常只有输入 Token 的 1/5 到 1/10、缓存写入往往更贵。
    一份「缺失维度按 0」的账单在缓存密集的部署上会系统性偏低，
    而页面上没有任何迹象表明它被补过。

    因此这一层拆成 ``PROVIDER`` 与 ``PROVIDER_PARTIAL``：四个成员的处置
    各不相同，没有一个是空转的。
    """

    #: 供应商回报了**全部四个维度**的用量。这是本项目能获得的最高可信度，
    #: 因此也充当需求里的「真实 Token」，账单可直接采信。
    PROVIDER = "provider"
    #: 供应商确实回报了用量，但**不是全部维度**（常见形态：不报缓存写入量）。
    #: 仍可采信，但要知道缺失维度按 0 计价，总额偏低。
    #:
    #: 判据是「维度是否齐全」而不是「值是否为 0」：上游明确报 0 是一个事实，
    #: 没报是一个空缺；把前者也标成 partial 会让绝大多数请求挂上一个
    #: 没有意义的标记。
    PROVIDER_PARTIAL = "provider_partial"
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
