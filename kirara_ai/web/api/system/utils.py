import base64
import hashlib
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from functools import lru_cache
from urllib.parse import urljoin, urlparse

import aiohttp
import psutil
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import sys_tags
from packaging.utils import (
    InvalidSdistFilename,
    InvalidWheelFilename,
    canonicalize_name,
    parse_sdist_filename,
    parse_wheel_filename,
)
from packaging.version import InvalidVersion, Version

# kirara-ai-webui 在 npm 上的 latest 标签仍指向 0.1.0，该版本把 backend.models
# 当作字符串数组渲染（对每一项调用 charAt/charCodeAt 取首字母和配色）。3.3 起
# 后端返回的是 ModelConfig 对象数组，旧前端渲染出的模型卡片会全部空白。
# beta 标签（0.1.1-beta.3 起）才按 model.id / model.type / model.ability 渲染，
# 因此 WebUI 的安装与更新检查统一走 beta 标签。
WEBUI_DIST_TAG = "beta"


def parse_release_version(raw_version: object) -> Version | None:
    """Parse a release version from an external or installed value.

    Registry responses are untrusted input.  A missing, placeholder, or malformed
    value must mean "no update", rather than turning an update check into a 500.
    ``packaging`` also understands the PEP 440 and npm-style spellings used by
    the backend and WebUI release metadata.
    """
    if not isinstance(raw_version, str):
        return None
    value = raw_version.strip()
    if not value or value.lower() == "unknown":
        return None
    value = value.removeprefix("v")
    try:
        return Version(value)
    except InvalidVersion:
        return None


def is_newer_release(candidate: object, current: object) -> bool:
    """Return whether two valid release values prove a forward upgrade."""
    candidate_version = parse_release_version(candidate)
    current_version = parse_release_version(current)
    return (
        candidate_version is not None
        and current_version is not None
        and candidate_version > current_version
    )


def get_installed_version() -> str:
    """获取当前安装的版本号"""
    try:
        # 使用 importlib.metadata 获取已安装的包版本
        from importlib.metadata import PackageNotFoundError, version
        try:
            return version("kirara-ai")
        except PackageNotFoundError:
            # 如果包未安装，尝试从 pkg_resources 获取
            from pkg_resources import get_distribution
            return get_distribution("kirara-ai").version
    except Exception:
        return "0.0.0"  # 如果所有方法都失败，返回默认版本号


@dataclass(frozen=True)
class ArtifactDigest:
    """registry 声明的升级包摘要。

    三种来源各给不同算法：PyPI 的 PEP 691 给 `hashes.sha256`；
    npm 给 `dist.shasum`（SHA-1）与 `dist.integrity`（SRI，通常是 sha512）。
    全都可能缺失，因此这个对象可以是空的——**空不等于通过**，
    调用方必须把空当作拒绝理由（见 `verify_artifact_digest`）。
    """

    sha256: str = ""
    sha1: str = ""
    integrity: str = ""

    def __bool__(self) -> bool:
        return bool(self.sha256 or self.sha1 or self.integrity)

    @staticmethod
    def from_pypi(file_info: object) -> "ArtifactDigest":
        """从 PEP 691 的文件条目里取摘要。

        返回空对象而不是 `None`：`None` 会诱使调用方写成
        `if digest: verify(...)`，于是「registry 没声明哈希」这条最该拒绝的路径
        反而跳过了校验。
        """
        if not isinstance(file_info, dict):
            return ArtifactDigest()
        hashes = file_info.get("hashes")
        if not isinstance(hashes, dict):
            return ArtifactDigest()
        sha256 = hashes.get("sha256")
        return ArtifactDigest(sha256=sha256 if isinstance(sha256, str) else "")

    @staticmethod
    def from_npm(dist: object) -> "ArtifactDigest":
        """从 npm 的 `dist` 里取 `shasum` 与 `integrity`。"""
        if not isinstance(dist, dict):
            return ArtifactDigest()
        shasum = dist.get("shasum")
        integrity = dist.get("integrity")
        return ArtifactDigest(
            sha1=shasum if isinstance(shasum, str) else "",
            integrity=integrity if isinstance(integrity, str) else "",
        )


PYPI_SIMPLE_JSON = "application/vnd.pypi.simple.v1+json"


def _pypi_simple_url(package_name: str, registry: str) -> str:
    """Build a PEP 503/691 project URL from the configured simple index."""
    parsed = urlparse(registry.rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("PyPI registry must be an HTTP(S) URL")
    return f"{registry.rstrip('/')}/{canonicalize_name(package_name)}/"


def _python_version_matches(requires_python: object) -> bool:
    if not requires_python:
        return True
    if not isinstance(requires_python, str):
        return False
    try:
        current_python = Version(".".join(map(str, sys.version_info[:3])))
        return SpecifierSet(requires_python).contains(
            current_python,
            prereleases=True,
        )
    except InvalidSpecifier:
        return False


def _pypi_artifact(
    package_name: str,
    file_info: object,
    compatible_tags: set,
) -> tuple[Version, int, str] | None:
    """Return sortable release metadata for an installable Simple API file."""
    if not isinstance(file_info, dict) or file_info.get("yanked"):
        return None
    if not _python_version_matches(file_info.get("requires-python")):
        return None
    filename = file_info.get("filename")
    download_url = file_info.get("url")
    if not isinstance(filename, str) or not isinstance(download_url, str):
        return None

    expected_name = canonicalize_name(package_name)
    try:
        distribution, release, _, wheel_tags = parse_wheel_filename(filename)
        if distribution != expected_name or not compatible_tags.intersection(wheel_tags):
            return None
        return release, 1, download_url
    except InvalidWheelFilename:
        pass

    try:
        distribution, release = parse_sdist_filename(filename)
        if distribution != expected_name:
            return None
        return release, 0, download_url
    except InvalidSdistFilename:
        return None


async def resolve_pypi_release(
    package_name: str, registry: str = "https://pypi.org/simple"
) -> tuple[str, str, ArtifactDigest]:
    """Resolve the newest installable release **with** the digest the index declares.

    与 `get_latest_pypi_version` 分成两个函数，而不是把后者改成返回三元组：
    后者有多个既有调用点（启动期检查、检查更新接口）与多处测试替身都按二元组
    解包，改 arity 会把一次安全加固变成一次连带破坏。安装路径用这个带摘要的版本，
    只读的「有没有新版本」继续用原来那个——它不下载任何东西，不需要摘要。
    """
    try:
        project_url = _pypi_simple_url(package_name, registry)
        async with aiohttp.ClientSession() as session:
            async with session.get(
                project_url,
                headers={"Accept": PYPI_SIMPLE_JSON},
            ) as response:
                response.raise_for_status()
                data = await response.json()
        compatible_tags = set(sys_tags())
        candidates: list[tuple[Version, int, str, ArtifactDigest]] = []
        for file_info in data.get("files", []):
            artifact = _pypi_artifact(package_name, file_info, compatible_tags)
            if not artifact:
                continue
            release, kind, download_url = artifact
            candidates.append(
                (release, kind, download_url, ArtifactDigest.from_pypi(file_info))
            )
        if not candidates:
            return "0.0.0", "", ArtifactDigest()
        release, _, download_url, digest = max(candidates, key=lambda item: item[:2])
        return str(release), urljoin(project_url, download_url), digest
    except Exception:
        return "0.0.0", "", ArtifactDigest()


async def get_latest_pypi_version(
    package_name: str, registry: str = "https://pypi.org/simple"
) -> tuple[str, str]:
    """Return the newest installable release exposed by a PEP 691 index.

    只回答「最新版本是什么、从哪下」。需要校验摘要的安装路径走
    `resolve_pypi_release`。
    """
    version, url, _ = await resolve_pypi_release(package_name, registry)
    return version, url


async def resolve_npm_release(
    package_name: str,
    registry: str = "https://registry.npmjs.org",
    dist_tag: str = "latest",
) -> tuple[str, str, ArtifactDigest]:
    """获取NPM包指定 dist-tag 的版本、下载URL与 registry 声明的摘要

    Args:
        package_name: npm 包名
        registry: npm registry 地址
        dist_tag: 要解析的 dist-tag，默认 "latest"。
            WebUI 需要传 "beta"：npm 上 kirara-ai-webui 的 latest 仍是 0.1.0，
            该版本把 backend.models 当作字符串数组渲染，而 3.3 起后端返回的是
            ModelConfig 对象数组，会导致「模型列表」整片空白。
            指定的 dist-tag 不存在时自动回退到 latest，避免上游撤销标签后无法安装。

    Returns:
        (版本号, tarball 下载地址, registry 声明的摘要)，失败时返回
        ("0.0.0", "", 空摘要)。空摘要**不等于校验通过**——
        `verify_artifact_digest` 会把它当作拒绝安装的理由。
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{registry}/{package_name}") as response:
                response.raise_for_status()
                data = await response.json()
                dist_tags = data.get("dist-tags", {})
                versions = data.get("versions", {})
                # 依次尝试：请求的 dist-tag -> latest，两者都要求对应版本真实存在
                candidates = [dist_tags.get(dist_tag), dist_tags.get("latest")]
                latest_version = next(
                    (v for v in candidates if v and v in versions), None
                )
                if not latest_version:
                    return "0.0.0", ""
                dist = versions[latest_version].get("dist", {})
                tarball_url = dist["tarball"]
                digest = ArtifactDigest.from_npm(dist)
        return latest_version, tarball_url, digest
    except Exception:
        return "0.0.0", "", ArtifactDigest()


async def get_latest_npm_version(
    package_name: str,
    registry: str = "https://registry.npmjs.org",
    dist_tag: str = "latest",
) -> tuple[str, str]:
    """Return the newest version and tarball URL for a dist-tag.

    只回答「最新版本是什么、从哪下」。需要校验摘要的安装路径走
    `resolve_npm_release`——拆成两个函数而不是改这个函数的 arity，
    是因为它有多个既有调用点与测试替身都按二元组解包。
    """
    version, url, _ = await resolve_npm_release(package_name, registry, dist_tag)
    return version, url
    


def _file_digest(path: str, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact_digest(path: str, expected: "ArtifactDigest | None") -> None:
    """校验下载的升级包，不通过就抛 `ValueError` 并删掉文件。

    此前 `download_file` 算了 SHA-256 并返回，却**没有任何调用点比对它**。
    算了不比对是最坏的一种形态：代码看起来做了校验（有 `hashlib`、有摘要返回值），
    审阅时容易认为已经校验过了。

    实际后果是**镜像源成了任意代码执行的入口**：镜像地址是用户可配的
    （`config.update.pypi_registry` / `npm_registry`），一个被投毒或被中间人替换
    的镜像可以返回任意 wheel，而升级流程会直接 `pip install` 它。TLS 只能证明
    「确实来自这个镜像」，证明不了「这个镜像给的东西没被换过」。

    **摘要缺失时拒绝安装。** 「没人告诉我该是什么」不等于「它是对的」；
    把缺失当通过等于留一个「只要别声明哈希」的绕过口，而投毒者正好可以自己
    决定不声明。

    **有多个摘要时逐个都要过。** 不能因为弱的那个（SHA-1）通过就放行 SHA-256
    不匹配的包。
    """
    if not expected:
        raise ValueError(
            "升级包校验失败：registry 未声明文件摘要，无法确认下载内容未被替换。"
            "请更换为提供哈希的镜像源后重试"
        )
    if not path or not os.path.isfile(path):
        raise ValueError("升级包校验失败：下载文件不存在")

    def _fail(reason: str) -> None:
        # 删掉已知有问题的包：留着它，下一次「重试升级」可能直接拿它。
        try:
            os.remove(path)
        except OSError:
            pass
        raise ValueError(f"升级包校验失败：{reason}")

    checks: list[tuple[str, str, str]] = []
    if expected.sha256:
        checks.append(("sha256", expected.sha256, "SHA-256"))
    if expected.sha1:
        # SHA-1 弱，但有比没有强得多：它仍然挡得住「镜像返回了完全不同的另一个包」
        # 这个主要威胁。
        checks.append(("sha1", expected.sha1, "SHA-1"))

    for algorithm, raw_expected, label in checks:
        actual = _file_digest(path, algorithm)
        if actual.lower() != raw_expected.strip().lower():
            _fail(f"{label} 与 registry 声明不一致")

    if expected.integrity:
        raw = expected.integrity.strip()
        algorithm, separator, encoded = raw.partition("-")
        if not separator or algorithm not in {"sha256", "sha384", "sha512"}:
            # 看不懂的 integrity 既不能当「没声明」放行，也不能当通过——
            # 两种都会让一次投毒安静地成功。
            _fail(f"无法解析 registry 的 integrity 声明（{raw[:32]}）")
        try:
            expected_bytes = base64.b64decode(encoded, validate=True)
        except Exception:  # noqa: BLE001
            _fail("registry 的 integrity 声明不是合法的 base64")
        else:
            if hashlib.new(algorithm, open(path, "rb").read()).digest() != expected_bytes:
                _fail(f"{algorithm} (SRI) 与 registry 声明不一致")

    if not checks and not expected.integrity:
        raise ValueError(
            "升级包校验失败：registry 未声明可用的文件摘要，无法确认下载内容"
        )


async def download_file(url: str, temp_dir: str) -> tuple[str, str]:
    local_filename = os.path.join(temp_dir, url.split('/')[-1])
    sha256_hash = hashlib.sha256()
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                total_size = int(response.headers.get('Content-Length', 0))
                bytes_downloaded = 0

                with open(local_filename, 'wb') as f:
                    async for chunk in response.content.iter_chunked(8192):
                        f.write(chunk)
                        sha256_hash.update(chunk)
                        bytes_downloaded += len(chunk)
                        if total_size > 0:
                            print(f"Downloaded {bytes_downloaded / total_size:.2%}", end='\r')
                print()  # 换行，确保进度条不覆盖后续输出
        return local_filename, sha256_hash.hexdigest()
    except Exception as e:
        print(f"下载失败: {e}")
        return "", ""

def _cpu_description_from_key_values(
    content: str, preferred_keys: tuple[str, ...]
) -> str | None:
    values: dict[str, str] = {}
    for line in content.splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        normalized_key = " ".join(key.casefold().split())
        normalized_value = value.strip()
        if normalized_value and normalized_key not in values:
            values[normalized_key] = normalized_value

    for key in preferred_keys:
        value = values.get(key)
        if value and not value.isdecimal():
            return value
    return None


def _run_cpu_info_command(command: list[str], keys: tuple[str, ...]) -> str | None:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=2,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    cpu_info = _cpu_description_from_key_values(result.stdout, keys)
    if cpu_info:
        return cpu_info

    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(lines) > 1 and lines[0].casefold() in keys:
        return lines[1]
    return None


@lru_cache(maxsize=1)
def get_cpu_info() -> str:
    """Return a useful CPU description in hosts and minimal containers."""
    cpu_info: str | None = None
    if sys.platform == "win32":
        cpu_info = _run_cpu_info_command(["wmic", "cpu", "get", "name"], ("name",))
    elif sys.platform.startswith("linux"):
        try:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as cpu_file:
                cpu_info = _cpu_description_from_key_values(
                    cpu_file.read(),
                    ("model name", "hardware", "cpu model", "processor"),
                )
        except OSError:
            pass
        if not cpu_info:
            cpu_info = _run_cpu_info_command(
                ["lscpu"], ("model name", "model", "architecture")
            )

    return cpu_info or platform.processor().strip() or platform.machine().strip() or "Unknown"

def get_memory_usage() -> dict:
    """获取内存使用情况"""
    process = psutil.Process()
    system_memory = psutil.virtual_memory()
    process_mem = process.memory_full_info().uss
    percent = system_memory.used / (system_memory.total)
    return {
        "percent": percent,
        "total": system_memory.total / 1024 / 1024,  # MB
        "free": system_memory.available / 1024 / 1024,  # MB
        "used": process_mem / 1024 / 1024,  # MB
    }

def get_cpu_usage() -> float:
    """获取CPU使用率"""
    try:
        return psutil.cpu_percent()
    except:
        return 0.0
