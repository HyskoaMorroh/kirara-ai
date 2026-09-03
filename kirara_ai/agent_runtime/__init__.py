"""Shared runtime primitives for channel-independent AI agents.

导出保持**惰性**（PEP 562 的 ``__getattr__``），因为这个包同时是一个
**独立命令入口**的父包：``python -m kirara_ai.agent_runtime.audit_hook_command``
是内置 ``hook:ai-debug`` 五个事件各自起的子进程，而 ``-m`` 按 runpy 的规定必须
先导入父包。

原来这里是六行 eager import，于是那个「零依赖」的命令要先把 executor 拉进来，
连带 pydantic 与 asyncio。实测（本机，重复 5 次取平均）：

    -m  走包 __init__ : 1.52s
    直接跑该文件      : 0.14s
    每次多付          : 1.38s

内置 hook 一轮对话触发五个事件，也就是**每轮多付约 7 秒**——而每个 hook 的
``timeout_ms`` 是 5000。这不是测试环境的怪相：生产里每一轮都在付，
而超时后 ``_terminate_process_tree`` 杀掉子进程、那个事件记成失败。

顶层 ``kirara_ai/__init__.py`` 早就因为同样的理由改成惰性了（见它的模块 docstring），
这里补上同一条。``from kirara_ai.agent_runtime import AgentRegistry`` 这类写法
一字不改，仍然可用——变的只是「什么时候才真的导入 executor」。
"""

from typing import Any

__all__ = [
    "AgentDefinition",
    "AgentRegistry",
    "AgentRuntimeExecutor",
    "AgentHookRuntime",
    "HookHandler",
    "HookOutcome",
    "HOOK_EVENTS",
    "ChannelContext",
    "ResourceBinding",
    "ResourceSnapshot",
    "RuntimeResult",
    "RuntimeStatus",
    "SessionStore",
    "SessionPolicy",
    "SUPPORTED_CHANNEL_TYPES",
    "effective_mcp_allowlist",
    "resolve_mcp_tool_allowlist",
]

#: 公开名 -> (子模块, 子模块里的属性名)。
#:
#: 写成显式表而不是「按名字猜模块」：猜错时的症状是一个本该存在的名字抛
#: ``AttributeError``，而那读起来像「这个 API 被删了」。表在这里可以被
#: ``tests/agent_runtime/test_lazy_package_exports.py`` 逐项核对。
_LAZY_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentDefinition": ("kirara_ai.agent_runtime.core", "AgentDefinition"),
    "AgentRegistry": ("kirara_ai.agent_runtime.core", "AgentRegistry"),
    "ChannelContext": ("kirara_ai.agent_runtime.core", "ChannelContext"),
    "ResourceBinding": ("kirara_ai.agent_runtime.core", "ResourceBinding"),
    "ResourceSnapshot": ("kirara_ai.agent_runtime.core", "ResourceSnapshot"),
    "SessionPolicy": ("kirara_ai.agent_runtime.core", "SessionPolicy"),
    "SUPPORTED_CHANNEL_TYPES": (
        "kirara_ai.agent_runtime.core",
        "SUPPORTED_CHANNEL_TYPES",
    ),
    "effective_mcp_allowlist": (
        "kirara_ai.agent_runtime.core",
        "effective_mcp_allowlist",
    ),
    "resolve_mcp_tool_allowlist": (
        "kirara_ai.agent_runtime.core",
        "resolve_mcp_tool_allowlist",
    ),
    "AgentRuntimeExecutor": (
        "kirara_ai.agent_runtime.executor",
        "AgentRuntimeExecutor",
    ),
    "RuntimeResult": ("kirara_ai.agent_runtime.executor", "RuntimeResult"),
    "RuntimeStatus": ("kirara_ai.agent_runtime.executor", "RuntimeStatus"),
    "AgentHookRuntime": ("kirara_ai.agent_runtime.hooks", "AgentHookRuntime"),
    "HookHandler": ("kirara_ai.agent_runtime.hooks", "HookHandler"),
    "HookOutcome": ("kirara_ai.agent_runtime.hooks", "HookOutcome"),
    "HOOK_EVENTS": ("kirara_ai.agent_runtime.hooks", "HOOK_EVENTS"),
    "SessionStore": ("kirara_ai.agent_runtime.session_store", "SessionStore"),
}


def __getattr__(name: str) -> Any:
    """按需导入一个公开名，并缓存到模块全局里。

    缓存（``globals()[name] = value``）是为了让第二次访问不再走这个函数：
    ``__getattr__`` 只在常规查找失败时被调用，写回之后就是普通属性访问。
    """

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attribute_name = target
    from importlib import import_module

    value = getattr(import_module(module_name), attribute_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """让 ``dir()`` 与自动补全仍然看得到全部公开名。

    不实现它的话，惰性导出在交互式环境里「消失」——而那会让人以为 API 被删了。
    """

    return sorted(__all__)
