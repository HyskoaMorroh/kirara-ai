"""Public IM package exports.

The registry and manager depend on adapter and event modules.  Loading them
while a lightweight IM submodule (for example ``message``) is being imported
creates a cycle through the plugin manager.  Keep the historical exports but
resolve them only when a caller asks for them.
"""

from importlib import import_module
from typing import Any

__all__ = ["IMRegistry", "IMManager"]

_LAZY_EXPORTS = {
    "IMRegistry": ("kirara_ai.im.im_registry", "IMRegistry"),
    "IMManager": ("kirara_ai.im.manager", "IMManager"),
}


def __getattr__(name: str) -> Any:
    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
