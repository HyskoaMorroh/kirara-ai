import re
from typing import Optional

from kirara_ai.im.adapter import IMAdapter
from kirara_ai.im.delivery_timing_store import DeliveryTimingStore
from kirara_ai.im.message import IMMessage, TextMessage
from kirara_ai.im.sender import ChatSender
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.logger import get_logger
from kirara_ai.agent_runtime import (
    AgentRegistry,
    AgentRuntimeExecutor,
    ChannelContext,
    RuntimeStatus,
)
from kirara_ai.web.auth.principal import (
    RuntimePrincipal,
    get_runtime_principal,
    runtime_principal_context,
)
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import CombinedDispatchRule
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.dispatch.rules.base import DispatchRule
from kirara_ai.workflow.core.execution.exceptions import WorkflowExecutionTimeoutException
from kirara_ai.workflow.core.execution.executor import WorkflowExecutor
from kirara_ai.workflow.core.workflow.base import Workflow
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry

from .exceptions import AgentConfigurationNotFound, WorkflowNotFoundException


class WorkflowDispatcher:
    """工作流调度器"""

    _CONFIRMATION_PATTERN = re.compile(
        r"^\s*\u786e\u8ba4\s+([0-9a-f]{32})\s*$",
        re.IGNORECASE,
    )

    def __init__(self, container: DependencyContainer):
        self.container = container
        self.logger = get_logger("WorkflowDispatcher")

        # 从容器获取注册表
        self.workflow_registry = container.resolve(WorkflowRegistry)
        self.dispatch_registry = container.resolve(DispatchRuleRegistry)

    def _creator_principal(self, context: ChannelContext) -> Optional[RuntimePrincipal]:
        """Return the creator principal when this channel identity is declared as one.

        需求 10 要求创建者能通过插件改服务器内容、其他人不能。
        `principal_can_control_agent` 是唯一门禁，而 principal 此前只由 HTTP
        Bearer 中间件注入——IM 入站链路全程没有它，于是这些渠道上
        **所有人**（包括创建者本人）都拿不到 MCP 工具、跑不了 command Hook。
        设计是「非创建者不行」，实现成了「谁都不行」。

        这里按 `agent_runtime.creator_channel_identities` 做显式匹配。
        默认空表，不声明就返回 ``None``——行为与此前逐字节一致。
        """
        config = self._global_config()
        if config is None:
            return None
        identities = getattr(
            getattr(config, "agent_runtime", None),
            "creator_channel_identities",
            None,
        )
        if not identities:
            return None
        # 群聊消息的 conversation_scope 形如 `group:<id>`，私聊是 `c2c:<sender>`。
        is_group = str(context.conversation_scope).startswith("group:")
        for identity in identities:
            if not self._identity_matches(identity, context, is_group=is_group):
                continue
            subject = self._creator_subject()
            if subject is None:
                # 拿不到创建者身份就不能编一个出来：一个不匹配任何
                # Agent owner 的 subject 只会让门禁静默失败，比没有更糟。
                return None
            return RuntimePrincipal(
                subject=subject,
                role="admin",
                scopes=frozenset({"*"}),
                is_creator=True,
            )
        return None

    @staticmethod
    def _identity_matches(
        identity,
        context: ChannelContext,
        *,
        is_group: bool,
    ) -> bool:
        if is_group and not getattr(identity, "allow_group_chat", False):
            return False
        if str(getattr(identity, "channel_type", "")).strip().lower() != context.channel_type:
            return False
        if str(getattr(identity, "sender_scope", "")).strip() != context.sender_scope:
            return False
        account = getattr(identity, "account_scope", None)
        if account is not None and str(account).strip() != context.account_scope:
            return False
        instance = getattr(identity, "adapter_instance", None)
        if instance is not None and str(instance).strip() != context.adapter_instance:
            return False
        return True

    def _global_config(self):
        try:
            from kirara_ai.config.global_config import GlobalConfig

            if not self.container.has(GlobalConfig):
                return None
            return self.container.resolve(GlobalConfig)
        except Exception:  # noqa: BLE001 - 身份解析失败只应失去授权，不应中断消息
            return None

    def _creator_subject(self) -> Optional[str]:
        try:
            from kirara_ai.web.auth.services import AuthService

            if not self.container.has(AuthService):
                return None
            subject = getattr(self.container.resolve(AuthService), "creator_subject", None)
        except Exception:  # noqa: BLE001 - 同上
            return None
        if not isinstance(subject, str) or not subject.strip():
            return None
        return subject

    def register_rule(self, rule: CombinedDispatchRule):
        """注册一个调度规则"""
        self.dispatch_registry.register(rule)
        self.logger.info(f"Registered dispatch rule: {rule}")

    @staticmethod
    def _record_stage(message: IMMessage, stage: str, **details) -> None:
        """记录一个链路阶段；旧的自定义 IMMessage 实现没有这个方法时静默跳过。

        观测不能成为新的失败点：第三方适配器可能传入自己的消息对象，
        缺少时间线接口只应失去观测数据，不应让这条消息发不出去。
        """
        recorder = getattr(message, "record_delivery_stage", None)
        if callable(recorder):
            try:
                recorder(stage, **details)
            except Exception:  # noqa: BLE001 - 观测失败不得影响消息投递
                pass

    async def dispatch(
        self,
        source: IMAdapter,
        message: IMMessage,
        *,
        require_agent: bool = False,
    ):
        """
        根据消息内容选择第一个匹配的规则进行处理。

        ``require_agent`` 用于已经声明为 Agent 入口的调用方（例如 WebUI
        统一对话入口）。这类入口不能在 Agent 未配置时静默降级到旧工作流；
        传统 IM 适配器保持默认的兼容行为。
        """
        # 渠道身份到创建者 principal 的桥。
        #
        # **只在当前没有身份时才建立**：WebUI 走 HTTP 中间件，身份在进入这里之前
        # 就已经设好了。无条件 `runtime_principal_context(...)` 会用 `None`
        # 把它清掉——那不是「IM 侧多一条路」，那是「HTTP 侧少一条路」，
        # WebUI 的插件能力会整体失效。
        #
        # 建立时必须包住**整条**派发链路（工作流、Agent 运行时、Hook、MCP 都在
        # 里面读它），并在退出时自动还原：principal 是 ContextVar，
        # 泄漏出去会让后续任务凭空获得授权。
        if get_runtime_principal() is not None:
            return await self._dispatch_within_principal(
                source,
                message,
                require_agent=require_agent,
            )
        principal = self._creator_principal(ChannelContext.from_message(source, message))
        if principal is None:
            return await self._dispatch_within_principal(
                source,
                message,
                require_agent=require_agent,
            )
        with runtime_principal_context(principal):
            return await self._dispatch_within_principal(
                source,
                message,
                require_agent=require_agent,
            )

    async def _dispatch_within_principal(
        self,
        source: IMAdapter,
        message: IMMessage,
        *,
        require_agent: bool = False,
    ):
        with self.container.scoped() as scoped_container:
            scoped_container.register(IMAdapter, source)
            scoped_container.register(IMMessage, message)

            # 收到事件的时刻必须先记下来：适配器只能测到自己那几步，
            # 「排队等到工作流开始」这一段此前没有任何时间戳，
            # 于是「QQ 慢」和「模型慢」在事后无法区分。
            self._record_stage(message, "received_event")

            confirmation_id = self._parse_confirmation(message.content)
            if confirmation_id is not None and self.container.has(
                AgentRuntimeExecutor
            ):
                return await self._dispatch_confirmation(
                    source,
                    message,
                    confirmation_id,
                )

            # 获取所有已启用的规则，按优先级排序
            active_rules = self.dispatch_registry.get_active_rules()

            for rule in active_rules:
                if rule.match(message, self.workflow_registry, scoped_container):
                    scoped_container.register(DispatchRule, rule)
                    try:
                        agent_id = rule.bound_agent_id
                        if agent_id is None:
                            agent_id = self._resolve_automatic_agent(
                                source,
                                message,
                                require_agent=require_agent,
                            )
                        if agent_id is not None:
                            return await self._dispatch_agent(
                                source,
                                message,
                                agent_id,
                            )

                        self.logger.debug(f"Matched rule {rule}, executing workflow")
                        workflow = rule.get_workflow(scoped_container)
                        if workflow is None:
                            raise WorkflowNotFoundException(f"Workflow for rule {rule.name} not found, please check the rule configuration")
                        scoped_container.register(Workflow, workflow)
                        executor = WorkflowExecutor(scoped_container)
                        scoped_container.register(WorkflowExecutor, executor)
                        # 遗留工作流分支同样要记「工作流开始」：否则未迁移到 Agent
                        # 的部署缺掉排队耗时这一段，「QQ 慢」在这些部署上无法拆开定位。
                        # 后半段（格式化 / 发送）由 SendIMMessage 在真正发出的回复
                        # 对象上补齐并落库——那里才拿得到适配器写入的阶段。
                        self._record_stage(message, "workflow_started", workflow=rule.name)
                        return await executor.run()
                    except WorkflowExecutionTimeoutException as e:
                        self.logger.error(f"Workflow execution timed out: {e}")
                        # 向上抛出，让 IM 适配器把失败原因回复给用户
                        raise
                    except AgentConfigurationNotFound as e:
                        self.logger.debug(f"Agent configuration is incomplete: {e}")
                        raise
                    except Exception as e:
                        self.logger.opt(exception=e).error(f"Workflow execution failed: {e}", exc_info=True)
                        # 向上抛出，让 IM 适配器把失败原因回复给用户
                        raise
            if require_agent:
                # Agent-required channels are keyed by channel identity, not by
                # the presence of a legacy Workflow rule.  Rules may still bind
                # a specific Agent when they match, but a standalone channel,
                # account, session, or default binding must also be routable.
                agent_id = self._resolve_automatic_agent(
                    source,
                    message,
                    require_agent=True,
                )
                if agent_id is not None:
                    return await self._dispatch_agent(source, message, agent_id)
            self.logger.debug("No matching rule found for message")
            return None

    def _resolve_automatic_agent(
        self,
        source: IMAdapter,
        message: IMMessage,
        *,
        require_agent: bool = False,
    ) -> str | None:
        """Resolve a registry binding while preserving the legacy workflow path.

        A registry lookup is only attempted when the runtime is wired.  A missing
        binding is an intentional compatibility signal; malformed or disabled
        bindings are configuration errors and must not silently run a workflow.
        """

        if not self.container.has(AgentRegistry):
            if require_agent:
                raise AgentConfigurationNotFound(
                    "No Agent is configured for this channel identity"
                )
            return None
        if not self.container.has(AgentRuntimeExecutor):
            if require_agent:
                raise AgentConfigurationNotFound(
                    "No Agent is configured for this channel identity"
                )
            return None

        registry = self.container.resolve(AgentRegistry)
        context = ChannelContext.from_message(source, message)
        requested_agent_id = getattr(source, "session_agent_id", None)
        try:
            return registry.resolve(
                context,
                requested_agent_id,
            ).agent_id
        except LookupError:
            if requested_agent_id is not None:
                raise
            if require_agent:
                raise AgentConfigurationNotFound(
                    "No Agent is configured for this channel identity"
                )
            self.logger.debug(
                "No Agent binding matched channel context; keeping workflow compatibility path"
            )
            return None
        except Exception:
            self.logger.opt(exception=True).error(
                "Agent binding is invalid for channel context"
            )
            raise

    async def _dispatch_agent(
        self,
        source: IMAdapter,
        message: IMMessage,
        agent_id: str,
    ):
        """Run a selected Agent and deliver exactly one channel response."""

        if not self.container.has(AgentRuntimeExecutor):
            raise RuntimeError("Agent runtime is not configured")

        runtime = self.container.resolve(AgentRuntimeExecutor)
        context = ChannelContext.from_message(source, message)
        self._record_stage(message, "workflow_started", agent_id=agent_id)
        result = await runtime.run(
            context,
            message,
            session_agent_id=agent_id,
        )
        self._record_model_stages(message, result)

        return await self._deliver_runtime_result(source, message, result)

    async def _dispatch_confirmation(
        self,
        source: IMAdapter,
        message: IMMessage,
        confirmation_id: str,
    ):
        """Resume a pending operation only from its originating channel context."""

        runtime = self.container.resolve(AgentRuntimeExecutor)
        context = ChannelContext.from_message(source, message)
        result = await runtime.confirm(confirmation_id, context)
        return await self._deliver_runtime_result(
            source,
            message,
            result,
            confirmation_flow=True,
        )

    def _record_model_stages(self, message: IMMessage, result) -> None:
        """把模型侧的首字节与完成时刻并入同一条链路时间线。

        ``LLMManager`` 已经在 ``ProviderAttempt`` 上记录了 ``first_byte_at``，
        但那份数据存在另一个结构里，从来没有和消息投递时间线合并过——
        于是「模型首字节用了多久」和「适配器发送用了多久」无法在同一条链路上比较。
        非流式请求没有真实首字节，这里不会伪造一个。
        """
        response = getattr(result, "response", None)
        usage = getattr(response, "usage", None)
        details = {}
        if usage is not None:
            for name in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(usage, name, None)
                if value is not None:
                    details[name] = value
        trace_ids = getattr(result, "trace_ids", ())
        if trace_ids:
            details["trace_id"] = trace_ids[-1]
        # 首字节必须先记：时间线按记录顺序保存，llm_first_byte 晚于 llm_completed
        # 入列会让「按阶段顺序读」的消费者看到一条倒序链路。
        first_byte_at = getattr(result, "llm_first_byte_at", None)
        if first_byte_at is not None:
            recorder = getattr(message, "record_delivery_stage_at", None)
            if callable(recorder):
                try:
                    recorder("llm_first_byte", first_byte_at, **details)
                except Exception:  # noqa: BLE001 - 观测失败不得影响消息投递
                    pass
        self._record_stage(message, "llm_completed", **details)

    def _carry_timeline(self, source_message: IMMessage, reply: IMMessage) -> None:
        """把入站阶段复制到回复消息，形成一条端到端时间线。"""
        events = getattr(source_message, "delivery_timeline", ())
        recorder = getattr(reply, "record_delivery_stage_at", None)
        if not events or not callable(recorder):
            return
        for event in events:
            try:
                recorder(event.stage, event.timestamp, **dict(event.details))
            except Exception:  # noqa: BLE001 - 观测失败不得影响消息投递
                return

    def _log_delivery_durations(self, message: IMMessage) -> None:
        """把各阶段耗时写进日志，便于事后回答「慢在哪一段」。"""
        durations = getattr(message, "delivery_durations", None)
        if not callable(durations):
            return
        try:
            measured = durations()
        except Exception:  # noqa: BLE001 - 观测失败不得影响消息投递
            return
        if measured:
            summary = " ".join(f"{name}={value:.3f}s" for name, value in measured.items())
            self.logger.debug(f"Delivery timeline: {summary}")

    def _persist_delivery_durations(
        self,
        source: IMAdapter,
        message: IMMessage,
        reply: IMMessage,
    ) -> None:
        """把本次回复的各阶段耗时落库，供事后按时间范围回查。

        日志只能回答「刚才那条为什么慢」；一周后问「上周二 QQ 慢是模型还是发送」
        需要行。这里只写时长与计数，不写任何消息正文；会话键做摘要后存储。
        """
        if not self.container.has(DeliveryTimingStore):
            return
        durations = getattr(reply, "delivery_durations", None)
        if not callable(durations):
            return
        try:
            measured = durations()
        except Exception:  # noqa: BLE001 - 观测失败不得影响消息投递
            return
        if not measured:
            return
        try:
            context = ChannelContext.from_message(source, message)
            stages = {event.stage for event in getattr(reply, "delivery_timeline", ())}
            segment_count = None
            retry_count = None
            for event in getattr(reply, "delivery_timeline", ()):
                if event.stage == "formatting_completed":
                    segment_count = event.details.get("segment_count", segment_count)
                if event.stage in {"send_succeeded", "send_failed"}:
                    retry_count = event.details.get("retry_count", retry_count)
            self.container.resolve(DeliveryTimingStore).record(
                channel=context.channel_type,
                adapter_instance=context.adapter_instance,
                durations=measured,
                status="failed" if "send_failed" in stages else "succeeded",
                conversation_key=context.conversation_scope,
                segment_count=segment_count,
                retry_count=retry_count,
            )
        except Exception:  # noqa: BLE001 - 观测失败不得影响消息投递
            self.logger.debug("Delivery timing persistence failed", exc_info=True)

    async def _deliver_runtime_result(
        self,
        source: IMAdapter,
        message: IMMessage,
        result,
        *,
        confirmation_flow: bool = False,
    ):
        """Deliver one sanitized runtime result through the originating adapter."""

        if result.status is RuntimeStatus.COMPLETED:
            if result.text:
                reply = IMMessage(
                    sender=ChatSender.get_bot_sender(),
                    message_elements=[TextMessage(result.text)],
                )
                # 把入站消息已经记录的阶段带到回复对象上，
                # 适配器随后追加的格式化与发送阶段才能与前半段拼成一条完整链路。
                self._carry_timeline(message, reply)
                await source.send_message(reply, message.sender)
                self._log_delivery_durations(reply)
                self._persist_delivery_durations(source, message, reply)
            return result

        if result.status is RuntimeStatus.AWAITING_CONFIRMATION:
            confirmation_id = result.confirmation_id or "pending"
            await source.send_message(
                IMMessage(
                    sender=ChatSender.get_bot_sender(),
                    message_elements=[
                        TextMessage(
                            "\u8be5\u64cd\u4f5c\u9700\u8981\u786e\u8ba4\u540e\u624d\u80fd\u7ee7\u7eed\u3002"
                            f"\u8bf7\u56de\u590d\uff1a\u786e\u8ba4 {confirmation_id}"
                        )
                    ],
                ),
                message.sender,
            )
            return result

        error_type = (result.error or {}).get("type", "RuntimeError")
        if confirmation_flow:
            safe_messages = {
                "ConfirmationSessionMismatch": "\u8be5\u786e\u8ba4\u53ea\u80fd\u5728\u539f\u4f1a\u8bdd\u4e2d\u6267\u884c\u3002",
                "ConfirmationExpired": "\u8be5\u786e\u8ba4\u5df2\u8fc7\u671f\uff0c\u8bf7\u91cd\u65b0\u53d1\u8d77\u64cd\u4f5c\u3002",
                "ConfirmationInProgress": "\u8be5\u64cd\u4f5c\u6b63\u5728\u6267\u884c\uff0c\u8bf7\u52ff\u91cd\u590d\u786e\u8ba4\u3002",
                "ConfirmationAlreadyProcessed": "\u8be5\u786e\u8ba4\u5df2\u5904\u7406\uff0c\u4e0d\u4f1a\u91cd\u590d\u6267\u884c\u3002",
                "ConfirmationNotFound": "\u672a\u627e\u5230\u8be5\u786e\u8ba4\uff0c\u53ef\u80fd\u5df2\u8fc7\u671f\u3002",
            }
            text = safe_messages.get(
                error_type,
                "\u786e\u8ba4\u64cd\u4f5c\u672a\u5b8c\u6210\uff0c\u8bf7\u91cd\u65b0\u53d1\u8d77\u3002",
            )
            await source.send_message(
                IMMessage(
                    sender=ChatSender.get_bot_sender(),
                    message_elements=[TextMessage(text)],
                ),
                message.sender,
            )
            return result
        raise RuntimeError(f"Agent runtime failed: {error_type}")

    @classmethod
    def _parse_confirmation(cls, content: str) -> str | None:
        match = cls._CONFIRMATION_PATTERN.fullmatch(content or "")
        return match.group(1).lower() if match else None
