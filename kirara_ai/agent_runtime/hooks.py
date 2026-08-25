"""Controlled per-Agent hook execution.

Agent hooks are intentionally separate from the application extension event bus.
They run at explicit points in one Agent turn and are described by JSON.  The
first implementation only dispatches host-registered handlers; resource files
cannot smuggle in Python, shell, or process execution.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Callable, Mapping, Optional

from .core import AgentDefinition, ChannelContext, ResourceBinding, ResourceSnapshot


HOOK_EVENTS = frozenset(
    {
        "PreToolUse",
        "PermissionRequest",
        "PostToolUse",
        "PreCompact",
        "PostCompact",
        "SessionStart",
        "SessionEnd",
        "UserPromptSubmit",
        "SubagentStart",
        "SubagentStop",
        "Stop",
    }
)
_MAX_DECLARATION_BYTES = 256 * 1024
_MAX_TIMEOUT_MS = 5000
_MAX_OUTPUT_BYTES = 16 * 1024
_SENSITIVE_KEY_PARTS = (
    "password",
    "token",
    "secret",
    "cookie",
    "authorization",
    "credential",
    "api_key",
    "private_key",
)
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(bearer\s+|basic\s+)[A-Za-z0-9._~+/=-]+|sk-[A-Za-z0-9_-]{12,}"
)


@dataclass(frozen=True)
class HookHandler:
    """One host-owned handler and the capabilities it is allowed to use."""

    callback: Callable[..., Any]
    capabilities: frozenset[str] = frozenset()


@dataclass(frozen=True)
class HookOutcome:
    """Redacted result of one Hook event dispatch."""

    event: str
    status: str
    blocked: bool = False
    executed: int = 0
    resource_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    output_bytes: int = 0


@dataclass(frozen=True)
class _HookSpec:
    resource_id: str
    version: str
    content_sha256: str
    handler: str
    timeout_ms: int
    max_output_bytes: int
    required_capabilities: frozenset[str] = frozenset()
    required_permissions: frozenset[str] = frozenset()
    deny: bool = False


class AgentHookRuntime:
    """Dispatch explicit, registered Agent hooks with failure isolation."""

    def __init__(
        self,
        *,
        resource_loader: Optional[Callable[..., Any]] = None,
        resource_service: Any = None,
        handlers: Optional[Mapping[str, Callable[..., Any] | HookHandler]] = None,
        audit_sink: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.resource_loader = resource_loader
        self.resource_service = resource_service
        self.audit_sink = audit_sink
        self.handlers: dict[str, HookHandler] = {}
        for name, handler in (handlers or {}).items():
            self.register_handler(name, handler)

    def register_handler(
        self,
        name: str,
        handler: Callable[..., Any] | HookHandler,
    ) -> None:
        key = str(name).strip()
        if not key or not re.fullmatch(r"[A-Za-z0-9._:-]+", key):
            raise ValueError("hook handler name is invalid")
        self.handlers[key] = handler if isinstance(handler, HookHandler) else HookHandler(handler)

    async def run_event(
        self,
        event: str,
        *,
        agent: AgentDefinition,
        context: ChannelContext,
        snapshot: ResourceSnapshot,
        payload: Optional[Mapping[str, Any]] = None,
    ) -> HookOutcome:
        """Run all explicitly bound hooks for one event.

        Hook output is summarized for audit only.  It is never returned as model
        context.  A hook can block only ``PreToolUse``; all other failures are
        isolated so a bad hook cannot take down the Agent turn.
        """

        if event not in HOOK_EVENTS:
            raise ValueError(f"unsupported Agent hook event: {event}")

        executed = 0
        blocked = False
        resource_ids: list[str] = []
        reasons: list[str] = []
        output_bytes = 0
        for binding in agent.hook_bindings:
            if not binding.enabled:
                self._audit(event, binding, context, "skipped", "binding_disabled")
                continue
            resource_ids.append(binding.resource_id)
            try:
                self._revalidate_binding(binding)
                declaration = self._read_declaration(binding)
                spec = self._spec_for_event(binding, declaration, event)
                if spec is None:
                    self._audit(event, binding, context, "skipped", "event_not_declared")
                    continue
                handler = self.handlers.get(spec.handler)
                if handler is None:
                    raise PermissionError("hook handler is not registered")
                self._check_capabilities(agent, binding, handler, spec)
                result = await self._invoke(
                    handler.callback,
                    self._redact(payload or {}),
                    timeout_ms=spec.timeout_ms,
                )
                encoded = self._encode_output(result)
                output_bytes += len(encoded)
                if len(encoded) > spec.max_output_bytes:
                    raise ValueError("hook output exceeds the configured limit")
                executed += 1
                denied = event == "PreToolUse" and (
                    spec.deny or self._is_denial(result)
                )
                if denied:
                    blocked = True
                    reasons.append("hook_denied_tool")
                    self._audit(event, binding, context, "blocked", "hook_denied_tool")
                else:
                    self._audit(event, binding, context, "success", None)
            except asyncio.TimeoutError:
                reasons.append("timeout")
                self._audit(event, binding, context, "timeout", "timeout")
            except Exception as error:
                reasons.append(self._reason(error))
                self._audit(event, binding, context, "error", type(error).__name__)

        status = "blocked" if blocked else ("success" if executed else "skipped")
        return HookOutcome(
            event=event,
            status=status,
            blocked=blocked,
            executed=executed,
            resource_ids=tuple(resource_ids),
            reasons=tuple(reasons),
            output_bytes=output_bytes,
        )

    def _revalidate_binding(self, binding: ResourceBinding) -> None:
        service = self.resource_service
        if service is None:
            return
        current = service.resolve_binding(
            binding.resource_id,
            "hook",
            version=binding.version,
            enabled=binding.enabled,
            version_policy=binding.version_policy,
        )
        if (
            current.version != binding.version
            or current.content_sha256 != binding.content_sha256
            or current.permissions != binding.permissions
            or current.source != binding.source
        ):
            raise PermissionError("hook resource identity changed")

    def _read_declaration(self, binding: ResourceBinding) -> dict[str, Any]:
        if self.resource_service is not None:
            content = self.resource_service.read_entry(binding.resource_id, binding.version)
        elif self.resource_loader is not None:
            content = self._load(binding.resource_id, binding.version)
        else:
            raise LookupError("hook resource loader is unavailable")
        if inspect.isawaitable(content):
            raise TypeError("hook resource loader must be synchronous")
        if not isinstance(content, str) or len(content.encode("utf-8")) > _MAX_DECLARATION_BYTES:
            raise ValueError("hook declaration is too large")
        try:
            declaration = json.loads(content)
        except (TypeError, json.JSONDecodeError) as error:
            raise ValueError("hook declaration must be JSON") from error
        if not isinstance(declaration, dict) or not isinstance(declaration.get("events", {}), dict):
            raise ValueError("hook declaration must contain an events object")
        if any(key in declaration for key in ("command", "commands", "script", "code", "python")):
            raise PermissionError("process-backed hook declarations are not supported")
        return declaration

    def _spec_for_event(
        self,
        binding: ResourceBinding,
        declaration: Mapping[str, Any],
        event: str,
    ) -> Optional[_HookSpec]:
        raw = declaration.get("events", {}).get(event)
        if raw is None:
            return None
        if not isinstance(raw, dict):
            raise ValueError("hook event declaration must be an object")
        handler = raw.get("handler")
        if not isinstance(handler, str) or not handler.strip():
            raise ValueError("hook event handler is required")
        timeout_ms = self._bounded_int(raw.get("timeout_ms", 1000), 1, _MAX_TIMEOUT_MS)
        max_output_bytes = self._bounded_int(
            raw.get("max_output_bytes", 4096), 1, _MAX_OUTPUT_BYTES
        )
        required_capabilities = self._string_set(raw.get("required_capabilities", []))
        required_permissions = self._string_set(raw.get("required_permissions", []))
        if not required_permissions.issubset(set(binding.permissions)):
            raise PermissionError("hook required permissions are not granted by the binding")
        return _HookSpec(
            resource_id=binding.resource_id,
            version=binding.version,
            content_sha256=binding.content_sha256,
            handler=handler.strip(),
            timeout_ms=timeout_ms,
            max_output_bytes=max_output_bytes,
            required_capabilities=required_capabilities,
            required_permissions=required_permissions,
            deny=bool(raw.get("deny", False)),
        )

    @staticmethod
    def _check_capabilities(
        agent: AgentDefinition,
        binding: ResourceBinding,
        handler: HookHandler,
        spec: _HookSpec,
    ) -> None:
        required = set(handler.capabilities) | set(spec.required_capabilities)
        if not required.issubset(agent.capabilities):
            raise PermissionError("hook capabilities are not granted to the Agent")
        if not set(binding.permissions).issubset({"workflow.read", "workflow.write"}):
            raise PermissionError("hook binding contains an unsupported permission")

    async def _invoke(
        self,
        callback: Callable[..., Any],
        payload: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Any:
        """Invoke a registered callback without using TypeError as dispatch.

        A callback's own TypeError is a real handler failure.  Inspecting the
        signature first keeps that error visible and also moves synchronous
        callbacks to a worker thread so the timeout applies to the awaitable
        seen by the event loop.
        """

        try:
            signature = inspect.signature(callback)
        except (TypeError, ValueError):
            signature = None

        if signature is None:
            callback_args = (payload,)
        else:
            try:
                signature.bind(payload)
            except TypeError:
                try:
                    signature.bind(payload, None)
                except TypeError as error:
                    raise TypeError(
                        "hook handler must accept payload or payload and context"
                    ) from error
                callback_args = (payload, None)
            else:
                callback_args = (payload,)

        timeout = timeout_ms / 1000
        if inspect.iscoroutinefunction(callback):
            return await asyncio.wait_for(callback(*callback_args), timeout=timeout)

        result = await asyncio.wait_for(
            asyncio.to_thread(callback, *callback_args),
            timeout=timeout,
        )
        if inspect.isawaitable(result):
            return await asyncio.wait_for(result, timeout=timeout)
        return result

    def _load(self, resource_id: str, version: str) -> Any:
        loader = self.resource_loader
        if loader is None:
            raise LookupError("hook resource loader is unavailable")
        bound_self = getattr(loader, "__self__", None)
        if getattr(loader, "__name__", "") == "__getitem__" and isinstance(
            bound_self, Mapping
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

    @staticmethod
    def _bounded_int(value: Any, minimum: int, maximum: int) -> int:
        if isinstance(value, bool):
            raise ValueError("hook numeric limit is invalid")
        try:
            number = int(value)
        except (TypeError, ValueError) as error:
            raise ValueError("hook numeric limit is invalid") from error
        if not minimum <= number <= maximum:
            raise ValueError("hook numeric limit is outside the allowed range")
        return number

    @staticmethod
    def _string_set(value: Any) -> frozenset[str]:
        if value is None:
            return frozenset()
        if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
            raise ValueError("hook string list is invalid")
        return frozenset(item.strip() for item in value)

    @staticmethod
    def _encode_output(value: Any) -> bytes:
        try:
            return json.dumps(value, ensure_ascii=True, default=str).encode("utf-8")
        except (TypeError, ValueError):
            return repr(value).encode("utf-8", errors="replace")

    @staticmethod
    def _is_denial(value: Any) -> bool:
        if isinstance(value, Mapping):
            return value.get("allow") is False or value.get("deny") is True
        return value is False

    @staticmethod
    def _reason(error: BaseException) -> str:
        return str(error)[:128] or type(error).__name__

    @classmethod
    def _redact(cls, value: Any, key: str = "") -> Any:
        if any(part in key.lower() for part in _SENSITIVE_KEY_PARTS):
            return "[redacted]"
        if isinstance(value, Mapping):
            return {str(item): cls._redact(child, str(item)) for item, child in value.items()}
        if isinstance(value, list):
            return [cls._redact(item, key) for item in value]
        if isinstance(value, tuple):
            return [cls._redact(item, key) for item in value]
        if isinstance(value, str):
            return _SENSITIVE_VALUE_PATTERN.sub("[redacted]", value)[:4096]
        return value

    def _audit(
        self,
        event: str,
        binding: ResourceBinding,
        context: ChannelContext,
        outcome: str,
        reason: Optional[str],
    ) -> None:
        if self.audit_sink is None:
            return
        record: dict[str, Any] = {
            "component": "agent_hook",
            "event": event,
            "resource_id": binding.resource_id,
            "resource_version": binding.version,
            "resource_sha256": binding.content_sha256,
            "outcome": outcome,
            "session": context.redacted(),
        }
        if reason:
            record["reason"] = reason[:128]
        try:
            self.audit_sink(record)
        except Exception:
            pass
