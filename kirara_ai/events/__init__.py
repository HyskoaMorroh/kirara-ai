"""Public event exports with lazy loading to keep submodule imports acyclic."""

from importlib import import_module
from typing import Any

__all__ = [
    "listen",
    "EventBus",
    "ApplicationStarted",
    "ApplicationStopping",
    "PluginStarted",
    "PluginStopped",
    "PluginLoaded",
    "IMAdapterStarted",
    "IMAdapterStopped",
    "LLMAdapterLoaded",
    "LLMAdapterUnloaded",
    "WorkflowExecutionBegin",
    "WorkflowExecutionEnd",
]

_LAZY_EXPORTS = {
    "listen": ("kirara_ai.events.listen", "listen"),
    "EventBus": ("kirara_ai.events.event_bus", "EventBus"),
    "ApplicationStarted": ("kirara_ai.events.application", "ApplicationStarted"),
    "ApplicationStopping": ("kirara_ai.events.application", "ApplicationStopping"),
    "PluginStarted": ("kirara_ai.events.plugin", "PluginStarted"),
    "PluginStopped": ("kirara_ai.events.plugin", "PluginStopped"),
    "PluginLoaded": ("kirara_ai.events.plugin", "PluginLoaded"),
    "IMAdapterStarted": ("kirara_ai.events.im", "IMAdapterStarted"),
    "IMAdapterStopped": ("kirara_ai.events.im", "IMAdapterStopped"),
    "LLMAdapterLoaded": ("kirara_ai.events.llm", "LLMAdapterLoaded"),
    "LLMAdapterUnloaded": ("kirara_ai.events.llm", "LLMAdapterUnloaded"),
    "WorkflowExecutionBegin": ("kirara_ai.events.workflow", "WorkflowExecutionBegin"),
    "WorkflowExecutionEnd": ("kirara_ai.events.workflow", "WorkflowExecutionEnd"),
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
