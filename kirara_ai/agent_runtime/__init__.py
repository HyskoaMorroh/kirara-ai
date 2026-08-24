"""Shared runtime primitives for channel-independent AI agents."""

from .core import (
    AgentDefinition,
    AgentRegistry,
    ChannelContext,
    ResourceBinding,
    ResourceSnapshot,
    SessionPolicy,
    effective_mcp_allowlist,
)
from .executor import AgentRuntimeExecutor, RuntimeResult, RuntimeStatus

__all__ = [
    "AgentDefinition",
    "AgentRegistry",
    "AgentRuntimeExecutor",
    "ChannelContext",
    "ResourceBinding",
    "ResourceSnapshot",
    "RuntimeResult",
    "RuntimeStatus",
    "SessionPolicy",
    "effective_mcp_allowlist",
]
