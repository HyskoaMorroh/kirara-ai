from collections import defaultdict
from typing import Any, Callable, DefaultDict, List, Optional, Type

from kirara_ai.events.event_bus import EventBus
from kirara_ai.plugin_manager.models import (
    CAPABILITY_NAMES,
    CapabilityName,
    ExtensionManifest,
    LIFECYCLE_NAMES,
    LifecycleName,
)


class PluginEventBus:
    def __init__(
        self,
        event_bus: EventBus,
        manifest: Optional[ExtensionManifest | dict] = None,
        audit_sink: Optional[Callable[[dict], None]] = None,
    ):
        self._event_bus = event_bus
        self._registered_listeners: List[Callable] = []  # 记录注册过的函数
        self.manifest = (
            ExtensionManifest.model_validate(manifest) if manifest is not None else None
        )
        self._audit_sink = audit_sink
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
        self._event_bus.register(event_type, listener)
        self._registered_listeners.append(listener)  # 记录注册的函数

    def unregister(self, event_type: Type, listener: Callable):
        self._event_bus.unregister(event_type, listener)

    def post(self, event):
        self._event_bus.post(event)

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

    def unregister_all(self):
        """反注册所有通过 @Event 注册的函数"""
        for listener in self._registered_listeners:
            for event_type in self._event_bus._listeners:
                if listener in self._event_bus._listeners[event_type]:
                    self._event_bus.unregister(event_type, listener)
        self._registered_listeners.clear()  # 清空记录
        self._lifecycle_hooks.clear()
