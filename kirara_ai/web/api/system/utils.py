import hashlib
import os
import subprocess
import sys
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


async def get_latest_pypi_version(
    package_name: str, registry: str = "https://pypi.org/simple"
) -> tuple[str, str]:
    """Return the newest installable release exposed by a PEP 691 index."""
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
        candidates = [
            artifact
            for file_info in data.get("files", [])
            if (
                artifact := _pypi_artifact(
                    package_name,
                    file_info,
                    compatible_tags,
                )
            )
        ]
        if not candidates:
            return "0.0.0", ""
        release, _, download_url = max(candidates, key=lambda item: item[:2])
        return str(release), urljoin(project_url, download_url)
    except Exception:
        return "0.0.0", ""
    

async def get_latest_npm_version(
    package_name: str,
    registry: str = "https://registry.npmjs.org",
    dist_tag: str = "latest",
) -> tuple[str, str]:
    """获取NPM包指定 dist-tag 的版本和下载URL

    Args:
        package_name: npm 包名
        registry: npm registry 地址
        dist_tag: 要解析的 dist-tag，默认 "latest"。
            WebUI 需要传 "beta"：npm 上 kirara-ai-webui 的 latest 仍是 0.1.0，
            该版本把 backend.models 当作字符串数组渲染，而 3.3 起后端返回的是
            ModelConfig 对象数组，会导致「模型列表」整片空白。
            指定的 dist-tag 不存在时自动回退到 latest，避免上游撤销标签后无法安装。

    Returns:
        (版本号, tarball 下载地址)，失败时返回 ("0.0.0", "")
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
                tarball_url = versions[latest_version]["dist"]["tarball"]
        return latest_version, tarball_url
    except Exception:
        return "0.0.0", ""
    


async def download_file(url: str, temp_dir: str) -> tuple[str, str]:
    """下载文件并返回文件路径和SHA256"""
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

@lru_cache(maxsize=1)
def get_cpu_info() -> str:
    """获取CPU信息，使用lru_cache进行缓存"""
    try:
        if sys.platform == 'win32':
            # Windows 系统下获取 CPU 信息
            result = subprocess.run(['wmic', 'cpu', 'get', 'name'], capture_output=True, text=True)
            if result.returncode == 0:
                cpu_info = result.stdout.strip().removeprefix('Name').strip()
        else:
            # Linux 系统下获取 CPU 信息
            with open('/proc/cpuinfo', 'r') as f:
                for line in f:
                    if line.startswith('model name'):
                        cpu_info = line.split(':')[1].strip()
                        break
        
        return cpu_info if cpu_info else "Unknown"
    except:
        return "Unknown"

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
