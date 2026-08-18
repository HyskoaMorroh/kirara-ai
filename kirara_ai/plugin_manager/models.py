from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

LifecycleName = Literal[
    "startup_completed",
    "shutdown_requested",
    "workflow_before",
    "workflow_after",
    "workflow_error",
    "dispatch_preview",
    "model_catalog_refreshed",
    "mcp_operation",
]
CapabilityName = Literal[
    "lifecycle_hooks",
    "events",
    "file",
    "network",
    "process",
    "config_write",
    "config-write",
    "secret",
]

LIFECYCLE_NAMES = frozenset(
    {
        "startup_completed",
        "shutdown_requested",
        "workflow_before",
        "workflow_after",
        "workflow_error",
        "dispatch_preview",
        "model_catalog_refreshed",
        "mcp_operation",
    }
)
CAPABILITY_NAMES = frozenset(
    {"lifecycle_hooks", "events", "file", "network", "process", "config_write", "config-write", "secret"}
)


class ExtensionCapabilities(BaseModel):
    """Explicit permissions available to a controlled extension."""

    lifecycle_hooks: bool = False
    events: bool = False
    file: bool = False
    network: bool = False
    process: bool = False
    config_write: bool = Field(False, alias="config-write")
    secret: bool = False

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    def allows(self, capability: CapabilityName) -> bool:
        field_name = "config_write" if capability == "config-write" else capability
        return bool(getattr(self, field_name))


class LifecycleHook(BaseModel):
    name: LifecycleName
    capability: CapabilityName = "lifecycle_hooks"


class ExtensionManifest(BaseModel):
    name: str
    version: str
    capabilities: ExtensionCapabilities = Field(
        default_factory=ExtensionCapabilities.model_construct
    )
    hooks: List[LifecycleHook] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def declared_hooks_have_capabilities(self):
        for hook in self.hooks:
            if not self.capabilities.allows(hook.capability):
                raise ValueError(
                    f"hook {hook.name} requires capability {hook.capability}"
                )
        return self


class PluginInfo(BaseModel):
    """插件信息"""

    name: str
    package_name: Optional[str] = None  # 外部插件的包名
    description: str
    version: str
    author: str
    is_internal: bool  # 是否为内部插件
    is_enabled: bool  # 是否启用
    requires_restart: bool = False # 是否需要重启
    metadata: Optional[Dict[str, Any]] = None
    manifest: Optional[ExtensionManifest] = None
