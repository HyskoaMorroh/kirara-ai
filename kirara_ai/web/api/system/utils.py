import hashlib
import os
import subprocess
import sys
from functools import lru_cache

import aiohttp
import psutil


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


async def get_latest_pypi_version(package_name: str) -> tuple[str, str]:
    """获取包的最新版本和下载URL"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"https://pypi.org/pypi/{package_name}/json") as response:
                response.raise_for_status()
                data = await response.json()
                latest_version = data["info"]["version"]
                # 获取最新版本的wheel包下载URL
                for url_info in data["urls"]:
                    if url_info["packagetype"] == "bdist_wheel":
                        return latest_version, url_info["url"]
        return latest_version, ""
    except Exception:
        return "0.0.0", ""
    

async def get_latest_npm_version(package_name: str, registry: str = "https://registry.npmjs.org") -> tuple[str, str]:
    """获取NPM包的最新版本和下载URL"""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{registry}/{package_name}") as response:
                response.raise_for_status()
                data = await response.json()
                latest_version = data["dist-tags"]["latest"]
                tarball_url = data["versions"][latest_version]["dist"]["tarball"]
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
