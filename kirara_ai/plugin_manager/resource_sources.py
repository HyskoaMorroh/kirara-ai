"""Server-side sources for CC Switch-style Skill discovery and installation.

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


class ResourceSourceError(ResourceValidationError):
    """A source coordinate or remote response violates the source contract."""


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        raise ResourceSourceError("remote source redirect is not allowed")


class ResourceSourceService:
    """Manage repository-backed resources without writing outside DATA_PATH."""

    def __init__(self, lifecycle: ResourceLifecycleService) -> None:
        self.lifecycle = lifecycle

    @staticmethod
    def validate_repository(owner: str, name: str, branch: str) -> tuple[str, str, str]:
        owner = str(owner or "").strip()
        name = str(name or "").strip()
        branch = str(branch or "").strip()
        if not _GITHUB_PART.fullmatch(owner) or not _GITHUB_PART.fullmatch(name):
            raise ResourceSourceError("GitHub repository coordinates are invalid")
        if (
            not _BRANCH_PART.fullmatch(branch)
            or ".." in branch
            or branch.endswith("/")
            or "//" in branch
            or any(character in branch for character in ("?", "#", "\\"))
        ):
            raise ResourceSourceError("GitHub branch is invalid")
        return owner, name, branch

    @classmethod
    def source_key(cls, owner: str, name: str, directory: str) -> str:
        owner, name, _ = cls.validate_repository(owner, name, "main")
        directory = cls._validate_directory(directory)
        return f"{owner}/{name}:{directory}"

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
        if address is not None and (address.is_private or address.is_loopback or not address.is_global):
            raise ResourceSourceError("remote source address is not public")
        return urllib.parse.urlunsplit(("https", hostname, parsed.path, parsed.query, ""))

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
            if PurePosixPath(member_name).name != "SKILL.md":
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
                    "source_url": f"https://github.com/{owner}/{name}/tree/{urllib.parse.quote(branch, safe='/')}/{directory}",
                }
            )
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
        branch: str = "main",
        directory: str,
        source_key: str | None = None,
    ) -> dict[str, Any]:
        owner, name, branch = self.validate_repository(owner, name, branch)
        directory = self._validate_directory(directory)
        generated_source_key = self.source_key(owner, name, directory)
        if source_key is not None and source_key != generated_source_key:
            raise ResourceSourceError("source identity is generated by the server")
        selected, metadata = self._fetch_skill_files(owner, name, branch, directory)
        resource_id = self._resource_id(generated_source_key)
        archive = self._build_skill_archive(
            resource_id=resource_id,
            source_key=generated_source_key,
            source_url=f"https://github.com/{owner}/{name}/tree/{urllib.parse.quote(branch, safe='/')}/{directory}",
            version="1.0.0",
            metadata={
                "provider": "github",
                "owner": owner,
                "repository": name,
                "branch": branch,
                "directory": directory,
                "name": metadata["name"] or directory.rsplit("/", 1)[-1],
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
            if not isinstance(metadata, Mapping) or metadata.get("provider") != "github":
                continue
            owner = str(metadata.get("owner", ""))
            repository = str(metadata.get("repository", ""))
            branch = str(metadata.get("branch", "main"))
            directory = str(metadata.get("directory", ""))
            try:
                owner, repository, branch = self.validate_repository(owner, repository, branch)
                directory = self._validate_directory(directory)
                source_key = self.source_key(owner, repository, directory)
                files, remote_metadata = self._fetch_skill_files(
                    owner, repository, branch, directory
                )
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
                        "next_version": self._next_version(resource["current_version"]),
                        "source_metadata": {
                            "provider": "github",
                            "owner": owner,
                            "repository": repository,
                            "branch": branch,
                            "directory": directory,
                            "name": remote_metadata["name"] or directory.rsplit("/", 1)[-1],
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
        directory = self._validate_directory(str(metadata.get("directory", "")))
        files, skill_metadata = self._fetch_skill_files(owner, repository, branch, directory)
        remote_hash = self._files_content_hash(files)
        if remote_hash == resource.get("content_sha256"):
            raise ResourceStateError("resource is already up to date")
        source_key = self.source_key(owner, repository, directory)
        archive = self._build_skill_archive(
            resource_id=resource_id,
            source_key=source_key,
            source_url=f"https://github.com/{owner}/{repository}/tree/{urllib.parse.quote(branch, safe='/')}/{directory}",
            version=self._next_version(resource["current_version"]),
            metadata={
                "provider": "github",
                "owner": owner,
                "repository": repository,
                "branch": branch,
                "directory": directory,
                "name": skill_metadata["name"] or directory.rsplit("/", 1)[-1],
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

    def _fetch_skill_files(
        self, owner: str, name: str, branch: str, directory: str
    ) -> tuple[dict[str, bytes], dict[str, str]]:
        payload = self._download_bytes(self._github_archive_url(owner, name, branch))
        members = self._read_repository_zip(payload)
        root = self._repository_root(members)
        prefix = f"{root}/{directory}/"
        selected = {
            member_name[len(prefix):]: content
            for member_name, content in members.items()
            if member_name.startswith(prefix)
            and member_name != prefix
            and not member_name.endswith("/")
        }
        if "SKILL.md" not in selected:
            raise ResourceSourceError("the requested directory does not contain SKILL.md")
        return selected, self._parse_skill_front_matter(selected["SKILL.md"])

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
    def _next_version(cls, current_version: str) -> str:
        try:
            parsed = Version(str(current_version))
        except InvalidVersion as error:
            raise ResourceSourceError("installed resource version is invalid") from error
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
        url = self.validate_remote_url(url)
        request = urllib.request.Request(
            url,
            headers={"Accept": "application/zip, application/json", "User-Agent": "KiraraAI/3"},
        )
        try:
            with urllib.request.build_opener(_NoRedirectHandler()).open(
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
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ResourceSourceError("SKILL.md must be UTF-8 text") from error
        if not text.startswith("---"):
            return {"name": "", "description": ""}
        lines = text.splitlines()
        try:
            end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
        except StopIteration:
            return {"name": "", "description": ""}
        values: dict[str, str] = {"name": "", "description": ""}
        for line in lines[1:end]:
            key, separator, value = line.partition(":")
            if separator and key.strip() in values:
                values[key.strip()] = value.strip().strip("'\"")[:1000]
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
            if not source and ":" in raw_id:
                source, skill_id = raw_id.split(":", 1)
            if not source or "/" not in source or not skill_id:
                continue
            owner, repository = source.split("/", 1)
            try:
                source_key = ResourceSourceService.source_key(owner, repository, skill_id)
            except ResourceSourceError:
                continue
            skills.append(
                {
                    "source_key": source_key,
                    "owner": owner,
                    "repository": repository,
                    "directory": skill_id,
                    "name": str(item.get("name", skill_id.rsplit("/", 1)[-1])),
                    "description": str(item.get("description", "")),
                    "installs": int(item.get("installs", 0) or 0),
                    "source_url": f"https://github.com/{source}/tree/main/{skill_id}",
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
