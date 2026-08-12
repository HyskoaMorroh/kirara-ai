import asyncio
import os
import tarfile
import tempfile
import time
from pathlib import Path

import aiohttp
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, Response

from kirara_ai.logger import get_logger
from kirara_ai.web.api.system.utils import download_file, get_latest_npm_version

logger = get_logger("WebUtils")

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
        
        latest_webui_version, webui_download_url = await get_latest_npm_version("kirara-ai-webui", npm_registry)
        
        if not webui_download_url:
            return False, "无法获取WebUI下载地址"
            
        logger.info(f"开始下载WebUI v{latest_webui_version}: {webui_download_url}")
        webui_file, webui_hash = await download_file(webui_download_url, temp_dir)
        
        if not webui_file:
            return False, "WebUI下载失败"
            
        # 确保安装目录存在
        os.makedirs(install_path, exist_ok=True)
        
        # 解压并安装前端
        logger.info(f"开始解压WebUI到 {install_path}")
        with tarfile.open(webui_file, "r:gz") as tar:
            # 解压 package/dist 里的所有文件到安装目录
            for member in tar.getmembers():
                if member.name.startswith("package/dist/"):
                    # 去掉 "package/dist/" 前缀
                    extracted_name = member.name[len("package/dist/"):]
                    if extracted_name:  # 跳过空路径
                        member.name = extracted_name
                        tar.extract(member, path=str(install_path))
                    
        return True, f"WebUI v{latest_webui_version} 安装成功"
    except Exception as e:
        logger.error(f"WebUI安装失败: {e}")
        return False, f"WebUI安装失败: {str(e)}"
    finally:
        if 'temp_dir' in locals():
            import shutil
            shutil.rmtree(temp_dir)
