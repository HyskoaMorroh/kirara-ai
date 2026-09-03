"""Controlled VPS dependency probes and installation tasks.

The browser submits only a server-known dependency ID. Commands, arguments,
paths and environment variables are defined here and never accepted from API
payloads.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


REGISTRY_FORMAT_VERSION = 1
TASKS_FORMAT_VERSION = 1
DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
MAX_COMMAND_OUTPUT_BYTES = 256 * 1024
MAX_PUBLIC_OUTPUT_CHARS = 12_000

_DEPENDENCY_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
_INLINE_SECRET_PATTERN = re.compile(
    r"(?i)\b(authorization|cookie|password|secret|token|api[-_ ]?key)\s*[:=]\s*([^\s,;]+)"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[^\s,;]+")
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)(?<![A-Za-z0-9])[A-Z]:\\[^\r\n\t\"']+")
_POSIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s/]+/)+[^\s,;:\"']+")

_ALLOWED_ENVIRONMENT_KEYS = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "TMP",
        "TEMP",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "NO_PROXY",
        "ALL_PROXY",
        "LANG",
        "LC_ALL",
        "TERM",
    }
)


class SystemDependencyError(RuntimeError):
    """Base error for system dependency operations."""


class DependencyNotFoundError(SystemDependencyError):
    """The submitted dependency ID is not in the server catalog."""


class DependencyInstallConfirmationRequired(SystemDependencyError):
    """A system-level installation was requested without confirmation."""


class DependencyInstallUnsupported(SystemDependencyError):
    """The current server catalog does not provide a controlled installer."""


class DependencyTaskStateError(SystemDependencyError):
    """The requested task transition is invalid."""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int | None
    output: str = ""
    timed_out: bool = False
    cancelled: bool = False


@dataclass(frozen=True)
class DependencyDefinition:
    dependency_id: str
    name: str
    description: str
    kind: str
    required_by: tuple[str, ...]
    probe_commands: tuple[tuple[str, ...], ...]
    install_commands: tuple[tuple[str, ...], ...] = ()
    prerequisites: tuple[str, ...] = ()
    timeout_seconds: int = DEFAULT_COMMAND_TIMEOUT_SECONDS
    operator_guidance: str | None = None

    @property
    def install_supported(self) -> bool:
        return bool(self.install_commands)

    def public(self, state: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "dependency_id": self.dependency_id,
            "name": self.name,
            "description": self.description,
            "kind": self.kind,
            "required_by": list(self.required_by),
            "prerequisites": list(self.prerequisites),
            "install_supported": self.install_supported,
            "operator_guidance": self.operator_guidance,
            "status": state.get("status", "unknown"),
            "ready": state.get("ready") is True,
            "version": state.get("version"),
            "summary": state.get("summary"),
            "checked_at": state.get("checked_at"),
            "last_task_id": state.get("last_task_id"),
        }


@dataclass
class DependencyProbe:
    dependency_id: str
    ready: bool
    status: str
    version: str | None
    summary: str
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dependency_id": self.dependency_id,
            "ready": self.ready,
            "status": self.status,
            "version": self.version,
            "summary": self.summary,
            "checked_at": self.checked_at,
        }


@dataclass
class DependencyInstallTask:
    task_id: str
    dependency_id: str
    status: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    retry_of: str | None = None
    cancel_requested: bool = False
    error_code: str | None = None
    error_summary: str | None = None
    output_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "dependency_id": self.dependency_id,
            "operation": "install",
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "retry_of": self.retry_of,
            "cancel_requested": self.cancel_requested,
            "error_code": self.error_code,
            "error_summary": self.error_summary,
            "output_tail": self.output_tail,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DependencyInstallTask":
        return cls(
            task_id=str(value["task_id"]),
            dependency_id=str(value["dependency_id"]),
            status=str(value.get("status", "failed")),
            created_at=str(value.get("created_at") or _timestamp()),
            started_at=_optional_string(value.get("started_at")),
            finished_at=_optional_string(value.get("finished_at")),
            retry_of=_optional_string(value.get("retry_of")),
            cancel_requested=value.get("cancel_requested") is True,
            error_code=_optional_string(value.get("error_code")),
            error_summary=_optional_string(value.get("error_summary")),
            output_tail=_sanitize_output(str(value.get("output_tail") or "")),
        )


CommandRunner = Callable[..., CommandResult]


def _definitions() -> tuple[DependencyDefinition, ...]:
    return (
        DependencyDefinition(
            dependency_id="node-runtime",
            name="Node.js Runtime",
            description="Node.js、npm 和 npx，供 MCP 与 Node.js 工具使用。",
            kind="runtime",
            required_by=("Context7 MCP", "Agent Browser"),
            probe_commands=(("node", "--version"), ("npm", "--version"), ("npx", "--version")),
            operator_guidance="请由 VPS 运维使用系统软件源安装受支持的 Node.js LTS、npm 和 npx。",
        ),
        DependencyDefinition(
            dependency_id="python-tooling",
            name="Python Tooling",
            description="uv 工具管理器，供 Python CLI 以隔离方式安装和升级。",
            kind="runtime",
            required_by=("Graphify",),
            probe_commands=(("uv", "--version"),),
            operator_guidance="请由 VPS 运维安装 uv，并确保服务进程的 PATH 可以访问它。",
        ),
        DependencyDefinition(
            dependency_id="agent-browser-cli",
            name="Agent Browser CLI",
            description="Agent Browser 命令行程序。Skill 包与该可执行程序是两个独立状态。",
            kind="cli",
            required_by=("agent-browser Skill",),
            probe_commands=(("agent-browser", "--version"),),
            install_commands=(("npm", "install", "-g", "agent-browser"),),
            prerequisites=("node-runtime",),
        ),
        DependencyDefinition(
            dependency_id="agent-browser-browser",
            name="Agent Browser Chromium",
            description="Agent Browser 实际启动浏览器所需的 Chromium 运行环境。",
            kind="browser-runtime",
            required_by=("agent-browser Skill",),
            probe_commands=(("agent-browser", "doctor", "--offline", "--quick", "--json"),),
            install_commands=(("agent-browser", "install"),),
            prerequisites=("agent-browser-cli",),
            timeout_seconds=900,
        ),
        DependencyDefinition(
            dependency_id="uvx-runtime",
            name="uvx Runner",
            description=(
                "uvx 一次性运行器，供以 uvx 启动的 stdio MCP 使用。"
                "与 uv 是两个命令：uv 装了不代表 uvx 在 PATH 上。"
            ),
            kind="mcp-runtime",
            required_by=("mcp:fetch", "mcp:time"),
            probe_commands=(("uvx", "--version"),),
            prerequisites=("python-tooling",),
            operator_guidance=(
                "uvx 随 uv 一起分发（uv 0.3+）。若 uv 可用而 uvx 不可用，"
                "通常是 uv 版本过旧或服务进程的 PATH 未包含 uv 的可执行目录。"
            ),
        ),
        DependencyDefinition(
            dependency_id="npx-runtime",
            name="npx Runner",
            description=(
                "npx 一次性运行器，供以 npx 启动的 stdio MCP 使用。"
                "Context7 有自己的登记项（context7-runtime），此项覆盖其余 npx 预设。"
            ),
            kind="mcp-runtime",
            required_by=(
                "mcp:memory",
                "mcp:sequential-thinking",
                "mcp:filesystem",
                "mcp:chrome-devtools",
                "mcp:playwright",
            ),
            probe_commands=(("npx", "--version"),),
            prerequisites=("node-runtime",),
            operator_guidance="npx 随 Node.js 一起安装；请先修复 Node.js Runtime。",
        ),
        DependencyDefinition(
            dependency_id="context7-runtime",
            name="Context7 MCP Runtime",
            description="运行 Context7 stdio MCP 所需的 npx 环境；MCP 连接状态另行展示。",
            kind="mcp-runtime",
            required_by=("mcp:context7",),
            probe_commands=(("npx", "--version"),),
            prerequisites=("node-runtime",),
            operator_guidance="Context7 由 npx 按服务器登记的固定包名启动；请先修复 Node.js Runtime。",
        ),
        DependencyDefinition(
            dependency_id="graphify-cli",
            name="Graphify CLI",
            description="Graphify 知识图谱命令行工具，PyPI 包名为 graphifyy。",
            kind="cli",
            required_by=("graphify Skill",),
            probe_commands=(("graphify", "--version"),),
            install_commands=(("uv", "tool", "install", "--upgrade", "graphifyy"),),
            prerequisites=("python-tooling",),
        ),
        DependencyDefinition(
            dependency_id="rtk-cli",
            name="RTK CLI",
            description=(
                "RTK（Rust Token Killer）终端输出压缩代理，用于降低命令输出的 token 占用。"
                "注意与同名的 Rust Type Kit 不是同一个工具：以 `rtk gain` 是否可用为准。"
            ),
            kind="cli",
            required_by=("终端输出压缩",),
            # 两条命令都必须通过。
            #
            # 只跑 `rtk --version` 无法与同名的 Rust Type Kit 区分——那个工具
            # 同样有 `--version`。描述里已经写明「以 `rtk gain` 是否可用为准」，
            # 但判据此前只在文档里、没有进探针：VPS 上装错 crate 时前端照样
            # 显示「就绪」，之后每次终端输出压缩都静默走偏。
            probe_commands=(("rtk", "--version"), ("rtk", "gain")),
            install_commands=(("cargo", "install", "--locked", "rtk-cli"),),
            operator_guidance=(
                "若服务器没有 cargo，请由 VPS 运维改用官方发行的二进制包安装，"
                "并确保服务进程的 PATH 可以访问 rtk。"
            ),
        ),
        DependencyDefinition(
            dependency_id="memsearch-cli",
            name="MemSearch CLI",
            description="MemSearch 跨会话记忆检索命令行工具，PyPI 包名为 memsearch。",
            kind="cli",
            required_by=("记忆检索",),
            probe_commands=(("memsearch", "--version"),),
            install_commands=(("uv", "tool", "install", "--upgrade", "memsearch"),),
            prerequisites=("python-tooling",),
        ),
        DependencyDefinition(
            dependency_id="context-mode-plugin",
            name="Context Mode CLI",
            description=(
                "Context Mode 在沙箱内处理大输出并只回传结论。"
                "npm 包名为 context-mode，自带 `context-mode` 可执行文件，"
                "可作为 stdio MCP 服务器运行——因此它是**服务器侧组件**，装在 VPS 上。"
            ),
            kind="cli",
            required_by=("大输出分析",),
            # 探测它**自己的**可执行文件。
            #
            # 这一条曾经写成 `("claude", "--version")` 并标为 `claude-plugin`,
            # 理由是「插件没有独立可执行文件」——那个判断是错的。实测：
            #
            #     $ npm view context-mode version   -> 1.0.169
            #     $ npm ls -g --depth=0             -> context-mode@1.0.169
            #     package.json: "bin": {"context-mode": "./cli.bundle.mjs"}
            #
            # 它是发布在 npm 上、有 bin 入口的普通包，其自述也写明支持
            # Claude Code / Gemini CLI / VS Code Copilot / OpenCode / Codex CLI。
            # 按 `claude --version` 探测的后果是：一台装了 context-mode 但没装
            # Claude CLI 的 VPS 会被报成 missing，而一台装了 Claude CLI 却没装
            # context-mode 的机器会被报成 ready——两个方向都答错。
            probe_commands=(("context-mode", "--version"),),
            install_commands=(("npm", "install", "-g", "context-mode"),),
            prerequisites=("node-runtime",),
        ),
        DependencyDefinition(
            dependency_id="caveman-plugin",
            name="Caveman CLI",
            description=(
                "Caveman 用于压缩输出表达。分发形态是一个安装器"
                "（本机实测为 caveman-installer，提供 `caveman` 可执行文件），"
                "而它在公共 npm 上不可达，因此服务器侧不能代为安装。"
            ),
            kind="cli",
            required_by=("输出压缩",),
            # 与 context-mode 一样探测自己的可执行文件——它确实有一个。
            # 但**不给** install_commands，因为公共 npm 上装不到：
            #
            #     $ npm view caveman-installer  -> E404 Not Found
            #     $ npm view caveman            -> 一个无关的 JS 模板引擎
            #
            # 本机那份是 caveman-installer@2.0.0（bin: caveman -> bin/install.js）。
            # 猜一个 `npm i -g caveman` 会装上那个模板引擎：命令存在、
            # 探测通过、而功能完全不是要的那个——比报 missing 更糟。
            probe_commands=(("caveman", "--version"),),
            prerequisites=("node-runtime",),
            operator_guidance=(
                "公共 npm 上没有可用的 caveman 命令行包（caveman-installer 为 404，"
                "caveman 是同名的模板引擎）。请由 VPS 运维按其官方分发渠道安装，"
                "并确保服务进程的 PATH 能访问 caveman。"
            ),
        ),
    )


#: `runtime_dependency` 声明值到登记项 id 的映射。
#:
#: 预设用命令名声明自己需要什么（`"uvx"` / `"npx"`），登记表用 id 索引探测项。
#: 两者不同名是刻意的：命令名是事实，id 是这套依赖系统的主键，未来同一个命令
#: 可能对应多条探测链（例如按平台分叉）。
_RUNTIME_DEPENDENCY_IDS = {
    "uvx": "uvx-runtime",
    "npx": "npx-runtime",
}

#: 技能名（或它的目录名）到服务器依赖 id 的映射。
#:
#: 需求 10 点名的五个工具都在登记表里有条目、探测与安装都能跑，断的是这一层：
#: 此前只认 `agent-browser` 与 `graphify` 两个名字，其余一律返回空列表——
#: 而空列表的含义是「这个技能不需要任何服务器依赖」。
#:
#: 后果有两处，都不报错：技能广告里不会出现「服务器上没有这个命令」
#: （`skill_readiness_note()` 拿到空列表就什么都不说，于是模型照着一份它执行不了的
#: 说明自信作答），安装界面也不显示这个技能缺什么。
#:
#: 需求 10 点名的五个工具全部收在这里。它们都有自己的可执行文件，
#: 因此「服务器上装了没有」这个问题对每一个都答得出来。
#:
#: `caveman` 是唯一一个 `install_supported` 为假的：它的可执行文件确实存在
#: （本机 `caveman-installer@2.0.0` 提供 `caveman`），但公共 npm 上装不到，
#: 因此登记项只探测、不代装，并在 `operator_guidance` 里说明由运维处理。
#: 仍然收进这张表是对的：「装了没有」是一个有答案的问题，而技能广告需要那个答案——
#: 不收的后果是模型照着一份它执行不了的说明自信作答。
_SKILL_NAME_DEPENDENCY_IDS: dict[str, tuple[str, ...]] = {
    "graphify": ("graphify-cli",),
    "memsearch": ("memsearch-cli",),
    # 需求 10 写的是「tk」，本机实际命令名是 `rtk`；两个写法都收，
    # 因为技能目录可能按任一种命名。
    "rtk": ("rtk-cli",),
    "tk": ("rtk-cli",),
    # 这两条此前被排除，理由是「它们是操作者本机的 Claude Code 插件」——
    # 那个判断是错的：`context-mode` 是 npm 上有 bin 入口的普通包，
    # 跨 Claude Code / Codex / VS Code 都能跑，完全可以装到 Linux VPS 上。
    "context-mode": ("context-mode-plugin",),
    "caveman": ("caveman-plugin",),
}


def known_dependency_ids() -> frozenset[str]:
    """Return every registered dependency id.

    给调用方一条校验途径：判定出的 id 必须能在登记表里查到，否则探测、安装与
    状态展示全都无从进行——而那种情况下界面只会显示一个查不到状态的依赖名。
    """
    return frozenset(definition.dependency_id for definition in _definitions())


def dependency_ids_for_resource(item: Mapping[str, Any]) -> list[str]:
    """Return the server dependencies one catalog item or resource needs.

    住在这里而不是资源目录服务里，是因为**两个**调用方需要同一份映射：

    - 安装界面（`ResourceCatalogService.project_dependencies`）用它显示
      「这个技能还缺什么」；
    - Agent 运行时用它决定要不要在技能广告里加一句「服务器上没有这个命令」。

    两边各写一份的后果不是重复代码，而是**两份会各自漂移的判断**：界面说已就绪、
    运行时说缺失（或者反过来），而这种不一致没有任何症状能让人察觉——
    模型只会照着一份它其实执行不了的说明自信作答。

    键位兼容目录项（`catalog_id` / `source_key` / `directory`）与已安装资源
    （`resource_id` / `source_metadata`）两种形状：同一个技能在这两处的字段名不同，
    而它需要的依赖是同一批。
    """
    metadata = item.get("source_metadata")
    metadata = metadata if isinstance(metadata, Mapping) else {}
    catalog_id = str(item.get("catalog_id") or metadata.get("catalog_id") or "").casefold()
    resource_id = str(item.get("resource_id") or "").casefold()
    source_key = str(item.get("source_key") or metadata.get("source_key") or "").casefold()

    if catalog_id == "mcp:context7" or source_key == "mcp:context7" or resource_id == "mcp.context7":
        return ["context7-runtime"]

    # 其余 stdio MCP 预设按它们自己声明的 `runtime_dependency` 判定。
    # 此前这个字段无人消费：7 个预设一律返回空列表，含义是「不需要任何依赖」，
    # 而它们全都需要——没有 uvx 的机器上 mcp:fetch 一次都起不来，
    # 界面上却只显示「连接失败 / 工具数 0」，不说缺什么。
    declared = item.get("runtime_dependency") or metadata.get("runtime_dependency")
    mapped = _RUNTIME_DEPENDENCY_IDS.get(str(declared or "").strip().casefold())
    if mapped is not None:
        return [mapped]

    if str(item.get("type") or "").casefold() != "skill":
        return []

    names = {
        str(item.get("name") or "").strip().casefold(),
        str(metadata.get("name") or "").strip().casefold(),
        str(item.get("directory") or "").strip("/").rsplit("/", 1)[-1].casefold(),
        str(metadata.get("directory") or "").strip("/").rsplit("/", 1)[-1].casefold(),
    }
    source_parts = {
        source_key.rsplit(":", 1)[-1].strip("/").rsplit("/", 1)[-1],
        str(item.get("directory") or "").strip("/").rsplit("/", 1)[-1].casefold(),
        str(metadata.get("directory") or "").strip("/").rsplit("/", 1)[-1].casefold(),
    }
    identifiers = names | source_parts
    if "agent-browser" in identifiers:
        # 两条：CLI 本体与它要拉的 Chromium。少一条就等于「装了命令但打不开浏览器」
        # 在界面上显示为就绪。
        return ["agent-browser-cli", "agent-browser-browser"]
    for name in identifiers:
        mapped = _SKILL_NAME_DEPENDENCY_IDS.get(name)
        if mapped is not None:
            return list(mapped)
    return []


class SystemDependencyService:
    """Persist and execute a server-owned dependency installation catalog."""

    def __init__(
        self,
        data_path: str | Path,
        *,
        command_runner: CommandRunner | None = None,
    ) -> None:
        self.data_path = Path(data_path).resolve()
        self.root = self.data_path / "dependencies"
        self.logs_path = self.root / "logs"
        self.registry_path = self.root / "registry.json"
        self.tasks_path = self.root / "tasks.json"
        self.audit_path = self.root / "audit.jsonl"
        self.root.mkdir(parents=True, exist_ok=True)
        self.logs_path.mkdir(parents=True, exist_ok=True)
        self._definitions = {item.dependency_id: item for item in _definitions()}
        self._runner = command_runner or self._run_command
        self._lock = threading.RLock()
        self._cancellation_events: dict[str, threading.Event] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._registry = self._load_registry()
        self._tasks = self._load_tasks()
        self._recover_interrupted_tasks()
        self._persist_registry()
        self._persist_tasks()

    def list_dependencies(self) -> list[dict[str, Any]]:
        with self._lock:
            return [
                definition.public(self._registry[dependency_id])
                for dependency_id, definition in self._definitions.items()
            ]

    def get_dependency(self, dependency_id: str) -> dict[str, Any]:
        definition = self._definition(dependency_id)
        with self._lock:
            return definition.public(self._registry[dependency_id])

    def probe(self, dependency_id: str) -> dict[str, Any]:
        definition = self._definition(dependency_id)
        probe = self._probe_definition(definition)
        with self._lock:
            state = self._registry[dependency_id]
            state.update(probe.to_dict())
            self._persist_registry()
        self._audit(dependency_id, "probe", "succeeded" if probe.ready else "failed")
        return definition.public(state)

    def list_tasks(self, *, dependency_id: str | None = None) -> list[dict[str, Any]]:
        if dependency_id is not None:
            self._definition(dependency_id)
        with self._lock:
            tasks = sorted(self._tasks.values(), key=lambda item: item.created_at, reverse=True)
            return [
                task.to_dict()
                for task in tasks
                if dependency_id is None or task.dependency_id == dependency_id
            ]

    def get_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            return self._task(task_id).to_dict()

    def install(
        self,
        dependency_id: str,
        *,
        confirmed: bool,
        start: bool = True,
        retry_of: str | None = None,
    ) -> dict[str, Any]:
        definition = self._definition(dependency_id)
        if not confirmed:
            raise DependencyInstallConfirmationRequired("dependency installation requires confirmation")
        if not definition.install_supported:
            raise DependencyInstallUnsupported("dependency installation is not supported by this server catalog")
        if retry_of is not None:
            with self._lock:
                original = self._task(retry_of)
                if original.dependency_id != dependency_id:
                    raise DependencyTaskStateError("retry dependency does not match the original task")

        task = DependencyInstallTask(
            task_id=f"dep-{uuid.uuid4().hex}",
            dependency_id=dependency_id,
            status="queued",
            created_at=_timestamp(),
            retry_of=retry_of,
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._registry[dependency_id]["last_task_id"] = task.task_id
            self._persist_tasks()
            self._persist_registry()
        self._audit(dependency_id, "install_queued", "succeeded", task_id=task.task_id)
        if start:
            self._start_task(task.task_id)
        return task.to_dict()

    def run_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._task(task_id)
            if task.status != "queued":
                raise DependencyTaskStateError("only queued dependency tasks can run")
            task.status = "running"
            task.started_at = _timestamp()
            cancellation_event = self._cancellation_events.setdefault(task_id, threading.Event())
            self._persist_tasks()
        self._audit(task.dependency_id, "install_started", "succeeded", task_id=task_id)

        definition = self._definitions[task.dependency_id]
        output_parts: list[str] = []

        def output_sink(value: str) -> None:
            sanitized = _sanitize_output(value)
            if not sanitized:
                return
            output_parts.append(sanitized)
            if sum(len(item) for item in output_parts) > MAX_PUBLIC_OUTPUT_CHARS * 2:
                output_parts[:] = ["".join(output_parts)[-MAX_PUBLIC_OUTPUT_CHARS:]]
            self._append_task_log(task_id, sanitized)

        try:
            current = self._probe_definition(definition, cancellation_event=cancellation_event)
            if current.ready:
                return self._finish_task(task, status="succeeded", output="dependency is already ready")

            for prerequisite_id in definition.prerequisites:
                prerequisite = self._probe_definition(
                    self._definitions[prerequisite_id],
                    cancellation_event=cancellation_event,
                )
                with self._lock:
                    self._registry[prerequisite_id].update(prerequisite.to_dict())
                    self._persist_registry()
                if not prerequisite.ready:
                    return self._finish_task(
                        task,
                        status="failed",
                        error_code="prerequisite_missing",
                        error_summary=f"required dependency is not ready: {prerequisite_id}",
                        output="".join(output_parts),
                    )

            for argv in definition.install_commands:
                streamed_output = False

                def command_output_sink(value: str) -> None:
                    nonlocal streamed_output
                    streamed_output = True
                    output_sink(value)

                result = self._runner(
                    argv,
                    timeout=definition.timeout_seconds,
                    cancellation_event=cancellation_event,
                    output_sink=command_output_sink,
                )
                if not streamed_output:
                    output_sink(result.output)
                if result.cancelled or cancellation_event.is_set():
                    return self._finish_task(
                        task,
                        status="cancelled",
                        error_code="cancelled",
                        error_summary="dependency installation was cancelled",
                        output="".join(output_parts),
                    )
                if result.timed_out:
                    return self._finish_task(
                        task,
                        status="failed",
                        error_code="timeout",
                        error_summary="dependency installation timed out",
                        output="".join(output_parts),
                    )
                if result.exit_code != 0:
                    return self._finish_task(
                        task,
                        status="failed",
                        error_code="install_failed",
                        error_summary="dependency installer returned a non-zero status",
                        output="".join(output_parts),
                    )

            post_probe = self._probe_definition(definition, cancellation_event=cancellation_event)
            with self._lock:
                self._registry[definition.dependency_id].update(post_probe.to_dict())
                self._persist_registry()
            if not post_probe.ready:
                return self._finish_task(
                    task,
                    status="failed",
                    error_code="post_probe_failed",
                    error_summary="dependency installer completed but readiness probe failed",
                    output="".join(output_parts),
                )
            return self._finish_task(task, status="succeeded", output="".join(output_parts))
        except Exception as error:
            return self._finish_task(
                task,
                status="failed",
                error_code="execution_error",
                error_summary=_sanitize_output(str(error)) or type(error).__name__,
                output="".join(output_parts),
            )
        finally:
            with self._lock:
                self._cancellation_events.pop(task_id, None)
                self._threads.pop(task_id, None)

    def retry_task(
        self,
        task_id: str,
        *,
        confirmed: bool,
        start: bool = True,
    ) -> dict[str, Any]:
        with self._lock:
            original = self._task(task_id)
            if original.status not in {"failed", "cancelled"}:
                raise DependencyTaskStateError("only failed or cancelled tasks can be retried")
            dependency_id = original.dependency_id
        retry = self.install(
            dependency_id,
            confirmed=confirmed,
            start=start,
            retry_of=task_id,
        )
        self._audit(dependency_id, "retry_created", "succeeded", task_id=retry["task_id"])
        return retry

    def cancel_task(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            task = self._task(task_id)
            if task.status == "queued":
                task.cancel_requested = True
                task.status = "cancelled"
                task.finished_at = _timestamp()
                task.error_code = "cancelled"
                task.error_summary = "dependency installation was cancelled"
            elif task.status == "running":
                task.cancel_requested = True
                self._cancellation_events.setdefault(task_id, threading.Event()).set()
            elif task.status not in {"failed", "succeeded", "cancelled"}:
                raise DependencyTaskStateError("dependency task cannot be cancelled")
            self._persist_tasks()
            result = task.to_dict()
        self._audit(task.dependency_id, "cancel_requested", "succeeded", task_id=task_id)
        return result

    def _definition(self, dependency_id: Any) -> DependencyDefinition:
        if not isinstance(dependency_id, str) or not _DEPENDENCY_ID_PATTERN.fullmatch(dependency_id):
            raise DependencyNotFoundError("dependency is not available")
        definition = self._definitions.get(dependency_id)
        if definition is None:
            raise DependencyNotFoundError("dependency is not available")
        return definition

    def _task(self, task_id: Any) -> DependencyInstallTask:
        if not isinstance(task_id, str) or not task_id.startswith("dep-"):
            raise DependencyTaskStateError("dependency task is not found")
        task = self._tasks.get(task_id)
        if task is None:
            raise DependencyTaskStateError("dependency task is not found")
        return task

    def _probe_definition(
        self,
        definition: DependencyDefinition,
        *,
        cancellation_event: threading.Event | None = None,
    ) -> DependencyProbe:
        cancellation_event = cancellation_event or threading.Event()
        outputs: list[str] = []
        for argv in definition.probe_commands:
            command_outputs: list[str] = []
            try:
                result = self._runner(
                    argv,
                    timeout=min(definition.timeout_seconds, 60),
                    cancellation_event=cancellation_event,
                    output_sink=lambda value: command_outputs.append(_sanitize_output(value)),
                )
            except OSError as error:
                # 探测一个不存在的可执行文件是「未安装」，不是接口错误。
                # 默认 runner 已经把 OSError 转成 exit 127；注入的自定义 runner
                # 可能直接抛出，这里同样按未安装处理，避免探测接口 500。
                return DependencyProbe(
                    definition.dependency_id,
                    False,
                    "missing",
                    None,
                    _sanitize_output(str(error)) or "dependency is not available",
                    _timestamp(),
                )
            outputs.extend(command_outputs or [_sanitize_output(result.output)])
            if result.cancelled or cancellation_event.is_set():
                return DependencyProbe(
                    definition.dependency_id,
                    False,
                    "cancelled",
                    None,
                    "readiness probe was cancelled",
                    _timestamp(),
                )
            if result.timed_out:
                return DependencyProbe(
                    definition.dependency_id,
                    False,
                    "failed",
                    None,
                    "readiness probe timed out",
                    _timestamp(),
                )
            if result.exit_code != 0:
                return DependencyProbe(
                    definition.dependency_id,
                    False,
                    "missing",
                    None,
                    _bounded_text("".join(outputs)) or "dependency is not available",
                    _timestamp(),
                )

        combined = _bounded_text("\n".join(item.strip() for item in outputs if item.strip()))
        version, summary = _public_probe_result(definition, combined)
        return DependencyProbe(
            definition.dependency_id,
            True,
            "ready",
            version,
            summary,
            _timestamp(),
        )

    def _start_task(self, task_id: str) -> None:
        thread = threading.Thread(
            target=self.run_task,
            args=(task_id,),
            name=f"kirara-dependency-{task_id[-8:]}",
            daemon=True,
        )
        with self._lock:
            self._threads[task_id] = thread
        thread.start()

    def _finish_task(
        self,
        task: DependencyInstallTask,
        *,
        status: str,
        output: str,
        error_code: str | None = None,
        error_summary: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            task.status = status
            task.finished_at = _timestamp()
            task.error_code = error_code
            task.error_summary = _bounded_text(_sanitize_output(error_summary or "")) or None
            task.output_tail = _bounded_text(_sanitize_output(output))
            self._persist_tasks()
            if status != "succeeded":
                state = self._registry[task.dependency_id]
                state.update(
                    {
                        "status": "failed" if status == "failed" else status,
                        "ready": False,
                        "summary": task.error_summary,
                        "checked_at": task.finished_at,
                    }
                )
                self._persist_registry()
            result = task.to_dict()
        self._audit(
            task.dependency_id,
            f"install_{status}",
            "succeeded" if status == "succeeded" else "failed",
            task_id=task.task_id,
            error_code=error_code,
        )
        return result

    def _run_command(
        self,
        argv: Sequence[str],
        *,
        timeout: int,
        cancellation_event: threading.Event,
        output_sink: Callable[[str], None],
    ) -> CommandResult:
        environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in _ALLOWED_ENVIRONMENT_KEYS
        }
        command = _resolve_command_argv(argv, environment)
        try:
            process = subprocess.Popen(
                command,
                cwd=self.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
        except OSError as error:
            return CommandResult(127, _sanitize_output(str(error)))

        captured: list[str] = []
        captured_size = 0

        def read_output() -> None:
            nonlocal captured_size
            if process.stdout is None:
                return
            for line in iter(process.stdout.readline, ""):
                sanitized = _sanitize_output(line)
                if not sanitized:
                    continue
                output_sink(sanitized)
                encoded_size = len(sanitized.encode("utf-8", errors="replace"))
                if captured_size < MAX_COMMAND_OUTPUT_BYTES:
                    captured.append(sanitized)
                    captured_size += encoded_size

        reader = threading.Thread(target=read_output, name="kirara-dependency-output", daemon=True)
        reader.start()
        deadline = time.monotonic() + max(1, timeout)
        timed_out = False
        cancelled = False
        while process.poll() is None:
            if cancellation_event.wait(0.1):
                cancelled = True
                self._terminate_process(process)
                break
            if time.monotonic() >= deadline:
                timed_out = True
                self._terminate_process(process)
                break
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        reader.join(timeout=2)
        output = _bounded_bytes("".join(captured), MAX_COMMAND_OUTPUT_BYTES)
        return CommandResult(
            exit_code=process.returncode,
            output=output,
            timed_out=timed_out,
            cancelled=cancelled,
        )

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

    def _load_registry(self) -> dict[str, dict[str, Any]]:
        loaded: Mapping[str, Any] = {}
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if payload.get("version") == REGISTRY_FORMAT_VERSION and isinstance(payload.get("dependencies"), dict):
                loaded = payload["dependencies"]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
            loaded = {}
        registry: dict[str, dict[str, Any]] = {}
        for dependency_id in self._definitions:
            value = loaded.get(dependency_id, {})
            registry[dependency_id] = {
                "dependency_id": dependency_id,
                "status": str(value.get("status", "unknown")) if isinstance(value, Mapping) else "unknown",
                "ready": value.get("ready") is True if isinstance(value, Mapping) else False,
                "version": _optional_string(value.get("version")) if isinstance(value, Mapping) else None,
                "summary": _sanitize_output(str(value.get("summary") or "")) if isinstance(value, Mapping) else None,
                "checked_at": _optional_string(value.get("checked_at")) if isinstance(value, Mapping) else None,
                "last_task_id": _optional_string(value.get("last_task_id")) if isinstance(value, Mapping) else None,
            }
        return registry

    def _load_tasks(self) -> dict[str, DependencyInstallTask]:
        try:
            payload = json.loads(self.tasks_path.read_text(encoding="utf-8"))
            if payload.get("version") != TASKS_FORMAT_VERSION or not isinstance(payload.get("tasks"), list):
                return {}
            tasks = [DependencyInstallTask.from_dict(item) for item in payload["tasks"] if isinstance(item, Mapping)]
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError, KeyError):
            return {}
        return {
            task.task_id: task
            for task in tasks
            if task.dependency_id in self._definitions and task.task_id.startswith("dep-")
        }

    def _recover_interrupted_tasks(self) -> None:
        changed = False
        for task in self._tasks.values():
            if task.status not in {"queued", "running"}:
                continue
            task.status = "failed"
            task.finished_at = _timestamp()
            task.error_code = "service_restarted"
            task.error_summary = "dependency installation was interrupted by a service restart"
            changed = True
        if changed:
            self._persist_tasks()

    def _persist_registry(self) -> None:
        self._atomic_json(
            self.registry_path,
            {"version": REGISTRY_FORMAT_VERSION, "dependencies": self._registry},
        )

    def _persist_tasks(self) -> None:
        self._atomic_json(
            self.tasks_path,
            {
                "version": TASKS_FORMAT_VERSION,
                "tasks": [task.to_dict() for task in self._tasks.values()],
            },
        )

    @staticmethod
    def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
        temporary = path.with_suffix(f"{path.suffix}.{uuid.uuid4().hex}.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as output:
                json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

    def _append_task_log(self, task_id: str, value: str) -> None:
        path = self.logs_path / f"{task_id}.log"
        sanitized = _sanitize_output(value)
        if not sanitized:
            return
        try:
            existing = path.stat().st_size if path.exists() else 0
            remaining = MAX_COMMAND_OUTPUT_BYTES - existing
            if remaining <= 0:
                return
            encoded = sanitized.encode("utf-8", errors="replace")[:remaining]
            with path.open("ab") as output:
                output.write(encoded)
        except OSError:
            pass

    def _audit(
        self,
        dependency_id: str,
        operation: str,
        result: str,
        *,
        task_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        event = {
            "dependency_id": dependency_id,
            "operation": operation,
            "result": result,
            "task_id": task_id,
            "error_code": error_code,
            "timestamp": _timestamp(),
        }
        try:
            with self.audit_path.open("a", encoding="utf-8", newline="\n") as output:
                output.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                output.flush()
                os.fsync(output.fileno())
        except OSError:
            pass


def _timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_command_argv(
    argv: Sequence[str], environment: Mapping[str, str]
) -> list[str]:
    command = [str(argument) for argument in argv]
    if not command:
        return command
    executable = shutil.which(command[0], path=environment.get("PATH"))
    if executable is not None:
        command[0] = executable
    return command


def _public_probe_result(
    definition: DependencyDefinition, output: str
) -> tuple[str | None, str]:
    if definition.dependency_id != "agent-browser-browser":
        version = output.splitlines()[0] if output else None
        return version, output or "dependency is ready"

    try:
        payload = json.loads(output)
    except (json.JSONDecodeError, TypeError):
        return None, "Agent Browser browser runtime is ready"

    checks = payload.get("checks") if isinstance(payload, Mapping) else None
    summary = payload.get("summary") if isinstance(payload, Mapping) else None
    version = None
    if isinstance(checks, list):
        for check in checks:
            if not isinstance(check, Mapping) or check.get("id") != "chrome.installed":
                continue
            match = re.search(r"\b\d+(?:\.\d+){1,3}\b", str(check.get("message") or ""))
            if match:
                version = match.group(0)
            break

    if isinstance(summary, Mapping):
        passed = _safe_count(summary.get("pass"))
        warned = _safe_count(summary.get("warn"))
        failed = _safe_count(summary.get("fail"))
        return version, (
            "Agent Browser browser checks completed: "
            f"{passed} passed, {warned} warnings, {failed} failed"
        )
    return version, "Agent Browser browser runtime is ready"


def _safe_count(value: Any) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _bounded_text(value: str, limit: int = MAX_PUBLIC_OUTPUT_CHARS) -> str:
    return value[-limit:]


def _bounded_bytes(value: str, limit: int) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value
    return encoded[-limit:].decode("utf-8", errors="replace")


def _sanitize_output(value: str) -> str:
    sanitized = _INLINE_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[redacted]", value)
    sanitized = _BEARER_PATTERN.sub("Bearer [redacted]", sanitized)
    sanitized = _WINDOWS_PATH_PATTERN.sub("<host-path>", sanitized)
    sanitized = _POSIX_PATH_PATTERN.sub("<host-path>", sanitized)
    return _bounded_text(sanitized, MAX_COMMAND_OUTPUT_BYTES)


__all__ = [
    "CommandResult",
    "DependencyDefinition",
    "DependencyInstallConfirmationRequired",
    "DependencyInstallTask",
    "DependencyInstallUnsupported",
    "DependencyNotFoundError",
    "DependencyProbe",
    "DependencyTaskStateError",
    "SystemDependencyError",
    "SystemDependencyService",
]
