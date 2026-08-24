from kirara_ai.im.adapter import IMAdapter
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
from kirara_ai.workflow.core.dispatch.models.dispatch_rules import CombinedDispatchRule
from kirara_ai.workflow.core.dispatch.registry import DispatchRuleRegistry
from kirara_ai.workflow.core.dispatch.rules.base import DispatchRule
from kirara_ai.workflow.core.execution.exceptions import WorkflowExecutionTimeoutException
from kirara_ai.workflow.core.execution.executor import WorkflowExecutor
from kirara_ai.workflow.core.workflow.base import Workflow
from kirara_ai.workflow.core.workflow.registry import WorkflowRegistry

from .exceptions import WorkflowNotFoundException


class WorkflowDispatcher:
    """工作流调度器"""

    def __init__(self, container: DependencyContainer):
        self.container = container
        self.logger = get_logger("WorkflowDispatcher")

        # 从容器获取注册表
        self.workflow_registry = container.resolve(WorkflowRegistry)
        self.dispatch_registry = container.resolve(DispatchRuleRegistry)

    def register_rule(self, rule: CombinedDispatchRule):
        """注册一个调度规则"""
        self.dispatch_registry.register(rule)
        self.logger.info(f"Registered dispatch rule: {rule}")

    async def dispatch(self, source: IMAdapter, message: IMMessage):
        """
        根据消息内容选择第一个匹配的规则进行处理
        """
        with self.container.scoped() as scoped_container:
            scoped_container.register(IMAdapter, source)
            scoped_container.register(IMMessage, message)

            # 获取所有已启用的规则，按优先级排序
            active_rules = self.dispatch_registry.get_active_rules()

            for rule in active_rules:
                if rule.match(message, self.workflow_registry, scoped_container):
                    scoped_container.register(DispatchRule, rule)
                    try:
                        agent_id = rule.bound_agent_id
                        if agent_id is None:
                            agent_id = self._resolve_automatic_agent(source, message)
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
                        return await executor.run()
                    except WorkflowExecutionTimeoutException as e:
                        self.logger.error(f"Workflow execution timed out: {e}")
                        # 向上抛出，让 IM 适配器把失败原因回复给用户
                        raise
                    except Exception as e:
                        self.logger.opt(exception=e).error(f"Workflow execution failed: {e}", exc_info=True)
                        # 向上抛出，让 IM 适配器把失败原因回复给用户
                        raise
            self.logger.debug("No matching rule found for message")
            return None

    def _resolve_automatic_agent(
        self,
        source: IMAdapter,
        message: IMMessage,
    ) -> str | None:
        """Resolve a registry binding while preserving the legacy workflow path.

        A registry lookup is only attempted when the runtime is wired.  A missing
        binding is an intentional compatibility signal; malformed or disabled
        bindings are configuration errors and must not silently run a workflow.
        """

        if not self.container.has(AgentRegistry):
            return None
        if not self.container.has(AgentRuntimeExecutor):
            return None

        registry = self.container.resolve(AgentRegistry)
        context = ChannelContext.from_message(source, message)
        try:
            return registry.resolve(context).agent_id
        except LookupError:
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
        result = await runtime.run(
            context,
            message,
            session_agent_id=agent_id,
        )

        if result.status is RuntimeStatus.COMPLETED:
            if result.text:
                await source.send_message(
                    IMMessage(
                        sender=ChatSender.get_bot_sender(),
                        message_elements=[TextMessage(result.text)],
                    ),
                    message.sender,
                )
            return result

        if result.status is RuntimeStatus.AWAITING_CONFIRMATION:
            confirmation_id = result.confirmation_id or "pending"
            await source.send_message(
                IMMessage(
                    sender=ChatSender.get_bot_sender(),
                    message_elements=[
                        TextMessage(
                            "该操作需要确认后才能继续。"
                            f"确认编号：{confirmation_id}"
                        )
                    ],
                ),
                message.sender,
            )
            return result

        error_type = (result.error or {}).get("type", "RuntimeError")
        raise RuntimeError(f"Agent runtime failed: {error_type}")
