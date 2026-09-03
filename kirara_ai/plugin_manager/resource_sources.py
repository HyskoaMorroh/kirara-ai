"""Server-side sources for Skill discovery and installation.

The browser only submits repository coordinates.  Downloads, archive parsing,
manifest generation, and persistence all happen inside the container so the
mounted ``DATA_PATH`` remains the single source of truth.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import PurePosixPath
from typing import Any, Mapping
import uuid

from packaging.version import InvalidVersion, Version

from .resource_lifecycle import (
    MAX_ARCHIVE_SIZE_BYTES,
    MAX_MEMBER_COUNT,
    MAX_MEMBER_SIZE_BYTES,
    MAX_UNCOMPRESSED_SIZE_BYTES,
    ResourceLifecycleService,
    ResourceStateError,
    ResourceValidationError,
)


MAX_SOURCE_RESPONSE_BYTES = MAX_ARCHIVE_SIZE_BYTES
MAX_SEARCH_QUERY_LENGTH = 120
_GITHUB_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
_BRANCH_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$")
_DIRECTORY_PART = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,255}$")
_SOURCE_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}:[A-Za-z0-9._/-]{1,256}$")
_ALLOWED_HOSTS = frozenset(
    {
        "api.github.com",
        "codeload.github.com",
        "github.com",
        "raw.githubusercontent.com",
        "skills.sh",
    }
)
_TRANSPARENT_PROXY_NETWORK = ipaddress.ip_network("198.18.0.0/15")


class ResourceSourceError(ResourceValidationError):
    """A source coordinate or remote response violates the source contract."""


class _ValidatedRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate every redirect target before urllib follows it."""

    def __init__(self, service: "ResourceSourceService") -> None:
        super().__init__()
        self.service = service

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        validated_url = self.service._validate_network_target(newurl)
        return super().redirect_request(req, fp, code, msg, headers, validated_url)


class ResourceSourceService:
    """Manage repository-backed resources without writing outside DATA_PATH."""

    def __init__(self, lifecycle: ResourceLifecycleService) -> None:
        self.lifecycle = lifecycle

    @staticmethod
    def validate_repository(owner: str, name: str, branch: str) -> tuple[str, str, str]:
        owner = str(owner or "").strip()
        name = str(name or "").strip()
        branch = str(branch or "").strip()
        ResourceSourceService._validate_repository_identity(owner, name)
        if (
            not _BRANCH_PART.fullmatch(branch)
            or ".." in branch
            or branch.endswith("/")
            or "//" in branch
            or any(character in branch for character in ("?", "#", "\\"))
        ):
            raise ResourceSourceError("GitHub branch is invalid")
        return owner, name, branch

    @staticmethod
    def _validate_repository_identity(owner: str, name: str) -> tuple[str, str]:
        owner = str(owner or "").strip()
        name = str(name or "").strip()
        if not _GITHUB_PART.fullmatch(owner) or not _GITHUB_PART.fullmatch(name):
            raise ResourceSourceError("GitHub repository coordinates are invalid")
        return owner, name

    @classmethod
    def source_key(cls, owner: str, name: str, directory: str) -> str:
        """由 **用户请求的** 目录构造 source identity。

        仓库根标记 `"."` 不是合法输入，见 `REPOSITORY_ROOT_MARKER`；
        内部已解析出「仓库本身就是 Skill」时用 `resolved_source_key`。
        """
        owner, name, _ = cls.validate_repository(owner, name, "main")
        directory = cls._validate_directory(directory)
        return f"{owner}/{name}:{directory}"

    @classmethod
    def resolved_source_key(cls, owner: str, name: str, resolved_directory: str) -> str:
        """由 **已解析的** 目录构造 source identity，允许仓库根标记。

        与 `source_key` 分开，是因为两者的信任边界不同：一个接受用户输入，
        一个接受 `_fetch_skill_files` 的结果。合成一个函数就等于让 `"."`
        重新成为可请求的输入。
        """
        owner, name, _ = cls.validate_repository(owner, name, "main")
        if resolved_directory == cls.REPOSITORY_ROOT_MARKER:
            return f"{owner}/{name}:{cls.REPOSITORY_ROOT_MARKER}"
        return f"{owner}/{name}:{cls._validate_directory(resolved_directory)}"

    @classmethod
    def validate_remote_url(cls, raw_url: str) -> str:
        if not isinstance(raw_url, str) or len(raw_url) > 2048:
            raise ResourceSourceError("remote source URL is invalid")
        parsed = urllib.parse.urlsplit(raw_url.strip())
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme.lower() != "https" or not hostname:
            raise ResourceSourceError("remote sources require HTTPS")
        if parsed.username or parsed.password or parsed.fragment:
            raise ResourceSourceError("remote source URL contains unsupported components")
        if parsed.port not in (None, 443):
            raise ResourceSourceError("remote source port is not allowed")
        if hostname not in _ALLOWED_HOSTS:
            raise ResourceSourceError("remote source host is not allowed")
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            address = None
        if address is not None:
            cls._validate_public_address(address)
        return urllib.parse.urlunsplit(("https", hostname, parsed.path, parsed.query, ""))

    @classmethod
    def _validate_network_target(cls, raw_url: str) -> str:
        validated = cls.validate_remote_url(raw_url)
        parsed = urllib.parse.urlsplit(validated)
        if parsed.hostname is not None:
            cls._validate_resolved_addresses(parsed.hostname, parsed.port or 443)
        return validated

    @staticmethod
    def _validate_public_address(address: ipaddress._BaseAddress) -> None:
        if not address.is_global:
            raise ResourceSourceError("remote source address is not public")

    @classmethod
    def _validate_resolved_addresses(cls, hostname: str, port: int) -> None:
        try:
            results = socket.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
        except (OSError, socket.gaierror) as error:
            raise ResourceSourceError("remote source host cannot be resolved") from error
        addresses: set[str] = set()
        for result in results:
            try:
                addresses.add(str(result[4][0]))
            except (IndexError, TypeError):
                continue
        if not addresses:
            raise ResourceSourceError("remote source host cannot be resolved")
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as error:
                raise ResourceSourceError("remote source host resolved to an invalid address") from error
            if (
                hostname.lower().rstrip(".") in _ALLOWED_HOSTS
                and address.version == 4
                and address in _TRANSPARENT_PROXY_NETWORK
            ):
                # Transparent proxy clients can map public hosts into RFC 2544's
                # benchmark range. The HTTPS host allowlist and certificate
                # validation remain authoritative for these synthetic addresses.
                continue
            cls._validate_public_address(address)

    #: 「整个仓库就是一个 Skill」的内部标记。
    #:
    #: 它是 `_fetch_skill_files` 在 `_resolve_skill_directory` 返回 `None` 时
    #: 自己填的**结果值**，不是用户可以请求的输入。此前 `_validate_directory`
    #: 在所有形态检查之前就把 `"."` 原样返回，于是这个值绕过了 `_DIRECTORY_PART`、
    #: `..`、`//`、`\\` 全部判据：直接请求 `"."` 会把整个仓库（上限 4096 个成员 /
    #: 128 MB）当成一个 Skill 装进来，`source_key` 变成 `owner/repo:.`，
    #: 重复安装检测也识别不到。
    REPOSITORY_ROOT_MARKER = "."

    @staticmethod
    def _validate_directory(directory: str) -> str:
        directory = str(directory or "").strip().strip("/")
        if (
            not _DIRECTORY_PART.fullmatch(directory)
            or ".." in directory
            or "//" in directory
            or "\\" in directory
        ):
            raise ResourceSourceError("Skill directory is invalid")
        path = PurePosixPath(directory)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise ResourceSourceError("Skill directory must remain inside the repository")
        return path.as_posix()

    @classmethod
    def _validate_stored_directory(cls, directory: str) -> str:
        """校验 **服务端此前存下的** 目录，允许仓库根标记。

        `check_updates` / `update_skill` 读的是安装时由 `_fetch_skill_files`
        写进 manifest 的 `directory`，对「仓库本身就是 Skill」的资源那就是
        `"."`。用面向用户输入的 `_validate_directory` 去校验它，会让这类资源
        既查不到更新也升不了级——堵掉一个绕过口，不能顺手把合法资源锁死。
        """
        if str(directory).strip() == cls.REPOSITORY_ROOT_MARKER:
            return cls.REPOSITORY_ROOT_MARKER
        return cls._validate_directory(directory)

    def add_repository(self, owner: str, name: str, branch: str = "main") -> dict[str, Any]:
        owner, name, branch = self.validate_repository(owner, name, branch)
        return self.lifecycle.upsert_source_repository(owner, name, branch, enabled=True)

    def list_repositories(self) -> list[dict[str, Any]]:
        return self.lifecycle.list_source_repositories()

    def set_repository_enabled(
        self, owner: str, name: str, branch: str, enabled: bool
    ) -> dict[str, Any]:
        owner, name, branch = self.validate_repository(owner, name, branch)
        return self.lifecycle.set_source_repository_enabled(owner, name, branch, enabled)

    def remove_repository(self, owner: str, name: str, branch: str) -> dict[str, Any]:
        """摘掉一条仓库来源登记，不动从它装过的资源。

        与启停走同一套坐标校验：一个没过校验的坐标压根不可能在登记表里，
        跳过校验只会把「坐标非法」报成「仓库不存在」，而那两件事的处置不同。
        """
        owner, name, branch = self.validate_repository(owner, name, branch)
        return self.lifecycle.remove_source_repository(owner, name, branch)

    def discover_repository(
        self, owner: str, name: str, branch: str = "main"
    ) -> list[dict[str, Any]]:
        owner, name, branch = self.validate_repository(owner, name, branch)
        url = self._github_archive_url(owner, name, branch)
        payload = self._download_bytes(url)
        members = self._read_repository_zip(payload)
        root = self._repository_root(members)
        discovered: list[dict[str, Any]] = []
        for member_name, content in sorted(members.items()):
            if PurePosixPath(member_name).name.casefold() != "skill.md":
                continue
            relative = PurePosixPath(member_name).relative_to(root)
            directory_path = relative.parent
            if str(directory_path) == ".":
                continue
            directory = directory_path.as_posix()
            metadata = self._parse_skill_front_matter(content)
            discovered.append(
                {
                    "source_key": self.source_key(owner, name, directory),
                    "owner": owner,
                    "repository": name,
                    "branch": branch,
                    "directory": directory,
                    "name": metadata["name"] or directory_path.name,
                    "description": metadata["description"],
                    # 让「发现」阶段就能看到上游声明的版本，而不是等装完才知道。
                    "version": metadata["version"],
                    "source_url": f"https://github.com/{owner}/{name}/tree/{urllib.parse.quote(branch, safe='/')}/{directory}",
                }
            )
        # 把条数记回仓库行：注册之后界面上此前看不出这个仓库有没有用——
        # 一个坐标拼错或压根不含 `SKILL.md` 的仓库，与一个装着几百个技能的
        # 仓库长得一模一样。数量是本次发现的自然副产品，不必让用户再点一次别的。
        #
        # 只在**成功**走到这里时记：失败路径上异常已经抛出去了，
        # 而把一次网络错误写成 0 比不写更糟——0 是「这个仓库配错了」的信号。
        #
        # 未登记的坐标记不上（`KeyError`），但那不该让直查失败：
        # `discover_repository` 的既有语义是「给一个坐标就能看里面有什么」，
        # 不要求先登记。为了记一个数而拒绝这条路径，是用新特性削掉旧能力。
        try:
            self.lifecycle.record_repository_discovery(
                owner, name, branch, count=len(discovered)
            )
        except KeyError:
            pass
        return discovered

    def search_skills(self, query: str, *, limit: int = 20, offset: int = 0) -> dict[str, Any]:
        query = str(query or "").strip()
        if not query or len(query) > MAX_SEARCH_QUERY_LENGTH:
            raise ResourceSourceError("Skill search query is invalid")
        if not isinstance(limit, int) or limit < 1 or limit > 50:
            raise ResourceSourceError("Skill search limit is outside the allowed range")
        if not isinstance(offset, int) or offset < 0:
            raise ResourceSourceError("Skill search offset is invalid")
        endpoint = self.validate_remote_url(
            "https://skills.sh/api/search?" + urllib.parse.urlencode(
                {"q": query, "limit": limit, "offset": offset}
            )
        )
        payload = self._request_json(endpoint)
        return self._normalize_search_results(payload, query, limit, offset)

    def install_skill(
        self,
        *,
        owner: str,
        name: str,
        branch: str | None = None,
        directory: str,
        source_key: str | None = None,
    ) -> dict[str, Any]:
        owner, name = self._validate_repository_identity(owner, name)
        branch = self._resolve_repository_branch(owner, name, branch)
        directory = self._validate_directory(directory)
        requested_source_key = self.source_key(owner, name, directory)
        if source_key is not None and source_key != requested_source_key:
            raise ResourceSourceError("source identity is generated by the server")
        selected, metadata, resolved_directory = self._fetch_skill_files(
            owner, name, branch, directory
        )
        generated_source_key = self.resolved_source_key(owner, name, resolved_directory)
        resource_id = self._resource_id(generated_source_key)
        archive = self._build_skill_archive(
            resource_id=resource_id,
            source_key=generated_source_key,
            source_url=self._skill_source_url(owner, name, branch, resolved_directory),
            # 采用上游 SKILL.md 声明的版本；缺失或不可解析时回落到 1.0.0。
            version=metadata["version"],
            metadata={
                "provider": "github",
                "owner": owner,
                "repository": name,
                "branch": branch,
                "directory": resolved_directory,
                "name": metadata["name"] or resolved_directory.rsplit("/", 1)[-1],
                "description": metadata["description"],
            },
            files=selected,
        )
        temporary = self.lifecycle.imports_path / f"remote-{hashlib.sha256(archive).hexdigest()}.zip"
        temporary.write_bytes(archive)
        try:
            installed = self.lifecycle.install_archive(temporary)
        finally:
            temporary.unlink(missing_ok=True)
        installed["source_key"] = generated_source_key
        return installed

    def check_updates(self, resource_id: str | None = None) -> list[dict[str, Any]]:
        """Compare registered GitHub resources with their current source.

        This operation is intentionally read-only.  It fetches repository
        contents inside the server and returns digests only; no browser path,
        archive bytes, or untrusted source identity is accepted.
        """

        resources = (
            [self.lifecycle.get_resource(resource_id)]
            if resource_id is not None
            else self.lifecycle.list_resources("skill")
        )
        results: list[dict[str, Any]] = []
        for resource in resources:
            metadata = resource.get("source_metadata") or {}
            if not isinstance(metadata, Mapping):
                continue
            provider = metadata.get("provider")
            if provider != "github":
                # catalog 与 skills.sh 安装的 Skill 也应有更新出口：此前直接跳过，
                # 于是这些来源装完就再也检查不到更新，界面上只会显示「无更新」。
                results.append(
                    {
                        "resource_id": resource.get("resource_id"),
                        "source_key": resource.get("source_key"),
                        "current_version": resource.get("current_version"),
                        "current_content_sha256": resource.get("content_sha256"),
                        "remote_content_sha256": None,
                        "update_available": False,
                        "update_channel_supported": False,
                        "source_provider": str(provider) if provider else None,
                        "error": (
                            "该来源暂不支持自动检查更新；请从来源页面重新安装以获取新版本"
                        ),
                    }
                )
                continue
            owner = str(metadata.get("owner", ""))
            repository = str(metadata.get("repository", ""))
            branch = str(metadata.get("branch", "main"))
            directory = str(metadata.get("directory", ""))
            try:
                owner, repository, branch = self.validate_repository(owner, repository, branch)
                directory = self._validate_stored_directory(directory)
                files, remote_metadata, resolved_directory = self._fetch_skill_files(
                    owner, repository, branch, directory
                )
                source_key = self.resolved_source_key(owner, repository, resolved_directory)
                remote_hash = self._files_content_hash(files)
                current_hash = str(resource.get("content_sha256", ""))
                results.append(
                    {
                        "resource_id": resource["resource_id"],
                        "source_key": source_key,
                        "current_version": resource["current_version"],
                        "current_content_sha256": current_hash,
                        "remote_content_sha256": remote_hash,
                        "update_available": remote_hash != current_hash,
                        "update_channel_supported": True,
                        "source_provider": "github",
                        "next_version": self._next_version(
                            resource["current_version"],
                            remote_version=remote_metadata.get("version"),
                        ),
                        "source_metadata": {
                            "provider": "github",
                            "owner": owner,
                            "repository": repository,
                            "branch": branch,
                            "directory": resolved_directory,
                            "name": remote_metadata["name"] or resolved_directory.rsplit("/", 1)[-1],
                            "description": remote_metadata["description"],
                        },
                    }
                )
            except Exception as error:
                # One unavailable repository must not hide the update state of
                # every other installed source.
                results.append(
                    {
                        "resource_id": resource.get("resource_id"),
                        "source_key": resource.get("source_key"),
                        "current_version": resource.get("current_version"),
                        "current_content_sha256": resource.get("content_sha256"),
                        "remote_content_sha256": None,
                        "update_available": False,
                        "update_channel_supported": True,
                        "source_provider": "github",
                        "error": self._safe_source_error(error),
                    }
                )
        return results

    def update_skill(self, resource_id: str) -> dict[str, Any]:
        """Install one changed GitHub Skill as a new server-side version."""

        resource = self.lifecycle.get_resource(resource_id)
        if resource.get("type") != "skill":
            raise ResourceSourceError("only Skill resources can be updated from a repository")
        metadata = resource.get("source_metadata") or {}
        if not isinstance(metadata, Mapping) or metadata.get("provider") != "github":
            raise ResourceSourceError("resource does not have a GitHub source")
        owner, repository, branch = self.validate_repository(
            str(metadata.get("owner", "")),
            str(metadata.get("repository", "")),
            str(metadata.get("branch", "main")),
        )
        directory = self._validate_stored_directory(str(metadata.get("directory", "")))
        files, skill_metadata, resolved_directory = self._fetch_skill_files(
            owner, repository, branch, directory
        )
        remote_hash = self._files_content_hash(files)
        if remote_hash == resource.get("content_sha256"):
            raise ResourceStateError("resource is already up to date")
        source_key = self.resolved_source_key(owner, repository, resolved_directory)
        archive = self._build_skill_archive(
            resource_id=resource_id,
            source_key=source_key,
            source_url=self._skill_source_url(owner, repository, branch, resolved_directory),
            version=self._next_version(
                resource["current_version"],
                remote_version=skill_metadata.get("version"),
            ),
            metadata={
                "provider": "github",
                "owner": owner,
                "repository": repository,
                "branch": branch,
                "directory": resolved_directory,
                "name": skill_metadata["name"] or resolved_directory.rsplit("/", 1)[-1],
                "description": skill_metadata["description"],
            },
            files=files,
        )
        temporary = self.lifecycle.imports_path / f"remote-update-{uuid.uuid4().hex}.zip"
        temporary.write_bytes(archive)
        try:
            return self.lifecycle.update_archive(temporary, expected_resource_id=resource_id)
        finally:
            temporary.unlink(missing_ok=True)

    def _github_archive_url(self, owner: str, name: str, branch: str) -> str:
        return self.validate_remote_url(
            f"https://codeload.github.com/{owner}/{name}/zip/refs/heads/{urllib.parse.quote(branch, safe='/')}"
        )

    def _resolve_repository_branch(
        self, owner: str, name: str, branch: str | None
    ) -> str:
        owner, name = self._validate_repository_identity(owner, name)
        if branch is not None and str(branch).strip():
            _, _, validated_branch = self.validate_repository(owner, name, branch)
            return validated_branch

        metadata_url = self.validate_remote_url(
            f"https://api.github.com/repos/{owner}/{name}"
        )
        metadata = self._request_json(metadata_url)
        default_branch = metadata.get("default_branch")
        if not isinstance(default_branch, str) or not default_branch.strip():
            raise ResourceSourceError("GitHub repository default branch is unavailable")
        _, _, validated_branch = self.validate_repository(owner, name, default_branch)
        return validated_branch

    def _fetch_skill_files(
        self, owner: str, name: str, branch: str, directory: str
    ) -> tuple[dict[str, bytes], dict[str, str], str]:
        payload = self._download_bytes(self._github_archive_url(owner, name, branch))
        members = self._read_repository_zip(payload)
        root = self._repository_root(members)
        resolved_directory = self._resolve_skill_directory(members, root, directory)
        if resolved_directory is None:
            selected = {
                PurePosixPath(member_name).relative_to(root).as_posix(): content
                for member_name, content in members.items()
                if member_name.startswith(f"{root}/")
                and not member_name.endswith("/")
            }
            resolved_directory = "."
        else:
            prefix = f"{root}/{resolved_directory}/"
            selected = {
                member_name[len(prefix):]: content
                for member_name, content in members.items()
                if member_name.startswith(prefix)
                and member_name != prefix
                and not member_name.endswith("/")
            }
        entry_names = [path for path in selected if PurePosixPath(path).name.casefold() == "skill.md"]
        if len(entry_names) != 1:
            raise ResourceSourceError("the requested directory does not contain SKILL.md")
        entry_name = entry_names[0]
        selected["SKILL.md"] = selected.pop(entry_name)
        return selected, self._parse_skill_front_matter(selected["SKILL.md"]), resolved_directory

    @classmethod
    def _resolve_skill_directory(
        cls, members: Mapping[str, bytes], repository_root: str, raw_directory: str
    ) -> str | None:
        """Resolve a skills.sh directory hint to a real repository Skill directory.

        skills.sh may return only the final Skill name (for example
        ``agent-browser``), while the Git repository stores it below a catalog
        path such as ``skills/agent-browser``.  A complete directory is always
        preferred.  Fallback discovery is anchored on ``SKILL.md`` so a
        same-name package or wrapper directory cannot be installed by mistake.

        ``raw_directory`` 可能是安装时存下的仓库根标记（``"."``），
        那种情况下要直接回到「仓库本身就是 Skill」这条路径，
        而不是把标记当成一个同名子目录去找。
        """

        if str(raw_directory).strip() == cls.REPOSITORY_ROOT_MARKER:
            return None

        requested = cls._validate_directory(raw_directory)
        direct_prefix = PurePosixPath(repository_root, requested)
        if any(
            PurePosixPath(member_name).parent == direct_prefix
            and PurePosixPath(member_name).name.casefold() == "skill.md"
            for member_name in members
        ):
            return requested

        target_name = PurePosixPath(requested).name.casefold()
        candidates: set[str] = set()
        for member_name in members:
            path = PurePosixPath(member_name)
            if path.name.casefold() != "skill.md" or len(path.parts) < 2:
                continue
            try:
                relative = path.relative_to(repository_root)
            except ValueError:
                continue
            parent = relative.parent
            if str(parent) == "." or parent.name.casefold() != target_name:
                continue
            candidates.add(parent.as_posix())

        if len(candidates) > 1:
            choices = ", ".join(sorted(candidates)[:4])
            raise ResourceSourceError(
                f"Skill directory is ambiguous; multiple SKILL.md directories match '{requested}': {choices}"
            )
        if candidates:
            return next(iter(candidates))

        # A repository can itself be a Skill.  Keep the requested name as the
        # stable source identity in _fetch_skill_files because there is no
        # repository-relative directory to store, while selecting the root
        # files for installation.
        if any(
            PurePosixPath(member_name).parent == PurePosixPath(repository_root)
            and PurePosixPath(member_name).name.casefold() == "skill.md"
            for member_name in members
        ):
            return None

        return requested

    @staticmethod
    def _skill_source_url(owner: str, name: str, branch: str | None, directory: str) -> str:
        if not branch:
            return f"https://github.com/{owner}/{name}"
        base = f"https://github.com/{owner}/{name}/tree/{urllib.parse.quote(branch, safe='/')}"
        return base if directory == "." else f"{base}/{directory}"

    @staticmethod
    def _files_content_hash(files: Mapping[str, bytes]) -> str:
        records = sorted(
            (
                path,
                len(content),
                hashlib.sha256(content).hexdigest(),
            )
            for path, content in files.items()
        )
        return hashlib.sha256(
            b"".join(f"{path}:{size}:{digest}\n".encode("ascii") for path, size, digest in records)
        ).hexdigest()

    @classmethod
    def _next_version(
        cls,
        current_version: str,
        *,
        remote_version: str | None = None,
    ) -> str:
        """Choose the version to install for a repository-sourced Skill.

        原实现只做本地 patch 递增，忽略上游 SKILL.md 里声明的 ``version``：
        安装恒为 ``1.0.0``，更新恒为 ``1.0.1``、``1.0.2``……于是
        ``ResourceLifecycleService`` 的降级保护（比较 semver）对远端 Skill 形同虚设——
        把上游旧版本重新装回来会被当成升级接受。

        现在优先采用上游声明的版本，但只在它确实高于已装版本时采用；
        上游没写版本、版本不可解析或版本反而变低时，仍退回本地 patch 递增，
        保证「内容变了但版本没动」的仓库依然能装进一个新版本。
        """
        try:
            parsed = Version(str(current_version))
        except InvalidVersion:
            parsed = None

        if remote_version:
            try:
                remote = Version(str(remote_version))
            except InvalidVersion:
                remote = None
            if remote is not None and (parsed is None or remote > parsed):
                return str(remote)

        if parsed is None:
            raise ResourceSourceError("installed resource version is invalid")
        release = parsed.release
        major, minor, patch = (release + (0, 0, 0))[:3]
        return f"{major}.{minor}.{patch + 1}"

    @staticmethod
    def _safe_source_error(error: Exception) -> str:
        return f"{type(error).__name__}: {str(error)[:160]}"[:200]

    @classmethod
    def _build_skill_archive(
        cls,
        *,
        resource_id: str,
        source_key: str,
        source_url: str,
        version: str,
        metadata: dict[str, Any],
        files: Mapping[str, bytes],
    ) -> bytes:
        records = [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in sorted(files.items())
        ]
        manifest = {
            "resource_id": resource_id,
            "type": "skill",
            "version": version,
            "source": source_url,
            "source_key": source_key,
            "source_metadata": metadata,
            "entry": "SKILL.md",
            "permissions": ["workflow.read"],
            "files": records,
            "content_sha256": cls._files_content_hash(files),
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as output:
            output.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
            for path, content in files.items():
                output.writestr(path, content)
        return archive.getvalue()

    def _download_bytes(self, url: str) -> bytes:
        url = self._validate_network_target(url)
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/zip, application/json", "User-Agent": "KiraraAI/3"},
        )
        try:
            with urllib.request.build_opener(_ValidatedRedirectHandler(self)).open(
                request, timeout=20
            ) as response:
                content_length = int(response.headers.get("Content-Length", "0") or 0)
                if content_length > MAX_SOURCE_RESPONSE_BYTES:
                    raise ResourceSourceError("remote source response is too large")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_SOURCE_RESPONSE_BYTES:
                        raise ResourceSourceError("remote source response is too large")
                    chunks.append(chunk)
                return b"".join(chunks)
        except ResourceSourceError:
            raise
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as error:
            raise ResourceSourceError("remote source download failed") from error

    def _request_json(self, url: str) -> Mapping[str, Any]:
        data = self._download_bytes(url)
        try:
            payload = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ResourceSourceError("skills.sh returned invalid JSON") from error
        if not isinstance(payload, Mapping):
            raise ResourceSourceError("skills.sh returned an invalid response")
        return payload

    @staticmethod
    def _read_repository_zip(payload: bytes) -> dict[str, bytes]:
        if len(payload) > MAX_ARCHIVE_SIZE_BYTES:
            raise ResourceSourceError("repository archive is too large")
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload), "r")
        except zipfile.BadZipFile as error:
            raise ResourceSourceError("repository archive is invalid") from error
        members: dict[str, bytes] = {}
        total = 0
        try:
            infos = archive.infolist()
            if len(infos) > MAX_MEMBER_COUNT:
                raise ResourceSourceError("repository archive contains too many files")
            for info in infos:
                name = PurePosixPath(info.filename)
                if info.filename.endswith("/"):
                    continue
                if name.is_absolute() or any(part in {"", ".", ".."} for part in name.parts):
                    raise ResourceSourceError("repository archive contains an unsafe path")
                if info.file_size < 0 or info.file_size > MAX_MEMBER_SIZE_BYTES:
                    raise ResourceSourceError("repository archive member is too large")
                total += info.file_size
                if total > MAX_UNCOMPRESSED_SIZE_BYTES:
                    raise ResourceSourceError("repository archive expands beyond the size limit")
                members[name.as_posix()] = archive.read(info)
        finally:
            archive.close()
        return members

    @staticmethod
    def _repository_root(members: Mapping[str, bytes]) -> str:
        roots = {name.split("/", 1)[0] for name in members}
        if len(roots) != 1:
            raise ResourceSourceError("repository archive has an ambiguous root")
        return next(iter(roots))

    @staticmethod
    def _parse_skill_front_matter(content: bytes) -> dict[str, str]:
        """Read ``name`` / ``description`` / ``version`` from a Skill's front matter.

        ``version`` 此前完全没有被读取，安装时写死 ``1.0.0``。这里把上游声明的
        版本取出来，不可解析或缺失时回落到 ``1.0.0``，让「上游声明什么版本」这件事
        第一次真正进入版本决策。
        """
        default_version = "1.0.0"
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ResourceSourceError("SKILL.md must be UTF-8 text") from error
        empty = {"name": "", "description": "", "version": default_version}
        if not text.startswith("---"):
            return dict(empty)
        lines = text.splitlines()
        try:
            end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
        except StopIteration:
            return dict(empty)
        values: dict[str, str] = dict(empty)
        for line in lines[1:end]:
            key, separator, value = line.partition(":")
            if separator and key.strip() in values:
                values[key.strip()] = value.strip().strip("'\"")[:1000]
        if not values["version"]:
            values["version"] = default_version
        else:
            try:
                Version(values["version"])
            except InvalidVersion:
                # 版本写得不合规范时不能中断安装，退回默认值即可。
                values["version"] = default_version
        return values

    @staticmethod
    def _resource_id(source_key: str) -> str:
        digest = hashlib.sha256(source_key.encode("utf-8")).hexdigest()[:16]
        return f"skill.{digest}"

    @staticmethod
    def _build_manifest(
        *,
        resource_id: str,
        source_key: str,
        source_url: str,
        metadata: dict[str, Any],
        files: Mapping[str, bytes],
    ) -> tuple[dict[str, Any], dict[str, bytes]]:
        records = [
            {
                "path": path,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for path, content in sorted(files.items())
        ]
        content_hash = hashlib.sha256(
            b"".join(
                f"{item['path']}:{item['size']}:{item['sha256']}\n".encode("ascii")
                for item in records
            )
        ).hexdigest()
        return (
            {
                "resource_id": resource_id,
                "type": "skill",
                "version": "1.0.0",
                "source": source_url,
                "source_key": source_key,
                "source_metadata": metadata,
                "entry": "SKILL.md",
                "permissions": ["workflow.read"],
                "files": records,
                "content_sha256": content_hash,
            },
            dict(files),
        )

    @staticmethod
    def _normalize_search_results(
        payload: Mapping[str, Any], query: str, limit: int, offset: int
    ) -> dict[str, Any]:
        raw_items = payload.get("skills", payload.get("results", []))
        if not isinstance(raw_items, list):
            raw_items = []
        skills: list[dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, Mapping):
                continue
            raw_id = str(item.get("id", "")).strip()
            source = str(item.get("source", "")).strip()
            skill_id = str(item.get("skillId", item.get("directory", ""))).strip().strip("/")
            raw_branch = item.get("branch")
            branch = str(raw_branch).strip() if raw_branch is not None else ""
            branch = branch or None
            if not source and ":" in raw_id:
                source, skill_id = raw_id.split(":", 1)
            if not source or "/" not in source or not skill_id:
                continue
            owner, repository = source.split("/", 1)
            try:
                owner, repository = ResourceSourceService._validate_repository_identity(
                    owner, repository
                )
                if branch is not None:
                    _, _, branch = ResourceSourceService.validate_repository(
                        owner, repository, branch
                    )
                source_key = ResourceSourceService.source_key(owner, repository, skill_id)
            except ResourceSourceError:
                continue
            skills.append(
                {
                    "source_key": source_key,
                    "owner": owner,
                    "repository": repository,
                    "branch": branch,
                    "directory": skill_id,
                    "name": str(item.get("name", skill_id.rsplit("/", 1)[-1])),
                    "description": str(item.get("description", "")),
                    "installs": int(item.get("installs", 0) or 0),
                    "source_url": ResourceSourceService._skill_source_url(
                        owner, repository, branch, skill_id
                    ),
                }
            )
        total = payload.get("total", payload.get("total_count", len(skills)))
        try:
            total = max(0, int(total))
        except (TypeError, ValueError):
            total = len(skills)
        return {
            "query": query,
            "skills": skills,
            "total_count": total,
            "limit": limit,
            "offset": offset,
        }
