from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


# ``session`` remains readable for old registries, but new installs and Agent
# bindings use the five explicit runtime resource kinds below.
RESOURCE_TYPES = frozenset({"skill", "prompt", "session", "memory", "mcp", "hook"})
INSTALLABLE_RESOURCE_TYPES = frozenset({"skill", "prompt", "memory", "mcp", "hook"})
# Resource permissions describe what a binding may request.  ``process.execute``
# is intentionally separate from workflow access: command Hooks need both the
# Agent capability and this binding permission before a process can start.
RESOURCE_PERMISSIONS = frozenset(
    {"workflow.read", "workflow.write", "process.execute"}
)


@dataclass(frozen=True)
class ResourceFile:
    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceManifest:
    resource_id: str
    type: str
    version: str
    source: str
    entry: str
    permissions: tuple[str, ...]
    files: tuple[ResourceFile, ...]
    content_sha256: str
    source_key: str | None = None
    source_metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["permissions"] = list(self.permissions)
        value["files"] = [file.to_dict() for file in self.files]
        if self.source_key is None:
            value.pop("source_key", None)
        if self.source_metadata is None:
            value.pop("source_metadata", None)
        return value
