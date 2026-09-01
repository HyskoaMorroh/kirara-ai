import asyncio
import json
import os
import shutil
import tarfile
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

import aiohttp
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response

from kirara_ai.logger import get_logger
from kirara_ai.web.api.system.utils import WEBUI_DIST_TAG, download_file, get_latest_npm_version

logger = get_logger("WebUtils")
MAX_WEBUI_ARCHIVE_FILES = 20_000
MAX_WEBUI_ARCHIVE_BYTES = 512 * 1024 * 1024


def get_installed_webui_version(install_path: Path) -> str:
    """Read the immutable npm package version emitted with the WebUI build."""
    try:
        metadata = json.loads(
            (Path(install_path) / "version.json").read_text(encoding="utf-8")
        )
        if not isinstance(metadata, dict):
            return "unknown"
        package_version = metadata.get("packageVersion")
        if isinstance(package_version, str) and package_version.strip():
            return package_version.strip()
        return "unknown"
    except (OSError, json.JSONDecodeError):
        return "unknown"


def static_build_freshness(*, installed: str, expected: str) -> dict[str, object]:
    """Compare the served static build against the version the backend ships with.

    `app.py` 把 `$PWD/web` 作为静态目录，而 `webui/` 才是源码——两者版本各走各的。
    本地开发或源码部署时很容易出现「后端是新的、前端是旧的」：用户按新文档去点
    一个按钮，按钮不存在，而 API 探针、健康检查、版本一致性检查全都通过，
    因为它们查的是后端。

    两条刻意的判定：

    - **不同即过期，不比大小。** 静态构建比后端新同样是不一致（例如切分支后
      忘了重建），一样需要重新构建，没有「新一点没关系」这种情形。
    - **读不到不等于过期。** 构建产物不存在是纯 API 部署的合法形态；把它报成
      「过期」会让一个正常部署看起来有问题，运维会去修一个不存在的故障。
    """

    installed_version = str(installed or "").strip()
    expected_version = str(expected or "").strip()
    if (
        not installed_version
        or not expected_version
        or installed_version == "unknown"
        or expected_version == "unknown"
    ):
        return {
            "status": "unknown",
            "stale": False,
            "installed": installed_version or "unknown",
            "expected": expected_version or "unknown",
        }
    stale = installed_version != expected_version
    return {
        "status": "stale" if stale else "current",
        "stale": stale,
        "installed": installed_version,
        "expected": expected_version,
    }


def _safe_webui_member_path(member: tarfile.TarInfo) -> PurePosixPath | None:
    prefix = PurePosixPath("package/dist")
    if "\\" in member.name:
        raise ValueError(f"unsafe WebUI archive member: {member.name}")
    path = PurePosixPath(member.name)
    if path.is_absolute() or ".." in path.parts or path.parts[:2] != prefix.parts:
        if path.parts[:2] == prefix.parts or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"unsafe WebUI archive member: {member.name}")
        return None
    relative = PurePosixPath(*path.parts[2:])
    return relative if relative.parts else None


def _extract_webui_archive(archive_path: Path, destination: Path) -> None:
    extracted_files = 0
    extracted_bytes = 0
    destination = destination.resolve()
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive.getmembers():
            relative = _safe_webui_member_path(member)
            if relative is None:
                continue
            if member.issym() or member.islnk() or not (member.isdir() or member.isfile()):
                raise ValueError(f"unsupported WebUI archive member: {member.name}")
            if member.size < 0:
                raise ValueError(f"invalid WebUI archive member size: {member.name}")
            target = destination.joinpath(*relative.parts).resolve()
            if not target.is_relative_to(destination):
                raise ValueError(f"unsafe WebUI archive member: {member.name}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            extracted_files += 1
            extracted_bytes += member.size
            if extracted_files > MAX_WEBUI_ARCHIVE_FILES:
                raise ValueError("WebUI archive contains too many files")
            if extracted_bytes > MAX_WEBUI_ARCHIVE_BYTES:
                raise ValueError("WebUI archive is too large")
            source = archive.extractfile(member)
            if source is None:
                raise ValueError(f"cannot read WebUI archive member: {member.name}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
    if not extracted_files or not (destination / "index.html").is_file():
        raise ValueError("WebUI archive does not contain package/dist/index.html")


def install_webui_archive(
    archive_path: Path, install_path: Path, package_version: str
) -> None:
    """Validate and atomically replace a WebUI installation from an npm tarball."""
    install_path = Path(install_path).resolve()
    install_path.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=f".{install_path.name}-stage-", dir=install_path.parent)
    )
    backup = install_path.parent / f".{install_path.name}-backup-{uuid.uuid4().hex}"
    moved_existing = False
    try:
        _extract_webui_archive(Path(archive_path), staging)
        metadata_path = staging / "version.json"
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            metadata = {}
        if not isinstance(metadata, dict):
            metadata = {}
        current_version = metadata.get("version")
        metadata["version"] = (
            current_version.strip()
            if isinstance(current_version, str) and current_version.strip()
            else package_version
        )
        metadata["packageVersion"] = package_version
        metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

        if install_path.exists():
            os.replace(install_path, backup)
            moved_existing = True
        os.replace(staging, install_path)
        if moved_existing:
            shutil.rmtree(backup)
    except Exception:
        if moved_existing and not install_path.exists() and backup.exists():
            os.replace(backup, install_path)
        raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        if backup.exists() and install_path.exists():
            shutil.rmtree(backup, ignore_errors=True)

async def create_no_cache_response(file_path: Path, request: Request) -> Response:
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")
    stat = file_path.stat()
    mtime = stat.st_mtime_ns
    size = stat.st_size
    etag = f"{mtime}-{size}"

    if_none_match = request.headers.get("if-none-match")
    if if_none_match == etag:
        return Response(status_code=304)

    response = FileResponse(file_path)
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return response 

async def test_npm_registry_speed(registries: list[str]) -> str:
    """测试多个NPM注册表的速度，返回最快的一个"""
    # 默认使用第一个
    fastest_registry = registries[0]
    fastest_avg_time = float('inf')
    
    # 每个注册表测试3次
    test_count = 3
    
    async def test_registry(registry: str) -> tuple[str, float]:
        total_time = 0
        success_count = 0
        
        for i in range(test_count):
            try:
                start_time = time.time()
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        f"{registry}/kirara-ai-webui", 
                        timeout=aiohttp.ClientTimeout(total=5)
                    ) as response:
                        if response.status == 200:
                            elapsed = time.time() - start_time
                            total_time += elapsed
                            success_count += 1
            except Exception as e:
                logger.warning(f"测试下载源 {registry} 第{i+1}次失败: {e}")
        
        # 计算平均响应时间，如果全部失败则返回无穷大
        avg_time = total_time / success_count if success_count > 0 else float('inf')
        return registry, avg_time
    
    # 并发测试所有注册表
    tasks = [test_registry(registry) for registry in registries]
    results = await asyncio.gather(*tasks)
    
    # 找出平均响应时间最快的注册表
    for registry, avg_time in results:
        if avg_time < fastest_avg_time:
            fastest_avg_time = avg_time
            fastest_registry = registry
    
    if fastest_avg_time != float('inf'):
        logger.info(f"选择最快的下载源: {fastest_registry}，平均响应时间: {fastest_avg_time:.2f}秒")
    else:
        logger.warning(f"所有下载源测试均失败，默认使用: {fastest_registry}")
    
    return fastest_registry

async def install_webui(install_path: Path) -> tuple[bool, str]:
    """
    安装最新版本的WebUI
    
    Args:
        install_path: 安装目录路径
        
    Returns:
        (成功状态, 消息)
    """
    try:
        # 测试多个NPM注册表的速度
        registries = [
            "https://registry.npmjs.org",
            "https://registry.npmmirror.com",
            "https://registry.yarnpkg.com",
            "https://mirrors.ustc.edu.cn/npm/",
        ]
        
        npm_registry = await test_npm_registry_speed(registries)

        temp_dir = tempfile.mkdtemp()
        logger.info(f"开始从 {npm_registry} 获取最新WebUI版本信息")

        latest_webui_version, webui_download_url = await get_latest_npm_version(
            "kirara-ai-webui", npm_registry, dist_tag=WEBUI_DIST_TAG
        )

        if not webui_download_url:
            return False, "无法获取WebUI下载地址"
            
        logger.info(f"开始下载WebUI v{latest_webui_version}: {webui_download_url}")
        webui_file, webui_hash = await download_file(webui_download_url, temp_dir)
        
        if not webui_file:
            return False, "WebUI下载失败"
            
        # 先在同一文件系统安全解压，再原子替换现有前端。
        logger.info(f"开始解压WebUI到 {install_path}")
        install_webui_archive(webui_file, install_path, latest_webui_version)
                    
        return True, f"WebUI v{latest_webui_version} 安装成功"
    except Exception as e:
        logger.error(f"WebUI安装失败: {e}")
        return False, f"WebUI安装失败: {str(e)}"
    finally:
        if 'temp_dir' in locals():
            shutil.rmtree(temp_dir)
