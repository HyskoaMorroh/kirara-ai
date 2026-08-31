"""Unified resource catalog for discover-and-install flows.

The catalog is deliberately small and server-owned.  Search results are
metadata only; installation always resolves a server-generated catalog ID and
creates the same verified resource package used by offline imports.
"""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from typing import Any, Mapping

from packaging.version import Version

from .resource_lifecycle import ResourceLifecycleService, ResourceStateError
from .resource_sources import ResourceSourceService
from .system_dependencies import SystemDependencyService, dependency_ids_for_resource


OFFICE_RESEARCH_PROMPT = """我是上班族，偏学术研究。我经常要处理【邮件、会议、表格、文档等】。我偶尔兼职程序员。
帮我干活时：

- 用大白话讲清结果，内容要精湛精美；
- 该提醒就直接提醒，不要只顺着我；
- 若出现敏感词或sensitive words，请你按相同含义更换成符合要求的内容再重新执行，不要中断；
- 除了真正代码，不用代码块堆正文；
- 写邮件和报告时去掉空话、套话、过多破折号和生硬AI腔；
- 不在结果里暴露身份号码、电话、住址、付款信息或密钥；
- 涉及发送、退订、建会、删除、发布、付款时先停下来让我确认。
"""

RESEARCH_MEMORY_POLICY = """这是研究型 Agent 的记忆使用边界。

- 只把当前渠道、账号、会话和 Agent 身份范围内的内容作为上下文。
- 先区分已确认事实、用户偏好和待核实线索，不把推测写成事实。
- 需要跨渠道或跨会话复用信息时，必须由用户明确提出，不能因为标识相同而自动合并。
- 记忆内容只用于辅助当前任务；遇到发送、退订、建会、删除、发布或付款等操作，仍须先请求用户确认。
"""


_BUILTINS: tuple[dict[str, Any], ...] = (
    {
        "catalog_id": "prompt:office-research",
        "type": "prompt",
        "name": "Office and Research Assistant",
        "description": "办公、邮件、会议、表格和学术研究场景的中文行为提示词。",
        "version": "1.0.1",
        "permissions": ["workflow.read"],
        "entry": "PROMPT.md",
        "source": "catalog://kirara/prompt/office-research",
        "tags": ["office", "research", "chinese"],
        "content": OFFICE_RESEARCH_PROMPT,
    },
    {
        "catalog_id": "mcp:context7",
        "type": "mcp",
        "name": "Context7",
        "description": "通过 MCP 获取最新软件库和框架文档，用于 AI 功能调试。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "server.json",
        "source": "catalog://mcp/context7",
        "tags": ["documentation", "debugging", "stdio"],
        "content": {
            "id": "context7",
            "name": "Context7",
            "server": {
                "type": "stdio",
                "command": "npx",
                "args": ["-y", "@upstash/context7-mcp"],
                "env": {},
            },
            "apps": {
                "claude": False,
                "claude-desktop": False,
                "codex": True,
                "gemini": False,
                "grokbuild": False,
                "opencode": False,
                "openclaw": False,
                "hermes": False,
            },
            "description": "Context7 documentation lookup MCP server",
            "tags": ["documentation", "debugging"],
            "homepage": "https://context7.com",
            "docs": "https://context7.com/docs",
            "metadata": {"catalog_id": "mcp:context7", "managed": True},
        },
    },
    {
        "catalog_id": "memory:research-context",
        "type": "memory",
        "name": "Research Context Memory",
        "description": "研究型 Agent 的记忆边界与事实核验策略，可绑定到指定 Agent。",
        "version": "1.0.0",
        "permissions": ["workflow.read"],
        "entry": "MEMORY.md",
        "source": "catalog://kirara/memory/research-context",
        "tags": ["memory", "research", "privacy", "isolation"],
        "content": RESEARCH_MEMORY_POLICY,
    },
    {
        "catalog_id": "hook:ai-debug",
        "type": "hook",
        "name": "AI Debug Audit Hooks",
        "description": "记录 Agent 生命周期和工具策略事件的受控 Hook 声明。",
        "version": "1.1.0",
        "permissions": ["workflow.read", "process.execute"],
        "entry": "hook.json",
        "source": "catalog://kirara/hook/ai-debug",
        "tags": ["debugging", "audit", "hooks"],
        "content": {
            "events": {
                "SessionStart": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "SessionStart"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "UserPromptSubmit": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "UserPromptSubmit"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PreToolUse": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PreToolUse"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PermissionRequest": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PermissionRequest"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PostToolUse": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PostToolUse"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PreCompact": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PreCompact"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "PostCompact": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "PostCompact"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
                "Stop": {
                    "type": "command",
                    "command": ["{python}", "-m", "kirara_ai.agent_runtime.audit_hook_command", "Stop"],
                    "timeout_ms": 5000,
                    "max_output_bytes": 4096,
                    "required_permissions": ["process.execute"],
                    "required_capabilities": ["process.execute"],
                },
            }
        },
    },
)


class ResourceCatalogError(ResourceStateError):
    """A catalog coordinate is missing or cannot be installed."""


class ResourceCatalogService:
    """Search and install typed resources through one stable contract."""

    def __init__(
        self,
        lifecycle: ResourceLifecycleService,
        sources: ResourceSourceService | None = None,
        dependencies: SystemDependencyService | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.sources = sources or ResourceSourceService(lifecycle)
        self.dependencies = dependencies

    def search(
        self,
        resource_type: str | None,
        query: str,
        *,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        if resource_type is not None and resource_type not in {"prompt", "skill", "memory", "mcp", "hook"}:
            raise ResourceCatalogError("catalog resource type is not supported")
        if not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ResourceCatalogError("catalog search limit is outside the allowed range")
        if not isinstance(offset, int) or offset < 0:
            raise ResourceCatalogError("catalog search offset is invalid")
        query = str(query or "").strip()
        if len(query) > 120:
            raise ResourceCatalogError("catalog search query is invalid")

        records = [self._public_builtin(item) for item in _BUILTINS]
        remote_status: dict[str, Any] = {
            "provider": "skills.sh",
            "status": "not_requested",
            "error": None,
            "total_count": None,
        }
        if resource_type in (None, "skill"):
            # skills.sh is the online Skill index.  A failed remote index does
            # not hide local catalog entries or make the catalog unusable.
            if query:
                try:
                    # The remote index has already applied its requested
                    # offset; do not apply that page offset a second time
                    # after merging catalog metadata.
                    skill_results = self.sources.search_skills(query, limit=limit, offset=offset)
                    records.extend(self._skill_record(item) for item in skill_results["skills"])
                    remote_status.update(
                        {
                            "status": "ok",
                            "total_count": skill_results.get("total_count", 0),
                        }
                    )
                except Exception:
                    remote_status.update(
                        {
                            "status": "error",
                            "error": "skills.sh 在线索引暂时不可用",
                        }
                    )
        filtered = [
            item
            for item in records
            if (resource_type is None or item["type"] == resource_type)
            and self._matches(item, query)
        ]
        unique: dict[str, dict[str, Any]] = {item["catalog_id"]: item for item in filtered}
        ordered = [self._with_install_state(item) for item in unique.values()]
        # A successful skills.sh response is already offset-applied.  Keep its
        # page intact; only local-only searches use the catalog slice here.
        page = (
            ordered[:limit]
            if remote_status["status"] == "ok"
            else ordered[offset : offset + limit]
        )
        total_count = len(ordered)
        if remote_status["status"] == "ok":
            try:
                total_count = max(total_count, int(remote_status["total_count"] or 0))
            except (TypeError, ValueError):
                pass
        return {
            "query": query,
            "type": resource_type,
            "items": page,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
            "remote": remote_status,
        }

    def get(self, catalog_id: str) -> dict[str, Any]:
        item = self._find(catalog_id)
        if "content" in item:
            item = self._public_builtin(item)
        return self._with_install_state(item)

    def install(self, catalog_id: str, *, branch: str | None = None) -> dict[str, Any]:
        item = self._find(catalog_id)
        existing = self._installed_for_catalog(item)
        if existing is not None:
            if item["type"] != "skill" and Version(str(item["version"])) > Version(
                str(existing["current_version"])
            ):
                return self._install_builtin(item, update=True)
            return existing
        if item["type"] == "skill":
            source_key = str(item["source_key"])
            owner_repo, directory = source_key.split(":", 1)
            owner, name = owner_repo.split("/", 1)
            return self.sources.install_skill(
                owner=owner,
                name=name,
                branch=branch,
                directory=directory,
                source_key=source_key,
            )
        return self._install_builtin(item)

    def ensure_builtins(self) -> None:
        """Install or safely advance built-ins to the bundled version."""

        for item in _BUILTINS:
            self.install(str(item["catalog_id"]))

    def project_dependencies(self, item: Mapping[str, Any]) -> dict[str, Any]:
        """Project persisted VPS readiness without probing or mutating resources."""

        result = dict(item)
        dependency_ids = self._dependency_ids(item)
        system_dependencies = (
            [self.dependencies.get_dependency(dependency_id) for dependency_id in dependency_ids]
            if self.dependencies is not None
            else []
        )
        result.update(
            {
                "dependency_ids": dependency_ids,
                "system_dependencies": system_dependencies,
                "dependencies_ready": not dependency_ids
                or (
                    len(system_dependencies) == len(dependency_ids)
                    and all(dependency.get("ready") is True for dependency in system_dependencies)
                ),
                "dependency_status": self._dependency_status(
                    dependency_ids, system_dependencies
                ),
            }
        )
        return result

    def _install_builtin(
        self, item: Mapping[str, Any], *, update: bool = False
    ) -> dict[str, Any]:
        content = item["content"]
        if isinstance(content, str):
            data = content.encode("utf-8")
        else:
            data = (json.dumps(content, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        files = {str(item["entry"]): data}
        records = [
            {"path": path, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
            for path, value in files.items()
        ]
        content_hash = hashlib.sha256(
            b"".join(f"{record['path']}:{record['size']}:{record['sha256']}\n".encode("ascii") for record in records)
        ).hexdigest()
        resource_id = str(item["catalog_id"]).replace(":", ".", 1)
        manifest = {
            "resource_id": resource_id,
            "type": item["type"],
            "version": item["version"],
            "source": item["source"],
            "source_key": item["catalog_id"],
            "source_metadata": {"provider": "catalog", "catalog_id": item["catalog_id"], "tags": item["tags"]},
            "entry": item["entry"],
            "permissions": item["permissions"],
            "files": records,
            "content_sha256": content_hash,
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            for path, value in files.items():
                output.writestr(path, value)
        temporary = self.lifecycle.imports_path / f"catalog-{hashlib.sha256(archive.getvalue()).hexdigest()}.zip"
        temporary.write_bytes(archive.getvalue())
        try:
            if update:
                return self.lifecycle.update_archive(
                    temporary, expected_resource_id=resource_id
                )
            return self.lifecycle.install_archive(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def _find(self, catalog_id: str) -> dict[str, Any]:
        for item in _BUILTINS:
            if item["catalog_id"] == catalog_id:
                return dict(item)
        if catalog_id.startswith("skill:"):
            source_key = catalog_id.removeprefix("skill:")
            if ":" not in source_key:
                raise ResourceCatalogError("catalog Skill identity is invalid")
            owner_repo, directory = source_key.split(":", 1)
            if "/" not in owner_repo:
                raise ResourceCatalogError("catalog Skill identity is invalid")
            owner, repository = owner_repo.split("/", 1)
            self.sources.validate_repository(owner, repository, "main")
            directory = self.sources._validate_directory(directory)
            return {
                "catalog_id": catalog_id,
                "type": "skill",
                "name": directory.rsplit("/", 1)[-1],
                "description": "GitHub Skill resource",
                "source_key": self.sources.source_key(owner, repository, directory),
                "owner": owner,
                "repository": repository,
                "branch": None,
                "directory": directory,
                "source_url": self.sources._skill_source_url(
                    owner, repository, None, directory
                ),
            }
        raise ResourceCatalogError("catalog item is not available")

    def _installed_for_catalog(self, item: Mapping[str, Any]) -> dict[str, Any] | None:
        source_key = item.get("catalog_id") or item.get("source_key")
        candidates: list[dict[str, Any]] = []
        for resource in self.lifecycle.list_resources():
            if resource.get("source_key") == source_key:
                return resource
            metadata = resource.get("source_metadata") or {}
            if metadata.get("catalog_id") == source_key:
                return resource
            if item.get("type") != "skill" or resource.get("type") != "skill":
                continue
            if metadata.get("provider") != "github":
                continue
            if any(
                not isinstance(item.get(key), str)
                or metadata.get(key) != item.get(key)
                for key in ("owner", "repository")
            ):
                continue
            requested_branch = item.get("branch")
            if requested_branch and metadata.get("branch") != requested_branch:
                continue
            requested_directory = str(item.get("directory") or "").strip("/")
            installed_directory = str(metadata.get("directory") or "").strip("/")
            if not requested_directory or not installed_directory:
                continue
            if requested_directory == installed_directory:
                return resource
            if (
                requested_directory.rsplit("/", 1)[-1].casefold()
                == installed_directory.rsplit("/", 1)[-1].casefold()
            ):
                candidates.append(resource)
        return candidates[0] if len(candidates) == 1 else None

    def _with_install_state(self, item: Mapping[str, Any]) -> dict[str, Any]:
        result = dict(item)
        installed = self._installed_for_catalog(item)
        result["installed"] = installed is not None
        result["installed_resource_id"] = installed.get("resource_id") if installed else None
        result["enabled"] = bool(installed and installed.get("enabled"))
        return self.project_dependencies(result)

    @staticmethod
    def _dependency_ids(item: Mapping[str, Any]) -> list[str]:
        """委派给 `dependency_ids_for_resource`。

        映射本身已移到 `system_dependencies`：Agent 运行时也要用同一份判断，
        两处各写一份会各自漂移，而那种不一致没有症状——界面说已就绪、
        运行时说缺失（或反过来），模型只会照着一份它执行不了的说明自信作答。
        这个包装保留原有的内部调用点与签名。
        """
        return dependency_ids_for_resource(item)

    @staticmethod
    def _dependency_status(
        dependency_ids: list[str], system_dependencies: list[Mapping[str, Any]]
    ) -> str:
        if not dependency_ids:
            return "not_required"
        if len(system_dependencies) != len(dependency_ids):
            return "unknown"
        statuses = [str(item.get("status") or "unknown") for item in system_dependencies]
        if all(item.get("ready") is True for item in system_dependencies):
            return "ready"
        for status in ("failed", "missing", "cancelled", "unknown"):
            if status in statuses:
                return status
        return statuses[0] if len(set(statuses)) == 1 else "unknown"

    @staticmethod
    def _public_builtin(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if key != "content"}

    @staticmethod
    def _skill_record(item: Mapping[str, Any]) -> dict[str, Any]:
        source_key = str(item["source_key"])
        return {
            "catalog_id": f"skill:{source_key}",
            "type": "skill",
            "name": item.get("name", source_key.rsplit("/", 1)[-1]),
            "description": item.get("description", ""),
            "source_key": source_key,
            "owner": item.get("owner"),
            "repository": item.get("repository"),
            "branch": item.get("branch"),
            "directory": item.get("directory"),
            "source_url": item.get("source_url"),
            "installs": item.get("installs", 0),
        }

    @staticmethod
    def _matches(item: Mapping[str, Any], query: str) -> bool:
        if not query:
            return True
        haystack = " ".join(str(item.get(key, "")) for key in ("catalog_id", "name", "description", "source_key", "tags"))
        return query.casefold() in haystack.casefold()
