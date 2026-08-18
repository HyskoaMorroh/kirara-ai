from collections import defaultdict
import subprocess
from pathlib import Path
from typing import Any, Callable, DefaultDict, List, Mapping, Optional, Sequence, Type

import requests

from kirara_ai.events.event_bus import EventBus
from kirara_ai.plugin_manager.models import (
    CAPABILITY_NAMES,
    CapabilityName,
    ExtensionManifest,
    LIFECYCLE_NAMES,
    LifecycleName,
)


class PluginEventBus:
    """Manifest-aware facade for host-provided extension operations.

    Capability checks cover only operations reached through this injected host
    facade. They cannot prevent in-process Python code from importing and using
    ``pathlib``, ``requests`` or ``subprocess`` directly.
    """

    def __init__(
        self,
        event_bus: EventBus,
        manifest: Optional[ExtensionManifest | dict] = None,
        audit_sink: Optional[Callable[[dict], None]] = None,
        secret_provider: Optional[Callable[[str], str]] = None,
    ):
        self._event_bus = event_bus
        self._registered_listeners: List[Callable] = []  # 记录注册过的函数
        self.manifest = (
            ExtensionManifest.model_validate(manifest) if manifest is not None else None
        )
        self._audit_sink = audit_sink
        self._secret_provider = secret_provider
        self._lifecycle_hooks: DefaultDict[str, List[Callable]] = defaultdict(list)

    def _audit(
        self,
        action: str,
        outcome: str,
        *,
        lifecycle: Optional[str] = None,
        capability: Optional[str] = None,
    ) -> None:
        if self._audit_sink is None:
            return
        record = {
            "component": "extension",
            "extension": self.manifest.name if self.manifest else "legacy",
            "action": action,
            "outcome": outcome,
        }
        if lifecycle is not None:
            record["lifecycle"] = lifecycle
        if capability is not None:
            record["capability"] = capability
        self._audit_sink(record)

    def register(self, event_type: Type, listener: Callable):
        if self.manifest is not None:
            self.require_capability("events")
        self._event_bus.register(event_type, listener)
        self._registered_listeners.append(listener)  # 记录注册的函数

    def unregister(self, event_type: Type, listener: Callable):
        if self.manifest is not None:
            self.require_capability("events")
        self._event_bus.unregister(event_type, listener)

    def post(self, event):
        if self.manifest is not None:
            self.require_capability("events")
        self._event_bus.post(event)

    def read_file(self, path: str | Path, *, encoding: str = "utf-8") -> str:
        self.require_capability("file")
        return Path(path).read_text(encoding=encoding)

    def write_file(
        self, path: str | Path, content: str, *, encoding: str = "utf-8"
    ) -> None:
        self.require_capability("file")
        Path(path).write_text(content, encoding=encoding)

    def request(self, method: str, url: str, **kwargs):
        self.require_capability("network")
        return requests.request(method, url, **kwargs)

    def run_process(self, args: Sequence[str], **kwargs) -> subprocess.CompletedProcess:
        self.require_capability("process")
        return subprocess.run(args, **kwargs)

    def write_config(
        self, path: str | Path, config: Mapping[str, Any], *, encoding: str = "utf-8"
    ) -> None:
        """Write caller-provided serialized configuration via the host boundary."""
        self.require_capability("config_write")
        import json

        Path(path).write_text(json.dumps(dict(config)), encoding=encoding)

    def get_secret(self, name: str) -> str:
        self.require_capability("secret")
        if self._secret_provider is None:
            raise LookupError("extension secret provider is unavailable")
        return self._secret_provider(name)

    def require_capability(self, capability: CapabilityName) -> None:
        """Reject protected access unless a manifest explicitly grants it."""
        if capability not in CAPABILITY_NAMES:
            self._audit("capability_check", "rejected", capability=capability)
            raise ValueError(f"unknown extension capability: {capability}")
        allowed = self.manifest is not None and self.manifest.capabilities.allows(capability)
        self._audit("capability_check", "allowed" if allowed else "rejected", capability=capability)
        if not allowed:
            raise PermissionError(f"extension capability not declared: {capability}")

    def register_lifecycle_hook(
        self, lifecycle: LifecycleName, listener: Callable
    ) -> None:
        if lifecycle not in LIFECYCLE_NAMES:
            self._audit("hook_registration", "rejected", lifecycle=lifecycle)
            raise ValueError(f"unknown lifecycle name: {lifecycle}")
        if self.manifest is None:
            self._audit("hook_registration", "rejected", lifecycle=lifecycle)
            raise PermissionError("lifecycle hooks require an extension manifest")
        hook = next((item for item in self.manifest.hooks if item.name == lifecycle), None)
        if hook is None:
            self._audit("hook_registration", "rejected", lifecycle=lifecycle)
            raise PermissionError(f"lifecycle hook not declared: {lifecycle}")
        self.require_capability(hook.capability)
        self._lifecycle_hooks[lifecycle].append(listener)
        self._registered_listeners.append(listener)
        self._audit(
            "hook_registration", "allowed", lifecycle=lifecycle,
            capability=hook.capability,
        )

    def emit_lifecycle(self, lifecycle: LifecycleName, payload: Any = None) -> None:
        """Emit an allowlisted lifecycle without recording payload contents."""
        if lifecycle not in LIFECYCLE_NAMES:
            raise ValueError(f"unknown lifecycle name: {lifecycle}")
        for listener in tuple(self._lifecycle_hooks.get(lifecycle, ())):
            listener(payload)
        if self._lifecycle_hooks.get(lifecycle):
            self.audit_lifecycle_delivery(lifecycle, "delivered")

    def audit_lifecycle_delivery(self, lifecycle: LifecycleName, outcome: str) -> None:
        self._audit("hook_delivery", outcome, lifecycle=lifecycle)

    def unregister_all(self):
        """反注册所有通过 @Event 注册的函数"""
        for listener in self._registered_listeners:
            for event_type in self._event_bus._listeners:
                if listener in self._event_bus._listeners[event_type]:
                    self._event_bus.unregister(event_type, listener)
        self._registered_listeners.clear()  # 清空记录
        self._lifecycle_hooks.clear()
