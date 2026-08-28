"""Public package surface with lazy imports.

The package is also the parent of small dependency-free module entry points,
such as the Agent Hook command protocol.  Importing the package must not
eagerly construct the Web application before those entry points can run.
Keeping the public names lazy preserves the historical ``kirara_ai`` API while
making lightweight module commands responsive on cold start.
"""

from importlib import import_module
from typing import Any

__all__ = ["init_application", "run_application", "get_logger", "ConfigLoader"]

_LAZY_EXPORTS = {
    "init_application": ("kirara_ai.entry", "init_application"),
    "run_application": ("kirara_ai.entry", "run_application"),
    "get_logger": ("kirara_ai.logger", "get_logger"),
    "ConfigLoader": ("kirara_ai.config.config_loader", "ConfigLoader"),
}


def __getattr__(name: str) -> Any:
    """Load a legacy top-level export only when it is actually requested."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
