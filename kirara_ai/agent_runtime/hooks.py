"""Controlled per-Agent Hook execution.

Agent Hooks are separate from the application extension event bus. They run
at explicit points in one Agent turn and support two controlled handler kinds:
host-registered Python callbacks and Codex-compatible command handlers.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
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
_MAX_COMMAND_PARTS = 128
_MAX_COMMAND_PART_BYTES = 8192
_MAX_ENV_ENTRIES = 64
_SUPPORTED_PERMISSIONS = frozenset(
    {"workflow.read", "workflow.write", "process.execute"}
)
_SAFE_INHERITED_ENV = frozenset(
    {
        "COMSPEC",
        "HOME",
        "LANG",
        "PATH",
        "PATHEXT",
        "SHELL",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
)
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
    """Redacted and bounded result of one Hook event dispatch."""

    event: str
    status: str
    blocked: bool = False
    executed: int = 0
    resource_ids: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    output_bytes: int = 0
    additional_context: tuple[str, ...] = ()
    system_messages: tuple[str, ...] = ()
    continue_execution: bool = True
    stop_reason: Optional[str] = None
    suppress_output: bool = False
    permission_decision: Optional[str] = None
    permission_decision_reason: Optional[str] = None
    updated_input: Optional[dict[str, Any]] = None
    permission_behavior: Optional[str] = None


@dataclass(frozen=True)
class _HookSpec:
    resource_id: str
    version: str
    content_sha256: str
    kind: str
    handler: Optional[str]
    command: tuple[str, ...] | str | None
    command_windows: tuple[str, ...] | str | None
    timeout_ms: int
    max_output_bytes: int
    cwd: Optional[str]
    env: tuple[tuple[str, str], ...]
    required_capabilities: frozenset[str] = frozenset()
    required_permissions: frozenset[str] = frozenset()
    deny: bool = False


@dataclass(frozen=True)
class _ParsedOutput:
    blocked: bool = False
    reason: Optional[str] = None
    additional_context: Optional[str] = None
    system_message: Optional[str] = None
    continue_execution: bool = True
    stop_reason: Optional[str] = None
    suppress_output: bool = False
    permission_decision: Optional[str] = None
    permission_decision_reason: Optional[str] = None
    updated_input: Optional[dict[str, Any]] = None
    permission_behavior: Optional[str] = None


class _HookOutputLimitError(ValueError):
    pass


class AgentHookRuntime:
    """Dispatch registered callbacks and capability-gated command Hooks."""

    def __init__(
        self,
        *,
        resource_loader: Optional[Callable[..., Any]] = None,
        resource_service: Any = None,
        handlers: Optional[Mapping[str, Callable[..., Any] | HookHandler]] = None,
        audit_sink: Optional[Callable[[dict[str, Any]], None]] = None,
        process_execution_enabled: bool = True,
        working_directory: str | Path | None = None,
    ) -> None:
        self.resource_loader = resource_loader
        self.resource_service = resource_service
        self.audit_sink = audit_sink
        self.process_execution_enabled = bool(process_execution_enabled)
        self.working_directory = Path(working_directory or os.getcwd()).resolve()
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
        correlation_id: Optional[str] = None,
    ) -> HookOutcome:
        """Run all explicitly bound Hooks for one event with failure isolation."""

        if event not in HOOK_EVENTS:
            raise ValueError(f"unsupported Agent hook event: {event}")

        executed = 0
        blocked = False
        had_error = False
        had_timeout = False
        resource_ids: list[str] = []
        reasons: list[str] = []
        output_bytes = 0
        additional_context: list[str] = []
        system_messages: list[str] = []
        continue_execution = True
        stop_reason: Optional[str] = None
        suppress_output = False
        permission_decision: Optional[str] = None
        permission_decision_reason: Optional[str] = None
        updated_input: Optional[dict[str, Any]] = None
        permission_behavior: Optional[str] = None

        hook_bindings = tuple(
            binding for binding in snapshot.resources if binding.resource_type == "hook"
        )
        for binding in hook_bindings:
            if not binding.enabled:
                self._audit(
                    event,
                    binding,
                    context,
                    "skipped",
                    "binding_disabled",
                    correlation_id=correlation_id,
                )
                continue
            resource_ids.append(binding.resource_id)
            try:
                self._revalidate_binding(binding)
                declaration = self._read_declaration(binding)
                spec = self._spec_for_event(binding, declaration, event)
                if spec is None:
                    self._audit(
                        event,
                        binding,
                        context,
                        "skipped",
                        "event_not_declared",
                        correlation_id=correlation_id,
                    )
                    continue

                redacted_payload = self._redact(payload or {})
                if spec.kind == "handler":
                    handler = self.handlers.get(spec.handler or "")
                    if handler is None:
                        raise PermissionError("hook handler is not registered")
                    self._check_capabilities(agent, binding, spec, handler=handler)
                    result = await self._invoke(
                        handler.callback,
                        redacted_payload,
                        timeout_ms=spec.timeout_ms,
                    )
                    encoded = self._encode_output(result)
                    if len(encoded) > spec.max_output_bytes:
                        raise _HookOutputLimitError(
                            "hook output exceeds the configured limit"
                        )
                    parsed = self._parse_output(event, result)
                else:
                    self._check_capabilities(agent, binding, spec)
                    encoded = await self._invoke_command(spec, redacted_payload)
                    parsed = self._parse_command_output(event, encoded)

                output_bytes += len(encoded)
                executed += 1
                denied = spec.deny or parsed.blocked
                if event == "PreToolUse" and denied:
                    blocked = True
                elif event in {"PermissionRequest", "PostToolUse", "UserPromptSubmit"}:
                    blocked = blocked or denied
                if denied:
                    reasons.append(parsed.reason or "hook_denied")

                if parsed.additional_context:
                    additional_context.append(parsed.additional_context)
                if parsed.system_message:
                    system_messages.append(parsed.system_message)
                continue_execution = continue_execution and parsed.continue_execution
                stop_reason = stop_reason or parsed.stop_reason
                suppress_output = suppress_output or parsed.suppress_output
                if parsed.permission_decision == "deny" or permission_decision is None:
                    permission_decision = parsed.permission_decision or permission_decision
                    permission_decision_reason = (
                        parsed.permission_decision_reason or permission_decision_reason
                    )
                if parsed.updated_input is not None:
                    updated_input = parsed.updated_input
                if parsed.permission_behavior is not None:
                    permission_behavior = parsed.permission_behavior

                self._audit(
                    event,
                    binding,
                    context,
                    "blocked" if denied else "success",
                    parsed.reason if denied else None,
                    correlation_id=correlation_id,
                )
            except asyncio.TimeoutError:
                had_timeout = True
                reasons.append("timeout")
                self._audit(
                    event,
                    binding,
                    context,
                    "timeout",
                    "timeout",
                    correlation_id=correlation_id,
                )
            except Exception as error:
                had_error = True
                reasons.append(type(error).__name__)
                self._audit(
                    event,
                    binding,
                    context,
                    "error",
                    type(error).__name__,
                    correlation_id=correlation_id,
                )

        if blocked:
            status = "blocked"
        elif executed:
            status = "success"
        elif had_error:
            status = "error"
        elif had_timeout:
            status = "timeout"
        else:
            status = "skipped"
        return HookOutcome(
            event=event,
            status=status,
            blocked=blocked,
            executed=executed,
            resource_ids=tuple(resource_ids),
            reasons=tuple(reasons),
            output_bytes=output_bytes,
            additional_context=tuple(additional_context),
            system_messages=tuple(system_messages),
            continue_execution=continue_execution,
            stop_reason=stop_reason,
            suppress_output=suppress_output,
            permission_decision=permission_decision,
            permission_decision_reason=permission_decision_reason,
            updated_input=updated_input,
            permission_behavior=permission_behavior,
        )

    def _revalidate_binding(self, binding: ResourceBinding) -> None:
        service = self.resource_service
        if service is None:
            return
        current = service.resolve_binding(
            binding.resource_id,
            "hook",
            version=binding.version,
            enabled=False,
            version_policy="fixed",
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
        if any(key in declaration for key in ("script", "code", "python")):
            raise PermissionError("executable Hook content must use a command declaration")
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

        raw_kind = raw.get("type")
        if raw_kind is None:
            kind = "handler" if raw.get("handler") is not None else "command"
        elif isinstance(raw_kind, str) and raw_kind.strip().lower() in {"handler", "command"}:
            kind = raw_kind.strip().lower()
        else:
            raise ValueError("hook event type must be handler or command")

        handler: Optional[str] = None
        command: tuple[str, ...] | str | None = None
        command_windows: tuple[str, ...] | str | None = None
        if kind == "handler":
            raw_handler = raw.get("handler")
            if not isinstance(raw_handler, str) or not raw_handler.strip():
                raise ValueError("hook event handler is required")
            handler = raw_handler.strip()
            if any(key in raw for key in ("command", "commandWindows")):
                raise ValueError("handler Hook cannot declare a command")
        else:
            command = self._command_value(raw.get("command"), required=True)
            if raw.get("commandWindows") is not None:
                command_windows = self._command_value(raw.get("commandWindows"), required=True)
            if raw.get("handler") is not None:
                raise ValueError("command Hook cannot declare a host handler")

        timeout_value = raw.get("timeout_ms")
        if timeout_value is None and raw.get("timeout") is not None:
            timeout_seconds = self._bounded_number(raw.get("timeout"), 0.001, 5.0)
            timeout_value = round(timeout_seconds * 1000)
        timeout_ms = self._bounded_int(timeout_value or 1000, 1, _MAX_TIMEOUT_MS)
        max_output_bytes = self._bounded_int(
            raw.get("max_output_bytes", 4096), 1, _MAX_OUTPUT_BYTES
        )
        required_capabilities = self._string_set(raw.get("required_capabilities", []))
        required_permissions = self._string_set(raw.get("required_permissions", []))
        if kind == "command":
            required_capabilities = required_capabilities | {"process.execute"}
            required_permissions = required_permissions | {"process.execute"}
        if not required_permissions.issubset(set(binding.permissions)):
            raise PermissionError("hook required permissions are not granted by the binding")

        raw_cwd = raw.get("cwd")
        if raw_cwd is not None and (
            not isinstance(raw_cwd, str) or not raw_cwd.strip() or len(raw_cwd) > 1024
        ):
            raise ValueError("hook working directory is invalid")
        env = self._environment_value(raw.get("env", {}))
        return _HookSpec(
            resource_id=binding.resource_id,
            version=binding.version,
            content_sha256=binding.content_sha256,
            kind=kind,
            handler=handler,
            command=command,
            command_windows=command_windows,
            timeout_ms=timeout_ms,
            max_output_bytes=max_output_bytes,
            cwd=raw_cwd.strip() if isinstance(raw_cwd, str) else None,
            env=env,
            required_capabilities=frozenset(required_capabilities),
            required_permissions=frozenset(required_permissions),
            deny=bool(raw.get("deny", False)),
        )

    def _check_capabilities(
        self,
        agent: AgentDefinition,
        binding: ResourceBinding,
        spec: _HookSpec,
        *,
        handler: HookHandler | None = None,
    ) -> None:
        required = set(spec.required_capabilities)
        if handler is not None:
            required.update(handler.capabilities)
        if not required.issubset(agent.capabilities):
            raise PermissionError("hook capabilities are not granted to the Agent")
        if not set(binding.permissions).issubset(_SUPPORTED_PERMISSIONS):
            raise PermissionError("hook binding contains an unsupported permission")
        if spec.kind == "command" and not self.process_execution_enabled:
            raise PermissionError("command Hook execution is disabled by runtime policy")

    async def _invoke(
        self,
        callback: Callable[..., Any],
        payload: Mapping[str, Any],
        *,
        timeout_ms: int,
    ) -> Any:
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

    async def _invoke_command(
        self,
        spec: _HookSpec,
        payload: Mapping[str, Any],
    ) -> bytes:
        command = spec.command_windows if os.name == "nt" and spec.command_windows else spec.command
        if command is None:
            raise ValueError("hook command is required")
        argv = self._command_argv(command)
        cwd = self._command_cwd(spec.cwd)
        env = self._command_environment(spec.env)

        process_kwargs: dict[str, Any] = {
            "cwd": str(cwd),
            "env": env,
            "stdin": asyncio.subprocess.PIPE,
            "stdout": asyncio.subprocess.PIPE,
            "stderr": asyncio.subprocess.PIPE,
        }
        if os.name == "nt":
            process_kwargs["creationflags"] = (
                getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
        else:
            process_kwargs["start_new_session"] = True

        process = await asyncio.create_subprocess_exec(*argv, **process_kwargs)
        input_bytes = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        tasks: list[asyncio.Task[Any]] = []
        try:
            if process.stdin is not None:
                process.stdin.write(input_bytes)
                await process.stdin.drain()
                process.stdin.close()
                if hasattr(process.stdin, "wait_closed"):
                    try:
                        await process.stdin.wait_closed()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
            tasks = [
                asyncio.create_task(process.wait()),
                asyncio.create_task(
                    self._read_bounded(process.stdout, spec.max_output_bytes)
                ),
                asyncio.create_task(
                    self._read_bounded(process.stderr, spec.max_output_bytes)
                ),
            ]
            exit_code, stdout, _stderr = await asyncio.wait_for(
                asyncio.gather(*tasks),
                timeout=spec.timeout_ms / 1000,
            )
        except asyncio.TimeoutError:
            await self._terminate_process_tree(process)
            raise
        except Exception:
            await self._terminate_process_tree(process)
            raise
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        if exit_code != 0:
            raise RuntimeError(f"hook command exited with status {exit_code}")
        return stdout

    @staticmethod
    async def _read_bounded(
        stream: asyncio.StreamReader | None,
        limit: int,
    ) -> bytes:
        if stream is None:
            return b""
        output = bytearray()
        while True:
            chunk = await stream.read(min(4096, limit + 1))
            if not chunk:
                return bytes(output)
            output.extend(chunk)
            if len(output) > limit:
                raise _HookOutputLimitError("hook output exceeds the configured limit")

    @staticmethod
    async def _terminate_process_tree(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        if os.name == "nt" and process.pid:
            try:
                killer = await asyncio.create_subprocess_exec(
                    "taskkill",
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                await killer.wait()
            except (FileNotFoundError, OSError):
                process.kill()
        elif process.pid:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError, OSError):
                process.kill()
        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except (asyncio.TimeoutError, ProcessLookupError):
            pass

    def _command_cwd(self, raw: Optional[str]) -> Path:
        if raw is None:
            return self.working_directory
        path = Path(raw)
        target = path.resolve() if path.is_absolute() else (self.working_directory / path).resolve()
        try:
            target.relative_to(self.working_directory)
        except ValueError as error:
            raise PermissionError("hook working directory escapes the runtime root") from error
        if not target.is_dir():
            raise ValueError("hook working directory does not exist")
        return target

    @staticmethod
    def _command_environment(entries: tuple[tuple[str, str], ...]) -> dict[str, str]:
        inherited = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in _SAFE_INHERITED_ENV or key.upper().startswith("LC_")
        }
        inherited.update(entries)
        return inherited

    @staticmethod
    def _command_argv(command: tuple[str, ...] | str) -> tuple[str, ...]:
        if isinstance(command, tuple):
            return tuple(sys.executable if item == "{python}" else item for item in command)
        if os.name == "nt":
            return (os.environ.get("COMSPEC", "cmd.exe"), "/D", "/S", "/C", command)
        return (os.environ.get("SHELL", "/bin/sh"), "-lc", command)

    @classmethod
    def _parse_command_output(cls, event: str, output: bytes) -> _ParsedOutput:
        if not output.strip():
            return _ParsedOutput()
        try:
            value = json.loads(output.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("hook command output must be one JSON object") from error
        return cls._parse_output(event, value)

    @classmethod
    def _parse_output(cls, event: str, value: Any) -> _ParsedOutput:
        if value is None:
            return _ParsedOutput()
        if value is False:
            return _ParsedOutput(blocked=event == "PreToolUse", reason="hook_denied")
        if not isinstance(value, Mapping):
            return _ParsedOutput()

        continue_execution = cls._optional_bool(value, "continue", True)
        suppress_output = cls._optional_bool(value, "suppressOutput", False)
        system_message = cls._optional_text(value.get("systemMessage"), "systemMessage")
        stop_reason = cls._optional_text(value.get("stopReason"), "stopReason")
        specific = value.get("hookSpecificOutput", {})
        if specific is None:
            specific = {}
        if not isinstance(specific, Mapping):
            raise ValueError("hookSpecificOutput must be an object")
        declared_event = specific.get("hookEventName")
        if declared_event is not None and declared_event != event:
            raise ValueError("hook output event does not match the dispatched event")
        additional_context = cls._optional_text(
            specific.get("additionalContext"), "additionalContext"
        )

        blocked = False
        reason = cls._optional_text(value.get("reason"), "reason")
        decision = value.get("decision")
        permission_decision = specific.get("permissionDecision")
        permission_decision_reason = cls._optional_text(
            specific.get("permissionDecisionReason"), "permissionDecisionReason"
        )
        updated_input = specific.get("updatedInput")
        permission_behavior: Optional[str] = None

        if event == "PreToolUse":
            if not continue_execution or stop_reason is not None or suppress_output:
                raise ValueError("PreToolUse returned unsupported universal control fields")
            if decision not in (None, "block"):
                raise ValueError("PreToolUse decision is unsupported")
            if permission_decision not in (None, "allow", "deny"):
                raise ValueError("PreToolUse permission decision is unsupported")
            if decision == "block":
                if not reason:
                    raise ValueError("PreToolUse block decision requires a reason")
                blocked = True
            if permission_decision == "deny":
                if not permission_decision_reason:
                    raise ValueError("PreToolUse deny decision requires a reason")
                blocked = True
                reason = permission_decision_reason
            if updated_input is not None:
                if permission_decision != "allow" or not isinstance(updated_input, dict):
                    raise ValueError("PreToolUse updatedInput requires permissionDecision allow")
            elif permission_decision == "allow":
                raise ValueError("PreToolUse allow decision requires updatedInput")
        elif event == "PermissionRequest":
            if not continue_execution or stop_reason is not None or suppress_output:
                raise ValueError("PermissionRequest returned unsupported universal control fields")
            if any(key in specific for key in ("updatedInput", "updatedPermissions")):
                raise ValueError("PermissionRequest cannot update input or permissions")
            decision_value = specific.get("decision")
            if decision_value is not None:
                if not isinstance(decision_value, Mapping):
                    raise ValueError("PermissionRequest decision must be an object")
                if decision_value.get("updatedInput") is not None or decision_value.get(
                    "updatedPermissions"
                ) is not None or decision_value.get("interrupt") is True:
                    raise ValueError("PermissionRequest decision cannot expand permissions")
                permission_behavior = decision_value.get("behavior")
                if permission_behavior not in {"allow", "deny"}:
                    raise ValueError("PermissionRequest behavior is invalid")
                permission_decision = permission_behavior
                permission_decision_reason = cls._optional_text(
                    decision_value.get("message"), "permission message"
                )
                blocked = permission_behavior == "deny"
                reason = permission_decision_reason or (
                    "PermissionRequest Hook denied approval" if blocked else None
                )
        elif event == "PostToolUse":
            if suppress_output:
                raise ValueError("PostToolUse suppressOutput is unsupported")
            if specific.get("updatedMCPToolOutput") is not None:
                raise ValueError("PostToolUse cannot replace MCP tool output")
            if decision not in (None, "block"):
                raise ValueError("PostToolUse decision is unsupported")
            if decision == "block":
                if not reason:
                    raise ValueError("PostToolUse block decision requires a reason")
                blocked = True
        elif event in {"UserPromptSubmit", "Stop", "SubagentStop"}:
            if decision not in (None, "block"):
                raise ValueError("hook block decision is unsupported")
            if decision == "block":
                if not reason:
                    raise ValueError("hook block decision requires a reason")
                blocked = True

        return _ParsedOutput(
            blocked=blocked,
            reason=reason,
            additional_context=additional_context,
            system_message=system_message,
            continue_execution=continue_execution,
            stop_reason=stop_reason,
            suppress_output=suppress_output,
            permission_decision=(
                str(permission_decision) if permission_decision is not None else None
            ),
            permission_decision_reason=permission_decision_reason,
            updated_input=dict(updated_input) if isinstance(updated_input, dict) else None,
            permission_behavior=permission_behavior,
        )

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
    def _bounded_number(value: Any, minimum: float, maximum: float) -> float:
        if isinstance(value, bool):
            raise ValueError("hook numeric limit is invalid")
        try:
            number = float(value)
        except (TypeError, ValueError) as error:
            raise ValueError("hook numeric limit is invalid") from error
        if not minimum <= number <= maximum:
            raise ValueError("hook numeric limit is outside the allowed range")
        return number

    @staticmethod
    def _string_set(value: Any) -> frozenset[str]:
        if value is None:
            return frozenset()
        if not isinstance(value, list) or any(
            not isinstance(item, str) or not item.strip() for item in value
        ):
            raise ValueError("hook string list is invalid")
        return frozenset(item.strip() for item in value)

    @staticmethod
    def _command_value(value: Any, *, required: bool) -> tuple[str, ...] | str | None:
        if value is None:
            if required:
                raise ValueError("hook command is required")
            return None
        if isinstance(value, str):
            if not value.strip() or len(value.encode("utf-8")) > _MAX_COMMAND_PART_BYTES:
                raise ValueError("hook command is invalid")
            return value.strip()
        if not isinstance(value, list) or not 1 <= len(value) <= _MAX_COMMAND_PARTS:
            raise ValueError("hook command must be a non-empty string list")
        parts: list[str] = []
        for item in value:
            if (
                not isinstance(item, str)
                or not item
                or "\x00" in item
                or len(item.encode("utf-8")) > _MAX_COMMAND_PART_BYTES
            ):
                raise ValueError("hook command argument is invalid")
            parts.append(item)
        return tuple(parts)

    @staticmethod
    def _environment_value(value: Any) -> tuple[tuple[str, str], ...]:
        if not isinstance(value, Mapping) or len(value) > _MAX_ENV_ENTRIES:
            raise ValueError("hook environment is invalid")
        result: list[tuple[str, str]] = []
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", key)
                or any(part in key.lower() for part in _SENSITIVE_KEY_PARTS)
                or not isinstance(item, str)
                or "\x00" in item
                or len(item.encode("utf-8")) > 4096
            ):
                raise ValueError("hook environment entry is invalid")
            result.append((key, item))
        return tuple(result)

    @staticmethod
    def _encode_output(value: Any) -> bytes:
        try:
            return json.dumps(value, ensure_ascii=True, default=str).encode("utf-8")
        except (TypeError, ValueError):
            return repr(value).encode("utf-8", errors="replace")

    @staticmethod
    def _optional_bool(value: Mapping[str, Any], key: str, default: bool) -> bool:
        item = value.get(key, default)
        if not isinstance(item, bool):
            raise ValueError(f"hook output {key} must be boolean")
        return item

    @staticmethod
    def _optional_text(value: Any, label: str) -> Optional[str]:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"hook output {label} must be text")
        trimmed = value.strip()
        if not trimmed or len(trimmed.encode("utf-8")) > 4096:
            raise ValueError(f"hook output {label} is invalid")
        return trimmed

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
        *,
        correlation_id: Optional[str] = None,
    ) -> None:
        if self.audit_sink is None:
            return
        record: dict[str, Any] = {
            "component": "agent_hook",
            "operation": "run_event",
            "event": event,
            "resource_id": binding.resource_id,
            "resource_version": binding.version,
            "resource_sha256": binding.content_sha256,
            "outcome": outcome,
            "correlation_id": correlation_id,
            "session": context.redacted(),
        }
        if reason:
            record["reason"] = reason[:128]
        try:
            self.audit_sink(record)
        except Exception:
            pass
