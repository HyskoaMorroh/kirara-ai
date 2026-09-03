from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import shutil
import stat
import tempfile
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit, urlunsplit

from packaging.version import InvalidVersion, Version

from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.workflow.core.execution.executor import WorkflowExecutor
from kirara_ai.workflow.core.workflow.base import Workflow

from .resource_models import (
    RESOURCE_PERMISSIONS,
    RESOURCE_TYPES,
    ResourceFile,
    ResourceManifest,
)


REGISTRY_FORMAT_VERSION = 2
LEGACY_REGISTRY_FORMAT_VERSION = 1
MAX_ARCHIVE_SIZE_BYTES = 64 * 1024 * 1024
MAX_UNCOMPRESSED_SIZE_BYTES = 128 * 1024 * 1024
MAX_MEMBER_SIZE_BYTES = 32 * 1024 * 1024
MAX_MEMBER_COUNT = 4096
MAX_MANIFEST_SIZE_BYTES = 1024 * 1024
MAX_AUDIT_PAGE_SIZE = 200

_ID_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,126}[A-Za-z0-9])?$")
_SEMVER_PATTERN = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SCRIPT_SUFFIXES = frozenset(
    {".bat", ".cmd", ".com", ".exe", ".js", ".msi", ".ps1", ".py", ".sh", ".vbs"}
)


#: 正文就是全部内容的资源类型——它们可以从一段纯文本创建。
#:
#: `skill` 与 `hook` 不在这里：前者的正文是给模型的行为说明（会被当作可执行的
#: 操作步骤照做），后者是命令声明、能起进程（`process.execute`）。
#: 那两类必须继续走「打包 + 审阅 + 显式确认」，一个纯文本输入框不是合适的入口。
#: `mcp` 同理——它的正文是服务器连接声明，启用即拉进程。
TEXT_AUTHORED_TYPES = frozenset({"prompt", "memory", "session"})

#: 从纯文本创建的资源的入口文件名，按类型区分。
#:
#: 与内置目录里同类条目保持一致（`prompt:office-research` 用 `PROMPT.md`），
#: 这样一条自建提示词与一条内置提示词在磁盘上是同一种形状。
_TEXT_ENTRY_NAMES = {
    "prompt": "PROMPT.md",
    "memory": "MEMORY.md",
    "session": "SESSION.md",
}

#: 只用于显示与检索的元数据键——可以在不改版本、不重新解包的情况下修补。
#:
#: 与 `source_metadata` 里其余的键有本质区别：`owner` / `repository` / `branch` /
#: `directory` / `catalog_id` 决定「去哪里取下一版」，改它们等于改更新来源；
#: 而这两个键只影响列表上显示什么字、搜索能不能命中。
#: 因此修补入口（`set_display_metadata`）按构造只接受这两个，
#: 而不是提供一个能改整个 `source_metadata` 的通用写口。
DISPLAY_METADATA_KEYS = ("name", "description")

#: 「这台机器怎么跑它」——可以在不改版本、不重新解包的情况下覆盖的 MCP 传输键。
#:
#: 与归档里 `server.json` 的其余字段有本质区别：`command` / `type` / `url` / `id`
#: 决定**跑的是哪个程序、连的是哪台服务器**，它们由 `content_sha256` 护着，
#: 是「目录发布了什么」；而这几个键是「这台机器允许什么、放在哪、等多久」。
#:
#: 因此覆盖入口（`set_runtime_overrides`）按构造只接受这几个，
#: 而不是提供一个能改整份传输声明的通用写口——放开 `command` 等于让
#: 「配一个可读目录」这个操作可以把 `npx` 换成任意程序。
#:
#: `extra_args` 是**追加**到归档 args 之后而不是替换：`mcp:filesystem` 的描述
#: 说的就是「在 args 末尾追加允许访问的目录」，而追加也让上游后续给 base args
#: 加的新参数继续生效，并把包名留在摘要保护的那一段里。
RUNTIME_OVERRIDE_KEYS = (
    "extra_args",
    "env",
    "headers",
    "cwd",
    "roots",
    "startup_timeout_ms",
)

#: `MCPTransportConfig.startup_timeout_ms` 的取值范围，与那个模型逐字一致。
#:
#: 在写入这一刻校验，而不是等 pydantic 在下次启动时炸：越界值放过去之后，
#: 整条资源无法启动，而报错指向 pydantic 校验，与用户「我改了个超时」看不出关系。
_STARTUP_TIMEOUT_BOUNDS = (1_000, 600_000)



class ResourceLifecycleError(RuntimeError):
    """Base error for resource validation and state transitions."""


class ResourceValidationError(ResourceLifecycleError):
    """The archive or requested version violates the resource contract."""


class ResourceStateError(ResourceLifecycleError):
    """The requested lifecycle transition is not currently allowed."""


class ResourceLifecycleService:
    """Install and run workflow-backed resources behind one controlled boundary."""

    def __init__(
        self,
        data_path: str | Path,
        *,
        workflow_registry: Any | None = None,
        container: DependencyContainer | None = None,
    ) -> None:
        self.data_path = Path(data_path).resolve()
        self.resource_path = self.data_path / "resources"
        self.installed_path = self.resource_path / "installed"
        self.staging_path = self.resource_path / ".staging"
        self.registry_path = self.resource_path / "registry.json"
        self.audit_path = self.resource_path / "audit.jsonl"
        self.imports_path = self.resource_path / "imports"
        self.workflow_registry = workflow_registry
        self.container = container
        self._lock = threading.RLock()

        self.installed_path.mkdir(parents=True, exist_ok=True)
        (self.resource_path / "backups").mkdir(parents=True, exist_ok=True)
        self.imports_path.mkdir(parents=True, exist_ok=True)
        self.staging_path.mkdir(parents=True, exist_ok=True)
        self._registry = self._load_registry()

    def get_storage_status(self) -> dict[str, Any]:
        """Expose the persistent resource contract without leaking host paths."""

        with self._lock:
            writable = os.access(self.resource_path, os.W_OK)
        return {
            "mode": "server_managed",
            "data_root": ".",
            "resource_root": "resources",
            "install_root": "resources/installed",
            "backup_root": "resources/backups",
            "writable": writable,
            "versioned": True,
        }

    def list_resources(self, resource_type: str | None = None) -> list[dict[str, Any]]:
        if resource_type is not None and resource_type not in RESOURCE_TYPES:
            raise ResourceValidationError("resource type is not supported")
        with self._lock:
            resources = [
                self._snapshot(resource)
                for resource in self._registry["resources"].values()
                if resource_type is None or resource["type"] == resource_type
            ]
        return sorted(resources, key=lambda resource: resource["resource_id"])

    def search_resources(
        self, keyword: str, *, resource_type: str | None = None
    ) -> list[dict[str, Any]]:
        """按关键词过滤已安装资源，**在服务器侧读正文**。

        存在的理由：提示词这个类型的全部内容就是正文，而名称与描述都是用户随手
        填的一行字。装了十几条之后，「哪一条里写了『先给结论』」只能靠逐条点开看，
        而那正是搜索框存在的理由。

        为什么不让 `list_resources()` 顺带返回正文（那样前端就能自己过滤）：

        - `read_entry` 每次读取都重新校验摘要（读清单、读文件、算 SHA-256）。
          对每条资源都做一遍等于把一次列表请求变成 N 次全文件哈希。
        - 正文可能有几十 KB，几十条就是一次几 MB 的响应，其中绝大部分与当次
          搜索无关。
        - 提示词正文会包含用户写进去的规则。无条件塞进每一次列表响应，
          等于让一个只想看清单的请求把全部正文都取回浏览器。

        因此这里读正文、但**只返回元数据**：搜索是为了缩小清单，不是取回内容。

        只对不含可执行内容的类型读正文（`TEXT_AUTHORED_TYPES`）。skill 与 hook 的
        正文是行为声明，把它们并进关键词搜索会让一次搜索读遍所有 hook 命令行——
        既慢，也把「找一条提示词」变成一次对全部可执行声明的全文检索。
        它们仍然可以按 ID / 名称 / 描述命中。

        读正文失败（文件被篡改、摘要不匹配）时**跳过正文这一面**而不是抛错：
        一条坏资源不该让「列出资源」这个动作不可用，那时用户既看不到清单，
        也无从知道是哪一条坏了。
        """

        needle = str(keyword or "").strip().casefold()
        resources = self.list_resources(resource_type)
        if not needle:
            # 「没在搜」不等于「搜不到」。
            return resources

        matched: list[dict[str, Any]] = []
        for resource in resources:
            haystack = [
                str(resource.get("resource_id") or ""),
                str(resource.get("name") or ""),
                str(resource.get("description") or ""),
            ]
            if resource.get("type") in TEXT_AUTHORED_TYPES:
                try:
                    haystack.append(
                        self.read_entry(
                            resource["resource_id"], resource.get("current_version")
                        )
                    )
                except Exception:  # noqa: BLE001 - 坏资源只丢正文这一面，不影响列表
                    pass
            if any(needle in value.casefold() for value in haystack):
                matched.append(resource)
        return matched

    def get_resource(self, resource_id: str) -> dict[str, Any]:
        with self._lock:
            return self._snapshot(self._require_resource(resource_id))

    def set_display_metadata(
        self,
        resource_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """修补一条已安装资源的显示名与描述，**不动版本、不动摘要、不重新解包**。

        这两个键只影响列表上显示什么字、搜索能不能命中，不参与
        `content_sha256`（摘要只由 `files` 算出），也不决定「去哪里取下一版」。
        因此修补它们既不需要抬版本号，也不需要重新走一遍安装：要求为了补一行
        显示名而给资源升一个版本，会在版本列表里留下一条与内容无关的记录，
        并触发一次多余的备份。

        按构造只接受 `DISPLAY_METADATA_KEYS` 里的两个键，而不是开一个能改整个
        `source_metadata` 的通用写口：`owner` / `repository` / `branch` /
        `directory` / `catalog_id` 决定更新来源，改它们等于把这条资源指向
        另一个上游，那是安装路径的权限，不该从「改个显示名」这个入口漏出去。

        传 `None` 表示不动那一项；传空白字符串表示清掉它——
        「没提供」与「明确清空」是两件事，用同一个值表达会让清空变得做不到。
        """

        with self._lock:
            resource = self._require_resource(resource_id)
            metadata = resource.get("source_metadata")
            metadata = copy.deepcopy(metadata) if isinstance(metadata, dict) else {}
            limits = {"name": 200, "description": 1000}
            changed = False
            for key, value in (("name", name), ("description", description)):
                if value is None:
                    continue
                cleaned = str(value).strip()[: limits[key]]
                if cleaned:
                    if metadata.get(key) != cleaned:
                        metadata[key] = cleaned
                        changed = True
                elif key in metadata:
                    del metadata[key]
                    changed = True
            if not changed:
                return self._snapshot(resource)
            return self._update_state(
                resource_id, {"source_metadata": metadata}, "set_display_metadata"
            )

    def set_runtime_overrides(
        self,
        resource_id: str,
        *,
        extra_args: Iterable[str] | None = None,
        env: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        cwd: str | None = None,
        roots: Iterable[str] | None = None,
        startup_timeout_ms: int | None = None,
    ) -> dict[str, Any]:
        """记录一条受管 MCP 资源在**这台机器**上怎么跑，不动版本、不动摘要。

        存在的理由：受管 MCP 资源住在资源注册表里，而唯一的编辑入口
        `PUT /mcp/servers/<id>` 只在 `config.mcp.servers` 里查找，因此它对任何
        受管服务器都返回 404。最明显的一条是 `mcp:filesystem`——它的描述亲口要求
        「启用前必须在 args 末尾追加允许访问的目录」，而装完之后没有任何入口能追加。

        为什么不改归档里的 `server.json`：那份声明有 `content_sha256` 护着，
        它是「目录发布了什么」；一个本机目录白名单是「这台机器允许什么」。
        混在一起会让每配一个目录都变成一次版本递增 + 一次备份，
        并且升级时用本机路径覆盖上游的新声明。

        可覆盖的键按构造只有 `RUNTIME_OVERRIDE_KEYS`：`command` / `type` / `url` /
        `id` 不在其中，那是摘要保护的身份——放开它们等于让「配一个可读目录」
        可以把 `npx` 换成任意程序、把本地 stdio 换成一个远端地址。

        传 `None` 表示不动那一项；传空集合/空串表示清掉它——
        「没提供」与「明确清空」是两件事，用同一个值表达会让清空做不到。
        """

        with self._lock:
            resource = self._require_resource(resource_id)
            if resource.get("type") != "mcp":
                raise ResourceValidationError(
                    "runtime overrides only apply to MCP resources"
                )
            current = resource.get("runtime_overrides")
            overrides = copy.deepcopy(current) if isinstance(current, dict) else {}
            changed = False

            for key, value in (("extra_args", extra_args), ("roots", roots)):
                if value is None:
                    continue
                cleaned = [
                    str(item).strip()
                    for item in value
                    if isinstance(item, (str, int, float)) and str(item).strip()
                ]
                if cleaned:
                    if overrides.get(key) != cleaned:
                        overrides[key] = cleaned
                        changed = True
                elif key in overrides:
                    del overrides[key]
                    changed = True

            # `env` / `headers` 按键合并：上游后续新增的默认值仍然生效，
            # 而整片替换会让「补一个变量」把其余变量一起清掉。
            for key, value in (("env", env), ("headers", headers)):
                if value is None:
                    continue
                if not isinstance(value, Mapping):
                    raise ResourceValidationError(f"{key} override must be a mapping")
                merged = dict(overrides.get(key) or {})
                for name, item in value.items():
                    text = str(name).strip()
                    if not text:
                        continue
                    if item is None or not str(item):
                        merged.pop(text, None)
                        continue
                    merged[text] = str(item)
                if merged:
                    if overrides.get(key) != merged:
                        overrides[key] = merged
                        changed = True
                elif key in overrides:
                    del overrides[key]
                    changed = True

            if cwd is not None:
                text = str(cwd).strip()
                if text:
                    if overrides.get("cwd") != text:
                        overrides["cwd"] = text
                        changed = True
                elif "cwd" in overrides:
                    del overrides["cwd"]
                    changed = True

            if startup_timeout_ms is not None:
                # `bool` 是 `int` 的子类，`True` 会被当成 1 通过下界检查。
                if isinstance(startup_timeout_ms, bool) or not isinstance(
                    startup_timeout_ms, int
                ):
                    raise ResourceValidationError(
                        "startup_timeout_ms override must be an integer"
                    )
                low, high = _STARTUP_TIMEOUT_BOUNDS
                if not low <= startup_timeout_ms <= high:
                    raise ResourceValidationError(
                        f"startup_timeout_ms override must be between {low} and {high}"
                    )
                if overrides.get("startup_timeout_ms") != startup_timeout_ms:
                    overrides["startup_timeout_ms"] = startup_timeout_ms
                    changed = True

            if not changed:
                return self._snapshot(resource)
            return self._update_state(
                resource_id,
                {"runtime_overrides": overrides},
                "set_runtime_overrides",
            )

    def _snapshot(self, resource: Mapping[str, Any]) -> dict[str, Any]:
        """把一条注册表记录复制出来，并把显示名与描述提到顶层。

        为什么要投影而不是在安装时把它们**存**成两个顶层字段：注册表里已经有
        一份（`source_metadata.name`），再存一份就有两份可以各自漂移，
        而漂移之后没有任何症状——界面显示旧名字，更新检查用新名字，
        两边都「有值」。这里从同一份数据派生，因此不可能不一致。

        投影而不是让每个调用方自己去 `source_metadata` 里掏，是因为掏这一下
        每处都得重写：前端过滤谓词、搜索、列表渲染、详情面板。
        此前正是如此——`resourceFilter.ts` 读 `resource.name`、
        搜索框写着「搜索名称、ID 或描述」，而记录里从来没有这个字段，
        于是三个匹配面里有两个从未命中过任何东西，且类型检查发现不了
        （那两个字段在谓词的入参类型里是可选的）。

        顶层已有非空值时不覆盖：将来若有资源类型真的把名称存成顶层字段，
        它说的话优先，这里只补空缺。
        """

        record = copy.deepcopy(dict(resource))
        metadata = record.get("source_metadata")
        for key in DISPLAY_METADATA_KEYS:
            existing = record.get(key)
            if isinstance(existing, str) and existing.strip():
                continue
            value = metadata.get(key) if isinstance(metadata, Mapping) else None
            record[key] = value.strip() if isinstance(value, str) and value.strip() else None
        return record

    @staticmethod
    def _with_display_metadata(
        base: Mapping[str, Any] | None, *display_sources: Mapping[str, Any] | None
    ) -> dict[str, Any] | None:
        """以 `base` 作为来源元数据，显示名按 `display_sources` 的先后取第一个非空。

        为什么需要把这两类键分开处理：`source_metadata` 在升级与回滚时被**整体
        替换**，那对 `owner` / `repository` / `branch` / `directory` / `catalog_id`
        是对的——它们说的是「这一版从哪里来、下一版去哪里取」，换了版本就该跟着换。

        但显示名与描述不是来源，它们是用户对这条资源的称呼。整体替换会让
        「我把它改叫『办公助手』」在下一次升级或回滚时静默消失：新清单往往压根不
        声明名称（手工打包的 ZIP、`author_document_version(name=None)` 都不写），
        于是名字变成 `None`、列表回落到显示 ID，而用户会以为是自己的重命名没保存上。

        优先级由调用方按**哪一份更新**来定，两种情形相反：

        - 升级（`update_archive`）：新清单是上游这一版的说法，它明确给了就用它，
          没给才沿用旧的；
        - 回滚（`restore_version` / `restore_backup`）：那份存档记录的是**当时**的
          叫法，比用户之后的重命名更旧。回滚的是内容，不该顺带撤销重命名，
          所以现存记录优先，存档只用来补空缺。

        任何一份都没有这个键时把它从结果里去掉，而不是留下 `base` 里的旧值——
        否则「清空名称」这个动作会在下一次升级时被悄悄撤销。
        """

        result = dict(base) if isinstance(base, Mapping) else {}
        for key in DISPLAY_METADATA_KEYS:
            chosen: str | None = None
            for source in display_sources:
                if not isinstance(source, Mapping):
                    continue
                value = source.get(key)
                if isinstance(value, str) and value.strip():
                    chosen = value.strip()
                    break
            if chosen is None:
                result.pop(key, None)
            else:
                result[key] = chosen
        # 结果为空时保持 `None`，与「有一个空字典」区分开：
        # `None` 的含义是「这条资源没有来源元数据」。
        if not result:
            return None if base is None else dict(base or {})
        return result

    def resolve_binding(
        self,
        resource_id: str,
        resource_type: str,
        *,
        version: str | None = None,
        enabled: bool = True,
        version_policy: str = "fixed",
    ) -> Any:
        """Build a trusted runtime binding from the server registry.

        The WebUI only submits an ID, type and relationship state.  Version,
        digest, source and permissions are always read from the registered
        manifest so an Agent registry cannot be upgraded by client-supplied
        metadata.
        """

        if resource_type not in RESOURCE_TYPES:
            raise ResourceValidationError("resource type is not supported")
        normalized_policy = str(version_policy).strip().lower()
        if normalized_policy not in {"fixed", "current"}:
            raise ResourceValidationError("resource version policy must be fixed or current")
        with self._lock:
            resource = copy.deepcopy(self._require_resource(resource_id))
            if resource["type"] != resource_type:
                raise ResourceValidationError("resource type does not match the registry")
            if enabled and not resource.get("enabled"):
                raise ResourceStateError("resource must be globally enabled before binding")
            selected_version = version or resource["current_version"]
            version_record = next(
                (item for item in resource["versions"] if item["version"] == selected_version),
                None,
            )
            if version_record is None:
                raise ResourceValidationError("resource version is not registered")
            self._load_registered_manifest(resource_id, selected_version, version_record)

        from kirara_ai.agent_runtime.core import ResourceBinding

        return ResourceBinding(
            resource_id=resource_id,
            resource_type=resource_type,
            version=selected_version,
            content_sha256=version_record["content_sha256"],
            enabled=enabled,
            permissions=tuple(version_record.get("permissions", ())),
            source=version_record.get("source", resource.get("source", "local")),
            version_policy=normalized_policy,
        )

    def read_entry(self, resource_id: str, version: str | None = None) -> str:
        """Read the enabled resource entry from one fixed registered version.

        The version is resolved while holding the registry lock and the file is
        validated against the version manifest before it is returned.  A
        running Agent can therefore pin a version without observing a later
        resource update or a tampered installed file.
        """

        with self._lock:
            resource = copy.deepcopy(self._require_resource(resource_id))
        selected_version = version or resource["current_version"]
        version_record = next(
            (item for item in resource["versions"] if item["version"] == selected_version),
            None,
        )
        if version_record is None:
            raise ResourceValidationError("resource version is not registered")

        version_path = self._version_path(resource_id, selected_version)
        manifest_path = version_path / "manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ResourceValidationError("registered resource manifest is unavailable")
        try:
            manifest = self._parse_manifest(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResourceValidationError("registered resource manifest is invalid") from error
        if (
            manifest.resource_id != resource_id
            or manifest.version != selected_version
            or manifest.content_sha256 != version_record["content_sha256"]
        ):
            raise ResourceValidationError("registered resource manifest does not match its registry")

        entry_path = version_path / PurePosixPath(manifest.entry)
        file_record = next((item for item in manifest.files if item.path == manifest.entry), None)
        if file_record is None or not entry_path.is_file() or entry_path.is_symlink():
            raise ResourceValidationError("registered resource entry is unavailable")
        try:
            data = entry_path.read_bytes()
        except OSError as error:
            raise ResourceValidationError("registered resource entry cannot be read") from error
        if len(data) != file_record.size or hashlib.sha256(data).hexdigest() != file_record.sha256:
            raise ResourceValidationError("registered resource entry digest does not match manifest")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ResourceValidationError("registered resource entry must be UTF-8 text") from error

    def read_entry_metadata(self, resource_id: str, version: str | None = None) -> dict[str, Any]:
        """Return fixed-version entry content together with its verified identity."""

        with self._lock:
            resource = copy.deepcopy(self._require_resource(resource_id))
        selected_version = version or resource["current_version"]
        content = self.read_entry(resource_id, selected_version)
        version_record = next(
            item for item in resource["versions"] if item["version"] == selected_version
        )
        return {
            "resource_id": resource_id,
            "version": selected_version,
            "entry": version_record["entry"],
            "content": content,
            "content_sha256": version_record["content_sha256"],
            "source": version_record["source"],
            "permissions": list(version_record["permissions"]),
        }

    def author_document(
        self,
        *,
        resource_id: str,
        resource_type: str,
        content: str,
        name: str | None = None,
        description: str | None = None,
        version: str = "1.0.0",
    ) -> dict[str, Any]:
        """从一段纯文本创建一个新资源，服务器侧完成打包与摘要。

        存在的理由：提示词这个类型的**全部内容就是正文**——没有可执行文件、
        没有依赖、没有外部来源。而此前唯一的写入路径是上传一个手工打包的 ZIP：
        用户得自己按八个必填字段写 `manifest.json`、按
        `path:size:sha256\\n` 逐行拼接再哈希算出 `content_sha256`。
        要求为一段纯文本走这一遍，等于把这个类型最主要的用法排除在产品之外。

        **不放弃完整性契约。** 这里走的是与目录内置件完全相同的那条路
        （`_install_builtin` 一直在做同样的事）：服务器算摘要、生成清单、打包、
        再交给 `install_archive`。因此落盘后的资源与一条内置提示词逐字节同形，
        `read_entry` 的摘要校验照常生效。给一个能就地改文件的编辑框才是错的——
        那会让资源在下一次载入时直接失败。

        **只对纯文本类型开放**（见 `TEXT_AUTHORED_TYPES`）。`skill` 的正文是给
        模型的行为说明，`hook` 是能起进程的命令声明，`mcp` 启用即拉进程；
        那三类必须继续走打包与显式确认。
        """
        resource_type = str(resource_type or "").strip()
        if resource_type not in TEXT_AUTHORED_TYPES:
            raise ResourceValidationError(
                "only prompt, memory, and session resources can be authored as text"
            )
        resource_id = str(resource_id or "").strip()
        if not _ID_PATTERN.fullmatch(resource_id):
            raise ResourceValidationError("resource ID is invalid")
        version = str(version or "").strip()
        if not _SEMVER_PATTERN.fullmatch(version):
            raise ResourceValidationError("resource version is not semantic versioning")
        body = str(content or "")
        if not body.strip():
            raise ResourceValidationError("resource content must not be empty")

        archive_path = self._build_text_archive(
            resource_id=resource_id,
            resource_type=resource_type,
            version=version,
            body=body,
            name=name,
            description=description,
        )
        try:
            return self.install_archive(archive_path)
        finally:
            archive_path.unlink(missing_ok=True)

    def author_document_version(
        self,
        resource_id: str,
        *,
        content: str,
        version: str,
        name: str | None = None,
        description: str | None = None,
    ) -> dict[str, Any]:
        """按新版本号写入改过的正文。

        改正文只能走版本递增：`content_sha256` 把清单与文件绑在一起，
        就地编辑的后果不是「改了没生效」，而是下一次载入直接失败。
        `update_archive` 负责版本必须递增、自动备份、装完保持停用等待确认。

        类型从**已安装的注册表**读，不接受调用方声明——否则可以先上传一个
        skill 的 ZIP、再用这条纯文本路径改它的正文，绕过打包与审阅。
        """
        existing = self.get_resource(resource_id)
        resource_type = str(existing.get("type") or "")
        if resource_type not in TEXT_AUTHORED_TYPES:
            raise ResourceValidationError(
                "only prompt, memory, and session resources can be authored as text"
            )
        version = str(version or "").strip()
        if not _SEMVER_PATTERN.fullmatch(version):
            raise ResourceValidationError("resource version is not semantic versioning")
        body = str(content or "")
        if not body.strip():
            raise ResourceValidationError("resource content must not be empty")

        metadata = existing.get("source_metadata")
        metadata = metadata if isinstance(metadata, dict) else {}
        archive_path = self._build_text_archive(
            resource_id=resource_id,
            resource_type=resource_type,
            version=version,
            body=body,
            name=name if name is not None else metadata.get("name"),
            description=(
                description if description is not None else metadata.get("description")
            ),
        )
        try:
            return self.update_archive(archive_path, expected_resource_id=resource_id)
        finally:
            archive_path.unlink(missing_ok=True)

    def _build_text_archive(
        self,
        *,
        resource_id: str,
        resource_type: str,
        version: str,
        body: str,
        name: str | None,
        description: str | None,
    ) -> Path:
        """把一段文本打成一个通得过 `_validate_archive` 的归档。

        摘要算法与 `_install_builtin` 逐字节一致（`path:size:sha256\\n` 逐行拼接
        再取 SHA-256）：两条路径产出的包必须同形，否则「自建的」与「内置的」
        在校验、备份、恢复上会出现不同行为，而那种差异只在出问题时才显形。
        """
        entry = _TEXT_ENTRY_NAMES[resource_type]
        data = body.encode("utf-8")
        record = {
            "path": entry,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        content_hash = hashlib.sha256(
            f"{record['path']}:{record['size']}:{record['sha256']}\n".encode("ascii")
        ).hexdigest()
        source_metadata: dict[str, Any] = {"provider": "authored"}
        if name and str(name).strip():
            source_metadata["name"] = str(name).strip()[:200]
        if description and str(description).strip():
            source_metadata["description"] = str(description).strip()[:1000]
        manifest = {
            "resource_id": resource_id,
            "type": resource_type,
            "version": version,
            # 来源标明是用户在本机写的，而不是伪装成某个目录条目：
            # 「这段提示词是谁给的」在排查行为差异时是第一个要回答的问题。
            "source": f"authored://local/{resource_type}/{resource_id}",
            "source_metadata": source_metadata,
            "entry": entry,
            # 一段文本不需要写权限，更不需要进程执行。
            "permissions": ["workflow.read"],
            "files": [record],
            "content_sha256": content_hash,
        }
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            archive.writestr(entry, data)
        payload = buffer.getvalue()
        # 落在受控的 imports 目录里：`install_archive` 会读它，之后由调用方删掉。
        target = self.imports_path / f"authored-{hashlib.sha256(payload).hexdigest()}.zip"
        target.write_bytes(payload)
        return target

    def install_archive(self, archive_path: str | Path) -> dict[str, Any]:
        archive_path = Path(archive_path)
        manifest: ResourceManifest | None = None
        try:
            with self._lock:
                manifest = self._validate_archive(archive_path)
                if manifest.resource_id in self._registry["resources"]:
                    raise ResourceValidationError("resource ID is already installed")

                staged_version = self._extract_to_staging(archive_path, manifest)
                final_version = self._version_path(manifest.resource_id, manifest.version)
                if final_version.exists() or final_version.is_symlink():
                    self._remove_path(staged_version)
                    raise ResourceValidationError("resource version already exists")

                final_version.parent.mkdir(parents=True, exist_ok=True)
                next_registry = copy.deepcopy(self._registry)
                installed_at = self._timestamp()
                version_record = self._version_record(manifest, installed_at)
                resource = {
                    "resource_id": manifest.resource_id,
                    "type": manifest.type,
                    "current_version": manifest.version,
                    "source": self._sanitize_source(manifest.source),
                    "source_key": manifest.source_key,
                    "source_metadata": copy.deepcopy(manifest.source_metadata),
                    "entry": manifest.entry,
                    "permissions": list(manifest.permissions),
                    "content_sha256": manifest.content_sha256,
                    "enabled": False,
                    "confirmation_required": True,
                    "workflow_id": None,
                    "installed_at": installed_at,
                    "updated_at": installed_at,
                    "versions": [version_record],
                }
                next_registry["resources"][manifest.resource_id] = resource

                os.replace(staged_version, final_version)
                try:
                    self._write_registry(next_registry)
                except Exception:
                    self._remove_path(final_version)
                    raise
                finally:
                    self._remove_path(staged_version.parents[1])
                self._registry = next_registry
                self._audit(manifest, "install", "success")
                return self._snapshot(resource)
        except Exception as error:
            self._audit(manifest, "install", "failure", error)
            raise

    def update_archive(
        self,
        archive_path: str | Path,
        *,
        expected_resource_id: str | None = None,
    ) -> dict[str, Any]:
        archive_path = Path(archive_path)
        manifest: ResourceManifest | None = None
        try:
            with self._lock:
                manifest = self._validate_archive(archive_path)
                if (
                    expected_resource_id is not None
                    and manifest.resource_id != expected_resource_id
                ):
                    raise ResourceValidationError(
                        "resource archive ID does not match the requested resource"
                    )
                current = self._require_resource(manifest.resource_id)
                if current["type"] != manifest.type:
                    raise ResourceValidationError("resource type cannot change")
                if Version(manifest.version) <= Version(current["current_version"]):
                    raise ResourceValidationError("resource version must increase")
                if any(
                    version["version"] == manifest.version
                    for version in current["versions"]
                ):
                    raise ResourceValidationError("resource version is already registered")

                self._backup_version(
                    manifest.resource_id,
                    current["current_version"],
                    reason="before-update",
                )

                staged_version = self._extract_to_staging(archive_path, manifest)
                final_version = self._version_path(manifest.resource_id, manifest.version)
                if final_version.exists() or final_version.is_symlink():
                    self._remove_path(staged_version)
                    raise ResourceValidationError("resource version already exists")

                final_version.parent.mkdir(parents=True, exist_ok=True)
                next_registry = copy.deepcopy(self._registry)
                next_resource = next_registry["resources"][manifest.resource_id]
                permissions_changed = set(next_resource["permissions"]) != set(
                    manifest.permissions
                )
                updated_at = self._timestamp()
                next_resource.update(
                    {
                        "current_version": manifest.version,
                        "source": self._sanitize_source(manifest.source),
                        "source_key": manifest.source_key,
                        # 来源整体换成新清单的，但用户改过的显示名带过去——
                        # 手工打包的 ZIP 往往不声明名称，整体替换会让一次升级
                        # 静默丢掉用户的重命名，名字回落成 ID。
                        "source_metadata": self._with_display_metadata(
                            manifest.source_metadata,
                            manifest.source_metadata,
                            current.get("source_metadata"),
                        ),
                        "entry": manifest.entry,
                        "permissions": list(manifest.permissions),
                        "content_sha256": manifest.content_sha256,
                        "updated_at": updated_at,
                    }
                )
                next_resource["versions"].append(
                    self._version_record(manifest, updated_at)
                )
                if permissions_changed:
                    next_resource["enabled"] = False
                    next_resource["confirmation_required"] = True

                os.replace(staged_version, final_version)
                try:
                    self._write_registry(next_registry)
                except Exception:
                    self._remove_path(final_version)
                    raise
                finally:
                    self._remove_path(staged_version.parents[1])
                self._registry = next_registry
                self._audit(manifest, "update", "success")
                return self._snapshot(next_resource)
        except Exception as error:
            self._audit(manifest, "update", "failure", error)
            raise

    def enable(self, resource_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        with self._lock:
            current = self._require_resource(resource_id)
            if current["enabled"] and not current["confirmation_required"]:
                return self._snapshot(current)
            if not confirmed:
                raise ResourceStateError("explicit confirmation is required")
            return self._update_state(
                resource_id,
                {"enabled": True, "confirmation_required": False},
                "enable",
            )

    def disable(self, resource_id: str) -> dict[str, Any]:
        with self._lock:
            current = self._require_resource(resource_id)
            if not current["enabled"]:
                return self._snapshot(current)
            return self._update_state(resource_id, {"enabled": False}, "disable")

    def bind_workflow(self, resource_id: str, workflow_id: str) -> dict[str, Any]:
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ResourceValidationError("workflow ID is required")
        with self._lock:
            self._require_resource(resource_id)
            if self.workflow_registry is None or self.container is None:
                raise ResourceStateError("workflow integration is unavailable")
            workflow = self.workflow_registry.get_workflow(workflow_id, self.container)
            if workflow is None:
                raise ResourceStateError("workflow does not exist")
            return self._update_state(
                resource_id, {"workflow_id": workflow_id}, "bind_workflow"
            )

    def restore_version(
        self, resource_id: str, version: str, *, confirmed: bool = False
    ) -> dict[str, Any]:
        """把资源回退到一个已注册且仍在磁盘上的版本。

        这里**不要求**资源绑定了工作流。`workflow_id` 只有绑定了工作流的资源才有，
        而 skill / prompt / hook / mcp / memory 从设计上就没有；曾经的
        「未绑定工作流就拒绝」让这个接口对那五类资源永远返回 409：
        一个被升级搞坏的 Skill 明明还留着上一版目录和备份，却没有任何办法回退，
        而报出来的理由（「没绑定工作流」）与用户正在做的事毫无关系，
        看起来像 bug 而不是限制。

        回退真正需要的前置条件是下面三条，且都在检查：显式确认、目标版本在注册表里、
        那个版本的目录还在磁盘上。
        """
        with self._lock:
            current = self._require_resource(resource_id)
            if not confirmed:
                raise ResourceStateError("explicit confirmation is required")
            version_record = next(
                (
                    item
                    for item in current["versions"]
                    if item["version"] == version
                ),
                None,
            )
            if version_record is None:
                raise ResourceValidationError("resource version is not registered")
            version_path = self._version_path(resource_id, version)
            if not version_path.is_dir() or version_path.is_symlink():
                raise ResourceValidationError("registered resource version is unavailable")

            self._load_registered_manifest(resource_id, version, version_record)
            self._backup_version(resource_id, current["current_version"], reason="before-restore")

            changes = {
                "current_version": version_record["version"],
                "source": version_record["source"],
                "source_key": version_record.get("source_key"),
                # 回滚的是**内容**，不是用户给这条资源起的名字：
                # 旧版本记录里存的是当时的显示名，用它覆盖等于让一次回滚
                # 顺带把重命名也撤销掉，而用户要回退的只是正文。
                "source_metadata": self._with_display_metadata(
                    version_record.get("source_metadata"),
                    current.get("source_metadata"),
                    version_record.get("source_metadata"),
                ),
                "entry": version_record["entry"],
                "permissions": list(version_record["permissions"]),
                "content_sha256": version_record["content_sha256"],
                "enabled": False,
                "confirmation_required": True,
            }
            return self._update_state(resource_id, changes, "restore")

    def remove(self, resource_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        """Remove a resource only after preserving its installed versions."""

        with self._lock:
            current = self._snapshot(self._require_resource(resource_id))
            if not confirmed:
                raise ResourceStateError("explicit confirmation is required")
            self._backup_version(resource_id, current["current_version"], reason="before-remove")
            next_registry = copy.deepcopy(self._registry)
            next_registry["resources"].pop(resource_id, None)
            installed_resource_path = self.installed_path / resource_id
            self._remove_path(installed_resource_path)
            try:
                self._write_registry(next_registry)
            except Exception:
                # The backup remains available for an administrator to restore.
                raise
            self._registry = next_registry
            self._audit_record(current, "remove", "success")
            return current

    async def execute(self, resource_id: str) -> dict[str, Any]:
        with self._lock:
            resource = copy.deepcopy(self._require_resource(resource_id))
        if not resource["enabled"]:
            raise ResourceStateError("resource is disabled")
        workflow_id = resource.get("workflow_id")
        if not workflow_id:
            raise ResourceStateError("resource is not bound to a workflow")
        if self.workflow_registry is None or self.container is None:
            raise ResourceStateError("workflow integration is unavailable")

        try:
            with self.container.scoped() as scoped_container:
                workflow = self.workflow_registry.get_workflow(
                    workflow_id, scoped_container
                )
                if workflow is None:
                    raise ResourceStateError("bound workflow does not exist")
                scoped_container.register(Workflow, workflow)
                executor = WorkflowExecutor(scoped_container)
                scoped_container.register(WorkflowExecutor, executor)
                result = await executor.run()
            self._audit_record(resource, "execute", "success")
            return result
        except Exception as error:
            self._audit_record(resource, "execute", "failure", error)
            raise

    def list_audit(
        self,
        *,
        offset: int = 0,
        limit: int = 50,
        resource_id: str | None = None,
        correlation_id: str | None = None,
        component: str | None = None,
        event: str | None = None,
        operation: str | None = None,
        outcome: str | None = None,
        status: str | None = None,
        agent_id: str | None = None,
        model_id: str | None = None,
        server: str | None = None,
    ) -> dict[str, Any]:
        if offset < 0:
            raise ResourceValidationError("audit offset cannot be negative")
        if limit < 1 or limit > MAX_AUDIT_PAGE_SIZE:
            raise ResourceValidationError("audit limit is outside the allowed range")
        with self._lock:
            records: list[dict[str, Any]] = []
            if self.audit_path.exists():
                with self.audit_path.open("r", encoding="utf-8") as audit_file:
                    for line in audit_file:
                        try:
                            record = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        record["component"] = self._normalize_audit_component(
                            record.get("component")
                        )
                        normalized_outcome = record.get("outcome", record.get("result"))
                        if "outcome" not in record and normalized_outcome is not None:
                            record["outcome"] = normalized_outcome
                        if "result" not in record and normalized_outcome is not None:
                            record["result"] = normalized_outcome
                        if resource_id is not None and record.get("resource_id") != resource_id:
                            continue
                        if correlation_id is not None and record.get("correlation_id") != correlation_id:
                            continue
                        if component is not None and record.get("component") != component:
                            continue
                        if event is not None and record.get("event") != event:
                            continue
                        if operation is not None and record.get("operation") != operation:
                            continue
                        if outcome is not None and normalized_outcome != outcome:
                            continue
                        if status is not None and record.get("status") != status:
                            continue
                        if agent_id is not None and record.get("agent_id") != agent_id:
                            continue
                        if model_id is not None and record.get("model_id") != model_id:
                            continue
                        if server is not None and record.get("server") != server:
                            continue
                        records.append(record)
        records.reverse()
        return {
            "items": records[offset : offset + limit],
            "total": len(records),
            "offset": offset,
            "limit": limit,
        }

    def append_runtime_audit(self, record: Mapping[str, Any]) -> None:
        """Persist a redacted runtime event in the unified audit stream.

        Runtime components may report rich internal objects to their local
        sink.  Only bounded metadata crosses this persistence boundary so a
        Hook, Agent, or MCP implementation cannot accidentally store prompt
        text, tool payloads, credentials, or provider error details.
        """

        if not isinstance(record, Mapping):
            return

        allowed_keys = {
            "component",
            "operation",
            "event",
            "status",
            "outcome",
            "blocked",
            "executed",
            "resource_id",
            "resource_version",
            "resource_sha256",
            # 供应商配置的审计对象是「哪个后端」。没有它，一条
            # `llm_backend / update` 记录只能证明「有人改过某个上游」，
            # 回答不了「改的是哪一个」——那等于没有留痕。
            # 后端名是用户自取的配置标签，不是凭据；凭据本身永远不进这里。
            "backend_name",
            "snapshot_sha256",
            "agent_id",
            "model_id",
            "correlation_id",
            "resource_count",
            "reason_count",
            "iteration",
            "message_count",
            "message_count_before",
            "message_count_after",
            "estimated_chars_before",
            "estimated_chars_after",
            "used_custom_compactor",
            "confirmation_id",
            "session",
            "server",
            "duration_ms",
            "subject_digest",
        }
        sanitized: dict[str, Any] = {}
        for key in allowed_keys:
            if key not in record:
                continue
            value = record[key]
            if key == "session":
                if isinstance(value, Mapping):
                    sanitized[key] = {
                        str(session_key): str(session_value)[:128]
                        for session_key, session_value in value.items()
                        if isinstance(session_value, (str, int, float, bool))
                    }
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                sanitized[key] = str(value)[:256] if isinstance(value, str) else value

        if not sanitized.get("component") or not sanitized.get("operation"):
            return
        sanitized["component"] = self._normalize_audit_component(
            sanitized["component"]
        )
        normalized_outcome = sanitized.get("outcome", sanitized.get("result"))
        if normalized_outcome is not None:
            sanitized["outcome"] = normalized_outcome
            sanitized["result"] = normalized_outcome
        sanitized["timestamp"] = self._timestamp()
        with self._lock:
            self.resource_path.mkdir(parents=True, exist_ok=True)
            try:
                with self.audit_path.open("a", encoding="utf-8", newline="\n") as audit_file:
                    audit_file.write(
                        json.dumps(sanitized, ensure_ascii=False, sort_keys=True) + "\n"
                    )
                    audit_file.flush()
                    os.fsync(audit_file.fileno())
            except OSError:
                # Runtime audit failure must not interrupt a user turn.
                pass

    @staticmethod
    def _normalize_audit_component(value: Any) -> Any:
        if value == "agent_hook_runtime":
            return "agent_hook"
        return value

    def upsert_source_repository(
        self, owner: str, name: str, branch: str, *, enabled: bool
    ) -> dict[str, Any]:
        """Persist one repository coordinate in the server registry.

        `discovered_skills` 初始为 `None`（还没发现过），而不是 0。
        两者必须分开：0 是「发现过、里面一个技能都没有」，也就是「这个仓库配错了」
        唯一的信号；写成 0 会让每个刚注册的仓库看起来都是配错的。

        重新登记同一个坐标时保留已记下的数：改一次启用状态不该把
        「识别到 864 个」清成「还没发现过」。
        """

        with self._lock:
            repositories = copy.deepcopy(self._registry.setdefault("repositories", []))
            previous = next(
                (
                    existing
                    for existing in repositories
                    if (existing.get("owner"), existing.get("name"), existing.get("branch"))
                    == (owner, name, branch)
                ),
                None,
            )
            item = {
                "owner": owner,
                "name": name,
                "branch": branch,
                "enabled": enabled,
                "discovered_skills": (
                    previous.get("discovered_skills") if previous else None
                ),
            }
            repositories = [
                existing
                for existing in repositories
                if (existing.get("owner"), existing.get("name"), existing.get("branch"))
                != (owner, name, branch)
            ]
            repositories.append(item)
            repositories.sort(key=lambda value: (value["owner"], value["name"], value["branch"]))
            next_registry = copy.deepcopy(self._registry)
            next_registry["repositories"] = repositories
            self._write_registry(next_registry)
            self._registry = next_registry
            return copy.deepcopy(item)

    def record_repository_discovery(
        self, owner: str, name: str, branch: str, *, count: int
    ) -> dict[str, Any]:
        """记下一次发现的技能条数。

        存在的理由：注册一个仓库之后，界面上此前完全看不出它有没有用——
        一个 owner/name 拼错、分支写错、或者压根不含 `SKILL.md` 的仓库，
        与一个装着几百个技能的仓库长得一模一样，都只是「已启用」。
        而 `discover_repository()` 本来就会返回逐条清单，数量是它的自然副产品。

        只改这一个坐标的这一个字段：启用状态不动（记数与启用无关，顺带改它
        会让一次只读查询变成一次配置写入），其余仓库不动。
        """

        if isinstance(count, bool) or not isinstance(count, int) or count < 0:
            raise ValueError("discovered skill count must be a non-negative integer")

        with self._lock:
            repositories = copy.deepcopy(self._registry.get("repositories", []))
            for item in repositories:
                if (item.get("owner"), item.get("name"), item.get("branch")) == (
                    owner,
                    name,
                    branch,
                ):
                    item["discovered_skills"] = count
                    next_registry = copy.deepcopy(self._registry)
                    next_registry["repositories"] = repositories
                    self._write_registry(next_registry)
                    self._registry = next_registry
                    return copy.deepcopy(item)
        # 未登记的仓库不能凭一次记数被凭空创建出来：那会让一个拼错的坐标
        # 悄悄变成一条注册记录。
        raise KeyError(f"{owner}/{name}@{branch}")

    def remove_source_repository(
        self, owner: str, name: str, branch: str
    ) -> dict[str, Any]:
        """摘掉一条仓库来源登记。

        为什么「停用」不够：停用表达的是「这个来源暂时不用」，删除表达的是
        「这个来源是错的 / 不再存在」。没有删除时，一个拼错的坐标
        （`anthropcis/skills`）会永久留在 `registry.json` 里——它可以被停用，
        但那条记录再也去不掉，仓库表上永远多一行说明不了任何事的死项，
        想清掉只能登服务器手改 JSON。

        **只摘来源登记，不动已装资源。** 从那个仓库装过的 Skill 已经在服务器上
        独立成包（有自己的清单与摘要）。一起删掉等于把「不再从这里拉新的」
        变成「把装过的都毁掉」，而后者是用户没有要求过的。

        未登记的坐标抛 `KeyError`：静默成功会让一个拼错的删除请求看起来
        和真的删掉一样。
        """

        with self._lock:
            repositories = copy.deepcopy(self._registry.get("repositories", []))
            removed: dict[str, Any] | None = None
            remaining: list[dict[str, Any]] = []
            for item in repositories:
                if (item.get("owner"), item.get("name"), item.get("branch")) == (
                    owner,
                    name,
                    branch,
                ):
                    removed = item
                    continue
                remaining.append(item)
            if removed is None:
                raise KeyError(f"{owner}/{name}@{branch}")
            next_registry = copy.deepcopy(self._registry)
            next_registry["repositories"] = remaining
            self._write_registry(next_registry)
            self._registry = next_registry
            removed.setdefault("discovered_skills", None)
            return copy.deepcopy(removed)

    def list_source_repositories(self) -> list[dict[str, Any]]:
        with self._lock:
            repositories = copy.deepcopy(self._registry.get("repositories", []))
        # 升级前写入的注册表没有这个字段。补 `None`（还没发现过）而不是 0，
        # 也不把它做成必填——后者会让升级之后注册表直接载入失败，
        # 而那时用户手里已经没有可用的仓库清单了。
        for item in repositories:
            item.setdefault("discovered_skills", None)
        return repositories

    def set_source_repository_enabled(
        self, owner: str, name: str, branch: str, enabled: bool
    ) -> dict[str, Any]:
        with self._lock:
            repositories = copy.deepcopy(self._registry.get("repositories", []))
            for item in repositories:
                if (item.get("owner"), item.get("name"), item.get("branch")) == (
                    owner,
                    name,
                    branch,
                ):
                    item["enabled"] = bool(enabled)
                    next_registry = copy.deepcopy(self._registry)
                    next_registry["repositories"] = repositories
                    self._write_registry(next_registry)
                    self._registry = next_registry
                    return copy.deepcopy(item)
        raise ResourceStateError("repository source is not registered")

    def list_backups(self, resource_id: str | None = None) -> list[dict[str, Any]]:
        """List backup metadata without exposing container or host paths."""

        with self._lock:
            root = self.resource_path / "backups"
            if not root.is_dir() or root.is_symlink():
                return []
            resource_dirs = [root / resource_id] if resource_id else list(root.iterdir())
            result: list[dict[str, Any]] = []
            for resource_dir in resource_dirs:
                if not resource_dir.is_dir() or resource_dir.is_symlink():
                    continue
                for backup_dir in resource_dir.iterdir():
                    if not backup_dir.is_dir() or backup_dir.is_symlink():
                        continue
                    metadata_path = backup_dir / "backup.json"
                    try:
                        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                        continue
                    if not self._valid_backup_metadata(metadata, backup_dir.name):
                        continue
                    result.append(
                        {
                            key: copy.deepcopy(metadata[key])
                            for key in (
                                "backup_id",
                                "resource_id",
                                "version",
                                "reason",
                                "created_at",
                                "content_sha256",
                            )
                            if key in metadata
                        }
                    )
            return sorted(result, key=lambda item: item.get("created_at", ""), reverse=True)

    def delete_backup(self, backup_id: str, *, confirmed: bool = True) -> dict[str, Any]:
        with self._lock:
            if not confirmed:
                raise ResourceStateError("explicit confirmation is required")
            path, metadata = self._find_backup(backup_id)
            self._remove_path(path)
            self._audit_record(
                metadata.get("resource", {}), "delete_backup", "success"
            )
            return {
                key: metadata[key]
                for key in ("backup_id", "resource_id", "version", "reason", "created_at")
                if key in metadata
            }

    def restore_backup(self, backup_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        with self._lock:
            if not confirmed:
                raise ResourceStateError("explicit confirmation is required")
            backup_path, metadata = self._find_backup(backup_id)
            resource_snapshot = metadata.get("resource")
            if not isinstance(resource_snapshot, dict):
                raise ResourceValidationError("backup resource metadata is invalid")
            version = metadata.get("version")
            resource_id = metadata.get("resource_id")
            if not isinstance(resource_id, str) or not isinstance(version, str):
                raise ResourceValidationError("backup identity is invalid")
            manifest = self._validate_backup_directory(
                backup_path, metadata, expected_resource_id=resource_id, expected_version=version
            )

            stage_root = self.staging_path / f"restore-{uuid.uuid4().hex}"
            staged_version = stage_root / resource_id / version
            try:
                shutil.copytree(backup_path, staged_version, symlinks=False)
                self._validate_resource_directory(staged_version, manifest, allow_backup=True)
                (staged_version / "backup.json").unlink(missing_ok=True)
                self._validate_resource_directory(staged_version, manifest, allow_backup=False)
                final_version = self._version_path(resource_id, version)
                final_version.parent.mkdir(parents=True, exist_ok=True)
                if final_version.exists() or final_version.is_symlink():
                    self._remove_path(final_version)
                os.replace(staged_version, final_version)
                restored = copy.deepcopy(resource_snapshot)
                # 备份里的记录是**当时**那一份，包括当时的显示名。
                # 恢复的是内容，不该顺带把用户之后的重命名撤销掉——
                # 那条资源仍然在注册表里时，以它现在的叫法为准。
                live = self._registry.get("resources", {}).get(resource_id)
                restored.update(
                    {
                        "resource_id": resource_id,
                        "current_version": version,
                        "enabled": False,
                        "confirmation_required": True,
                        "updated_at": self._timestamp(),
                        "source_metadata": self._with_display_metadata(
                            restored.get("source_metadata"),
                            live.get("source_metadata") if isinstance(live, dict) else None,
                            restored.get("source_metadata"),
                        ),
                    }
                )
                next_registry = copy.deepcopy(self._registry)
                next_registry.setdefault("resources", {})[resource_id] = restored
                try:
                    self._write_registry(next_registry)
                except Exception:
                    self._remove_path(final_version)
                    raise
                self._registry = next_registry
                self._audit_record(restored, "restore_backup", "success")
                return self._snapshot(restored)
            finally:
                self._remove_path(stage_root)

    def import_archive(self, archive_path: str | Path) -> dict[str, Any]:
        """Install an archive staged below ``resources/imports``."""

        archive = Path(archive_path).resolve()
        try:
            archive.relative_to(self.imports_path.resolve())
        except ValueError as error:
            raise ResourceValidationError("import archive must be staged by the server") from error
        return self.install_archive(archive)

    def discover_importable_archives(self) -> list[dict[str, Any]]:
        """List archives already sitting in ``resources/imports``, without installing.

        需求 10 把「导入已有」与「从ZIP安装」并列，而只支持浏览器上传时两者
        在机制上是同一件事。这个方法覆盖的是用户手里**没有可上传文件**的场景：
        运维用 ``scp`` 把一批包放进了服务器，或者包有几十 MB 走浏览器既慢又易断。
        那时他要的是「服务器上已经有的那些，列出来让我选」。

        四条边界：

        - **只扫 ``resources/imports`` 这一层。** 让请求方指定目录等于给出一个
          任意文件系统读取接口；连子目录也不递归，见 `import_discovered_archive`。
        - **只列，不装。** 发现是只读的；安装仍走原有的确认与校验链路。
        - **已装过的包标出来而不是隐藏。** 从列表里消失会让人以为文件没放对，
          于是反复重传同一个包。
        - **坏包只影响自己那一行。** 一个损坏的 ZIP 不该让整份列表打不开，
          那会把一个坏文件放大成功能不可用。

        返回值里只有文件名，没有宿主机路径：路径不该经由接口流出去。
        """
        entries: list[dict[str, Any]] = []
        try:
            candidates = sorted(
                path
                for path in self.imports_path.iterdir()
                if path.is_file()
                and not path.is_symlink()
                and path.suffix.lower() == ".zip"
            )
        except OSError:
            # 目录读不到时返回空列表而不是抛出：一个只读挂载不该让「导入」
            # 这个页面整体打不开。
            return entries

        with self._lock:
            installed = {
                resource_id: record.get("current_version")
                for resource_id, record in self._registry["resources"].items()
            }

        for path in candidates:
            entry: dict[str, Any] = {
                "file_name": path.name,
                "size": None,
                "resource_id": None,
                "type": None,
                "version": None,
                "installed": False,
                "installed_version": None,
                "is_upgrade": False,
                "error": None,
            }
            try:
                entry["size"] = path.stat().st_size
            except OSError:
                pass
            try:
                manifest = self._validate_archive(path)
            except ResourceValidationError as error:
                # 原因如实给出（「不是合法 ZIP」与「清单缺字段」处置不同），
                # 但不带宿主路径。
                entry["error"] = str(error)
                entries.append(entry)
                continue
            except Exception as error:  # noqa: BLE001 - 一个坏包不该打挂整份列表
                entry["error"] = f"读取失败：{error}"
                entries.append(entry)
                continue

            entry["resource_id"] = manifest.resource_id
            entry["type"] = manifest.type
            entry["version"] = manifest.version
            current = installed.get(manifest.resource_id)
            if current is not None:
                entry["installed"] = True
                entry["installed_version"] = current
                # 「已装 1.0.0、盘上有 2.0.0」与「已装 2.0.0」处置不同：
                # 前者点「更新」，后者什么都不用做。
                try:
                    entry["is_upgrade"] = Version(manifest.version) > Version(current)
                except InvalidVersion:
                    entry["is_upgrade"] = False
            entries.append(entry)
        return entries

    def import_discovered_archive(self, file_name: str) -> dict[str, Any]:
        """Install one archive discovered by :meth:`discover_importable_archives`.

        参数是**文件名**，不是路径。只认 ``resources/imports`` 这一层：
        允许子路径就等于把「文件名」悄悄变成「相对路径」，而那要把穿越安全性
        重新论证一遍。分隔符、``..`` 与绝对路径一律直接拒绝，
        而不是先拼接再检查——先拼接的写法只要有一处规范化差异就会漏。
        """
        name = (file_name or "").strip()
        if not name:
            raise ResourceValidationError("import archive name is required")
        if name in {".", ".."} or "/" in name or "\\" in name:
            raise ResourceValidationError("import archive name must not contain a path")
        if Path(name).is_absolute() or Path(name).name != name:
            raise ResourceValidationError("import archive name must not contain a path")
        candidate = self.imports_path / name
        if not candidate.is_file() or candidate.is_symlink():
            raise ResourceValidationError("import archive does not exist")
        return self.import_archive(candidate)

    def _update_state(
        self, resource_id: str, changes: dict[str, Any], operation: str
    ) -> dict[str, Any]:
        next_registry = copy.deepcopy(self._registry)
        resource = next_registry["resources"][resource_id]
        resource.update(changes)
        resource["updated_at"] = self._timestamp()
        try:
            self._write_registry(next_registry)
        except Exception as error:
            self._audit_record(resource, operation, "failure", error)
            raise
        self._registry = next_registry
        self._audit_record(resource, operation, "success")
        return self._snapshot(resource)

    def _load_registry(self) -> dict[str, Any]:
        if not self.registry_path.exists():
            return {
                "format_version": REGISTRY_FORMAT_VERSION,
                "resources": {},
                "repositories": [],
            }
        try:
            payload = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ResourceValidationError("resource registry cannot be read") from error
        if not isinstance(payload, dict) or not isinstance(payload.get("resources"), dict):
            raise ResourceValidationError("resource registry format is invalid")
        version = payload.get("format_version")
        if version == LEGACY_REGISTRY_FORMAT_VERSION:
            payload["format_version"] = REGISTRY_FORMAT_VERSION
            payload.setdefault("repositories", [])
            for resource in payload["resources"].values():
                if not isinstance(resource, dict):
                    continue
                resource.setdefault("source_key", None)
                resource.setdefault("source_metadata", None)
                for record in resource.get("versions", []):
                    if isinstance(record, dict):
                        record.setdefault("source_key", None)
                        record.setdefault("source_metadata", None)
            self._write_registry(payload)
        elif version != REGISTRY_FORMAT_VERSION:
            raise ResourceValidationError("resource registry format is invalid")
        if not isinstance(payload.get("repositories"), list):
            raise ResourceValidationError("resource repository registry is invalid")
        return payload

    def _write_registry(self, registry: dict[str, Any]) -> None:
        self.resource_path.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".registry-", suffix=".tmp", dir=self.resource_path
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
                json.dump(registry, file, ensure_ascii=False, indent=2, sort_keys=True)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary_path, self.registry_path)
        finally:
            temporary_path.unlink(missing_ok=True)

    def _validate_archive(self, archive_path: Path) -> ResourceManifest:
        if not archive_path.is_file() or archive_path.is_symlink():
            raise ResourceValidationError("resource archive does not exist")
        if archive_path.stat().st_size > MAX_ARCHIVE_SIZE_BYTES:
            raise ResourceValidationError("resource archive exceeds the maximum size")
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                members = self._validate_members(archive)
                manifest_info = members.get("manifest.json")
                if manifest_info is None or manifest_info.is_dir():
                    raise ResourceValidationError("manifest.json is required")
                if manifest_info.file_size > MAX_MANIFEST_SIZE_BYTES:
                    raise ResourceValidationError("resource manifest is too large")
                try:
                    payload = json.loads(archive.read(manifest_info).decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise ResourceValidationError("resource manifest is invalid JSON") from error
                manifest = self._parse_manifest(payload)
                expected = {"manifest.json", *(file.path for file in manifest.files)}
                actual = {name for name, info in members.items() if not info.is_dir()}
                if actual != expected:
                    raise ResourceValidationError("archive contains undeclared or missing files")
                self._verify_files(archive, members, manifest)
                return manifest
        except zipfile.BadZipFile as error:
            raise ResourceValidationError("resource archive is not a valid ZIP file") from error

    def _validate_members(
        self, archive: zipfile.ZipFile
    ) -> dict[str, zipfile.ZipInfo]:
        infos = archive.infolist()
        if len(infos) > MAX_MEMBER_COUNT:
            raise ResourceValidationError("resource archive contains too many files")
        members: dict[str, zipfile.ZipInfo] = {}
        folded_names: set[str] = set()
        total_size = 0
        for info in infos:
            name = self._validate_relative_path(info.filename)
            folded = name.casefold()
            if name in members or folded in folded_names:
                raise ResourceValidationError("resource archive contains duplicate paths")
            unix_mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(unix_mode):
                raise ResourceValidationError("resource archive contains a symbolic link")
            if info.file_size < 0 or info.file_size > MAX_MEMBER_SIZE_BYTES:
                raise ResourceValidationError("resource archive member exceeds the size limit")
            total_size += info.file_size
            if total_size > MAX_UNCOMPRESSED_SIZE_BYTES:
                raise ResourceValidationError("resource archive expands beyond the size limit")
            members[name] = info
            folded_names.add(folded)
        return members

    def _parse_manifest(self, payload: Any) -> ResourceManifest:
        required = {
            "resource_id",
            "type",
            "version",
            "source",
            "entry",
            "permissions",
            "files",
            "content_sha256",
        }
        if not isinstance(payload, dict) or any(payload.get(key) is None for key in required):
            raise ResourceValidationError("resource manifest is missing required fields")
        resource_id = payload["resource_id"]
        if not isinstance(resource_id, str) or not _ID_PATTERN.fullmatch(resource_id):
            raise ResourceValidationError("resource ID is invalid")
        resource_type = payload["type"]
        if resource_type not in RESOURCE_TYPES:
            raise ResourceValidationError("resource type is not supported")
        version = payload["version"]
        if not isinstance(version, str) or not _SEMVER_PATTERN.fullmatch(version):
            raise ResourceValidationError("resource version is not semantic versioning")
        try:
            Version(version)
        except InvalidVersion as error:
            raise ResourceValidationError("resource version is invalid") from error
        source = payload["source"]
        if not isinstance(source, str) or not source.strip() or len(source) > 2048:
            raise ResourceValidationError("resource source is invalid")
        entry = self._validate_relative_path(payload["entry"])

        permissions = payload["permissions"]
        if (
            not isinstance(permissions, list)
            or not all(isinstance(permission, str) for permission in permissions)
            or len(set(permissions)) != len(permissions)
            or any(permission not in RESOURCE_PERMISSIONS for permission in permissions)
        ):
            raise ResourceValidationError("resource permissions are invalid")

        file_payloads = payload["files"]
        if not isinstance(file_payloads, list) or not file_payloads:
            raise ResourceValidationError("resource files are required")
        files: list[ResourceFile] = []
        seen_paths: set[str] = set()
        for item in file_payloads:
            if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
                raise ResourceValidationError("resource file metadata is invalid")
            path = self._validate_relative_path(item["path"])
            if path == "manifest.json" or path.casefold() in seen_paths:
                raise ResourceValidationError("resource file path is duplicated or reserved")
            size = item["size"]
            digest = item["sha256"]
            if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                raise ResourceValidationError("resource file size is invalid")
            if not isinstance(digest, str) or not _SHA256_PATTERN.fullmatch(digest):
                raise ResourceValidationError("resource file digest is invalid")
            files.append(ResourceFile(path=path, size=size, sha256=digest))
            seen_paths.add(path.casefold())
        if entry.casefold() not in seen_paths:
            raise ResourceValidationError("resource entry is not declared")
        if resource_type in {"prompt", "session"} and any(
            PurePosixPath(file.path).suffix.lower() in _SCRIPT_SUFFIXES for file in files
        ):
            raise ResourceValidationError("prompt and session resources cannot contain scripts")

        content_sha256 = payload["content_sha256"]
        if not isinstance(content_sha256, str) or not _SHA256_PATTERN.fullmatch(
            content_sha256
        ):
            raise ResourceValidationError("resource content digest is invalid")
        source_key = payload.get("source_key")
        if source_key is not None and (
            not isinstance(source_key, str) or not source_key.strip() or len(source_key) > 512
        ):
            raise ResourceValidationError("resource source key is invalid")
        source_metadata = payload.get("source_metadata")
        if source_metadata is not None and not isinstance(source_metadata, dict):
            raise ResourceValidationError("resource source metadata is invalid")
        return ResourceManifest(
            resource_id=resource_id,
            type=resource_type,
            version=version,
            source=source.strip(),
            entry=entry,
            permissions=tuple(permissions),
            files=tuple(files),
            content_sha256=content_sha256,
            source_key=source_key.strip() if isinstance(source_key, str) else None,
            source_metadata=copy.deepcopy(source_metadata) if source_metadata is not None else None,
        )

    def _verify_files(
        self,
        archive: zipfile.ZipFile,
        members: dict[str, zipfile.ZipInfo],
        manifest: ResourceManifest,
    ) -> None:
        for file in manifest.files:
            info = members.get(file.path)
            if info is None or info.is_dir() or info.file_size != file.size:
                raise ResourceValidationError("resource file size does not match manifest")
            digest = hashlib.sha256()
            with archive.open(info, "r") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
            if digest.hexdigest() != file.sha256:
                raise ResourceValidationError("resource file digest does not match manifest")
        if self._content_hash(manifest.files) != manifest.content_sha256:
            raise ResourceValidationError("resource content digest does not match manifest")

    def _extract_to_staging(
        self, archive_path: Path, manifest: ResourceManifest
    ) -> Path:
        stage_root = self.staging_path / uuid.uuid4().hex
        version_path = stage_root / manifest.resource_id / manifest.version
        version_path.mkdir(parents=True, exist_ok=False)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                for file in manifest.files:
                    target = version_path / PurePosixPath(file.path)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(file.path, "r") as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                (version_path / "manifest.json").write_text(
                    json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
            return version_path
        except Exception:
            self._remove_path(stage_root)
            raise

    def _require_resource(self, resource_id: str) -> dict[str, Any]:
        if not isinstance(resource_id, str):
            raise ResourceValidationError("resource ID is invalid")
        resource = self._registry["resources"].get(resource_id)
        if resource is None:
            raise ResourceStateError("resource is not installed")
        return resource

    def _load_registered_manifest(
        self,
        resource_id: str,
        version: str,
        version_record: dict[str, Any],
    ) -> ResourceManifest:
        version_path = self._version_path(resource_id, version)
        manifest_path = version_path / "manifest.json"
        if not manifest_path.is_file() or manifest_path.is_symlink():
            raise ResourceValidationError("registered resource manifest is unavailable")
        try:
            manifest = self._parse_manifest(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResourceValidationError("registered resource manifest is invalid") from error
        if (
            manifest.resource_id != resource_id
            or manifest.version != version
            or manifest.content_sha256 != version_record["content_sha256"]
        ):
            raise ResourceValidationError("registered resource manifest does not match its registry")
        return manifest

    def _version_path(self, resource_id: str, version: str) -> Path:
        if not _ID_PATTERN.fullmatch(resource_id) or not _SEMVER_PATTERN.fullmatch(version):
            raise ResourceValidationError("resource path identity is invalid")
        target = (self.installed_path / resource_id / version).resolve()
        try:
            target.relative_to(self.installed_path.resolve())
        except ValueError as error:
            raise ResourceValidationError("resource path escapes the install directory") from error
        return target

    @staticmethod
    def _validate_relative_path(value: Any) -> str:
        if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
            raise ResourceValidationError("resource path is invalid")
        path = PurePosixPath(value)
        if path.is_absolute() or value.startswith("/") or any(
            part in {"", ".", ".."} for part in path.parts
        ):
            raise ResourceValidationError("resource path must remain inside the archive")
        if re.match(r"^[A-Za-z]:", value):
            raise ResourceValidationError("resource path cannot be drive-qualified")
        return path.as_posix()

    @staticmethod
    def _content_hash(files: Iterable[ResourceFile]) -> str:
        content = b"".join(
            f"{file.path}:{file.size}:{file.sha256}\n".encode("ascii")
            for file in sorted(files, key=lambda item: item.path)
        )
        return hashlib.sha256(content).hexdigest()

    def _version_record(
        self, manifest: ResourceManifest, installed_at: str
    ) -> dict[str, Any]:
        return {
            "version": manifest.version,
            "source": self._sanitize_source(manifest.source),
            "source_key": manifest.source_key,
            "source_metadata": copy.deepcopy(manifest.source_metadata),
            "entry": manifest.entry,
            "permissions": list(manifest.permissions),
            "content_sha256": manifest.content_sha256,
            "installed_at": installed_at,
        }

    def _backup_version(self, resource_id: str, version: str, *, reason: str) -> Path | None:
        """Copy one registered version into the persistent backup directory."""

        source = self._version_path(resource_id, version)
        if not source.is_dir() or source.is_symlink():
            return None
        timestamp = self._timestamp()
        backup_id = f"backup-{uuid.uuid4().hex}"
        target = self.resource_path / "backups" / resource_id / backup_id
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, symlinks=False, dirs_exist_ok=False)
        resource = copy.deepcopy(self._registry.get("resources", {}).get(resource_id, {}))
        metadata = {
            "backup_id": backup_id,
            "resource_id": resource_id,
            "version": version,
            "reason": reason,
            "created_at": timestamp,
            "content_sha256": resource.get("content_sha256"),
            "resource": resource,
        }
        (target / "backup.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target

    @staticmethod
    def _valid_backup_metadata(metadata: Any, directory_name: str) -> bool:
        return (
            isinstance(metadata, dict)
            and metadata.get("backup_id") == directory_name
            and isinstance(metadata.get("resource_id"), str)
            and isinstance(metadata.get("version"), str)
            and isinstance(metadata.get("resource"), dict)
        )

    def _find_backup(self, backup_id: str) -> tuple[Path, dict[str, Any]]:
        if not isinstance(backup_id, str) or not re.fullmatch(r"backup-[a-f0-9]{32}", backup_id):
            raise ResourceValidationError("backup ID is invalid")
        root = self.resource_path / "backups"
        for resource_dir in root.iterdir() if root.is_dir() else ():
            if not resource_dir.is_dir() or resource_dir.is_symlink():
                continue
            path = resource_dir / backup_id
            metadata_path = path / "backup.json"
            if (
                not path.is_dir()
                or path.is_symlink()
                or not metadata_path.is_file()
                or metadata_path.is_symlink()
            ):
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
                raise ResourceValidationError("backup metadata is invalid") from error
            if not self._valid_backup_metadata(metadata, backup_id):
                raise ResourceValidationError("backup metadata is invalid")
            return path, metadata
        raise ResourceStateError("backup is not found")

    def _validate_backup_directory(
        self,
        backup_path: Path,
        metadata: Mapping[str, Any],
        *,
        expected_resource_id: str,
        expected_version: str,
    ) -> ResourceManifest:
        """Validate a backup completely before it can replace an installed version."""
        if not isinstance(metadata, dict):
            raise ResourceValidationError("backup metadata is invalid")
        if metadata.get("resource_id") != expected_resource_id or metadata.get("version") != expected_version:
            raise ResourceValidationError("backup metadata identity does not match")
        if not backup_path.is_dir() or backup_path.is_symlink():
            raise ResourceValidationError("backup directory is unavailable")

        metadata_path = backup_path / "backup.json"
        manifest_path = backup_path / "manifest.json"
        if (
            not metadata_path.is_file()
            or metadata_path.is_symlink()
            or not manifest_path.is_file()
            or manifest_path.is_symlink()
        ):
            raise ResourceValidationError("backup metadata or manifest is unavailable")
        try:
            stored_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = self._parse_manifest(manifest_payload)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResourceValidationError("backup metadata or manifest is invalid") from error

        if stored_metadata != metadata:
            metadata = stored_metadata
        if not self._valid_backup_metadata(metadata, backup_path.name):
            raise ResourceValidationError("backup metadata is invalid")
        if manifest.resource_id != expected_resource_id or manifest.version != expected_version:
            raise ResourceValidationError("backup manifest identity does not match")
        if metadata.get("content_sha256") != manifest.content_sha256:
            raise ResourceValidationError("backup metadata digest does not match manifest")
        resource_snapshot = metadata.get("resource")
        if (
            not isinstance(resource_snapshot, dict)
            or resource_snapshot.get("resource_id") != manifest.resource_id
            or resource_snapshot.get("current_version") != manifest.version
            or resource_snapshot.get("content_sha256") != manifest.content_sha256
        ):
            raise ResourceValidationError("backup resource snapshot does not match manifest")

        self._validate_resource_directory(backup_path, manifest, allow_backup=True)
        return manifest

    def _validate_resource_directory(
        self, root: Path, manifest: ResourceManifest, *, allow_backup: bool
    ) -> None:
        """Validate a materialized resource tree against its manifest."""
        if not root.is_dir() or root.is_symlink():
            raise ResourceValidationError("resource directory is unavailable")

        expected_files = {"manifest.json", *(file.path for file in manifest.files)}
        if allow_backup:
            expected_files.add("backup.json")
        actual_files: set[str] = set()
        actual_directories: set[str] = set()
        try:
            for directory, directories, files in os.walk(root, topdown=True, followlinks=False):
                directory_path = Path(directory)
                if directory_path.is_symlink():
                    raise ResourceValidationError("resource backup contains a symbolic link")
                for name in directories:
                    child = directory_path / name
                    if child.is_symlink():
                        raise ResourceValidationError("resource backup contains a symbolic link")
                    actual_directories.add(child.relative_to(root).as_posix())
                for name in files:
                    child = directory_path / name
                    if child.is_symlink():
                        raise ResourceValidationError("resource backup contains a symbolic link")
                    actual_files.add(child.relative_to(root).as_posix())
        except OSError as error:
            raise ResourceValidationError("resource directory cannot be inspected") from error

        if actual_files != expected_files:
            raise ResourceValidationError("backup contains undeclared or missing files")
        expected_directories = {
            parent.as_posix()
            for filename in expected_files
            for parent in PurePosixPath(filename).parents
            if str(parent) != "."
        }
        if actual_directories != expected_directories:
            raise ResourceValidationError("backup contains undeclared directories")

        manifest_path = root / "manifest.json"
        try:
            parsed_manifest = self._parse_manifest(
                json.loads(manifest_path.read_text(encoding="utf-8"))
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResourceValidationError("resource manifest is invalid") from error
        if (
            parsed_manifest.resource_id != manifest.resource_id
            or parsed_manifest.version != manifest.version
            or parsed_manifest.content_sha256 != manifest.content_sha256
        ):
            raise ResourceValidationError("resource manifest does not match its expected identity")

        for file in manifest.files:
            path = root / PurePosixPath(file.path)
            try:
                if path.stat().st_size != file.size:
                    raise ResourceValidationError("resource file size does not match manifest")
                digest = hashlib.sha256()
                with path.open("rb") as source:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as error:
                raise ResourceValidationError("resource file cannot be read") from error
            if digest.hexdigest() != file.sha256:
                raise ResourceValidationError("resource file digest does not match manifest")
        if self._content_hash(manifest.files) != manifest.content_sha256:
            raise ResourceValidationError("resource content digest does not match manifest")

    def _audit(
        self,
        manifest: ResourceManifest | None,
        operation: str,
        result: str,
        error: Exception | None = None,
    ) -> None:
        if manifest is None:
            record = {
                "resource_id": None,
                "type": None,
                "current_version": None,
                "source_summary": None,
                "content_sha256": None,
            }
        else:
            record = {
                "resource_id": manifest.resource_id,
                "type": manifest.type,
                "current_version": manifest.version,
                "source_summary": self._source_summary(manifest.source),
                "content_sha256": manifest.content_sha256,
            }
        self._append_audit(record, operation, result, error)

    def _audit_record(
        self,
        resource: dict[str, Any],
        operation: str,
        result: str,
        error: Exception | None = None,
    ) -> None:
        record = {
            "resource_id": resource.get("resource_id"),
            "type": resource.get("type"),
            "current_version": resource.get("current_version"),
            "source_summary": self._source_summary(resource.get("source", "")),
            "content_sha256": resource.get("content_sha256"),
        }
        self._append_audit(record, operation, result, error)

    def _append_audit(
        self,
        record: dict[str, Any],
        operation: str,
        result: str,
        error: Exception | None,
    ) -> None:
        event = {
            **record,
            "component": "resource_lifecycle",
            "operation": operation,
            "result": result,
            "outcome": result,
            "timestamp": self._timestamp(),
            "error_category": type(error).__name__ if error is not None else None,
        }
        with self._lock:
            try:
                self.resource_path.mkdir(parents=True, exist_ok=True)
                with self.audit_path.open("a", encoding="utf-8", newline="\n") as audit_file:
                    audit_file.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                    audit_file.flush()
                    os.fsync(audit_file.fileno())
            except OSError:
                # Audit failure cannot expose resource content or undo a completed atomic publish.
                pass

    @staticmethod
    def _sanitize_source(source: str) -> str:
        source = source.strip()
        parsed = urlsplit(source)
        if parsed.scheme and parsed.netloc:
            hostname = parsed.hostname or ""
            port = f":{parsed.port}" if parsed.port is not None else ""
            return urlunsplit((parsed.scheme, f"{hostname}{port}", parsed.path, "", ""))[:512]
        return source[:512]

    @classmethod
    def _source_summary(cls, source: str) -> str:
        sanitized = cls._sanitize_source(source)
        return hashlib.sha256(sanitized.encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _timestamp() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _remove_path(path: Path) -> None:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path, ignore_errors=True)
        else:
            path.unlink(missing_ok=True)
