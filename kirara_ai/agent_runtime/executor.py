"""Channel-independent execution of one Agent turn.

This module deliberately owns orchestration only.  Provider resilience remains
in ``LLMManager`` and MCP transport/connection state remains in its manager.
The executor is the policy boundary that combines both with a frozen resource
view for one conversation turn.
"""

from __future__ import annotations

import inspect
import json
import uuid
from collections.abc import Mapping as ABCMapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

from kirara_ai.im.message import IMMessage
from kirara_ai.llm.format.message import (
    LLMChatContentPartType,
    LLMChatImageContent,
    LLMChatMessage,
    LLMChatTextContent,
    LLMToolCallContent,
)
from kirara_ai.llm.format.request import LLMChatRequest, Tool, ToolParameters
from kirara_ai.llm.format.response import LLMChatResponse
from kirara_ai.llm.format.tool import LLMToolResultContent, ToolCall
from kirara_ai.llm.resilience import ChatExecutionResult
from kirara_ai.agent_runtime.core import (
    AgentDefinition,
    AgentRegistry,
    ChannelContext,
    ResourceSnapshot,
    effective_mcp_allowlist,
)
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService


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
    error: Optional[dict[str, str]] = None
    trace_ids: tuple[str, ...] = ()


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
    agent_policy_signature: tuple[Any, ...]
    tool_signature: tuple[Any, ...]
    session_allowlist: Optional[frozenset[str]]
    workflow_allowlist: Optional[frozenset[str]]
    model_id: str
    tool_iterations: int
    trace_ids: tuple[str, ...]


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
        audit_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.agent_registry = agent_registry
        self.llm_manager = llm_manager
        self.mcp_manager = mcp_manager
        self.resource_loader = resource_loader or (lambda _resource_id: "")
        self.resource_service = resource_service
        self.audit_sink = audit_sink
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
    ) -> RuntimeResult:
        """Execute one inbound message and never perform an unconfirmed tool."""

        try:
            agent = self._trusted_agent(self.agent_registry.resolve(context, session_agent_id))
            session_allowlist = self._optional_set(session_mcp_allowlist)
            workflow_allowlist = self._optional_set(workflow_mcp_allowlist)
            tool_entries = self._read_tool_entries()
            effective_tools = self._effective_tool_names(
                agent,
                tool_entries,
                session_allowlist,
                workflow_allowlist,
            )
            messages = self._build_messages(agent, message, history)
            tools = self._build_tools(tool_entries, effective_tools) if agent.allow_tools else []
            model_id = agent.model_priority[0]
            snapshot = agent.snapshot(model_id=model_id)
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
            )
            self._audit("run", result)
            return result
        except Exception as error:
            result = RuntimeResult(
                status=RuntimeStatus.FAILED,
                context=context,
                error={"type": type(error).__name__, "message": self._safe_error(error)},
            )
            self._audit("run", result)
            return result

    async def confirm(self, confirmation_id: str) -> RuntimeResult:
        """Approve one pending call and resume its original model turn."""

        pending = self._pending.pop(confirmation_id, None)
        if pending is None:
            return RuntimeResult(
                status=RuntimeStatus.FAILED,
                confirmation_id=confirmation_id,
                error={"type": "ConfirmationNotFound", "message": "confirmation is missing or expired"},
            )
        try:
            # A confirmation is a short-lived capability. Re-read the Agent,
            # tool cache and transport state before using it.
            current_agent = self._trusted_agent(
                self.agent_registry.get(pending.agent.agent_id)
            )
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
                    error={
                        "type": "ConfirmationExpired",
                        "message": "Agent or MCP binding changed before confirmation",
                    },
                )
                self._audit("confirm", result)
                return result
            result = await self._execute_tool_call(
                pending.pending_call,
                current_agent,
                pending.session_allowlist,
                pending.workflow_allowlist,
                self._mcp_server_ids(current_agent),
                confirmed=True,
            )
            messages = list(pending.messages)
            messages.append(self._assistant_tool_message(pending.pending_call))
            messages.append(LLMChatMessage(role="tool", content=[result]))
            response = await self._run_loop(
                context=pending.context,
                agent=current_agent,
                snapshot=pending.snapshot,
                messages=messages,
                tools=self._build_tools(current_entries, current_effective),
                tool_names=current_effective,
                session_allowlist=pending.session_allowlist,
                workflow_allowlist=pending.workflow_allowlist,
                model_id=pending.model_id,
                tool_iterations=pending.tool_iterations + 1,
                trace_ids=pending.trace_ids,
            )
            self._audit("confirm", response)
            return response
        except Exception as error:
            result = RuntimeResult(
                status=RuntimeStatus.FAILED,
                context=pending.context,
                agent_id=pending.agent.agent_id,
                snapshot=pending.snapshot,
                confirmation_id=confirmation_id,
                error={"type": type(error).__name__, "message": self._safe_error(error)},
            )
            self._audit("confirm", result)
            return result

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
    ) -> RuntimeResult:
        current_messages = list(messages)
        current_model = model_id
        current_traces = list(trace_ids)
        response: Optional[LLMChatResponse] = None

        # One final model request is made with tool_choice=none after the
        # configured number of tool rounds, so a model cannot extend the loop.
        for iteration in range(tool_iterations, agent.max_tool_iterations + 1):
            request = LLMChatRequest(
                messages=current_messages,
                model=current_model,
                tools=tools if agent.allow_tools and iteration < agent.max_tool_iterations else None,
                tool_choice=("none" if iteration >= agent.max_tool_iterations else None),
            )
            response, current_model, trace_id = await self._execute_model(
                request, agent.model_priority, current_model
            )
            if trace_id:
                current_traces.append(trace_id)
            if not response.message.tool_calls or iteration >= agent.max_tool_iterations:
                return self._completed_result(
                    context, agent, snapshot, current_messages, response, tuple(current_traces)
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
                    confirmation_id = uuid.uuid4().hex
                    self._pending[confirmation_id] = _PendingConfirmation(
                        confirmation_id=confirmation_id,
                        context=context,
                        agent=agent,
                        snapshot=snapshot,
                        messages=current_messages,
                        pending_call=call,
                        tool_names=tool_names,
                        mcp_server_ids=self._mcp_server_ids(agent),
                        agent_policy_signature=self._agent_policy_signature(agent),
                        tool_signature=self._tool_signature(self._read_tool_entries().get(name)),
                        session_allowlist=session_allowlist,
                        workflow_allowlist=workflow_allowlist,
                        model_id=current_model,
                        tool_iterations=iteration,
                        trace_ids=tuple(current_traces),
                    )
                    return RuntimeResult(
                        status=RuntimeStatus.AWAITING_CONFIRMATION,
                        context=context,
                        agent_id=agent.agent_id,
                        snapshot=snapshot,
                        response=response,
                        messages=current_messages,
                        confirmation_id=confirmation_id,
                        trace_ids=tuple(current_traces),
                    )
                tool_result = await self._execute_tool_call(
                    call,
                    agent,
                    session_allowlist,
                    workflow_allowlist,
                    self._mcp_server_ids(agent),
                    confirmed=False,
                )
                current_messages.append(self._assistant_tool_message(call))
                current_messages.append(LLMChatMessage(role="tool", content=[tool_result]))

        # The loop always returns from the final no-tools request.  This guard
        # protects the contract if the loop is changed in a future revision.
        return RuntimeResult(
            status=RuntimeStatus.FAILED,
            context=context,
            agent_id=agent.agent_id,
            snapshot=snapshot,
            error={"type": "RuntimeLoopError", "message": "runtime loop ended without a model response"},
        )

    async def _execute_model(
        self,
        request: LLMChatRequest,
        model_priority: Sequence[str],
        current_model: str,
    ) -> tuple[LLMChatResponse, str, str]:
        candidates = list(dict.fromkeys([current_model, *model_priority]))
        last_error: Optional[BaseException] = None
        for model_id in candidates:
            if hasattr(request, "model_copy"):
                candidate_request = request.model_copy(update={"model": model_id})
            else:
                candidate_request = request.copy(update={"model": model_id})
            try:
                result = self.llm_manager.execute_chat(candidate_request)
                if inspect.isawaitable(result):
                    result = await result
                response = result.response if isinstance(result, ChatExecutionResult) else getattr(result, "response", result)
                if not isinstance(response, LLMChatResponse):
                    raise TypeError("LLM manager returned an invalid chat response")
                trace_id = str(getattr(result, "trace_id", "") or "")
                return response, model_id, trace_id
            except Exception as error:
                last_error = error
                continue
        if last_error is not None:
            raise last_error
        raise LookupError("Agent has no available model")

    async def _execute_tool_call(
        self,
        call: ToolCall,
        agent: AgentDefinition,
        session_allowlist: Optional[frozenset[str]],
        workflow_allowlist: Optional[frozenset[str]],
        mcp_server_ids: frozenset[str],
        *,
        confirmed: bool,
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
                session_allowlist=session_allowlist,
                workflow_allowlist=workflow_allowlist,
                confirmed=confirmed,
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
    ) -> list[LLMChatMessage]:
        sections: list[str] = []
        for binding in (*agent.prompt_bindings, *agent.skill_bindings):
            if not binding.enabled:
                continue
            content = self._load_resource(binding.resource_id, binding.version)
            if inspect.isawaitable(content):
                raise TypeError("resource_loader must be synchronous during snapshot creation")
            if content is None:
                continue
            text = str(content).strip()
            if text:
                sections.append(f"[{binding.resource_type}:{binding.resource_id}]\n{text}")
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
                        version=binding.version,
                        enabled=binding.enabled,
                    )
                )
            return tuple(resolved)

        return AgentDefinition(
            agent_id=agent.agent_id,
            display_name=agent.display_name,
            enabled=agent.enabled,
            workflow_id=agent.workflow_id,
            model_priority=agent.model_priority,
            provider_allowlist=agent.provider_allowlist,
            capabilities=agent.capabilities,
            prompt_bindings=resolve_bindings(agent.prompt_bindings, "prompt"),
            skill_bindings=resolve_bindings(agent.skill_bindings, "skill"),
            mcp_bindings=resolve_bindings(agent.mcp_bindings, "mcp"),
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
        return frozenset(
            binding.resource_id for binding in agent.mcp_bindings if binding.enabled
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
        bound_tools = frozenset(
            name
            for name, entry in entries.items()
            if str(getattr(entry, "server_id", "")) in bound_servers
        )
        return effective_mcp_allowlist(
            agent_allowlist=agent.mcp_allowlist,
            session_allowlist=session_allowlist,
            workflow_allowlist=workflow_allowlist,
            connected_tools=bound_tools,
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
                item.content_sha256,
                item.enabled,
            )
            for item in agent.resource_bindings
        )
        return (
            agent.enabled,
            tuple(agent.model_priority),
            bindings,
            tuple(sorted(agent.mcp_allowlist)),
            agent.allow_tools,
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
            trace_ids=trace_ids,
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
            "session": result.context.redacted() if result.context else None,
            "snapshot_sha256": result.snapshot.content_sha256 if result.snapshot else None,
            "confirmation_id": bool(result.confirmation_id),
            "error_type": result.error.get("type") if result.error else None,
        }
        try:
            self.audit_sink(record)
        except Exception:
            return
