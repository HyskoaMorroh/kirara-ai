"""Shared runtime primitives for channel-independent AI agents."""

from .core import (
    AgentDefinition,
    AgentRegistry,
    ChannelContext,
    ResourceBinding,
    ResourceSnapshot,
    SessionPolicy,
    SUPPORTED_CHANNEL_TYPES,
    effective_mcp_allowlist,
    resolve_mcp_tool_allowlist,
)
from .executor import AgentRuntimeExecutor, RuntimeResult, RuntimeStatus
from .hooks import AgentHookRuntime, HookHandler, HookOutcome, HOOK_EVENTS
from .session_store import SessionStore

__all__ = [
    "AgentDefinition",
    "AgentRegistry",
    "AgentRuntimeExecutor",
    "AgentHookRuntime",
    "HookHandler",
    "HookOutcome",
    "HOOK_EVENTS",
    "ChannelContext",
    "ResourceBinding",
    "ResourceSnapshot",
    "RuntimeResult",
    "RuntimeStatus",
    "SessionStore",
    "SessionPolicy",
    "SUPPORTED_CHANNEL_TYPES",
    "effective_mcp_allowlist",
    "resolve_mcp_tool_allowlist",
]
