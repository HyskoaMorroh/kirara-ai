"""In-process coordination for manifested extension lifecycle hooks.

This module enforces the host APIs Kirara AI injects into manifested plugins.
It is deliberately not a Python sandbox: plugin code running in this process can
still import and call Python or operating-system APIs directly.
"""

from __future__ import annotations

from typing import Any
from weakref import WeakSet

from kirara_ai.plugin_manager.models import LifecycleName


class ExtensionLifecycleHost:
    """Dispatch sanitized application lifecycle summaries to plugin host buses."""

    def __init__(self) -> None:
        self._buses: WeakSet[Any] = WeakSet()

    def register(self, bus: Any) -> None:
        self._buses.add(bus)

    def unregister(self, bus: Any) -> None:
        self._buses.discard(bus)

    def emit(self, lifecycle: LifecycleName, payload: dict[str, Any]) -> None:
        for bus in tuple(self._buses):
            try:
                bus.emit_lifecycle(lifecycle, dict(payload))
            except Exception:
                bus.audit_lifecycle_delivery(lifecycle, "error")
