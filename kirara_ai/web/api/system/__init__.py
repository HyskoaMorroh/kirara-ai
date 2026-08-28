"""System API exports.

The route module imports helpers from ``kirara_ai.web.utils`` while the web
application imports this package during its own initialization.  Importing
the blueprint eagerly makes lightweight adapter imports depend on that cycle.
Keep the public export compatible, but resolve it only when requested.
"""

from importlib import import_module
from typing import Any

__all__ = ["system_bp"]


def __getattr__(name: str) -> Any:
    if name != "system_bp":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = import_module("kirara_ai.web.api.system.routes").system_bp
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

