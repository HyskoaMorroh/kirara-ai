"""Small, dependency-free policy objects used by every inbound channel.

The objects in this module deliberately contain references and policy data, not
provider credentials.  They are immutable once created so a running request
cannot observe a resource or permission update halfway through a model turn.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import threading
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any, Iterable, Mapping, Optional, Sequence
from urllib.parse import quote

from kirara_ai.im.message import IMMessage
from kirara_ai.im.sender import ChatType
from kirara_ai.web.auth.principal import get_runtime_principal


_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z0-9._:@/-]+$")
_RESOURCE_TYPES = frozenset({"prompt", "skill", "memory", "mcp", "hook"})
SUPPORTED_CHANNEL_TYPES = frozenset(
    {"webui", "onebot", "qqbot", "telegram", "wecom"}
)
_AGENT_REGISTRY_FORMAT_VERSION = 1


def _clean_identifier(value: Any, fallback: str) -> str:
    """Return a stable internal identifier without allowing path ambiguity."""

    if value is None:
        return fallback
    text = str(value).strip()
    if not text:
        return fallback
    if not _IDENTIFIER_PATTERN.fullmatch(text):
        # Keep common adapter IDs readable while avoiding control characters and
        # separators that could make two scope keys collide.
        text = re.sub(r"[^A-Za-z0-9._:@/-]+", "_", text)
    return text[:256] or fallback


def _as_frozenset(values: Optional[Iterable[str]]) -> frozenset[str]:
    if values is None:
        return frozenset()
    return frozenset(str(value).strip() for value in values if str(value).strip())


def _as_tuple(values: Optional[Iterable[str]]) -> tuple[str, ...]:
    if values is None:
        return ()
    return tuple(str(value).strip() for value in values if str(value).strip())


def principal_can_control_agent(owner_subject: Optional[str]) -> bool:
    """Return whether the current creator owns the explicitly bound Agent."""

    if not isinstance(owner_subject, str) or not owner_subject:
        return False
    principal = get_runtime_principal()
    return bool(
        principal is not None
        and principal.is_creator
        and secrets.compare_digest(principal.subject, owner_subject)
    )


class _ImmutableTuple(tuple):
    """Tuple that fails at in-place concatenation instead of leaking a setter error."""

    def __iadd__(self, other: object):
        raise TypeError("immutable runtime snapshot")

    def __imul__(self, other: object):
        raise TypeError("immutable runtime snapshot")


@dataclass(frozen=True)
class ChannelContext:
    """Canonical identity of a message's channel, account and conversation."""

    channel_type: str
    adapter_instance: str
    account_scope: str
    conversation_scope: str
    sender_scope: str

    def __post_init__(self) -> None:
        for name in (
            "channel_type",
            "adapter_instance",
            "account_scope",
            "conversation_scope",
            "sender_scope",
        ):
            value = _clean_identifier(getattr(self, name), "unknown")
            object.__setattr__(self, name, value)

    @classmethod
    def from_message(cls, adapter: Any, message: IMMessage) -> "ChannelContext":
        """Build a context from an adapter and the normalized IM message."""

        channel_type = getattr(adapter, "channel_type", None)
        if channel_type is None:
            channel_type = getattr(adapter, "adapter_type", None)
        if channel_type is None:
            channel_type = adapter.__class__.__name__.removesuffix("Adapter")
        channel_type = _clean_identifier(channel_type, "unknown").lower()

        adapter_instance = getattr(adapter, "adapter_instance", None)
        if adapter_instance is None:
            adapter_instance = getattr(adapter, "name", None)
        adapter_instance = _clean_identifier(adapter_instance, "default")

        sender = message.sender
        raw_metadata = sender.raw_metadata or {}
        event_account = None
        if channel_type == "onebot":
            # OneBot can multiplex several bot accounts through one reverse
            # WebSocket.  The event self_id is therefore more specific than
            # the adapter's configured default account.
            event_account = raw_metadata.get("onebot_self_id")

        account_scope = event_account
        if account_scope is None:
            account_scope = getattr(adapter, "account_scope", None)
        if account_scope is None:
            account_scope = getattr(adapter, "account_id", None)
        if account_scope is None:
            config = getattr(adapter, "config", None)
            for field_name in (
                "account_scope",
                "account_id",
                "app_id",
                "corp_id",
                "bot_id",
                "agent_id",
            ):
                candidate = getattr(config, field_name, None)
                if candidate is not None and str(candidate).strip():
                    account_scope = candidate
                    break
        if account_scope is None:
            # IMManager assigns adapter_instance to the persisted adapter name.
            # Using it as the last fallback keeps two configured instances
            # separate even when a provider does not expose a public bot ID.
            account_scope = adapter_instance
        account_scope = _clean_identifier(account_scope, "default")

        sender_scope = _clean_identifier(sender.user_id, "unknown-sender")
        if sender.chat_type == ChatType.GROUP:
            conversation_scope = f"group:{_clean_identifier(sender.group_id, 'unknown-group')}"
        else:
            conversation_scope = f"c2c:{sender_scope}"

        return cls(
            channel_type=channel_type,
            adapter_instance=adapter_instance,
            account_scope=account_scope,
            conversation_scope=conversation_scope,
            sender_scope=sender_scope,
        )

    @property
    def session_key(self) -> str:
        return "/".join(
            quote(component, safe="._:@-")
            for component in self._session_components
        )

    @property
    def legacy_session_key(self) -> str:
        """Return the pre-encoding key used by existing persisted bindings."""

        return "/".join(self._session_components)

    @property
    def _session_components(self) -> tuple[str, str, str, str, str]:
        return (
            self.channel_type,
            self.adapter_instance,
            self.account_scope,
            self.conversation_scope,
            self.sender_scope,
        )

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

    def redacted(self) -> dict[str, str]:
        """Return an audit/log representation without raw external identities."""

        return {
            "channel_type": self.channel_type,
            "adapter_instance": self._digest(self.adapter_instance),
            "account_scope": self._digest(self.account_scope),
            "conversation_scope": self._digest(self.conversation_scope),
            "sender_scope": self._digest(self.sender_scope),
        }


@dataclass(frozen=True)
class ResourceBinding:
    """A versioned runtime resource binding included in one Agent snapshot."""

    resource_id: str
    resource_type: str
    version: str
    content_sha256: str
    enabled: bool = True
    permissions: tuple[str, ...] = ()
    source: str = "local"
    version_policy: str = "fixed"

    def __post_init__(self) -> None:
        resource_type = str(self.resource_type).strip().lower()
        if resource_type not in _RESOURCE_TYPES:
            raise ValueError(f"Unsupported runtime resource type: {resource_type}")
        resource_id = _clean_identifier(self.resource_id, "unknown-resource")
        version = str(self.version).strip() or "unversioned"
        content_sha256 = str(self.content_sha256).strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise ValueError("Resource content hash must be a SHA-256 hexadecimal digest")
        version_policy = str(self.version_policy).strip().lower()
        if version_policy not in {"fixed", "current"}:
            raise ValueError("Resource version policy must be fixed or current")
        object.__setattr__(self, "resource_id", resource_id)
        object.__setattr__(self, "resource_type", resource_type)
        object.__setattr__(self, "version", version)
        object.__setattr__(self, "content_sha256", content_sha256)
        object.__setattr__(self, "permissions", _as_tuple(self.permissions))
        object.__setattr__(self, "source", _clean_identifier(self.source, "local"))
        object.__setattr__(self, "version_policy", version_policy)


@dataclass(frozen=True)
class ResourceSnapshot:
    """Immutable resource/model view captured at the beginning of one turn."""

    resources: tuple[ResourceBinding, ...]
    model_id: Optional[str] = None
    agent_id: Optional[str] = None
    model_priority: tuple[str, ...] = ()
    provider_allowlist: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    content_sha256: str = ""

    def __post_init__(self) -> None:
        resources = _ImmutableTuple(self.resources)
        object.__setattr__(self, "resources", resources)
        object.__setattr__(self, "model_priority", _ImmutableTuple(_as_tuple(self.model_priority)))
        object.__setattr__(self, "provider_allowlist", _ImmutableTuple(_as_tuple(self.provider_allowlist)))
        if self.created_at.tzinfo is None:
            object.__setattr__(
                self, "created_at", self.created_at.replace(tzinfo=timezone.utc)
            )
        if not self.content_sha256:
            object.__setattr__(self, "content_sha256", self._calculate_hash())
        elif not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256.lower()):
            raise ValueError("Snapshot content hash must be a SHA-256 hexadecimal digest")

    @classmethod
    def create(
        cls,
        resources: Sequence[ResourceBinding],
        model_id: Optional[str] = None,
        *,
        agent_id: Optional[str] = None,
        model_priority: Optional[Iterable[str]] = None,
        provider_allowlist: Optional[Iterable[str]] = None,
        created_at: Optional[datetime] = None,
    ) -> "ResourceSnapshot":
        return cls(
            resources=tuple(resources),
            model_id=model_id,
            agent_id=agent_id,
            model_priority=_as_tuple(model_priority),
            provider_allowlist=_as_tuple(provider_allowlist),
            created_at=created_at or datetime.now(timezone.utc),
        )

    def _calculate_hash(self) -> str:
        payload = {
            "agent_id": self.agent_id,
            "model_id": self.model_id,
            "model_priority": list(self.model_priority),
            "provider_allowlist": list(self.provider_allowlist),
            "resources": [
                {
                    "resource_id": item.resource_id,
                    "resource_type": item.resource_type,
                    "version": item.version,
                    "version_policy": item.version_policy,
                    "content_sha256": item.content_sha256,
                    "enabled": item.enabled,
                    "permissions": list(item.permissions),
                    "source": item.source,
                }
                for item in self.resources
            ],
        }
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "model_id": self.model_id,
            "model_priority": list(self.model_priority),
            "provider_allowlist": list(self.provider_allowlist),
            "created_at": self.created_at.isoformat(),
            "content_sha256": self.content_sha256,
            "resources": [
                {
                    "resource_id": item.resource_id,
                    "resource_type": item.resource_type,
                    "version": item.version,
                    "version_policy": item.version_policy,
                    "content_sha256": item.content_sha256,
                    "enabled": item.enabled,
                    "permissions": list(item.permissions),
                    "source": item.source,
                }
                for item in self.resources
            ],
        }


def effective_mcp_allowlist(
    *,
    agent_allowlist: Iterable[str],
    session_allowlist: Optional[Iterable[str]] = None,
    workflow_allowlist: Optional[Iterable[str]] = None,
    connected_tools: Optional[Iterable[str]] = None,
) -> frozenset[str]:
    """Calculate executable MCP tools using only progressively narrower sets."""

    agent = _as_frozenset(agent_allowlist)
    effective = set(agent)
    for label, values in (
        ("session", session_allowlist),
        ("workflow", workflow_allowlist),
    ):
        if values is None:
            continue
        candidate = _as_frozenset(values)
        if not candidate.issubset(agent):
            extra = sorted(candidate - agent)
            raise ValueError(f"{label} MCP allowlist cannot expand Agent permissions: {extra}")
        effective.intersection_update(candidate)
    if connected_tools is not None:
        effective.intersection_update(_as_frozenset(connected_tools))
    return frozenset(effective)


def resolve_mcp_tool_allowlist(
    *,
    agent_allowlist: Iterable[str],
    tool_entries: Mapping[str, Any],
    agent_mcp_server_ids: Optional[Iterable[str]] = None,
    session_allowlist: Optional[Iterable[str]] = None,
    workflow_allowlist: Optional[Iterable[str]] = None,
) -> frozenset[str]:
    """Resolve stable MCP policy names to the current tool-cache names.

    Persisted policies use ``server_id.original_name`` so reconnect order and
    cache collision aliases cannot change their meaning. Legacy original names
    remain valid only when they identify exactly one tool in the Agent's bound
    server set. Session and workflow policies are resolved through the same
    identities before enforcing that they only narrow Agent permissions.
    """

    bound_servers = (
        _as_frozenset(agent_mcp_server_ids)
        if agent_mcp_server_ids is not None
        else None
    )
    identities: dict[str, tuple[str, str, str]] = {}
    for cache_name, entry in tool_entries.items():
        server_id = str(getattr(entry, "server_id", "")).strip()
        if not server_id or (
            bound_servers is not None and server_id not in bound_servers
        ):
            continue
        info = getattr(entry, "tool_info", entry)
        original_name = str(
            getattr(entry, "original_name", "")
            or getattr(info, "name", "")
            or cache_name
        ).strip()
        if not original_name:
            continue
        display_name = str(cache_name).strip()
        identities[display_name] = (
            server_id,
            original_name,
            f"{server_id}.{original_name}",
        )

    canonical_index: dict[str, set[str]] = {}
    original_index: dict[str, set[str]] = {}
    for cache_name, (_server_id, original_name, canonical_name) in identities.items():
        canonical_index.setdefault(canonical_name, set()).add(cache_name)
        original_index.setdefault(original_name, set()).add(cache_name)

    def resolve(values: Iterable[str]) -> frozenset[str]:
        resolved: set[str] = set()
        for token in _as_frozenset(values):
            # Stable server-qualified identities win over compatibility aliases.
            # A cache/display name is the next-most-specific spelling; an
            # unqualified original name is accepted only when it is unique.
            canonical_matches = canonical_index.get(token, set())
            if len(canonical_matches) == 1:
                matches = canonical_matches
            else:
                original_matches = original_index.get(token, set())
                cache_matches = {token} if token in identities else set()
                compatibility_matches = original_matches | cache_matches
                matches = (
                    compatibility_matches
                    if len(compatibility_matches) == 1
                    else set()
                )
            if len(matches) == 1:
                resolved.update(matches)
        return frozenset(resolved)

    raw_agent = _as_frozenset(agent_allowlist)
    agent = resolve(raw_agent)
    effective = set(agent)
    for label, values in (
        ("session", session_allowlist),
        ("workflow", workflow_allowlist),
    ):
        if values is None:
            continue
        raw_candidate = _as_frozenset(values)
        candidate = resolve(raw_candidate)
        unresolved_expansions = raw_candidate - raw_agent
        if not candidate.issubset(agent):
            unresolved_expansions.update(candidate - agent)
        else:
            unresolved_expansions = {
                token
                for token in unresolved_expansions
                if not resolve((token,)).issubset(agent)
                or not resolve((token,))
            }
        if unresolved_expansions:
            raise ValueError(
                f"{label} MCP allowlist cannot expand Agent permissions: "
                f"{sorted(unresolved_expansions)}"
            )
        effective.intersection_update(candidate)
    return frozenset(effective)


@dataclass(frozen=True)
class SessionPolicy:
    """Permissions narrowed for one session or workflow invocation."""

    agent_mcp_allowlist: frozenset[str]
    mcp_allowlist: frozenset[str]
    max_tool_iterations: int = 8

    @classmethod
    def from_allowlists(
        cls,
        *,
        agent_allowlist: Iterable[str],
        session_allowlist: Optional[Iterable[str]] = None,
        workflow_allowlist: Optional[Iterable[str]] = None,
        connected_tools: Optional[Iterable[str]] = None,
        max_tool_iterations: int = 8,
    ) -> "SessionPolicy":
        if max_tool_iterations < 0:
            raise ValueError("max_tool_iterations must be non-negative")
        agent = _as_frozenset(agent_allowlist)
        return cls(
            agent_mcp_allowlist=agent,
            mcp_allowlist=effective_mcp_allowlist(
                agent_allowlist=agent,
                session_allowlist=session_allowlist,
                workflow_allowlist=workflow_allowlist,
                connected_tools=connected_tools,
            ),
            max_tool_iterations=max_tool_iterations,
        )


@dataclass(frozen=True)
class AgentDefinition:
    """An enabled model/resource/tool policy selected for a channel session."""

    agent_id: str
    owner_subject: Optional[str] = None
    display_name: Optional[str] = None
    enabled: bool = True
    workflow_id: Optional[str] = None
    model_priority: tuple[str, ...] = ()
    provider_allowlist: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    prompt_bindings: tuple[ResourceBinding, ...] = ()
    skill_bindings: tuple[ResourceBinding, ...] = ()
    memory_bindings: tuple[ResourceBinding, ...] = ()
    mcp_bindings: tuple[ResourceBinding, ...] = ()
    hook_bindings: tuple[ResourceBinding, ...] = ()
    mcp_allowlist: frozenset[str] = frozenset()
    allow_tools: bool = True
    max_tool_iterations: int = 8

    def __post_init__(self) -> None:
        agent_id = _clean_identifier(self.agent_id, "agent")
        owner_subject = self.owner_subject
        if owner_subject is not None:
            if not isinstance(owner_subject, str):
                raise TypeError("Agent owner subject must be text")
            owner_subject = owner_subject.strip()
            if not owner_subject or len(owner_subject) > 256:
                raise ValueError("Agent owner subject is invalid")
        if not self.model_priority:
            raise ValueError("Agent must define at least one model candidate")
        if self.max_tool_iterations < 0:
            raise ValueError("max_tool_iterations must be non-negative")
        object.__setattr__(self, "agent_id", agent_id)
        object.__setattr__(self, "owner_subject", owner_subject)
        object.__setattr__(self, "model_priority", _as_tuple(self.model_priority))
        object.__setattr__(self, "provider_allowlist", _as_frozenset(self.provider_allowlist))
        object.__setattr__(self, "capabilities", _as_frozenset(self.capabilities))
        object.__setattr__(self, "mcp_allowlist", _as_frozenset(self.mcp_allowlist))
        for field_name in (
            "prompt_bindings",
            "skill_bindings",
            "memory_bindings",
            "mcp_bindings",
            "hook_bindings",
        ):
            bindings = tuple(getattr(self, field_name))
            expected_type = field_name.removesuffix("_bindings")
            if any(item.resource_type != expected_type for item in bindings):
                raise ValueError(f"{field_name} contains a resource of the wrong type")
            object.__setattr__(self, field_name, _ImmutableTuple(bindings))

    @property
    def resource_bindings(self) -> tuple[ResourceBinding, ...]:
        return (
            self.prompt_bindings
            + self.skill_bindings
            + self.memory_bindings
            + self.mcp_bindings
            + self.hook_bindings
        )

    def snapshot(self, model_id: Optional[str] = None) -> ResourceSnapshot:
        return ResourceSnapshot.create(
            self.resource_bindings,
            model_id=model_id or self.model_priority[0],
            agent_id=self.agent_id,
            model_priority=self.model_priority,
            provider_allowlist=sorted(self.provider_allowlist),
        )


class AgentRegistry:
    """Agent definitions and channel bindings with optional server persistence."""

    def __init__(self, data_path: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._registry_path: Optional[Path] = None
        if data_path is not None:
            self._registry_path = Path(data_path).resolve() / "agents" / "registry.json"
        self._agents: dict[str, AgentDefinition] = {}
        self._channel_bindings: dict[str, str] = {}
        self._account_bindings: dict[tuple[str, str, str], str] = {}
        self._session_bindings: dict[str, str] = {}
        self._default_agent_id: Optional[str] = None
        if self._registry_path is not None and self._registry_path.exists():
            self._apply_state(self._load_state())

    @property
    def agents(self) -> Mapping[str, AgentDefinition]:
        return MappingProxyType(self._agents)

    @property
    def default_agent_id(self) -> Optional[str]:
        return self._default_agent_id

    def get(self, agent_id: str) -> AgentDefinition:
        with self._lock:
            agent = self._agents.get(agent_id)
            if agent is None:
                raise LookupError(f"Unknown Agent: {agent_id}")
            return agent

    def list(self) -> list[AgentDefinition]:
        with self._lock:
            return [self._agents[agent_id] for agent_id in sorted(self._agents)]

    def set_enabled(self, agent_id: str, enabled: bool) -> AgentDefinition:
        with self._lock:
            agent = self.get(agent_id)
            if not enabled and self._default_agent_id == agent_id:
                raise ValueError("The global default Agent cannot be disabled")
            updated = replace(agent, enabled=enabled)
            next_state = self._capture_state()
            next_state["agents"][agent_id] = updated
            self._commit(next_state)
            return updated

    def to_dict(self) -> dict[str, Any]:
        """Return the persisted public state without provider credentials."""

        with self._lock:
            return self._serialize_state(self._capture_state())

    def register(self, agent: AgentDefinition) -> None:
        with self._lock:
            if agent.agent_id in self._agents:
                raise ValueError(f"Agent already registered: {agent.agent_id}")
            agent = self._bind_creation_owner(agent)
            next_state = self._capture_state()
            next_state["agents"][agent.agent_id] = agent
            if next_state["default_agent_id"] is None and agent.agent_id == "default":
                next_state["default_agent_id"] = agent.agent_id
            self._commit(next_state)

    def update(self, agent: AgentDefinition) -> None:
        with self._lock:
            if agent.agent_id not in self._agents:
                raise KeyError(agent.agent_id)
            agent = self._preserve_owner(self._agents[agent.agent_id], agent)
            next_state = self._capture_state()
            next_state["agents"][agent.agent_id] = agent
            self._commit(next_state)

    def remove(self, agent_id: str) -> None:
        with self._lock:
            if agent_id not in self._agents:
                return
            if self._default_agent_id == agent_id:
                raise ValueError("The global default Agent cannot be removed")
            if agent_id in self._channel_bindings.values():
                raise ValueError("An Agent with a channel binding cannot be removed")
            if agent_id in self._account_bindings.values():
                raise ValueError("An Agent with an account binding cannot be removed")
            if agent_id in self._session_bindings.values():
                raise ValueError("An Agent with a session binding cannot be removed")
            next_state = self._capture_state()
            next_state["agents"].pop(agent_id)
            self._commit(next_state)

    def set_default(self, agent_id: str) -> None:
        with self._lock:
            self._require_enabled(agent_id)
            next_state = self._capture_state()
            next_state["default_agent_id"] = agent_id
            self._commit(next_state)

    def configure(
        self,
        agent: AgentDefinition,
        *,
        channels: Sequence[str] = (),
        accounts: Sequence[tuple[str, str, str]] = (),
        sessions: Sequence[str] = (),
        is_default: bool = False,
        create: bool = False,
    ) -> None:
        """Atomically replace an Agent definition and all relations it owns."""

        with self._lock:
            exists = agent.agent_id in self._agents
            if create and exists:
                raise ValueError(f"Agent already registered: {agent.agent_id}")
            if not create and not exists:
                raise KeyError(agent.agent_id)
            agent = (
                self._bind_creation_owner(agent)
                if create
                else self._preserve_owner(self._agents[agent.agent_id], agent)
            )

            normalized_channels = tuple(
                self._normalize_channel_type(channel) for channel in channels
            )
            normalized_accounts = tuple(
                (
                    self._normalize_channel_type(channel),
                    _clean_identifier(adapter, "default"),
                    _clean_identifier(account, "default"),
                )
                for channel, adapter, account in accounts
            )
            normalized_sessions = tuple(str(session).strip() for session in sessions)
            if any(not session for session in normalized_sessions):
                raise ValueError("session relation cannot be empty")
            for session in normalized_sessions:
                channel = session.split("/", 1)[0]
                self._normalize_channel_type(channel)

            has_relations = bool(
                is_default
                or normalized_channels
                or normalized_accounts
                or normalized_sessions
            )
            if not agent.enabled and has_relations:
                raise ValueError(f"Agent is disabled: {agent.agent_id}")

            next_state = self._capture_state()
            next_state["agents"][agent.agent_id] = agent
            next_state["channel_bindings"] = {
                key: value
                for key, value in next_state["channel_bindings"].items()
                if value != agent.agent_id
            }
            next_state["account_bindings"] = {
                key: value
                for key, value in next_state["account_bindings"].items()
                if value != agent.agent_id
            }
            next_state["session_bindings"] = {
                key: value
                for key, value in next_state["session_bindings"].items()
                if value != agent.agent_id
            }
            for channel in normalized_channels:
                next_state["channel_bindings"][channel] = agent.agent_id
            for account in normalized_accounts:
                next_state["account_bindings"][account] = agent.agent_id
            for session in normalized_sessions:
                next_state["session_bindings"][session] = agent.agent_id

            if is_default:
                next_state["default_agent_id"] = agent.agent_id
            elif next_state["default_agent_id"] == agent.agent_id:
                next_state["default_agent_id"] = None
            self._commit(next_state)

    def bind_channel(self, channel_type: str, agent_id: str) -> None:
        with self._lock:
            self._require_enabled(agent_id)
            next_state = self._capture_state()
            next_state["channel_bindings"][self._normalize_channel_type(channel_type)] = agent_id
            self._commit(next_state)

    def unbind_channel(self, channel_type: str) -> None:
        with self._lock:
            key = _clean_identifier(channel_type, "unknown").lower()
            next_state = self._capture_state()
            next_state["channel_bindings"].pop(key, None)
            self._commit(next_state)

    def bind_account(
        self,
        channel_type: str,
        adapter_instance: str,
        account_scope: str,
        agent_id: str,
    ) -> None:
        with self._lock:
            self._require_enabled(agent_id)
            key = (
                self._normalize_channel_type(channel_type),
                _clean_identifier(adapter_instance, "default"),
                _clean_identifier(account_scope, "default"),
            )
            next_state = self._capture_state()
            next_state["account_bindings"][key] = agent_id
            self._commit(next_state)

    def unbind_account(self, channel_type: str, adapter_instance: str, account_scope: str) -> None:
        with self._lock:
            key = (
                _clean_identifier(channel_type, "unknown").lower(),
                _clean_identifier(adapter_instance, "default"),
                _clean_identifier(account_scope, "default"),
            )
            next_state = self._capture_state()
            next_state["account_bindings"].pop(key, None)
            self._commit(next_state)

    def bind_session(self, session_key: str | ChannelContext, agent_id: str) -> None:
        with self._lock:
            self._require_enabled(agent_id)
            key = session_key.session_key if isinstance(session_key, ChannelContext) else str(session_key)
            self._normalize_channel_type(key.split("/", 1)[0])
            next_state = self._capture_state()
            next_state["session_bindings"][key] = agent_id
            self._commit(next_state)

    def unbind_session(self, session_key: str | ChannelContext) -> None:
        with self._lock:
            next_state = self._capture_state()
            if isinstance(session_key, ChannelContext):
                next_state["session_bindings"].pop(session_key.session_key, None)
                next_state["session_bindings"].pop(session_key.legacy_session_key, None)
            else:
                next_state["session_bindings"].pop(str(session_key), None)
            self._commit(next_state)

    def update_resource_bindings(
        self,
        agent_id: str,
        *,
        prompt_bindings: Sequence[ResourceBinding],
        skill_bindings: Sequence[ResourceBinding],
        mcp_bindings: Sequence[ResourceBinding],
        memory_bindings: Sequence[ResourceBinding] | None = None,
        hook_bindings: Sequence[ResourceBinding] | None = None,
        mcp_allowlist: Iterable[str] | None = None,
    ) -> AgentDefinition:
        with self._lock:
            agent = self.get(agent_id)
            updated = replace(
                agent,
                prompt_bindings=tuple(prompt_bindings),
                skill_bindings=tuple(skill_bindings),
                memory_bindings=(agent.memory_bindings if memory_bindings is None else tuple(memory_bindings)),
                mcp_bindings=tuple(mcp_bindings),
                hook_bindings=(agent.hook_bindings if hook_bindings is None else tuple(hook_bindings)),
                mcp_allowlist=agent.mcp_allowlist if mcp_allowlist is None else frozenset(mcp_allowlist),
            )
            next_state = self._capture_state()
            next_state["agents"][agent_id] = updated
            self._commit(next_state)
            return updated

    def relation_summary(self, agent_id: str) -> dict[str, Any]:
        with self._lock:
            self.get(agent_id)
            channels = sorted(
                channel for channel, bound_agent in self._channel_bindings.items()
                if bound_agent == agent_id
            )
            accounts = [
                {
                    "channel_type": key[0],
                    "adapter_instance": key[1],
                    "account_scope": key[2],
                }
                for key, bound_agent in sorted(self._account_bindings.items())
                if bound_agent == agent_id
            ]
            sessions = sorted(
                key for key, bound_agent in self._session_bindings.items()
                if bound_agent == agent_id
            )
            return {
                "channels": channels,
                "accounts": accounts,
                "sessions": sessions,
                "is_default": self._default_agent_id == agent_id,
            }

    def resolve(
        self,
        context: ChannelContext,
        session_agent_id: Optional[str] = None,
    ) -> AgentDefinition:
        persisted_session_agent = self._session_bindings.get(context.session_key)
        if persisted_session_agent is None:
            persisted_session_agent = self._session_bindings.get(
                context.legacy_session_key
            )
        candidates = [
            session_agent_id,
            persisted_session_agent,
            self._account_bindings.get(
                (context.channel_type, context.adapter_instance, context.account_scope)
            ),
            self._channel_bindings.get(context.channel_type),
            self._default_agent_id,
        ]
        for candidate in candidates:
            if candidate is None:
                continue
            return self._require_enabled(candidate)
        raise LookupError("No global default Agent is configured")

    def _require_enabled(self, agent_id: str) -> AgentDefinition:
        agent = self._agents.get(agent_id)
        if agent is None:
            raise LookupError(f"Unknown Agent: {agent_id}")
        if not agent.enabled:
            raise ValueError(f"Agent is disabled: {agent_id}")
        return agent

    @staticmethod
    def _normalize_channel_type(channel_type: Any) -> str:
        channel = _clean_identifier(channel_type, "unknown").lower()
        if channel not in SUPPORTED_CHANNEL_TYPES:
            raise ValueError(f"Unsupported channel type: {channel}")
        return channel

    def _capture_state(self) -> dict[str, Any]:
        return {
            "agents": dict(self._agents),
            "channel_bindings": dict(self._channel_bindings),
            "account_bindings": dict(self._account_bindings),
            "session_bindings": dict(self._session_bindings),
            "default_agent_id": self._default_agent_id,
        }

    @staticmethod
    def _bind_creation_owner(agent: AgentDefinition) -> AgentDefinition:
        principal = get_runtime_principal()
        if agent.owner_subject is None:
            if principal is not None and principal.is_creator:
                return replace(agent, owner_subject=principal.subject)
            return agent
        if (
            principal is None
            or not principal.is_creator
            or not secrets.compare_digest(principal.subject, agent.owner_subject)
        ):
            raise PermissionError(
                "Agent owner can only be assigned by the matching creator"
            )
        return agent

    @staticmethod
    def _preserve_owner(
        current: AgentDefinition,
        replacement: AgentDefinition,
    ) -> AgentDefinition:
        if current.owner_subject is None:
            if replacement.owner_subject is not None:
                raise ValueError("Agent owner cannot be assigned after creation")
            return replacement
        if replacement.owner_subject is None:
            return replace(replacement, owner_subject=current.owner_subject)
        if not secrets.compare_digest(
            current.owner_subject,
            replacement.owner_subject,
        ):
            raise ValueError("Agent owner cannot be transferred")
        return replacement

    def _commit(self, state: dict[str, Any]) -> None:
        self._validate_state(state)
        if self._registry_path is not None:
            self._write_state(state)
        self._apply_state(state)

    def _apply_state(self, state: dict[str, Any]) -> None:
        self._agents = dict(state["agents"])
        self._channel_bindings = dict(state["channel_bindings"])
        self._account_bindings = dict(state["account_bindings"])
        self._session_bindings = dict(state["session_bindings"])
        self._default_agent_id = state["default_agent_id"]

    def _validate_state(self, state: dict[str, Any]) -> None:
        agent_ids = set(state["agents"])
        references = {
            *state["channel_bindings"].values(),
            *state["account_bindings"].values(),
            *state["session_bindings"].values(),
        }
        default_agent_id = state["default_agent_id"]
        if default_agent_id is not None:
            references.add(default_agent_id)
        missing = references - agent_ids
        if missing:
            raise ValueError(f"Agent registry references unknown Agents: {sorted(missing)}")

    @staticmethod
    def _binding_to_dict(binding: ResourceBinding) -> dict[str, Any]:
        return {
            "resource_id": binding.resource_id,
            "resource_type": binding.resource_type,
            "version": binding.version,
            "version_policy": binding.version_policy,
            "content_sha256": binding.content_sha256,
            "enabled": binding.enabled,
            "permissions": list(binding.permissions),
            "source": binding.source,
        }

    @classmethod
    def _agent_to_dict(cls, agent: AgentDefinition) -> dict[str, Any]:
        return {
            "agent_id": agent.agent_id,
            "owner_subject": agent.owner_subject,
            "display_name": agent.display_name,
            "enabled": agent.enabled,
            "workflow_id": agent.workflow_id,
            "model_priority": list(agent.model_priority),
            "provider_allowlist": sorted(agent.provider_allowlist),
            "capabilities": sorted(agent.capabilities),
            "prompt_bindings": [cls._binding_to_dict(item) for item in agent.prompt_bindings],
            "skill_bindings": [cls._binding_to_dict(item) for item in agent.skill_bindings],
            "memory_bindings": [cls._binding_to_dict(item) for item in agent.memory_bindings],
            "mcp_bindings": [cls._binding_to_dict(item) for item in agent.mcp_bindings],
            "hook_bindings": [cls._binding_to_dict(item) for item in agent.hook_bindings],
            "mcp_allowlist": sorted(agent.mcp_allowlist),
            "allow_tools": agent.allow_tools,
            "max_tool_iterations": agent.max_tool_iterations,
        }

    @classmethod
    def _serialize_state(cls, state: dict[str, Any]) -> dict[str, Any]:
        return {
            "format_version": _AGENT_REGISTRY_FORMAT_VERSION,
            "agents": [
                cls._agent_to_dict(state["agents"][agent_id])
                for agent_id in sorted(state["agents"])
            ],
            "default_agent_id": state["default_agent_id"],
            "channel_bindings": [
                {"channel_type": channel_type, "agent_id": agent_id}
                for channel_type, agent_id in sorted(state["channel_bindings"].items())
            ],
            "account_bindings": [
                {
                    "channel_type": key[0],
                    "adapter_instance": key[1],
                    "account_scope": key[2],
                    "agent_id": agent_id,
                }
                for key, agent_id in sorted(state["account_bindings"].items())
            ],
            "session_bindings": [
                {"session_key": session_key, "agent_id": agent_id}
                for session_key, agent_id in sorted(state["session_bindings"].items())
            ],
        }

    @staticmethod
    def _load_binding(payload: Any, expected_type: str) -> ResourceBinding:
        if not isinstance(payload, dict):
            raise ValueError("Agent resource binding is invalid")
        values = dict(payload)
        # Older registries represented only pinned versions.
        values.setdefault("version_policy", "fixed")
        binding = ResourceBinding(**values)
        if binding.resource_type != expected_type:
            raise ValueError("Agent resource binding has the wrong type")
        return binding

    @classmethod
    def _load_agent(cls, payload: Any) -> AgentDefinition:
        if not isinstance(payload, dict):
            raise ValueError("Agent definition is invalid")
        values = dict(payload)
        values.setdefault("owner_subject", None)
        for field_name, resource_type in (
            ("prompt_bindings", "prompt"),
            ("skill_bindings", "skill"),
            ("memory_bindings", "memory"),
            ("mcp_bindings", "mcp"),
            ("hook_bindings", "hook"),
        ):
            raw_bindings = values.get(field_name, [])
            if not isinstance(raw_bindings, list):
                raise ValueError("Agent resource bindings are invalid")
            values[field_name] = tuple(
                cls._load_binding(item, resource_type) for item in raw_bindings
            )
        return AgentDefinition(**values)

    def _load_state(self) -> dict[str, Any]:
        assert self._registry_path is not None
        try:
            payload = json.loads(self._registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("Agent registry cannot be read") from error
        if (
            not isinstance(payload, dict)
            or payload.get("format_version") != _AGENT_REGISTRY_FORMAT_VERSION
            or not isinstance(payload.get("agents"), list)
        ):
            raise ValueError("Agent registry format is invalid")

        agents = {
            agent.agent_id: agent
            for agent in (self._load_agent(item) for item in payload["agents"])
        }
        if len(agents) != len(payload["agents"]):
            raise ValueError("Agent registry contains duplicate Agent IDs")

        def require_list(name: str) -> list[Any]:
            value = payload.get(name, [])
            if not isinstance(value, list):
                raise ValueError(f"Agent registry {name} is invalid")
            return value

        try:
            channel_bindings = {
                _clean_identifier(item["channel_type"], "unknown").lower(): str(item["agent_id"])
                for item in require_list("channel_bindings")
            }
            account_bindings = {
                (
                    _clean_identifier(item["channel_type"], "unknown").lower(),
                    _clean_identifier(item["adapter_instance"], "default"),
                    _clean_identifier(item["account_scope"], "default"),
                ): str(item["agent_id"])
                for item in require_list("account_bindings")
            }
            session_bindings = {
                str(item["session_key"]): str(item["agent_id"])
                for item in require_list("session_bindings")
            }
        except (KeyError, TypeError) as error:
            raise ValueError("Agent registry binding is invalid") from error

        state = {
            "agents": agents,
            "channel_bindings": channel_bindings,
            "account_bindings": account_bindings,
            "session_bindings": session_bindings,
            "default_agent_id": payload.get("default_agent_id"),
        }
        self._validate_state(state)
        return state

    def _write_state(self, state: dict[str, Any]) -> None:
        assert self._registry_path is not None
        self._registry_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".registry-", suffix=".tmp", dir=self._registry_path.parent
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                json.dump(
                    self._serialize_state(state),
                    file,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self._registry_path)
        finally:
            temporary_path.unlink(missing_ok=True)
