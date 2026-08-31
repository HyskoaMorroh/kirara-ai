from __future__ import annotations

import copy
import hashlib
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
                copy.deepcopy(resource)
                for resource in self._registry["resources"].values()
                if resource_type is None or resource["type"] == resource_type
            ]
        return sorted(resources, key=lambda resource: resource["resource_id"])

    def get_resource(self, resource_id: str) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._require_resource(resource_id))

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
                return copy.deepcopy(resource)
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
                        "source_metadata": copy.deepcopy(manifest.source_metadata),
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
                return copy.deepcopy(next_resource)
        except Exception as error:
            self._audit(manifest, "update", "failure", error)
            raise

    def enable(self, resource_id: str, *, confirmed: bool = False) -> dict[str, Any]:
        with self._lock:
            current = self._require_resource(resource_id)
            if current["enabled"] and not current["confirmation_required"]:
                return copy.deepcopy(current)
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
                return copy.deepcopy(current)
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
                "source_metadata": copy.deepcopy(version_record.get("source_metadata")),
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
            current = copy.deepcopy(self._require_resource(resource_id))
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
        """Persist one repository coordinate in the server registry."""

        with self._lock:
            repositories = copy.deepcopy(self._registry.setdefault("repositories", []))
            item = {"owner": owner, "name": name, "branch": branch, "enabled": enabled}
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

    def list_source_repositories(self) -> list[dict[str, Any]]:
        with self._lock:
            return copy.deepcopy(self._registry.get("repositories", []))

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
                restored.update(
                    {
                        "resource_id": resource_id,
                        "current_version": version,
                        "enabled": False,
                        "confirmation_required": True,
                        "updated_at": self._timestamp(),
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
                return copy.deepcopy(restored)
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
        return copy.deepcopy(resource)

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
