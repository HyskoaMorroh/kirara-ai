"""Channel-independent execution of one Agent turn.

This module deliberately owns orchestration only.  Provider resilience remains
in ``LLMManager`` and MCP transport/connection state remains in its manager.
The executor is the policy boundary that combines both with a frozen resource
view for one conversation turn.
"""

from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
import secrets
import threading
import time
import uuid
from collections.abc import Mapping as ABCMapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from kirara_ai.im.adapter import IncrementalDeliveryAdapter, IncrementalReplyHandle
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.llm.format.message import (
    LLMChatContentPartType,
    LLMChatImageContent,
    LLMChatMessage,
    LLMChatTextContent,
    LLMToolCallContent,
)
from kirara_ai.llm.format.request import LLMChatRequest, Tool, ToolParameters
from kirara_ai.llm.format.response import LLMChatResponse, Message, Usage, UsageSource
from kirara_ai.llm.format.tool import LLMToolResultContent, ToolCall
from kirara_ai.llm.resilience import (
    ChatExecutionResult,
    ErrorCategory,
    FailoverExecutionError,
    RequestCancelledError,
    RETRYABLE_ERROR_CATEGORIES,
    StreamInterruptedError,
    classify_llm_error,
)
from kirara_ai.memory.entry import MemoryEntry
from kirara_ai.agent_runtime.core import (
    DEFAULT_TEAMMATE_DEPTH,
    TEAMMATE_TOOL_PREFIX,
    AgentDefinition,
    AgentRegistry,
    ChannelContext,
    ResourceSnapshot,
    build_teammate_tools,
    principal_can_control_agent,
    resolve_mcp_tool_allowlist,
)
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.plugin_manager.system_dependencies import dependency_ids_for_resource
from kirara_ai.web.auth.principal import get_runtime_principal

from .session_store import SessionStore
from .hooks import AgentHookRuntime, HookOutcome
from .skills import (
    SKILL_TOOL_PREFIX,
    build_skill_tools,
    skill_advertisement,
    skill_catalog_section,
    skill_readiness_note,
)
from .tool_search import (
    DEFAULT_TOOL_SEARCH_THRESHOLD,
    TOOL_SEARCH_TOOL_NAME,
    build_tool_search_tool,
    search_tool_entries,
    should_use_tool_search,
    tool_catalog_section,
)

#: 流式用量的可合并维度。列举而不是遍历 ``model_fields``：``source`` 不是计数，
#: 需要另一套规则（见 ``merge_stream_usage``）。
_USAGE_COUNT_FIELDS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cached_tokens",
    "cache_write_tokens",
)

#: 真正会向上游发起流式请求的两档。``incremental`` 在 ``aggregate`` 之上再把
#: 内容推给用户，取回方式相同。
_STREAMING_MODES = frozenset({"aggregate", "incremental"})


class IncrementalReplyDelivery:
    """把生成中的文本推给一个具备「编辑已发出消息」能力的渠道（需求 4）。

    `aggregate` 只是把流在服务端吃完再发一条完整消息——用户端从来没收到过流式，
    等待期间界面上什么都没有。`incremental` 在它之上多一步：边生成边改写同一条消息。

    这个类刻意做成**可以完全惰性**的：

    * 渠道对象为 ``None``（WebUI 的 HTTP 路径没有适配器）或不实现增量协议
      （QQ / 企业微信）时，所有方法都是空操作。判空集中在这里而不是散落在调用点：
      散落时漏掉一处就是一次 ``AttributeError`` 打断整轮对话。
    * 任何一次推送失败都**就地停用自己**并且不抛。增量是体验优化，
      整段投递路径仍会在最后把完整回复发出去；而一个正在限流的渠道上反复改写
      只会加深限流，每次失败还要付一次往返。
    """

    def __init__(self, channel: Any, *, recipient: Any) -> None:
        self._channel = channel if isinstance(channel, IncrementalDeliveryAdapter) else None
        self._recipient = recipient
        self._handle: Optional[IncrementalReplyHandle] = None
        self._failed = self._channel is None
        self._delivered = False

    @property
    def active(self) -> bool:
        """是否仍在向渠道推送。失败或渠道不支持时为 ``False``。"""
        return self._channel is not None and self._handle is not None and not self._failed

    @property
    def delivered(self) -> bool:
        """最终文本是否已经确实写到平台上了。

        这是「要不要再走整段投递」的唯一判据。**不能**用「本轮尝试过增量」代替：
        占位消息发不出去、中途改写被限流、渠道压根不支持编辑——这几种情况下
        用户此刻屏幕上没有完整回复，整段投递必须照常发生。反过来，收尾成功之后
        再整段投递一次，用户会看到同一段几千字的内容出现两遍。
        """
        return self._delivered

    async def start(self) -> None:
        if self._channel is None or self._failed:
            return
        try:
            self._handle = await self._channel.begin_incremental_reply(self._recipient)
        except Exception:  # noqa: BLE001 - 建立占位失败只损失增量体验
            self._handle = None
        if self._handle is None:
            # 拿不到占位消息就没有可改写的目标。停用而不是对着 None 反复尝试。
            self._failed = True

    async def push(self, text: str) -> None:
        """推送**到目前为止的完整文本**（不是增量片段）。"""
        if not self.active or not text or not text.strip():
            # 空文本改写会把占位消息变成空消息（平台会拒），或者把「正在生成」抹掉，
            # 让用户以为回复已经结束。
            return
        try:
            await self._channel.update_incremental_reply(self._handle, text)
        except Exception:  # noqa: BLE001 - 推送失败不该让整轮失败
            self._failed = True

    async def complete(self, text: str) -> None:
        """收尾，交出最终文本。没有走过增量路径时是空操作。

        只有这一步成功之后 :attr:`delivered` 才为真：那时用户屏幕上的那条消息
        与整段投递路径会给出的内容逐字一致，再发一遍就是重复。
        """
        if self._channel is None or self._handle is None or self._failed:
            # 一轮里根本没走增量路径（例如工具轮）时，不该凭空发一条消息出来。
            return
        try:
            await self._channel.finish_incremental_reply(self._handle, text)
        except Exception:  # noqa: BLE001 - 收尾失败由整段投递兜底
            self._failed = True
            return
        self._delivered = True


def resolve_reply_stream_mode(
    *,
    agent_mode: Optional[str],
    channel_modes: Optional[Mapping[str, str]],
    channel_type: Optional[str],
    process_mode: Optional[str],
    request_mode: Optional[str] = None,
) -> str:
    """按「Agent 显式声明 > 渠道默认 > 本次请求 > 进程默认」定出这一轮的取回方式。

    原本只有一个进程级 ``reply_stream_mode``，一个部署里所有 Agent、所有渠道共用
    同一档。而这两个维度本就该不同：一个接了慢上游的 Agent 需要流式带来的首字节
    超时保护，另一个走本地小模型、毫秒级返回的 Agent 打开它只是白付一次握手；
    而渠道之间对流式的承载能力也不同——能编辑已发出消息（Telegram）或本身就是
    追加式传输（WebUI 的 SSE）的渠道才能兑现 ``incremental``。于是运维只能二选一，
    而两种选择都对一部分入口是错的。

    ``request_mode`` 是**本次调用**声明的取回方式，只有确实按流式协议接住回复的
    入口才该传它（目前是 WebUI 的 SSE 路由）。它的位置刻意放在渠道之后：客户端
    打开了一条流，默认就该往里推——否则这条路由在默认配置下永远只发 ``start`` 与
    ``done``，一个「已实现」的功能在所有未特意配置的部署上都不生效。但只要运维在
    Agent 或渠道上写下了明确取值，那句配置仍然优先，因为它是一个人做出的决定。

    两条边界：

    * **无法识别的取值一律当作「跟随上层」，绝不当作开启。** 把拼写错误理解成
      开启会让一处笔误静默改变整条取回路径，而配置界面上它看起来是有效的。
    * **各层都缺省时返回 ``off``**，与本特性之前逐字节一致：升级不会让任何既有
      部署突然改变取回方式。
    """

    def normalized(value: Optional[str]) -> Optional[str]:
        if not isinstance(value, str):
            return None
        candidate = value.strip().lower()
        if candidate in _STREAMING_MODES or candidate == "off":
            return candidate
        return None

    explicit = normalized(agent_mode)
    if explicit is not None:
        return explicit

    if channel_modes and isinstance(channel_type, str):
        channel_explicit = normalized(channel_modes.get(channel_type.strip().lower()))
        if channel_explicit is not None:
            return channel_explicit

    requested = normalized(request_mode)
    if requested is not None:
        return requested

    return normalized(process_mode) or "off"


def merge_stream_usage(accumulated: Optional[Usage], incoming: Optional[Usage]) -> Optional[Usage]:
    """把一个分片回报的用量并入已累积的那份，按字段合并而不是整体替换。

    流式响应把用量拆在多个分片里回报是常见形态：OpenAI 兼容端点里
    ``prompt_tokens`` 往往只在第一个带 usage 的分片出现（提示词在请求时就已确定），
    而 ``completion_tokens`` / ``total_tokens`` 要等生成结束才有值。

    整体替换会让最后一个带 usage 的分片抹掉先前那份，表现是**账单里的输入 Token
    变成「未上报」**，而这条请求明明报过输入用量。更麻烦的是 ``usage_source``
    仍然是 ``provider``，所以界面上没有任何「这条数据不完整」的迹象——
    它看起来是一条正常的、便宜的请求。

    合并口径：

    * 后到的**非 None** 值优先。增量计数的上游每片回报「到目前为止」的值，
      最后那份才是最终值。
    * 后到的 ``None`` **不覆盖**已有值。``None`` 是「这个分片没提」，
      不是「这个值是空」——与本项目在别处坚持的「``None`` 与 ``0`` 是两件事」同一条
      规则。``0`` 会照常覆盖，因为它是「报了，确实没有」这个论断。
    * ``source`` 只升不降：上游报过用量这件事，不该被一个没写 source 的收尾分片
      抹成 ``UNKNOWN``——那会让这条请求在统计里从「有据可依」掉进「不明」。
    """
    if incoming is None:
        return accumulated
    if accumulated is None:
        return incoming

    merged = accumulated.model_copy()
    for name in _USAGE_COUNT_FIELDS:
        value = getattr(incoming, name, None)
        if value is not None:
            setattr(merged, name, value)
    if incoming.source is not UsageSource.UNKNOWN:
        merged.source = incoming.source
    return merged


class RuntimeStatus(str, Enum):
    COMPLETED = "completed"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    FAILED = "failed"


@dataclass
class RuntimeResult:
    status: RuntimeStatus
    text: str = ""
    response: Optional[LLMChatResponse] = None
    messages: list[LLMChatMessage] = field(default_factory=list)
    context: Optional[ChannelContext] = None
    agent_id: Optional[str] = None
    snapshot: Optional[ResourceSnapshot] = None
    confirmation_id: Optional[str] = None
    correlation_id: Optional[str] = None
    error: Optional[dict[str, str]] = None
    trace_ids: tuple[str, ...] = ()
    #: 模型吐出第一个可见字节的挂钟时刻，仅流式请求可测。
    #:
    #: 非流式请求在 HTTP 响应到达前没有任何可观测的中间事件，因此这里保持
    #: ``None``，而不是拿「请求返回时刻」冒充首字节——后者会把「模型思考了 20 秒」
    #: 记成「首字节 20 秒、生成 0 秒」，与真实情况正好相反。
    llm_first_byte_at: Optional[datetime] = None
    #: 这一轮的最终文本是否已经通过增量投递写到渠道上了。
    #:
    #: 为真时调用方**不得**再整段投递一次：用户屏幕上那条被改写完成的消息与
    #: 整段投递会发出的内容逐字相同，再发一遍就是同一段回复出现两遍。
    #:
    #: 默认必须是 ``False``：默认反了会让所有非增量回复静默消失。只有增量收尾
    #: 确实成功（`IncrementalReplyDelivery.delivered`）时才置真，因此占位失败、
    #: 改写被限流、渠道不支持编辑这几种情况仍然走整段投递兜底。
    delivered_incrementally: bool = False


@dataclass
class _PendingConfirmation:
    confirmation_id: str
    context: ChannelContext
    agent: AgentDefinition
    snapshot: ResourceSnapshot
    messages: list[LLMChatMessage]
    pending_call: ToolCall
    tool_names: frozenset[str]
    mcp_server_ids: frozenset[str]
    principal_subject_digest: str
    agent_policy_signature: tuple[Any, ...]
    tool_signature: tuple[Any, ...]
    session_allowlist: Optional[frozenset[str]]
    workflow_allowlist: Optional[frozenset[str]]
    model_id: str
    tool_iterations: int
    trace_ids: tuple[str, ...]
    correlation_id: str


class AgentRuntimeExecutor:
    """Run an Agent with immutable resources and a bounded MCP tool loop."""

    def __init__(
        self,
        *,
        agent_registry: AgentRegistry,
        llm_manager: Any,
        mcp_manager: Any,
        resource_loader: Optional[Callable[..., Any]] = None,
        resource_service: Optional[ResourceLifecycleService] = None,
        dependency_service: Any = None,
        audit_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        session_store: Optional[SessionStore] = None,
        memory_manager: Any = None,
        hook_runtime: Optional[AgentHookRuntime] = None,
        context_char_threshold: Optional[int] = None,
        compactor: Optional[Callable[..., Any]] = None,
        reply_stream_mode: str = "off",
        channel_reply_stream_modes: Optional[Mapping[str, str]] = None,
        tool_search_threshold: int = DEFAULT_TOOL_SEARCH_THRESHOLD,
        turn_deadline_seconds: float = 0.0,
    ) -> None:
        if context_char_threshold is not None:
            if isinstance(context_char_threshold, bool) or not isinstance(
                context_char_threshold, int
            ) or context_char_threshold < 0:
                raise ValueError("context_char_threshold must be a non-negative integer")
        if compactor is not None and not callable(compactor):
            raise TypeError("compactor must be callable")
        # 进程级默认不接受 `inherit`：它没有上层可以跟随，写成它等于没有默认值。
        if reply_stream_mode not in {"off", "aggregate", "incremental"}:
            raise ValueError(
                "reply_stream_mode must be one of off, aggregate, incremental"
            )
        #: 各渠道的默认取回方式，优先级在 Agent 声明之下、进程默认之上（需求 4）。
        #:
        #: 各渠道对流式的承载能力本就不同：Telegram 能编辑已发出的消息，
        #: WebUI 的在线对话走 SSE（一条事件就是一次追加），这两个能兑现
        #: `incremental`；QQ / OneBot 与企业微信没有等价能力，
        #: 在那里它自动退化成 `aggregate`。
        #: 此前只有一个进程级开关，运维只能二选一，两种选择都对一部分入口是错的。
        #: 不合法的取值在这里就被剔除而不是留到运行时——一处笔误不该静默改变
        #: 整条取回路径，而配置界面上它看起来是有效的。
        self.channel_reply_stream_modes: dict[str, str] = {
            str(channel).strip().lower(): str(mode).strip().lower()
            for channel, mode in (channel_reply_stream_modes or {}).items()
            if str(mode).strip().lower() in {"off", "aggregate", "incremental"}
        }
        #: 工具数超过它才改用「目录 + 搜索」（需求 8 的 Tool Search）。
        #:
        #: ``0`` 表示关闭，拿回逐字节一致的全量注入行为。用阈值而不是布尔开关，
        #: 是因为这件事的收益完全取决于工具数量：同一个开关在「三个工具」和
        #: 「四十个工具」上一个是纯损失、一个是纯收益。
        if isinstance(tool_search_threshold, bool) or not isinstance(
            tool_search_threshold, int
        ) or tool_search_threshold < 0:
            raise ValueError("tool_search_threshold must be a non-negative integer")
        self.tool_search_threshold = tool_search_threshold
        if isinstance(turn_deadline_seconds, bool) or not isinstance(
            turn_deadline_seconds, (int, float)
        ) or turn_deadline_seconds < 0:
            raise ValueError("turn_deadline_seconds must be a non-negative number")
        self.turn_deadline_seconds = float(turn_deadline_seconds)
        self.agent_registry = agent_registry
        self.llm_manager = llm_manager
        self.mcp_manager = mcp_manager
        self.resource_service = resource_service
        #: 服务器组件（CLI / 浏览器 / 运行时）的安装状态来源。
        #:
        #: 用来回答「这份技能里的命令在这台服务器上到底装了没有」。缺省 ``None``
        #: 时不给任何就绪提示——「不知道」不能冒充「已就绪」，也不能冒充「缺失」。
        self.dependency_service = dependency_service
        self.resource_loader = resource_loader or (
            resource_service.read_entry
            if resource_service is not None
            else (lambda _resource_id: "")
        )
        self.audit_sink = audit_sink
        self.session_store = session_store
        self.memory_manager = memory_manager
        self.hook_runtime = hook_runtime or AgentHookRuntime(
            resource_loader=self.resource_loader,
            resource_service=self.resource_service,
            audit_sink=self.audit_sink,
        )
        self.context_char_threshold = context_char_threshold or 0
        self.compactor = compactor
        #: 回复生成模式。``aggregate`` 走流式请求再整段投递：IM 平台普遍不支持
        #: 逐字编辑消息，所以这里不做逐字推送；真正的收益是让流式首字节超时、
        #: 静默超时与「首字节之前的故障转移」这三条容错路径实际生效。
        self.reply_stream_mode = reply_stream_mode
        self._pending: dict[str, _PendingConfirmation] = {}

    async def run(
        self,
        context: ChannelContext,
        message: IMMessage,
        *,
        session_agent_id: Optional[str] = None,
        session_mcp_allowlist: Optional[Iterable[str]] = None,
        workflow_mcp_allowlist: Optional[Iterable[str]] = None,
        history: Optional[Sequence[LLMChatMessage]] = None,
        teammate_depth: Optional[int] = None,
        incremental_channel: Any = None,
        reply_stream_mode: Optional[str] = None,
    ) -> RuntimeResult:
        """Execute one inbound message and never perform an unconfirmed tool.

        ``teammate_depth`` 是本轮还允许的委派层数（需求 8 的 Teammates 模式）。
        默认取 ``DEFAULT_TEAMMATE_DEPTH``；队友被委派执行时由调用方递减，
        因此 A→B→A 不会无限递归。

        ``incremental_channel`` 是投递这一轮回复的适配器对象（需求 4）。
        它实现 ``IncrementalDeliveryAdapter`` 且本轮解析出的取回方式是
        ``incremental`` 时，生成中的内容会边生成边改写同一条消息推给用户。
        传 ``None``（或一个不支持编辑的适配器）时整条增量链路是空操作，
        行为与此前逐字节一致。

        ``reply_stream_mode`` 是**本次调用**声明的取回方式，只有确实按流式协议
        接住回复的入口才该传（目前是 WebUI 的 SSE 路由）。它在
        :func:`resolve_reply_stream_mode` 的优先级里排在 Agent 与渠道之后、
        进程默认之前：客户端既然打开了一条流，默认就该往里推，否则那条路由在
        默认配置下永远只发 ``start`` 与 ``done``；而运维一旦在 Agent 或渠道上写下
        明确取值，那句配置仍然优先。
        """

        correlation_id = uuid.uuid4().hex
        agent: Optional[AgentDefinition] = None
        snapshot: Optional[ResourceSnapshot] = None
        result: Optional[RuntimeResult] = None
        depth = (
            DEFAULT_TEAMMATE_DEPTH if teammate_depth is None else max(0, int(teammate_depth))
        )
        # 增量投递的 sink 在这里就构造好：渠道不支持时它是惰性的，因此下游不需要
        # 到处判空——判空散落在调用点时，漏掉一处就是一次 AttributeError 打断整轮。
        incremental = IncrementalReplyDelivery(
            incremental_channel, recipient=message.sender
        )
        try:
            agent = self._trusted_agent(self.agent_registry.resolve(context, session_agent_id))
            session_allowlist = self._optional_set(session_mcp_allowlist)
            workflow_allowlist = self._optional_set(workflow_mcp_allowlist)
            tool_entries = self._read_tool_entries()
            effective_tools = (
                self._effective_tool_names(
                    agent,
                    tool_entries,
                    session_allowlist,
                    workflow_allowlist,
                )
                if principal_can_control_agent(agent.owner_subject)
                else frozenset()
            )
            stored_history = history
            memory_history: list[LLMChatMessage] = []
            if history is None:
                stored_history = None
                if self.session_store is not None:
                    stored_history = self.session_store.load_history(
                        context.session_key,
                        agent_id=agent.agent_id,
                    )
                if self.memory_manager is not None:
                    memory_history = self._load_memory_history(
                        context,
                        agent.agent_id,
                        correlation_id=correlation_id,
                    )
            if not stored_history:
                stored_history = memory_history
            model_id = agent.model_priority[0]
            snapshot = agent.snapshot(model_id=model_id)
            session_hook = await self._run_hook(
                "SessionStart",
                agent=agent,
                context=context,
                snapshot=snapshot,
                payload={"session_key": context.session_key, "agent_id": agent.agent_id},
                correlation_id=correlation_id,
            )
            prompt_hook = await self._run_hook(
                "UserPromptSubmit",
                agent=agent,
                context=context,
                snapshot=snapshot,
                payload={"text": message.content, "has_images": bool(message.images)},
                correlation_id=correlation_id,
            )
            # 本轮技能载入一次，目录、工具与整篇注入共用同一份结果——
            # 分头各载一次会让同一个 Skill 在一轮里被读两遍，
            # 而两遍之间资源被更新时，目录说的是一件事、注入的正文是另一件事。
            # 出参：`_build_messages` 在按绑定顺序载入技能时把可广告的追加进来。
            # 这样每个技能整轮只被载入一次（`read_entry` 每次都要重新校验摘要），
            # 且目录里写的版本与真正会被取回的正文必然同源。
            skill_advertisements: list[dict[str, str]] = []
            messages = self._build_messages(
                agent, message, stored_history, skill_advertisements
            )
            messages = self._inject_hook_context(
                messages,
                (session_hook, prompt_hook),
            )
            tools = self._build_tools(tool_entries, effective_tools) if agent.allow_tools else []
            # Tool Search（需求 8）：工具多到一定数量时不再全量注入完整 schema，
            # 改成「一行目录 + 一个搜索工具」。机制与技能的渐进披露完全对称，
            # 因此运维只需要理解一套心智模型。
            #
            # 只对 MCP 工具做这件事：队友委派工具与技能工具本身就已经是压缩形态
            # （每个只有一句描述），把它们也藏起来只会多一轮往返。
            searchable_tools: list[Tool] = []
            if should_use_tool_search(tools, threshold=self.tool_search_threshold):
                searchable_tools = list(tools)
                catalog = tool_catalog_section(searchable_tools)
                if catalog:
                    messages = self._append_system_text(messages, catalog)
                tools = [build_tool_search_tool()]
            # 委派工具与 MCP 工具同一形态，因此模型侧不需要区分「这是队友还是工具」。
            # 深度耗尽或未配置队友时列表为空，行为与此前一致。
            teammate_tools = (
                build_teammate_tools(
                    agent, self.agent_registry.agents, depth_remaining=depth
                )
                if agent.allow_tools
                else []
            )
            tools = [*tools, *teammate_tools]
            teammate_names = frozenset(tool.name for tool in teammate_tools)
            # 技能调用工具：与 MCP 工具、队友委派同一形态，模型侧不需要区分。
            # 只为「已广告」的技能生成——没有前置元数据的技能已整篇注入，
            # 再给一个工具会让同一份内容在上下文里出现两次。
            skill_tools = build_skill_tools(skill_advertisements)
            tools = [*tools, *skill_tools]
            skill_names = frozenset(tool.name for tool in skill_tools)
            result = await self._run_loop(
                context=context,
                agent=agent,
                snapshot=snapshot,
                messages=messages,
                tools=tools,
                tool_names=effective_tools,
                session_allowlist=session_allowlist,
                workflow_allowlist=workflow_allowlist,
                model_id=model_id,
                tool_iterations=0,
                trace_ids=(),
                correlation_id=correlation_id,
                teammate_tool_names=teammate_names,
                teammate_depth=depth,
                skill_tool_names=skill_names,
                searchable_tools=tuple(searchable_tools),
                incremental=incremental,
                reply_stream_mode=reply_stream_mode,
            )
            if result.status is RuntimeStatus.COMPLETED:
                # 收尾必须在返回之前：让那条被反复改写的消息的最终内容与整段投递
                # 路径逐字一致。跳过它会让用户永远停在最后一次节流之前的半句话上，
                # 而日志显示这一轮成功。
                await incremental.complete(result.text or "")
                # 收尾确实成功时告知调用方屏幕上已经有完整回复了，否则它会再整段
                # 投递一次，同一段内容出现两遍。失败时保持 False 走兜底。
                result.delivered_incrementally = incremental.delivered
                self._persist_history(context.session_key, agent.agent_id, result)
                self._persist_memory(
                    context,
                    agent.agent_id,
                    result,
                    correlation_id=correlation_id,
                )
            self._audit("run", result)
            return result
        except Exception as error:
            result = RuntimeResult(
                status=RuntimeStatus.FAILED,
                context=context,
                correlation_id=correlation_id,
                error={"type": type(error).__name__, "message": self._safe_error(error)},
            )
            self._audit("run", result)
            return result
        finally:
            if agent is not None and snapshot is not None:
                await self._run_hook(
                    "Stop",
                    agent=agent,
                    context=context,
                    snapshot=snapshot,
                    payload={
                        "status": result.status.value if result is not None else "failed",
                        "agent_id": agent.agent_id,
                        "confirmation_pending": bool(
                            result is not None
                            and result.status is RuntimeStatus.AWAITING_CONFIRMATION
                        ),
                    },
                    correlation_id=correlation_id,
                )

    async def confirm(
        self,
        confirmation_id: str,
        context: ChannelContext,
    ) -> RuntimeResult:
        """Approve one pending call and resume its original model turn."""

        pending: Optional[_PendingConfirmation] = None
        if self.session_store is not None:
            outcome, record = self.session_store.claim_pending(
                confirmation_id,
                context.session_key,
            )
            if outcome != "executing":
                return self._confirmation_claim_failure(
                    confirmation_id,
                    context,
                    outcome,
                    correlation_id=(
                        self._record_correlation_id(record)
                        if record is not None
                        else None
                    ),
                )
            pending = self._pending.pop(confirmation_id, None)
            if pending is None:
                assert record is not None
                try:
                    pending = self._restore_pending(record, context)
                except Exception as error:
                    self.session_store.complete_pending(
                        confirmation_id,
                        "failed",
                        error_type="InvalidConfirmationRecord",
                    )
                    result = RuntimeResult(
                        status=RuntimeStatus.FAILED,
                        context=context,
                        confirmation_id=confirmation_id,
                        correlation_id=self._record_correlation_id(record),
                        error={
                            "type": type(error).__name__,
                            "message": self._safe_error(error),
                        },
                    )
                    self._audit("confirm", result)
                    return result
        else:
            pending = self._pending.get(confirmation_id)
            if pending is not None and pending.context.session_key != context.session_key:
                return self._confirmation_claim_failure(
                    confirmation_id,
                    context,
                    "session_mismatch",
                    correlation_id=pending.correlation_id,
                )
            if pending is not None:
                self._pending.pop(confirmation_id, None)
        if pending is None:
            return self._confirmation_claim_failure(
                confirmation_id,
                context,
                "not_found",
            )
        stop_agent = pending.agent
        stop_snapshot = pending.snapshot
        try:
            # A confirmation is a short-lived capability. Re-read the Agent,
            # tool cache and transport state before using it.
            current_agent = self._trusted_agent(
                self.agent_registry.get(pending.agent.agent_id)
            )
            stop_agent = current_agent
            principal = get_runtime_principal()
            if (
                principal is None
                or not principal.is_creator
                or not secrets.compare_digest(
                    principal.subject_digest,
                    pending.principal_subject_digest,
                )
                or not principal_can_control_agent(current_agent.owner_subject)
            ):
                result = RuntimeResult(
                    status=RuntimeStatus.FAILED,
                    context=pending.context,
                    agent_id=pending.agent.agent_id,
                    snapshot=pending.snapshot,
                    confirmation_id=confirmation_id,
                    correlation_id=pending.correlation_id,
                    error={
                        "type": "ConfirmationPrincipalMismatch",
                        "message": "confirmation principal or Agent owner changed",
                    },
                )
                if self.session_store is not None:
                    self.session_store.complete_pending(
                        confirmation_id,
                        "failed",
                        error_type="ConfirmationPrincipalMismatch",
                    )
                self._audit("confirm", result)
                return result
            current_entries = self._read_tool_entries()
            current_name = (
                pending.pending_call.function.name
                if pending.pending_call.function
                else ""
            )
            current_effective = self._effective_tool_names(
                current_agent,
                current_entries,
                pending.session_allowlist,
                pending.workflow_allowlist,
            )
            current_entry = current_entries.get(current_name)
            if (
                not current_agent.enabled
                or self._agent_policy_signature(current_agent)
                != pending.agent_policy_signature
                or current_name not in current_effective
                or self._tool_signature(current_entry) != pending.tool_signature
                or not self._tool_is_connected(current_entry)
            ):
                result = RuntimeResult(
                    status=RuntimeStatus.FAILED,
                    context=pending.context,
                    agent_id=pending.agent.agent_id,
                    snapshot=pending.snapshot,
                    confirmation_id=confirmation_id,
                    correlation_id=pending.correlation_id,
                    error={
                        "type": "ConfirmationExpired",
                        "message": "Agent or MCP binding changed before confirmation",
                    },
                )
                if self.session_store is not None:
                    self.session_store.complete_pending(
                        confirmation_id,
                        "expired",
                        error_type="ConfirmationExpired",
                    )
                self._audit("confirm", result)
                return result
            pre_tool_outcome = await self._run_hook(
                "PreToolUse",
                agent=current_agent,
                context=pending.context,
                snapshot=pending.snapshot,
                payload={
                    "tool_name": current_name,
                    "arguments": pending.pending_call.function.arguments or {},
                    "confirmed": True,
                },
                correlation_id=pending.correlation_id,
            )
            if pre_tool_outcome.blocked:
                if self.session_store is not None:
                    self.session_store.complete_pending(
                        confirmation_id,
                        "failed",
                        error_type="AgentHookDenied",
                    )
                result = RuntimeResult(
                    status=RuntimeStatus.FAILED,
                    context=pending.context,
                    agent_id=current_agent.agent_id,
                    snapshot=pending.snapshot,
                    confirmation_id=confirmation_id,
                    correlation_id=pending.correlation_id,
                    error={
                        "type": "AgentHookDenied",
                        "message": "tool call was denied by Agent Hook",
                    },
                )
                self._audit("confirm", result)
                return result
            pending_call, input_error = self._apply_hook_tool_input(
                pending.pending_call,
                pre_tool_outcome,
                current_entry,
            )
            if input_error is not None:
                if self.session_store is not None:
                    self.session_store.complete_pending(
                        confirmation_id,
                        "failed",
                        error_type="InvalidHookToolInput",
                    )
                result = RuntimeResult(
                    status=RuntimeStatus.FAILED,
                    context=pending.context,
                    agent_id=current_agent.agent_id,
                    snapshot=pending.snapshot,
                    confirmation_id=confirmation_id,
                    correlation_id=pending.correlation_id,
                    error={
                        "type": "InvalidHookToolInput",
                        "message": input_error,
                    },
                )
                self._audit("confirm", result)
                return result
            tool_result = await self._execute_tool_call(
                pending_call,
                current_agent,
                pending.session_allowlist,
                pending.workflow_allowlist,
                self._mcp_server_ids(current_agent),
                confirmed=True,
                correlation_id=pending.correlation_id,
            )
            post_tool_outcome = await self._run_hook(
                "PostToolUse",
                agent=current_agent,
                context=pending.context,
                snapshot=pending.snapshot,
                payload={
                    "tool_name": current_name,
                    "is_error": tool_result.isError,
                    "result": tool_result.content,
                },
                correlation_id=pending.correlation_id,
            )
            tool_result = self._post_tool_result(
                pending_call,
                tool_result,
                post_tool_outcome,
            )
            if self.session_store is not None:
                self.session_store.complete_pending(
                    confirmation_id,
                    "failed" if tool_result.isError else "succeeded",
                    error_type=("ToolExecutionFailed" if tool_result.isError else None),
                )
            messages = list(pending.messages)
            messages.append(self._assistant_tool_message(pending_call))
            messages.append(LLMChatMessage(role="tool", content=[tool_result]))
            # 恢复这一轮必须重新带上技能与委派工具。
            #
            # `pending.messages` 里的 system 消息含技能目录（那是确认前构建的），
            # 只重建 MCP 工具会让模型看到一份广告、却调不到被广告的工具，
            # 得到「permission denied」——一个由我们自己制造的死路。
            # 队友委派同理：不重建的话确认一次之后队友就凭空消失了。
            resumed_skill_tools = (
                build_skill_tools(self._skill_advertisements(current_agent))
                if current_agent.allow_tools
                else []
            )
            resumed_teammate_tools = (
                build_teammate_tools(
                    current_agent,
                    self.agent_registry.agents,
                    depth_remaining=DEFAULT_TEAMMATE_DEPTH,
                )
                if current_agent.allow_tools
                else []
            )
            response = await self._run_loop(
                context=pending.context,
                agent=current_agent,
                snapshot=pending.snapshot,
                messages=messages,
                tools=[
                    *self._build_tools(current_entries, current_effective),
                    *resumed_teammate_tools,
                    *resumed_skill_tools,
                ],
                tool_names=current_effective,
                session_allowlist=pending.session_allowlist,
                workflow_allowlist=pending.workflow_allowlist,
                model_id=pending.model_id,
                tool_iterations=pending.tool_iterations + 1,
                trace_ids=pending.trace_ids,
                correlation_id=pending.correlation_id,
                teammate_tool_names=frozenset(
                    tool.name for tool in resumed_teammate_tools
                ),
                teammate_depth=DEFAULT_TEAMMATE_DEPTH,
                skill_tool_names=frozenset(tool.name for tool in resumed_skill_tools),
            )
            result = response
            if response.status is RuntimeStatus.COMPLETED and self.session_store is not None:
                self._persist_history(
                    pending.context.session_key,
                    current_agent.agent_id,
                    response,
                )
                self._persist_memory(
                    pending.context,
                    current_agent.agent_id,
                    response,
                    correlation_id=pending.correlation_id,
                )
            self._audit("confirm", response)
            return response
        except Exception as error:
            if self.session_store is not None:
                record = self.session_store.get_confirmation(confirmation_id)
                if record is not None and record["status"] == "executing":
                    self.session_store.complete_pending(
                        confirmation_id,
                        "failed",
                        error_type=type(error).__name__,
                    )
            result = RuntimeResult(
                status=RuntimeStatus.FAILED,
                context=pending.context,
                agent_id=pending.agent.agent_id,
                snapshot=pending.snapshot,
                confirmation_id=confirmation_id,
                correlation_id=pending.correlation_id,
                error={"type": type(error).__name__, "message": self._safe_error(error)},
            )
            self._audit("confirm", result)
            return result
        finally:
            await self._run_hook(
                "Stop",
                agent=stop_agent,
                context=pending.context,
                snapshot=stop_snapshot,
                payload={
                    "status": result.status.value if "result" in locals() else "failed",
                    "agent_id": stop_agent.agent_id,
                    "confirmation_pending": False,
                },
                correlation_id=pending.correlation_id,
            )

    async def _run_loop(
        self,
        *,
        context: ChannelContext,
        agent: AgentDefinition,
        snapshot: ResourceSnapshot,
        messages: list[LLMChatMessage],
        tools: list[Tool],
        tool_names: frozenset[str],
        session_allowlist: Optional[frozenset[str]],
        workflow_allowlist: Optional[frozenset[str]],
        model_id: str,
        tool_iterations: int,
        trace_ids: tuple[str, ...],
        correlation_id: str,
        teammate_tool_names: frozenset[str] = frozenset(),
        teammate_depth: int = 0,
        skill_tool_names: frozenset[str] = frozenset(),
        searchable_tools: tuple[Tool, ...] = (),
        incremental: Optional[IncrementalReplyDelivery] = None,
        reply_stream_mode: Optional[str] = None,
    ) -> RuntimeResult:
        current_messages = list(messages)
        current_model = model_id
        current_traces = list(trace_ids)
        response: Optional[LLMChatResponse] = None
        # Tool Search（需求 8）：本轮已经被搜出来、因此可以直接调用的工具。
        #
        # 搜到之后必须把它**放进工具列表**，否则模型下一轮调用它会撞
        # 「permission denied for MCP tool」——它按我们给的目录做了正确的事，
        # 却被判成越权，而那个错误无法自查。
        current_tools = list(tools)
        disclosed_tool_names: set[str] = set()
        # 只有流式请求能测到首字节；多轮工具调用时保留**最后一次**模型请求的
        # 首字节，因为它才是最终回复文本的起点。
        model_timings: dict[str, Any] = {}
        # 整轮共享一个取消信号与一个递减的总预算：工具轮次会多次调用模型，
        # 每次都从头给满预算等于没有总预算。预算耗尽时下一次调用直接被拒，
        # 正在等待的那次由取消信号中断。
        turn_budget = self.turn_deadline_seconds
        turn_cancellation: Optional[threading.Event] = (
            threading.Event() if turn_budget > 0 else None
        )
        turn_started = time.monotonic() if turn_budget > 0 else 0.0

        def remaining_budget() -> Optional[float]:
            if turn_budget <= 0:
                return None
            left = turn_budget - (time.monotonic() - turn_started)
            if left <= 0:
                # 预算已耗尽：先置取消信号，让仍在等待的上游请求尽快松手。
                if turn_cancellation is not None:
                    turn_cancellation.set()
                return 0.0
            return left

        # One final model request is made with tool_choice=none after the
        # configured number of tool rounds, so a model cannot extend the loop.
        for iteration in range(tool_iterations, agent.max_tool_iterations + 1):
            current_messages = await self._maybe_compact_messages(
                current_messages,
                context=context,
                agent=agent,
                snapshot=snapshot,
                model_id=current_model,
                iteration=iteration,
                correlation_id=correlation_id,
            )
            request = LLMChatRequest(
                messages=current_messages,
                model=current_model,
                tools=current_tools if agent.allow_tools and iteration < agent.max_tool_iterations else None,
                tool_choice=("none" if iteration >= agent.max_tool_iterations else None),
            )
            response, current_model, trace_id = await self._execute_model(
                request,
                agent.model_priority,
                current_model,
                provider_allowlist=agent.provider_allowlist,
                correlation_id=correlation_id,
                timings=model_timings,
                cancellation_event=turn_cancellation,
                deadline_seconds=remaining_budget(),
                stream_mode=resolve_reply_stream_mode(
                    agent_mode=getattr(agent, "reply_stream_mode", None),
                    channel_modes=self.channel_reply_stream_modes,
                    channel_type=getattr(context, "channel_type", None),
                    process_mode=self.reply_stream_mode,
                    request_mode=reply_stream_mode,
                ),
                incremental=incremental,
            )
            if trace_id:
                current_traces.append(trace_id)
            if not response.message.tool_calls or iteration >= agent.max_tool_iterations:
                return self._completed_result(
                    context,
                    agent,
                    snapshot,
                    current_messages,
                    response,
                    tuple(current_traces),
                    correlation_id,
                    llm_first_byte_at=model_timings.get("llm_first_byte_at"),
                )

            calls = list(response.message.tool_calls)
            for call in calls:
                if call.function is None or not call.function.name:
                    current_messages.append(self._assistant_tool_message(call))
                    current_messages.append(
                        LLMChatMessage(
                            role="tool",
                            content=[self._error_result(call, "tool call is missing a function name")],
                        )
                    )
                    continue
                name = call.function.name
                if name == TOOL_SEARCH_TOOL_NAME and searchable_tools:
                    # Tool Search（需求 8）：本地检索，不动服务器也不外呼，
                    # 因此与技能载入同一判断——不走 `_requires_confirmation`。
                    # 给它加人工确认等于每次找工具都要人点一下同意。
                    query = ""
                    arguments = call.function.arguments
                    if isinstance(arguments, dict):
                        raw_query = arguments.get("query")
                        query = raw_query if isinstance(raw_query, str) else ""
                    matched = search_tool_entries(searchable_tools, query)
                    for tool in matched:
                        # 搜到就放进工具列表：只把名字告诉模型、不放进列表，
                        # 它下一轮调用会撞「permission denied」——按我们给的目录
                        # 做了正确的事却被判成越权，而那个错误无法自查。
                        if tool.name not in disclosed_tool_names:
                            disclosed_tool_names.add(tool.name)
                            current_tools.append(tool)
                    current_messages.append(self._assistant_tool_message(call))
                    current_messages.append(
                        LLMChatMessage(
                            role="tool",
                            content=[self._tool_search_result(call, matched)],
                        )
                    )
                    continue
                if name in teammate_tool_names:
                    # 委派：跑队友的一次完整 turn，把它的回答作为 tool 结果带回。
                    # 委派本身不动服务器，因此不需要人工确认；队友自身的高危工具
                    # 仍走原有确认链路，委派不是绕过授权的旁路。
                    delegated = await self._delegate_to_teammate(
                        call,
                        context=context,
                        depth_remaining=teammate_depth,
                        correlation_id=correlation_id,
                    )
                    current_messages.append(self._assistant_tool_message(call))
                    current_messages.append(
                        LLMChatMessage(role="tool", content=[delegated])
                    )
                    continue
                if name in skill_tool_names:                    # 技能载入：把这篇技能的正文交给模型，让它据此作答。
                    #
                    # 不走 `_requires_confirmation`：这是一次**本地只读**，
                    # 读的还是本轮快照已经固定的那个版本，不动服务器也不外呼。
                    # 给它加人工确认，等于每次用技能都要人点一下同意。
                    current_messages.append(self._assistant_tool_message(call))
                    current_messages.append(
                        LLMChatMessage(
                            role="tool",
                            content=[self._load_skill_body(call, agent)],
                        )
                    )
                    continue
                if name not in tool_names:
                    current_messages.append(self._assistant_tool_message(call))
                    current_messages.append(
                        LLMChatMessage(
                            role="tool",
                            content=[self._error_result(call, f"permission denied for MCP tool: {name}")],
                        )
                    )
                    continue
                if self._requires_confirmation(name):
                    hook_outcome = await self._run_hook(
                        "PreToolUse",
                        agent=agent,
                        context=context,
                        snapshot=snapshot,
                        payload={
                            "tool_name": name,
                            "arguments": call.function.arguments or {},
                            "confirmed": False,
                        },
                        correlation_id=correlation_id,
                    )
                    if hook_outcome.blocked:
                        current_messages.append(self._assistant_tool_message(call))
                        current_messages.append(
                            LLMChatMessage(
                                role="tool",
                                content=[self._error_result(call, "tool call was denied by Agent Hook")],
                            )
                        )
                        continue
                    call, input_error = self._apply_hook_tool_input(
                        call,
                        hook_outcome,
                        self._read_tool_entries().get(name),
                    )
                    if input_error is not None:
                        current_messages.append(self._assistant_tool_message(call))
                        current_messages.append(
                            LLMChatMessage(
                                role="tool",
                                content=[self._error_result(call, input_error)],
                            )
                        )
                        continue
                    confirmation_id = uuid.uuid4().hex
                    permission_outcome = await self._run_hook(
                        "PermissionRequest",
                        agent=agent,
                        context=context,
                        snapshot=snapshot,
                        payload={
                            "tool_name": name,
                            "arguments": call.function.arguments or {},
                            "confirmation_id": confirmation_id,
                        },
                        correlation_id=correlation_id,
                    )
                    if permission_outcome.blocked or permission_outcome.permission_behavior == "deny":
                        current_messages.append(self._assistant_tool_message(call))
                        current_messages.append(
                            LLMChatMessage(
                                role="tool",
                                content=[
                                    self._error_result(
                                        call,
                                        permission_outcome.permission_decision_reason
                                        or "confirmation was denied by Agent Hook",
                                    )
                                ],
                            )
                        )
                        continue
                    principal = get_runtime_principal()
                    if (
                        principal is None
                        or not principal_can_control_agent(agent.owner_subject)
                    ):
                        current_messages.append(self._assistant_tool_message(call))
                        current_messages.append(
                            LLMChatMessage(
                                role="tool",
                                content=[
                                    self._error_result(
                                        call,
                                        "permission denied for host tool execution",
                                    )
                                ],
                            )
                        )
                        continue
                    self._pending[confirmation_id] = _PendingConfirmation(
                        confirmation_id=confirmation_id,
                        context=context,
                        agent=agent,
                        snapshot=snapshot,
                        messages=current_messages,
                        pending_call=call,
                        tool_names=tool_names,
                        mcp_server_ids=self._mcp_server_ids(agent),
                        principal_subject_digest=principal.subject_digest,
                        agent_policy_signature=self._agent_policy_signature(agent),
                        tool_signature=self._tool_signature(self._read_tool_entries().get(name)),
                        session_allowlist=session_allowlist,
                        workflow_allowlist=workflow_allowlist,
                        model_id=current_model,
                        tool_iterations=iteration,
                        trace_ids=tuple(current_traces),
                        correlation_id=correlation_id,
                    )
                    self._persist_pending(
                        self._pending[confirmation_id],
                    )
                    return RuntimeResult(
                        status=RuntimeStatus.AWAITING_CONFIRMATION,
                        context=context,
                        agent_id=agent.agent_id,
                        snapshot=snapshot,
                        response=response,
                        messages=current_messages,
                        confirmation_id=confirmation_id,
                        correlation_id=correlation_id,
                        trace_ids=tuple(current_traces),
                    )
                hook_outcome = await self._run_hook(
                    "PreToolUse",
                    agent=agent,
                    context=context,
                    snapshot=snapshot,
                    payload={
                        "tool_name": name,
                        "arguments": call.function.arguments or {},
                        "confirmed": False,
                    },
                    correlation_id=correlation_id,
                )
                if hook_outcome.blocked:
                    current_messages.append(self._assistant_tool_message(call))
                    current_messages.append(
                        LLMChatMessage(
                            role="tool",
                            content=[self._error_result(call, "tool call was denied by Agent Hook")],
                        )
                    )
                    continue
                call, input_error = self._apply_hook_tool_input(
                    call,
                    hook_outcome,
                    self._read_tool_entries().get(name),
                )
                if input_error is not None:
                    current_messages.append(self._assistant_tool_message(call))
                    current_messages.append(
                        LLMChatMessage(
                            role="tool",
                            content=[self._error_result(call, input_error)],
                        )
                    )
                    continue
                tool_result = await self._execute_tool_call(
                    call,
                    agent,
                    session_allowlist,
                    workflow_allowlist,
                    self._mcp_server_ids(agent),
                    confirmed=False,
                    correlation_id=correlation_id,
                )
                post_tool_outcome = await self._run_hook(
                    "PostToolUse",
                    agent=agent,
                    context=context,
                    snapshot=snapshot,
                    payload={
                        "tool_name": name,
                        "is_error": tool_result.isError,
                        "result": tool_result.content,
                    },
                    correlation_id=correlation_id,
                )
                tool_result = self._post_tool_result(
                    call,
                    tool_result,
                    post_tool_outcome,
                )
                current_messages.append(self._assistant_tool_message(call))
                current_messages.append(LLMChatMessage(role="tool", content=[tool_result]))
                # 工具轮的 Hook 上下文必须进入**下一次**模型请求。
                #
                # `_inject_hook_context` 此前只在 SessionStart + UserPromptSubmit
                # 之后调用一次，于是 PreToolUse / PostToolUse 返回的
                # `additionalContext` / `systemMessage` 被解析、被审计成
                # `status: ok`，然后丢掉——协议里有、解析通过、审计说成功，
                # 唯独不起作用，Hook 作者只会怀疑自己的业务逻辑。
                #
                # 注入一次即可：注入的内容进入 `current_messages`，随后每轮都带着，
                # 重复注入会让同一段文本在长对话里出现十几次。
                current_messages = self._inject_hook_context(
                    current_messages,
                    (hook_outcome, post_tool_outcome),
                )

        # The loop always returns from the final no-tools request.  This guard
        # protects the contract if the loop is changed in a future revision.
        return RuntimeResult(
            status=RuntimeStatus.FAILED,
            context=context,
            agent_id=agent.agent_id,
            snapshot=snapshot,
            correlation_id=correlation_id,
            error={"type": "RuntimeLoopError", "message": "runtime loop ended without a model response"},
        )

    async def _delegate_to_teammate(
        self,
        call: ToolCall,
        *,
        context: ChannelContext,
        depth_remaining: int,
        correlation_id: str,
    ) -> LLMToolResultContent:
        """Run one teammate turn and return its answer as a tool result.

        队友用自己的模型链、提示词、技能与工具白名单执行，且**看不到**主 Agent 的
        对话历史——因此工具描述里明确要求 ``task`` 自带完整背景。

        失败一律作为 tool 结果返回而不是抛出：让模型看到「这条路不通」并改口，
        比让整轮对话失败好——用户问的问题本身通常还是能答的。
        """
        name = call.function.name if call.function else ""
        teammate_id = name.removeprefix(TEAMMATE_TOOL_PREFIX)
        arguments = (call.function.arguments if call.function else None) or {}
        task = arguments.get("task") if isinstance(arguments, dict) else None
        if not isinstance(task, str) or not task.strip():
            # 空任务不是有效委派：队友看不到主对话，凭空猜只会浪费一轮。
            return self._error_result(call, "delegation requires a non-empty task")
        try:
            teammate = self.agent_registry.get(teammate_id)
        except Exception:
            return self._error_result(call, f"unknown teammate Agent: {teammate_id}")
        if teammate is None or not teammate.enabled:
            return self._error_result(call, f"unknown teammate Agent: {teammate_id}")

        delegated_message = IMMessage(
            sender=ChatSender.get_bot_sender(),
            message_elements=[TextMessage(task.strip())],
        )
        try:
            result = await self.run(
                context,
                delegated_message,
                session_agent_id=teammate_id,
                # 队友不继承主 Agent 的历史：它是一次独立的、自带背景的子任务。
                history=(),
                # 深度递减：A→B→A 不会无限递归。
                teammate_depth=max(0, depth_remaining - 1),
            )
        except Exception as error:  # noqa: BLE001 - 委派失败不应终止整轮
            return self._error_result(
                call, f"teammate execution failed: {type(error).__name__}"
            )
        if result.status is not RuntimeStatus.COMPLETED or not (result.text or "").strip():
            return self._error_result(
                call, f"teammate {teammate_id} produced no answer"
            )
        return LLMToolResultContent(
            id=call.id,
            name=name,
            content=result.text,
            isError=False,
        )

    async def _maybe_compact_messages(
        self,
        messages: Sequence[LLMChatMessage],
        *,
        context: ChannelContext,
        agent: AgentDefinition,
        snapshot: ResourceSnapshot,
        model_id: str,
        iteration: int,
        correlation_id: str,
    ) -> list[LLMChatMessage]:
        """Run ``PreCompact`` and reduce old context when the limit is reached.

        The compaction boundary is deliberately before model request creation.
        Hook output is an audit-only side effect, while a custom compactor can
        only replace the historical prefix: the first system message, the
        latest user turn, and every message in its tool chain remain intact.
        """

        current = list(messages)
        threshold = self.context_char_threshold
        estimated_before = self._estimate_messages_chars(current)
        if threshold <= 0 or estimated_before <= threshold:
            return current

        await self._run_hook(
            "PreCompact",
            agent=agent,
            context=context,
            snapshot=snapshot,
            payload={
                "message_count": len(current),
                "estimated_chars": estimated_before,
                "model_id": model_id,
                "iteration": iteration,
            },
            correlation_id=correlation_id,
        )

        used_custom_compactor = False
        compactor_error: Optional[str] = None
        compacted: list[LLMChatMessage]
        if self.compactor is not None:
            try:
                candidate = await self._invoke_compactor(
                    self.compactor,
                    [message.model_copy(deep=True) for message in current],
                    context,
                )
                compacted = self._validate_compacted_messages(current, candidate)
                used_custom_compactor = True
            except Exception as error:
                # A user supplied compactor is an optimization, not a reason
                # to fail an otherwise valid Agent turn.
                compactor_error = type(error).__name__
                compacted = self._default_compact_messages(current, threshold)
        else:
            compacted = self._default_compact_messages(current, threshold)

        estimated_after = self._estimate_messages_chars(compacted)
        self._audit_compaction(
            context=context,
            agent=agent,
            model_id=model_id,
            iteration=iteration,
            message_count_before=len(current),
            message_count_after=len(compacted),
            estimated_chars_before=estimated_before,
            estimated_chars_after=estimated_after,
            used_custom_compactor=used_custom_compactor,
            compactor_error=compactor_error,
            correlation_id=correlation_id,
        )
        await self._run_hook(
            "PostCompact",
            agent=agent,
            context=context,
            snapshot=snapshot,
            payload={
                "message_count_before": len(current),
                "message_count_after": len(compacted),
                "estimated_chars_before": estimated_before,
                "estimated_chars_after": estimated_after,
                "model_id": model_id,
                "iteration": iteration,
            },
            correlation_id=correlation_id,
        )
        return compacted

    @staticmethod
    async def _invoke_compactor(
        compactor: Callable[..., Any],
        messages: list[LLMChatMessage],
        context: ChannelContext,
    ) -> Any:
        """Call a sync or async compactor with one or two supported arguments."""

        try:
            signature = inspect.signature(compactor)
        except (TypeError, ValueError):
            signature = None

        if signature is None:
            arguments = (messages,)
        else:
            try:
                signature.bind(messages, context)
            except TypeError:
                try:
                    signature.bind(messages)
                except TypeError as error:
                    raise TypeError(
                        "compactor must accept messages or messages and context"
                    ) from error
                arguments = (messages,)
            else:
                arguments = (messages, context)

        if inspect.iscoroutinefunction(compactor):
            return await compactor(*arguments)
        result = await asyncio.to_thread(compactor, *arguments)
        return await result if inspect.isawaitable(result) else result

    @classmethod
    def _validate_compacted_messages(
        cls,
        original: Sequence[LLMChatMessage],
        candidate: Any,
    ) -> list[LLMChatMessage]:
        """Accept only a compactor result that preserves the protected suffix."""

        if isinstance(candidate, (str, bytes)) or not isinstance(candidate, Sequence):
            raise TypeError("compactor must return a sequence of LLM messages")
        compacted = list(candidate)
        if any(not isinstance(message, LLMChatMessage) for message in compacted):
            raise TypeError("compactor returned an invalid LLM message")

        first_system = next(
            (message for message in original if message.role == "system"),
            None,
        )
        latest_user_index = next(
            (
                index
                for index in range(len(original) - 1, -1, -1)
                if original[index].role == "user"
            ),
            None,
        )
        current_turn = (
            list(original[latest_user_index:])
            if latest_user_index is not None
            else []
        )

        if first_system is not None:
            if not compacted or compacted[0] != first_system:
                raise ValueError("compactor removed or moved the first system message")
        if current_turn:
            if len(compacted) < len(current_turn) or compacted[-len(current_turn):] != current_turn:
                raise ValueError("compactor changed the current user/tool turn")
        if not compacted:
            raise ValueError("compactor returned an empty message list")
        cls._require_valid_tool_pairs(compacted)
        return compacted

    @staticmethod
    def _require_valid_tool_pairs(messages: Sequence[LLMChatMessage]) -> None:
        """Reject tool results without the assistant call they answer."""

        for index, message in enumerate(messages):
            if message.role != "tool":
                continue
            if index == 0 or messages[index - 1].role != "assistant":
                raise ValueError("compactor returned an orphaned tool result")
            calls = [
                item
                for item in messages[index - 1].content
                if isinstance(item, LLMToolCallContent)
            ]
            results = [
                item
                for item in message.content
                if isinstance(item, LLMToolResultContent)
            ]
            if not calls or any(
                not any(
                    (result.id and call.id == result.id)
                    or (not result.id and call.name == result.name)
                    for call in calls
                )
                for result in results
            ):
                raise ValueError("compactor changed a tool call/result pair")

    @classmethod
    def _default_compact_messages(
        cls,
        messages: Sequence[LLMChatMessage],
        threshold: int,
    ) -> list[LLMChatMessage]:
        """Drop the oldest complete user turns before the current turn."""

        original = list(messages)
        system_index = next(
            (index for index, message in enumerate(original) if message.role == "system"),
            None,
        )
        latest_user_index = next(
            (
                index
                for index in range(len(original) - 1, -1, -1)
                if original[index].role == "user"
            ),
            None,
        )
        if latest_user_index is None:
            return original

        protected_system = [original[system_index]] if system_index is not None else []
        history_start = (system_index + 1) if system_index is not None else 0
        history = original[history_start:latest_user_index]
        current_turn = original[latest_user_index:]

        # Group history by user turns so a tool result is never left behind
        # without the assistant tool-call message that introduced it.
        turns: list[list[LLMChatMessage]] = []
        turn: Optional[list[LLMChatMessage]] = None
        for message in history:
            if message.role == "user":
                if turn:
                    turns.append(turn)
                turn = [message]
            elif turn is not None:
                turn.append(message)
        if turn:
            turns.append(turn)

        while turns:
            candidate = protected_system + [item for group in turns for item in group] + current_turn
            if cls._estimate_messages_chars(candidate) <= threshold:
                return candidate
            turns.pop(0)

        return protected_system + current_turn

    @classmethod
    def _estimate_messages_chars(cls, messages: Sequence[LLMChatMessage]) -> int:
        return sum(len(cls._message_text(message)) for message in messages)

    def _audit_compaction(
        self,
        *,
        context: ChannelContext,
        agent: AgentDefinition,
        model_id: str,
        iteration: int,
        message_count_before: int,
        message_count_after: int,
        estimated_chars_before: int,
        estimated_chars_after: int,
        used_custom_compactor: bool,
        compactor_error: Optional[str],
        correlation_id: str,
    ) -> None:
        if self.audit_sink is None:
            return
        record: dict[str, Any] = {
            "component": "agent_runtime",
            "operation": "compact",
            "event": "PreCompact",
            "status": "success",
            "agent_id": agent.agent_id,
            "correlation_id": correlation_id,
            "model_id": model_id,
            "iteration": iteration,
            "message_count_before": message_count_before,
            "message_count_after": message_count_after,
            "estimated_chars_before": estimated_chars_before,
            "estimated_chars_after": estimated_chars_after,
            "used_custom_compactor": used_custom_compactor,
            "session": context.redacted(),
        }
        if compactor_error:
            record["compactor_error_type"] = compactor_error
            record["status"] = "fallback"
        try:
            self.audit_sink(record)
        except Exception:
            pass

    async def _execute_model(
        self,
        request: LLMChatRequest,
        model_priority: Sequence[str],
        current_model: str,
        *,
        provider_allowlist: Iterable[str] = (),
        correlation_id: Optional[str] = None,
        timings: Optional[dict[str, Any]] = None,
        cancellation_event: Optional[threading.Event] = None,
        deadline_seconds: Optional[float] = None,
        stream_mode: Optional[str] = None,
        incremental: Optional[IncrementalReplyDelivery] = None,
    ) -> tuple[LLMChatResponse, str, str]:
        """Run one model request, advancing the model chain on replayable errors.

        ``timings`` 是可选的输出参数，用来带回只有流式请求才存在的观测点
        （目前是 ``llm_first_byte_at``）。做成 out 参数而不是扩展返回值，是为了
        不改动这个方法的元组形状——既有调用方与测试都按三元组解包。

        ``cancellation_event`` / ``deadline_seconds`` 把取消与总截止时间下传到
        ``LLMManager``。此前这两个参数在 manager 侧完整实现却**没有任何生产调用方**，
        于是「取消传播」和「请求总截止时间」在真实部署里从未生效：一个卡住的上游
        会一直占着线程与连接直到进程退出。
        """
        candidates = list(dict.fromkeys([current_model, *model_priority]))

        last_error: Optional[BaseException] = None
        for model_id in candidates:
            if hasattr(request, "model_copy"):
                candidate_request = request.model_copy(update={"model": model_id})
            else:
                candidate_request = request.copy(update={"model": model_id})
            try:
                execute_chat = self.llm_manager.execute_chat
                options: dict[str, Any] = {}
                try:
                    signature = inspect.signature(execute_chat)
                except (TypeError, ValueError):
                    signature = None
                if signature is not None:
                    parameters = signature.parameters.values()
                    if (
                        "provider_allowlist" in signature.parameters
                        or any(
                            parameter.kind == inspect.Parameter.VAR_KEYWORD
                            for parameter in parameters
                        )
                    ):
                        options["provider_allowlist"] = provider_allowlist
                    if "correlation_id" in signature.parameters or any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters
                    ):
                        options["correlation_id"] = correlation_id
                    # 取消信号与总截止时间同样按签名探测再传：既有第三方
                    # LLMManager 实现可能没有这两个参数，硬传会 TypeError。
                    accepts_kwargs = any(
                        parameter.kind == inspect.Parameter.VAR_KEYWORD
                        for parameter in parameters
                    )
                    if cancellation_event is not None and (
                        "cancellation_event" in signature.parameters or accepts_kwargs
                    ):
                        options["cancellation_event"] = cancellation_event
                    if deadline_seconds is not None and (
                        "deadline_seconds" in signature.parameters or accepts_kwargs
                    ):
                        options["deadline_seconds"] = deadline_seconds
                # 流式模式下走 execute_stream，让首字节超时、静默超时与
                # 「首字节之前的故障转移」真正生效；结果聚合成一条响应返回，
                # 调用方看到的形状与非流式完全一致。
                if self._stream_request_allowed(candidate_request, stream_mode):
                    response, trace_id = await self._execute_model_streaming(
                        candidate_request,
                        model_id,
                        options,
                        timings=timings,
                        # 工具轮不做增量：工具调用的中间产物不是给用户看的文本，
                        # 推出去等于把内部状态当回复。带工具的请求现在**可以**走流式
                        # （聚合器会保住 tool_calls），但增量投递仍然只给文本轮。
                        incremental=(
                            incremental
                            if stream_mode == "incremental" and not candidate_request.tools
                            else None
                        ),
                    )
                    return response, model_id, trace_id
                result = execute_chat(candidate_request, **options)
                if inspect.isawaitable(result):
                    result = await result
                response = result.response if isinstance(result, ChatExecutionResult) else getattr(result, "response", result)
                if not isinstance(response, LLMChatResponse):
                    raise TypeError("LLM manager returned an invalid chat response")
                trace_id = str(getattr(result, "trace_id", "") or "")
                return response, model_id, trace_id
            except Exception as error:
                last_error = error
                if self._can_failover_to_next_model(error):
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise LookupError("Agent has no available model")

    def _stream_chat_available(self, mode: Optional[str] = None) -> bool:
        """Whether this request may be served as a stream.

        ``mode`` 是本轮解析出的取回方式（见 :func:`resolve_reply_stream_mode`）。
        省略时退回进程级值，让既有第三方调用方与测试保持可用。
        """
        effective = mode if mode is not None else self.reply_stream_mode
        return effective in _STREAMING_MODES and callable(
            getattr(self.llm_manager, "execute_stream", None)
        )

    def _stream_request_allowed(
        self,
        request: LLMChatRequest,
        mode: Optional[str] = None,
        *,
        supports_tool_calls: Optional[bool] = None,
    ) -> bool:
        """这一次请求能不能走流式。

        此前的条件是 `_stream_chat_available(mode) and not request.tools`——
        「凡是带工具的请求都不许流式」。而 `tools` 在**第 0 轮**就非空：`run()` 先拼好
        MCP 工具、队友委派工具与技能工具再进循环。于是一个绑了任何工具的 Agent，
        它绝大多数只需一次回复的对话从头到尾没有一次流式请求，也就拿不到
        `stream_first_byte_timeout_seconds`、`stream_idle_timeout_seconds` 与
        首字节前的安全故障转移——而 21.3 要求这三项必须生效。绑了 MCP 的 Agent
        是本项目的主流用法，不是边缘情况。

        那条限制的原始理由是「聚合文本会丢掉 `tool_calls`」。它只对**真的产生了
        工具调用**的那一轮成立，而真正的缺口在适配器：流式解析只读 `delta.content`。
        补上累积之后，限制就该按**适配器能力**而不是「有没有带工具」来下。

        ``supports_tool_calls`` 供测试直接注入；生产路径问 LLMManager，
        由它按该模型的全部候选供应商取最弱的那个（故障转移会在候选之间切换，
        只有第一家支持的话，一次转移之后工具调用就静默消失了）。
        """
        if not self._stream_chat_available(mode):
            return False
        if not request.tools:
            return True
        if supports_tool_calls is None:
            probe = getattr(self.llm_manager, "stream_supports_tool_calls", None)
            if not callable(probe):
                # 老的 manager 问不出能力：保持此前「带工具不流式」的行为，
                # 那是安全的一侧——工具调用完整，只是少了超时保护。
                return False
            supports_tool_calls = bool(probe(request.model or ""))
        return bool(supports_tool_calls)

    async def _execute_model_streaming(
        self,
        request: LLMChatRequest,
        model_id: str,
        options: dict[str, Any],
        *,
        timings: Optional[dict[str, Any]] = None,
        incremental: Optional[IncrementalReplyDelivery] = None,
    ) -> tuple[LLMChatResponse, str]:
        """Consume a stream and return it as one aggregated response.

        ``incremental`` 非空且渠道能改写已经交付出去的内容时，边聚合边把
        **到目前为止的完整文本**推给用户（需求 4 的 ``incremental`` 档）。
        不具备该能力的渠道上它是空操作，因此这里不需要分支——
        逐字发新消息会变成几十条碎片消息，比一条完整回复更糟，那条路不存在。

        能兑现的是 Telegram（`editMessageText`）与 WebUI 的在线对话（SSE，
        一条事件就是一次追加）；QQ / OneBot 与企业微信没有等价能力，在那里默认取的
        是流式**请求**本身的收益——首字节超时、静默超时，以及「首字节之前可以安全
        切换 Provider」。
        工具调用不走这条路：工具轮次需要结构化的 tool_calls，聚合文本会丢掉它。

        同时这里是整条链路上**唯一**能测到「模型首字节」的位置：拿到第一个非空
        文本片段的时刻就是首字节时刻。测到后经 ``timings`` 带回，最终落到投递
        时间线的 ``llm_first_byte`` 阶段；否则 ``llm_first_byte_seconds`` 与
        ``llm_generation_seconds`` 两列在真实部署里永远是 NULL。
        """
        execution = self.llm_manager.execute_stream(request, **options)
        chunks: list[str] = []
        # 工具调用与文本一起聚合。只拼文本时工具调用会在这一步静默消失，
        # 上层于是把「模型想调工具」当成「模型答完了」——那比一刀切成非流式更糟：
        # 前者是能力缺失，后者是静默错误。
        tool_calls: list[LLMToolCallContent] = []
        usage = None
        finish_reason = ""
        first_byte_at: Optional[datetime] = None
        try:
            for chunk in execution:
                message = getattr(chunk, "message", None)
                for part in getattr(message, "content", None) or ():
                    if isinstance(part, LLMToolCallContent):
                        tool_calls.append(part)
                        continue
                    text = getattr(part, "text", None)
                    if isinstance(text, str) and text:
                        if first_byte_at is None:
                            first_byte_at = datetime.now(timezone.utc)
                            if incremental is not None:
                                # 占位消息在**第一个非空片段**之后才建立，不在请求
                                # 之前：请求还可能因参数错误立刻失败，那时已经发出
                                # 的「正在生成回复…」就成了一条永远不会被改写的消息。
                                await incremental.start()
                        chunks.append(text)
                        if incremental is not None:
                            await incremental.push("".join(chunks))
                if getattr(chunk, "usage", None) is not None:
                    usage = merge_stream_usage(usage, chunk.usage)
                reason = getattr(message, "finish_reason", None)
                if reason:
                    finish_reason = reason
        finally:
            close = getattr(execution, "close", None)
            if callable(close):
                close()

        if timings is not None and first_byte_at is not None:
            timings["llm_first_byte_at"] = first_byte_at

        # 没有工具调用时形状与此前逐字一致：一个文本部件，哪怕文本是空的。
        content: list[Any] = [LLMChatTextContent(text="".join(chunks))]
        content.extend(tool_calls)
        aggregated = LLMChatResponse(
            model=model_id,
            usage=usage,
            message=Message(
                role="assistant",
                content=content,
                finish_reason=finish_reason,
            ),
        )
        # 供应商级的回复策略（目前是「隐藏 AI 署名」）必须在**聚合之后**执行：
        # 一句署名很可能被切成两个分片，逐片判断两片都不像署名，整句就原样漏出去。
        # 由 LLMManager 按真正成交的那家供应商的配置处理，与非流式同口径。
        #
        # 只接受 `LLMChatResponse` 返回值：聚合结果是已知良好的值，
        # 策略返回别的东西属于编程错误，不该把它当成回复投递给用户。
        policy = getattr(
            self.llm_manager, "apply_response_policy_for_attempts", None
        )
        if callable(policy):
            adjusted = policy(aggregated, getattr(execution, "attempts", ()) or ())
            if isinstance(adjusted, LLMChatResponse):
                aggregated = adjusted
        return aggregated, str(getattr(execution, "trace_id", "") or "")

    @staticmethod
    def _can_failover_to_next_model(error: BaseException) -> bool:
        """Allow model failover only for failures that are safe to replay.

        ``LLMManager`` already applies this policy between providers.  The
        Agent layer must preserve it when it advances to the next model in the
        configured chain; otherwise an authentication or policy failure could
        be replayed against unrelated upstreams.
        """

        if isinstance(error, (RequestCancelledError, StreamInterruptedError)):
            return False
        if isinstance(error, FailoverExecutionError):
            attempts = tuple(error.attempts)
            if not attempts:
                cause = error.cause
                return cause is None or AgentRuntimeExecutor._can_failover_to_next_model(cause)
            categories = {
                str(attempt.error_category or "").strip().lower()
                for attempt in attempts
            }
            return bool(categories) and categories.issubset(
                {category.value for category in RETRYABLE_ERROR_CATEGORIES}
                | {"circuit_open"}
            )
        return classify_llm_error(error) in RETRYABLE_ERROR_CATEGORIES

    async def _run_hook(
        self,
        event: str,
        *,
        agent: AgentDefinition,
        context: ChannelContext,
        snapshot: ResourceSnapshot,
        payload: Optional[Mapping[str, Any]] = None,
        correlation_id: Optional[str] = None,
    ) -> HookOutcome:
        """Run a Hook event as an isolated policy side effect.

        Hook runtime defects are audited and isolated from the Agent turn.
        Valid structured outcomes remain intact so each lifecycle event can
        apply only the fields permitted by its contract.
        """

        try:
            outcome = await self.hook_runtime.run_event(
                event,
                agent=agent,
                context=context,
                snapshot=snapshot,
                payload=payload,
                **(
                    {"correlation_id": correlation_id}
                    if correlation_id is not None
                    else {}
                ),
            )
            if not isinstance(outcome, HookOutcome):
                raise TypeError("Agent Hook runtime returned an invalid outcome")
        except Exception as error:
            outcome = HookOutcome(
                event=event,
                status="error",
                reasons=(type(error).__name__,),
            )
        if self.audit_sink is not None:
            try:
                self.audit_sink(
                    {
                        "component": "agent_hook",
                        "operation": "run_event",
                        "event": event,
                        "status": outcome.status,
                        "blocked": outcome.blocked,
                        "executed": outcome.executed,
                        "resource_count": len(outcome.resource_ids),
                        "reason_count": len(outcome.reasons),
                        "correlation_id": correlation_id,
                        "session": context.redacted(),
                    }
                )
            except Exception:
                pass
        return outcome

    @staticmethod
    def _inject_hook_context(
        messages: list[LLMChatMessage],
        outcomes: Iterable[HookOutcome],
    ) -> list[LLMChatMessage]:
        context_parts: list[str] = []
        for outcome in outcomes:
            context_parts.extend(outcome.system_messages)
            context_parts.extend(outcome.additional_context)
        if not context_parts:
            return messages
        hook_message = LLMChatMessage(
            role="system",
            content=[LLMChatTextContent(text="\n\n".join(context_parts))],
        )
        insert_at = 1 if messages and messages[0].role == "system" else 0
        return [*messages[:insert_at], hook_message, *messages[insert_at:]]

    @classmethod
    def _apply_hook_tool_input(
        cls,
        call: ToolCall,
        outcome: HookOutcome,
        entry: Any,
    ) -> tuple[ToolCall, Optional[str]]:
        if outcome.updated_input is None:
            return call, None
        error = cls._validate_tool_arguments(entry, outcome.updated_input)
        if error is not None:
            return call, f"invalid arguments returned by PreToolUse Hook: {error}"
        function = call.function
        return ToolCall(
            id=call.id,
            type=call.type,
            model=call.model,
            function=(
                None
                if function is None
                else function.model_copy(update={"arguments": dict(outcome.updated_input)})
            ),
        ), None

    @staticmethod
    def _validate_tool_arguments(entry: Any, arguments: Mapping[str, Any]) -> Optional[str]:
        if entry is None:
            return "tool definition is unavailable"
        info = getattr(entry, "tool_info", entry)
        schema = getattr(info, "inputSchema", None) or getattr(info, "input_schema", None)
        if not isinstance(schema, dict):
            return "tool input schema is invalid"
        try:
            from jsonschema import ValidationError, validate

            # The normalized LLM tool contract is strict by default.  MCP
            # schemas that omit the flag must therefore not become an escape
            # hatch for Hook-supplied identity or routing fields.
            validation_schema = dict(schema)
            validation_schema.setdefault("additionalProperties", False)
            validate(instance=dict(arguments), schema=validation_schema)
        except ValidationError as error:
            return error.message[:256]
        except Exception as error:
            return f"schema validation failed: {type(error).__name__}"
        return None

    @staticmethod
    def _post_tool_result(
        call: ToolCall,
        result: LLMToolResultContent,
        outcome: HookOutcome,
    ) -> LLMToolResultContent:
        if not outcome.blocked:
            return result
        reason = outcome.reasons[0] if outcome.reasons else "tool result requires review"
        return AgentRuntimeExecutor._error_result(
            call,
            f"PostToolUse Hook blocked this result: {reason}",
        )

    async def _execute_tool_call(
        self,
        call: ToolCall,
        agent: AgentDefinition,
        session_allowlist: Optional[frozenset[str]],
        workflow_allowlist: Optional[frozenset[str]],
        mcp_server_ids: frozenset[str],
        *,
        confirmed: bool,
        correlation_id: Optional[str] = None,
    ) -> LLMToolResultContent:
        name = call.function.name if call.function else "unknown"
        args = (call.function.arguments or {}) if call.function else {}
        if not isinstance(args, dict):
            return self._error_result(call, "tool arguments must be an object")
        try:
            result = self.mcp_manager.call_tool(
                name,
                args,
                agent_allowlist=frozenset(agent.mcp_allowlist),
                agent_mcp_server_ids=mcp_server_ids,
                agent_owner_subject=agent.owner_subject,
                session_allowlist=session_allowlist,
                workflow_allowlist=workflow_allowlist,
                confirmed=confirmed,
                **(
                    {"correlation_id": correlation_id}
                    if correlation_id is not None
                    else {}
                ),
            )
            if inspect.isawaitable(result):
                result = await result
            if result is None:
                return self._error_result(call, f"MCP tool failed or was rejected: {name}")
            is_error = bool(getattr(result, "isError", False))
            return LLMToolResultContent(
                id=call.id,
                name=name,
                content=self._tool_result_text(result),
                isError=is_error,
            )
        except Exception as error:
            return self._error_result(call, f"MCP tool error: {type(error).__name__}")

    def _build_messages(
        self,
        agent: AgentDefinition,
        message: IMMessage,
        history: Optional[Sequence[LLMChatMessage]],
        skill_advertisements: Optional[list[dict[str, str]]] = None,
    ) -> list[LLMChatMessage]:
        """Assemble this turn's messages.

        ``skill_advertisements`` 是一个**出参**：本方法在按绑定顺序载入资源时，
        把可广告的技能追加进去，调用方据此生成 `skill_` 工具。

        做成出参而不是让调用方先算一遍，是为了让一轮里每个 Skill 只被载入一次——
        `read_entry` 每次都会重新校验清单与文件摘要，读两遍不只是慢，
        还会让「本轮读到的内容前后一致」依赖运气：两遍之间资源被更新时，
        目录说的是一件事、注入的正文是另一件事。同时保持原有的载入顺序
        （prompt → skill → memory），资源载入顺序本身是有测试在断言的契约。
        """
        sections: list[str] = []
        for binding in (
            *agent.prompt_bindings,
            *agent.skill_bindings,
            *agent.memory_bindings,
        ):
            if not binding.enabled:
                continue
            content = self._load_resource(binding.resource_id, binding.version)
            if inspect.isawaitable(content):
                raise TypeError("resource_loader must be synchronous during snapshot creation")
            if content is None:
                continue
            text = str(content).strip()
            if not text:
                continue
            if binding.resource_type == "skill" and agent.allow_tools:
                # 渐进披露（需求 10，与主流 Agent 客户端同一原理）：
                # 能广告的技能只在系统提示词里留一行目录，正文由 `skill_` 工具
                # 在模型真的要用时取回。整篇注入的代价是「技能数 × 请求数」，
                # 而其中绝大部分与当轮问题无关。
                #
                # `allow_tools` 关闭时没有工具可调，一行目录就是一句模型无法兑现的
                # 空头承诺，因此那时一律整篇注入——与本特性之前逐字节一致。
                advertisement = skill_advertisement(
                    binding.resource_id,
                    text,
                    readiness_note=self._skill_readiness_note(binding.resource_id),
                )
                if advertisement is not None:
                    if skill_advertisements is not None:
                        skill_advertisements.append(advertisement)
                    continue
            sections.append(f"[{binding.resource_type}:{binding.resource_id}]\n{text}")
        catalog = skill_catalog_section(skill_advertisements or [])
        if catalog:
            sections.append(catalog)
        system = "\n\n".join(sections)
        messages = [
            LLMChatMessage(role="system", content=[LLMChatTextContent(text=system)]),
        ]
        messages.extend(history or ())
        user_content: list[LLMChatContentPartType] = [LLMChatTextContent(text=message.content)]
        user_content.extend(
            LLMChatImageContent(media_id=image.media_id) for image in message.images
        )
        messages.append(LLMChatMessage(role="user", content=user_content))
        return messages

    def _skill_advertisements(self, agent: AgentDefinition) -> list[dict[str, str]]:
        """只算技能广告，不组装消息。

        供**恢复路径**使用：那里的 `messages` 来自确认前的快照（不重建），
        但工具列表必须重建，否则模型会看到一份广告却调不到被广告的工具，
        拿到一句我们自己制造的 "permission denied"。

        正常一轮不走这里——那条路上 `_build_messages` 顺带产出广告，
        以保证每个技能整轮只被载入一次。
        """
        advertisements: list[dict[str, str]] = []
        if not agent.allow_tools:
            return advertisements
        for binding in agent.skill_bindings:
            if not binding.enabled:
                continue
            content = self._load_resource(binding.resource_id, binding.version)
            if inspect.isawaitable(content):
                raise TypeError("resource_loader must be synchronous during snapshot creation")
            if content is None:
                continue
            advertisement = skill_advertisement(
                binding.resource_id,
                str(content).strip(),
                readiness_note=self._skill_readiness_note(binding.resource_id),
            )
            if advertisement is not None:
                advertisements.append(advertisement)
        return advertisements

    def _skill_readiness_note(self, resource_id: str) -> str:
        """这个技能依赖的服务器组件是否就绪；就绪或未知时返回空串。

        没装 CLI 却照着技能写命令，最坏的表现不是报错而是**一个自信的假答案**：
        模型无从得知 `agent-browser` 在服务器上并不存在，只能把「我已经打开了
        浏览器」当成事实继续往下答，而用户看不出与真的执行成功有什么区别。

        依赖 id 走 `dependency_ids_for_resource`：安装界面判「要不要提示去装」
        和运行时判「能不能真的执行」必须是同一条规则，各写一份的话两边迟早
        对不上，而对不上的那一刻没有任何症状。

        任何异常都吞掉：这段代码在每一轮对话上运行，让一次正常提问因为读依赖
        状态失败而失败，比少这一句提示糟得多。
        """
        if self.dependency_service is None or self.resource_service is None:
            return ""
        try:
            resource = self.resource_service.get_resource(resource_id)
            dependency_ids = dependency_ids_for_resource(resource)
        except Exception:
            return ""
        return skill_readiness_note(dependency_ids, self.dependency_service)

    def _load_skill_body(self, call: ToolCall, agent: AgentDefinition) -> LLMToolResultContent:
        """把一个技能的正文作为 tool 结果返回。

        版本取自**本轮快照里的绑定**而不是「当前版本」：一次对话中途被更新的技能
        不该让前后两轮遵循不同的说明，那种不一致无法从对话记录里看出来。

        失败一律作为 tool 结果返回而不是抛出：让模型看到「这条路不通」并改口，
        比让整轮对话失败好——用户问的问题通常还是能答的。
        """
        name = call.function.name if call.function else ""
        resource_id = name.removeprefix(SKILL_TOOL_PREFIX)
        binding = next(
            (
                item
                for item in agent.skill_bindings
                if item.enabled and item.resource_id == resource_id
            ),
            None,
        )
        if binding is None:
            return self._error_result(call, f"unknown skill: {resource_id}")
        try:
            content = self._load_resource(binding.resource_id, binding.version)
        except Exception as error:  # noqa: BLE001 - 载入失败不该终止整轮
            return self._error_result(
                call, f"skill could not be loaded: {type(error).__name__}"
            )
        if inspect.isawaitable(content):
            return self._error_result(call, "skill loader must be synchronous")
        text = "" if content is None else str(content).strip()
        if not text:
            return self._error_result(call, f"skill is empty: {resource_id}")
        return LLMToolResultContent(id=call.id, name=name, content=text)

    @staticmethod
    def _tool_search_result(
        call: ToolCall, matched: Sequence[Tool]
    ) -> LLMToolResultContent:
        """把搜索结果作为 tool 结果返回：完整的参数定义，不只是名字。

        返回完整定义而不是名字列表，是因为交出一个没有参数的壳子会让模型用空参数
        去调，然后拿到一个它无法理解的校验错误。

        无命中时明确说「没找到」并提示换个词，而**不猜**最接近的那个：
        猜一个交出去比返回空更糟——模型会调用一个它没有要求的工具。
        """
        name = call.function.name if call.function else TOOL_SEARCH_TOOL_NAME
        if not matched:
            return LLMToolResultContent(
                id=call.id,
                name=name,
                content=(
                    "没有匹配的工具。可以换一个更宽的关键词再搜一次，"
                    "或者直接用你已有的知识回答。"
                ),
            )
        lines = ["以下工具现在可以直接调用："]
        for tool in matched:
            schema = {
                "name": tool.name,
                "description": tool.description,
                "parameters": {
                    "type": "object",
                    "properties": tool.parameters.properties,
                    "required": list(tool.parameters.required or []),
                },
            }
            lines.append(json.dumps(schema, ensure_ascii=False))
        return LLMToolResultContent(id=call.id, name=name, content="\n".join(lines))

    @staticmethod
    def _append_system_text(
        messages: list[LLMChatMessage], text: str
    ) -> list[LLMChatMessage]:
        """把一段文字并进**已有的**首条 system 消息，而不是新增一条。

        新增一条会让同一轮里出现两条 system 消息：部分上游只认第一条，
        于是这段目录被静默丢弃——而那正是「功能存在但不生效」的形态。
        没有 system 消息时才新建一条。
        """
        if not text:
            return messages
        result = list(messages)
        for index, message in enumerate(result):
            if message.role != "system":
                continue
            merged = [*message.content, LLMChatTextContent(text=text)]
            result[index] = LLMChatMessage(role="system", content=merged)
            return result
        return [LLMChatMessage(role="system", content=[LLMChatTextContent(text=text)]), *result]

    def _load_resource(self, resource_id: str, version: str) -> Any:
        """Load one resource at the version captured by this turn.

        New loaders accept ``(resource_id, version)`` so an Agent cannot observe
        a later resource update.  Existing integrations used a one-argument
        callback; inspect the callable before invoking it so a TypeError raised
        by the loader itself is not mistaken for a legacy signature.
        """

        loader = self.resource_loader
        # ``dict.__getitem__`` is a common legacy adapter in tests and small
        # integrations.  Its builtin signature is not introspectable on some
        # Python versions, but the bound mapping unambiguously accepts one key.
        bound_self = getattr(loader, "__self__", None)
        if getattr(loader, "__name__", "") == "__getitem__" and isinstance(
            bound_self, ABCMapping
        ):
            return loader(resource_id)
        try:
            signature = inspect.signature(loader)
        except (TypeError, ValueError):
            return loader(resource_id, version)

        positional = tuple(
            parameter
            for parameter in signature.parameters.values()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        )
        accepts_varargs = any(
            parameter.kind == inspect.Parameter.VAR_POSITIONAL
            for parameter in signature.parameters.values()
        )
        if accepts_varargs or len(positional) >= 2:
            return loader(resource_id, version)
        return loader(resource_id)

    def _trusted_agent(self, agent: AgentDefinition) -> AgentDefinition:
        """Rebuild bindings from the server registry before a turn starts."""

        if self.resource_service is None:
            return agent

        def resolve_bindings(bindings, resource_type):
            resolved = []
            for binding in bindings:
                resolved.append(
                    self.resource_service.resolve_binding(
                        binding.resource_id,
                        resource_type,
                        version=(
                            None
                            if binding.version_policy == "current"
                            else binding.version
                        ),
                        enabled=binding.enabled,
                        version_policy=binding.version_policy,
                    )
                )
            return tuple(resolved)

        return AgentDefinition(
            agent_id=agent.agent_id,
            owner_subject=agent.owner_subject,
            display_name=agent.display_name,
            enabled=agent.enabled,
            workflow_id=agent.workflow_id,
            model_priority=agent.model_priority,
            provider_allowlist=agent.provider_allowlist,
            capabilities=agent.capabilities,
            prompt_bindings=resolve_bindings(agent.prompt_bindings, "prompt"),
            skill_bindings=resolve_bindings(agent.skill_bindings, "skill"),
            memory_bindings=resolve_bindings(agent.memory_bindings, "memory"),
            mcp_bindings=resolve_bindings(agent.mcp_bindings, "mcp"),
            hook_bindings=resolve_bindings(agent.hook_bindings, "hook"),
            mcp_allowlist=agent.mcp_allowlist,
            allow_tools=agent.allow_tools,
            max_tool_iterations=agent.max_tool_iterations,
        )

    def _read_tool_entries(self) -> Mapping[str, Any]:
        getter = getattr(self.mcp_manager, "get_tools", None)
        if not callable(getter):
            return {}
        entries = getter()
        return entries if isinstance(entries, Mapping) else {}

    @staticmethod
    def _mcp_server_ids(agent: AgentDefinition) -> frozenset[str]:
        """Return MCP runtime IDs while preserving resource IDs in snapshots.

        Managed MCP resources use the ``mcp.<server_id>`` namespace so they
        cannot collide with prompts, skills, or legacy configuration entries.
        The MCP manager itself indexes live servers by the inner server ID.
        """
        return frozenset(
            binding.resource_id.removeprefix("mcp.")
            for binding in agent.mcp_bindings
            if binding.enabled
        )

    @classmethod
    def _effective_tool_names(
        cls,
        agent: AgentDefinition,
        entries: Mapping[str, Any],
        session_allowlist: Optional[frozenset[str]],
        workflow_allowlist: Optional[frozenset[str]],
    ) -> frozenset[str]:
        bound_servers = cls._mcp_server_ids(agent)
        return resolve_mcp_tool_allowlist(
            agent_allowlist=agent.mcp_allowlist,
            tool_entries=entries,
            agent_mcp_server_ids=bound_servers,
            session_allowlist=session_allowlist,
            workflow_allowlist=workflow_allowlist,
        )

    @staticmethod
    def _tool_signature(entry: Any) -> tuple[Any, ...]:
        if entry is None:
            return ()
        info = getattr(entry, "tool_info", entry)
        schema = getattr(info, "inputSchema", None) or getattr(info, "input_schema", None) or {}
        try:
            schema_value = json.dumps(schema, ensure_ascii=True, sort_keys=True, default=str)
        except (TypeError, ValueError):
            schema_value = repr(schema)
        return (
            str(getattr(entry, "server_id", "")),
            str(getattr(entry, "original_name", getattr(info, "name", ""))),
            str(getattr(info, "name", "")),
            str(getattr(info, "description", "") or ""),
            schema_value,
        )

    @staticmethod
    def _agent_policy_signature(agent: AgentDefinition) -> tuple[Any, ...]:
        bindings = tuple(
            (
                item.resource_type,
                item.resource_id,
                item.version,
                item.version_policy,
                item.content_sha256,
                item.enabled,
            )
            for item in agent.resource_bindings
        )
        owner_digest = (
            hashlib.sha256(agent.owner_subject.encode("utf-8")).hexdigest()
            if agent.owner_subject is not None
            else None
        )
        return (
            agent.enabled,
            owner_digest,
            tuple(agent.model_priority),
            tuple(sorted(agent.provider_allowlist)),
            tuple(sorted(agent.capabilities)),
            agent.max_tool_iterations,
            bindings,
            tuple(sorted(agent.mcp_allowlist)),
            agent.allow_tools,
            # 队友集合变化必须让待确认操作失效：否则确认的是一件事、
            # 执行的是另一件事。
            tuple(agent.teammate_agent_ids),
        )

    def _tool_is_connected(self, entry: Any) -> bool:
        if entry is None:
            return False
        server_id = getattr(entry, "server_id", None)
        getter = getattr(self.mcp_manager, "get_server", None)
        if not callable(getter):
            return True
        server = getter(server_id)
        if server is None:
            return False
        state = getattr(server, "state", None)
        state_value = getattr(state, "value", state)
        return str(state_value).lower() == "connected"

    @staticmethod
    def _build_tools(entries: Mapping[str, Any], allowlist: Iterable[str]) -> list[Tool]:
        allowed = set(allowlist)
        result: list[Tool] = []
        for name, entry in entries.items():
            if name not in allowed:
                continue
            info = getattr(entry, "tool_info", entry)
            schema = getattr(info, "inputSchema", None) or getattr(info, "input_schema", None) or {}
            if not isinstance(schema, dict):
                schema = {}
            properties = schema.get("properties") or {}
            required = schema.get("required") or []
            result.append(
                Tool(
                    name=name,
                    description=str(getattr(info, "description", "") or name),
                    parameters=ToolParameters(
                        properties=properties,
                        required=list(required),
                        additionalProperties=schema.get("additionalProperties", False),
                    ),
                )
            )
        return result

    def _requires_confirmation(self, name: str) -> bool:
        checker = getattr(self.mcp_manager, "requires_confirmation", None)
        if callable(checker):
            return bool(checker(name))
        entry = self._read_tool_entries().get(name)
        info = getattr(entry, "tool_info", entry)
        checker = getattr(self.mcp_manager, "_tool_requires_confirmation", None)
        return bool(checker(info)) if callable(checker) else False

    @staticmethod
    def _assistant_tool_message(call: ToolCall) -> LLMChatMessage:
        function = call.function
        name = function.name if function and function.name else "unknown"
        parameters = function.arguments if function else {}
        return LLMChatMessage(
            role="assistant",
            content=[LLMToolCallContent(id=call.id, name=name, parameters=parameters or {})],
        )

    @staticmethod
    def _error_result(call: ToolCall, message: str) -> LLMToolResultContent:
        name = call.function.name if call.function and call.function.name else "unknown"
        return LLMToolResultContent(
            id=call.id,
            name=name,
            content={"error": message},
            isError=True,
        )

    @staticmethod
    def _tool_result_text(result: Any) -> Any:
        content = getattr(result, "content", result)
        if not isinstance(content, list):
            return content
        values = []
        for item in content:
            text = getattr(item, "text", None)
            values.append(text if text is not None else str(item))
        return "\n".join(str(value) for value in values)

    @staticmethod
    def _completed_result(
        context: ChannelContext,
        agent: AgentDefinition,
        snapshot: ResourceSnapshot,
        messages: list[LLMChatMessage],
        response: LLMChatResponse,
        trace_ids: tuple[str, ...],
        correlation_id: str,
        *,
        llm_first_byte_at: Optional[datetime] = None,
    ) -> RuntimeResult:
        text = "".join(
            part.text
            for part in response.message.content
            if isinstance(part, LLMChatTextContent)
        ).strip()
        return RuntimeResult(
            status=RuntimeStatus.COMPLETED,
            text=text,
            response=response,
            messages=messages,
            context=context,
            agent_id=agent.agent_id,
            snapshot=snapshot,
            correlation_id=correlation_id,
            trace_ids=trace_ids,
            llm_first_byte_at=llm_first_byte_at,
        )

    @staticmethod
    def _optional_set(values: Optional[Iterable[str]]) -> Optional[frozenset[str]]:
        if values is None:
            return None
        return frozenset(str(value).strip() for value in values if str(value).strip())

    @staticmethod
    def _safe_error(error: BaseException) -> str:
        return str(error)[:256] or type(error).__name__

    def _audit(self, operation: str, result: RuntimeResult) -> None:
        if self.audit_sink is None:
            return
        record = {
            "component": "agent_runtime",
            "operation": operation,
            "status": result.status.value,
            "agent_id": result.agent_id,
            "correlation_id": result.correlation_id,
            "session": result.context.redacted() if result.context else None,
            "snapshot_sha256": result.snapshot.content_sha256 if result.snapshot else None,
            "confirmation_id": bool(result.confirmation_id),
            "error_type": result.error.get("type") if result.error else None,
        }
        try:
            self.audit_sink(record)
        except Exception:
            return

    def _persist_history(
        self,
        session_key: str,
        agent_id: str,
        result: RuntimeResult,
    ) -> None:
        if self.session_store is None or result.response is None:
            return
        messages = [message for message in result.messages if message.role != "system"]
        messages.append(result.response.message)
        self.session_store.save_history(session_key, messages, agent_id=agent_id)

    @staticmethod
    def _history_key(context: ChannelContext, agent_id: str) -> str:
        """Return the compatibility lookup key; Agent isolation is in the record."""

        return context.session_key

    @classmethod
    def _memory_key(cls, context: ChannelContext, agent_id: str) -> str:
        payload = "\x1f".join(
            (
                context.channel_type,
                context.adapter_instance,
                context.account_scope,
                context.conversation_scope,
                context.sender_scope,
                str(agent_id),
            )
        )
        return f"agent-runtime:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"

    def _memory_scope(self) -> Any:
        registry = getattr(self.memory_manager, "scope_registry", None)
        getter = getattr(registry, "get_scope", None)
        if not callable(getter):
            return None
        config = getattr(self.memory_manager, "config", None)
        scope_name = getattr(config, "default_scope", "member")
        return getter(scope_name)

    def _load_memory_history(
        self,
        context: ChannelContext,
        agent_id: str,
        *,
        correlation_id: Optional[str] = None,
    ) -> list[LLMChatMessage]:
        try:
            entries = self.memory_manager.query(
                self._memory_scope(),
                self._memory_sender(context),
                extra_identifier=self._memory_key(context, agent_id),
            )
            messages: list[LLMChatMessage] = []
            for entry in entries or ():
                metadata = getattr(entry, "metadata", {}) or {}
                runtime_metadata = metadata.get("agent_runtime")
                if isinstance(runtime_metadata, dict):
                    if str(runtime_metadata.get("agent_id", "")) != str(agent_id):
                        continue
                    serialized = runtime_metadata.get("message")
                    if isinstance(serialized, dict):
                        try:
                            messages.append(LLMChatMessage.model_validate(serialized))
                        except (TypeError, ValueError):
                            continue
                        continue
                content = str(getattr(entry, "content", "") or "").strip()
                if content:
                    messages.append(
                        LLMChatMessage(
                            role="user",
                            content=[LLMChatTextContent(text=content)],
                        )
                    )
            return messages
        except Exception as error:
            self._audit_memory_error(
                "load",
                error,
                context,
                correlation_id=correlation_id,
            )
            return []

    def _persist_memory(
        self,
        context: ChannelContext,
        agent_id: str,
        result: RuntimeResult,
        *,
        correlation_id: Optional[str] = None,
    ) -> None:
        if self.memory_manager is None or result.response is None:
            return
        try:
            scope = self._memory_scope()
            memory_key = self._memory_key(context, agent_id)
            turn_messages = self._current_turn_messages(result.messages)
            turn_messages.append(result.response.message)
            for message in turn_messages:
                if message.role == "system":
                    continue
                sender = self._memory_sender(context)
                serialized = self._redact_memory_message(message)
                self.memory_manager.store(
                    scope,
                    MemoryEntry(
                        sender=sender,
                        content=self._message_text(serialized),
                        timestamp=datetime.now(timezone.utc),
                        metadata={
                            "agent_runtime": {
                                "version": 1,
                                "agent_id": agent_id,
                                "message": serialized.model_dump(mode="json"),
                            }
                        },
                    ),
                    extra_identifier=memory_key,
                )
        except Exception as error:
            self._audit_memory_error(
                "store",
                error,
                context,
                correlation_id=correlation_id or result.correlation_id,
            )

    @staticmethod
    def _memory_sender(context: ChannelContext) -> ChatSender:
        """Build one sender identity for every message in the conversation."""

        if context.conversation_scope.startswith("group:"):
            group_id = context.conversation_scope.partition(":")[2]
            return ChatSender.from_group_chat(
                context.sender_scope,
                group_id or "unknown-group",
                context.sender_scope,
            )
        return ChatSender.from_c2c_chat(
            context.sender_scope,
            context.sender_scope,
        )

    @staticmethod
    def _current_turn_messages(messages: Sequence[LLMChatMessage]) -> list[LLMChatMessage]:
        start = 0
        for index in range(len(messages) - 1, -1, -1):
            if messages[index].role == "user":
                start = index
                break
        return list(messages[start:])

    @classmethod
    def _redact_memory_message(cls, message: LLMChatMessage) -> LLMChatMessage:
        payload = message.model_dump(mode="python")
        return LLMChatMessage.model_validate(cls._redact_memory_value(payload))

    @classmethod
    def _redact_memory_value(cls, value: Any, key: str = "") -> Any:
        sensitive = (
            "password",
            "token",
            "secret",
            "cookie",
            "authorization",
            "credential",
            "api_key",
        )
        if any(item in key.lower() for item in sensitive):
            return "[redacted]"
        if isinstance(value, dict):
            return {
                str(item): cls._redact_memory_value(child, str(item))
                for item, child in value.items()
            }
        if isinstance(value, list):
            return [cls._redact_memory_value(item, key) for item in value]
        return value

    @staticmethod
    def _message_text(message: LLMChatMessage) -> str:
        parts = []
        for content in message.content:
            if isinstance(content, LLMChatTextContent):
                parts.append(content.text)
            elif isinstance(content, LLMChatImageContent):
                parts.append(f"[image:{content.media_id}]")
            else:
                parts.append(content.model_dump_json())
        return "\n".join(parts)

    def _audit_memory_error(
        self,
        operation: str,
        error: BaseException,
        context: ChannelContext,
        correlation_id: Optional[str] = None,
    ) -> None:
        if self.audit_sink is None:
            return
        try:
            self.audit_sink(
                {
                    "component": "agent_runtime_memory",
                    "operation": operation,
                    "status": "failed",
                    "session": context.redacted(),
                    "correlation_id": correlation_id,
                    "error_type": type(error).__name__,
                }
            )
        except Exception:
            return

    def _persist_pending(self, pending: _PendingConfirmation) -> None:
        if self.session_store is None:
            return
        self.session_store.save_pending(
            {
                "confirmation_id": pending.confirmation_id,
                "agent_id": pending.agent.agent_id,
                "snapshot": pending.snapshot.to_dict(),
                "messages": [message.model_dump(mode="json") for message in pending.messages],
                "pending_call": pending.pending_call.model_dump(mode="json"),
                "tool_names": sorted(pending.tool_names),
                "mcp_server_ids": sorted(pending.mcp_server_ids),
                "principal_subject_digest": pending.principal_subject_digest,
                "agent_policy_signature": pending.agent_policy_signature,
                "tool_signature": pending.tool_signature,
                "session_allowlist": sorted(pending.session_allowlist or ()),
                "workflow_allowlist": sorted(pending.workflow_allowlist or ()),
                "model_id": pending.model_id,
                "tool_iterations": pending.tool_iterations,
                "trace_ids": list(pending.trace_ids),
                "correlation_id": pending.correlation_id,
            },
            session_key=pending.context.session_key,
        )

    def _restore_pending(
        self,
        record: Mapping[str, Any],
        context: ChannelContext,
    ) -> _PendingConfirmation:
        try:
            snapshot_payload = record["snapshot"]
            from datetime import datetime
            from .core import ResourceBinding

            snapshot = ResourceSnapshot(
                resources=tuple(
                    ResourceBinding(**item)
                    for item in snapshot_payload["resources"]
                ),
                model_id=snapshot_payload.get("model_id"),
                agent_id=snapshot_payload.get("agent_id"),
                model_priority=tuple(snapshot_payload.get("model_priority", ())),
                provider_allowlist=tuple(snapshot_payload.get("provider_allowlist", ())),
                created_at=datetime.fromisoformat(snapshot_payload["created_at"]),
                content_sha256=snapshot_payload["content_sha256"],
            )
            agent = self.agent_registry.get(str(record["agent_id"]))
            messages = [
                LLMChatMessage.model_validate(item) for item in record["messages"]
            ]
            pending_call = ToolCall.model_validate(record["pending_call"])
            principal_subject_digest = str(record["principal_subject_digest"])
            if (
                len(principal_subject_digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in principal_subject_digest
                )
            ):
                raise ValueError("pending confirmation principal is invalid")
            return _PendingConfirmation(
                confirmation_id=str(record["confirmation_id"]),
                context=context,
                agent=agent,
                snapshot=snapshot,
                messages=messages,
                pending_call=pending_call,
                tool_names=frozenset(record.get("tool_names", ())),
                mcp_server_ids=frozenset(record.get("mcp_server_ids", ())),
                principal_subject_digest=principal_subject_digest,
                agent_policy_signature=self._tupleize(record.get("agent_policy_signature", ())),
                tool_signature=tuple(record.get("tool_signature", ())),
                session_allowlist=(
                    frozenset(record["session_allowlist"])
                    if record.get("session_allowlist")
                    else None
                ),
                workflow_allowlist=(
                    frozenset(record["workflow_allowlist"])
                    if record.get("workflow_allowlist")
                    else None
                ),
                model_id=str(record["model_id"]),
                tool_iterations=int(record["tool_iterations"]),
                trace_ids=tuple(record.get("trace_ids", ())),
                correlation_id=self._record_correlation_id(record),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("pending confirmation record is invalid") from error

    @staticmethod
    def _confirmation_claim_failure(
        confirmation_id: str,
        context: ChannelContext,
        outcome: str,
        correlation_id: Optional[str] = None,
    ) -> RuntimeResult:
        errors = {
            "not_found": (
                "ConfirmationNotFound",
                "confirmation is missing or expired",
            ),
            "session_mismatch": (
                "ConfirmationSessionMismatch",
                "confirmation belongs to a different session",
            ),
            "executing": (
                "ConfirmationInProgress",
                "confirmation is already executing",
            ),
            "succeeded": (
                "ConfirmationAlreadyProcessed",
                "confirmation was already processed",
            ),
            "failed": (
                "ConfirmationAlreadyProcessed",
                "confirmation was already processed",
            ),
            "expired": (
                "ConfirmationExpired",
                "confirmation has expired",
            ),
        }
        error_type, message = errors.get(
            outcome,
            ("ConfirmationUnavailable", "confirmation is unavailable"),
        )
        return RuntimeResult(
            status=RuntimeStatus.FAILED,
            context=context,
            confirmation_id=confirmation_id,
            correlation_id=correlation_id,
            error={"type": error_type, "message": message},
        )

    @staticmethod
    def _record_correlation_id(record: Mapping[str, Any]) -> str:
        """Read or backfill the turn ID for a pre-correlation pending record."""

        value = record.get("correlation_id")
        return str(value) if value else uuid.uuid4().hex

    @classmethod
    def _tupleize(cls, value: Any) -> Any:
        if isinstance(value, list):
            return tuple(cls._tupleize(item) for item in value)
        if isinstance(value, dict):
            return tuple(sorted((key, cls._tupleize(item)) for key, item in value.items()))
        return value
