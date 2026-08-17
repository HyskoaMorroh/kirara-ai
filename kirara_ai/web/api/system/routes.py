import asyncio
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from packaging import version
from quart import Blueprint, current_app, g, request, send_file, websocket

from kirara_ai.backup import BackupService, BackupValidationError
from kirara_ai.backup.service import MAX_ARCHIVE_SIZE_BYTES
from kirara_ai.config import DATA_PATH
from kirara_ai.config.config_loader import CONFIG_FILE, ConfigLoader
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.im.manager import IMManager
from kirara_ai.internal import set_restart_flag, shutdown_event
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.logger import WebSocketLogHandler, get_logger
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.web.api.system.utils import (WEBUI_DIST_TAG, download_file, get_cpu_info, get_cpu_usage,
                                            get_installed_version, get_latest_npm_version, get_latest_pypi_version,
                                            get_memory_usage)
from kirara_ai.web.auth.services import AuthService
from kirara_ai.workflow.core.workflow import WorkflowRegistry

from ...auth.middleware import require_auth
from .models import SystemStatus, SystemStatusResponse, UpdateCheckResponse

system_bp = Blueprint("system", __name__)

# 记录启动时间
start_time = time.time()

# 获取系统日志记录器
logger = get_logger("System-API")


def get_backup_service() -> BackupService:
    """Create a backup service rooted in the application's configured data directory."""
    return BackupService(DATA_PATH)


async def save_uploaded_backup():
    """Persist one uploaded backup archive to a temporary file for validation."""
    if request.content_length and request.content_length > MAX_BACKUP_UPLOAD_SIZE:
        return None, ({"error": "backup archive exceeds the maximum size"}, 413)

    files = await request.files
    uploaded_file = files.get("backup")
    if not uploaded_file or not uploaded_file.filename:
        return None, ({"error": "backup file is required"}, 400)

    temporary_file = tempfile.NamedTemporaryFile(
        prefix="kirara-upload-", suffix=".kirara-backup.zip", delete=False
    )
    temporary_file.close()
    archive_path = Path(temporary_file.name)
    try:
        await uploaded_file.save(archive_path)
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    return archive_path, None


MAX_BACKUP_UPLOAD_SIZE = MAX_ARCHIVE_SIZE_BYTES

@system_bp.websocket('/logs')
async def logs_websocket():
    """WebSocket端点，用于实时推送日志"""
    try:
        token_data = await websocket.receive()
        
        token = json.loads(token_data)["token"]
    except Exception as e:
        logger.error(f"WebSocket连接错误: {e}")
        await websocket.close(code=1008, reason="Invalid token")
        return
    auth_service: AuthService = g.container.resolve(AuthService)
    if not auth_service.verify_token(token):
        await websocket.close(code=1008, reason="Invalid token")
        return
    try:

        # 将当前WebSocket连接添加到日志处理器
        WebSocketLogHandler.add_websocket(websocket._get_current_object(), asyncio.get_event_loop())
        
        # 保持连接打开，直到客户端断开
        while not shutdown_event.is_set():
            await asyncio.sleep(1)
    finally:
        # 从日志处理器中移除当前连接
        WebSocketLogHandler.remove_websocket(websocket._get_current_object())

@system_bp.route("/config", methods=["GET"])
@require_auth
async def get_system_config():
    """获取系统配置"""
    try:
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        return {
            "web": {
                "host": config.web.host,
                "port": config.web.port
            },
            "plugins": {
                "market_base_url": config.plugins.market_base_url
            },
            "update": {
                "pypi_registry": config.update.pypi_registry,
                "npm_registry": config.update.npm_registry
            },
            "system": {
                "timezone": config.system.timezone
            },
            "tracing": {
                "llm_tracing_content": config.tracing.llm_tracing_content
            }
        }
    except Exception as e:
        return {"error": str(e)}, 500

@system_bp.route("/config/web", methods=["POST"])
@require_auth
async def update_web_config():
    """更新Web配置"""
    try:
        data = await request.get_json()
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        
        config.web.host = data["host"]
        config.web.port = data["port"]
        
        # 保存配置
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        return {"status": "success", "restart_required": True}
    except Exception as e:
        return {"error": str(e)}, 500

@system_bp.route("/config/plugins", methods=["POST"])
@require_auth
async def update_plugins_config():
    """更新插件配置"""
    try:
        data = await request.get_json()
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        
        config.plugins.market_base_url = data["market_base_url"]
        
        # 保存配置
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}, 500

@system_bp.route("/config/update", methods=["POST"])
@require_auth
async def update_registry_config():
    """更新更新源配置"""
    try:
        data = await request.get_json()
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        
        if not hasattr(config, "update"):
            config.update = {}
        
        config.update.pypi_registry = data["pypi_registry"]
        config.update.npm_registry = data["npm_registry"]
        
        # 保存配置
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}, 500

@system_bp.route("/config/system", methods=["POST"])
@require_auth
async def update_system_config():
    """更新系统配置"""
    try:
        data = await request.get_json()
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        
        # 检查时区是否变化
        timezone_changed = False
        if "timezone" in data and data["timezone"] != config.system.timezone:
            config.system.timezone = data["timezone"]
            timezone_changed = True
        
        # 保存配置
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        
        # 如果时区变化，设置系统时区并调用 tzset
        if timezone_changed and hasattr(time, "tzset"):
            os.environ["TZ"] = config.system.timezone
            time.tzset()
            
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}, 500
    
@system_bp.route("/config/tracing", methods=["POST"])
@require_auth
async def update_tracing_config():
    """更新追踪配置"""
    try:
        data = await request.get_json()
        config: GlobalConfig = g.container.resolve(GlobalConfig)
        
        config.tracing.llm_tracing_content = data["llm_tracing_content"]
        
        # 保存配置
        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
        return {"status": "success"}
    except Exception as e:
        return {"error": str(e)}, 500


@system_bp.route("/backups/export", methods=["GET"])
@require_auth
async def export_backup():
    """Export all portable application data as a complete backup archive."""
    try:
        archive_path = get_backup_service().create_backup("export")
        return await send_file(
            archive_path,
            as_attachment=True,
            attachment_filename=archive_path.name,
            mimetype="application/zip",
        )
    except Exception:
        logger.opt(exception=True).error("Failed to export backup")
        return {"error": "backup export failed"}, 500


@system_bp.route("/backups/inspect", methods=["POST"])
@require_auth
async def inspect_backup():
    """Validate an uploaded backup archive without applying any changes."""
    archive_path, error_response = await save_uploaded_backup()
    if error_response:
        return error_response

    try:
        manifest = get_backup_service().inspect_backup(archive_path)
        return {
            "format_version": manifest.format_version,
            "created_at": manifest.created_at,
            "application_version": manifest.application_version,
            "components": sorted(manifest.components),
            "file_count": len(manifest.files),
            "uncompressed_size": manifest.uncompressed_size,
        }
    except BackupValidationError as error:
        return {"error": str(error)}, 400
    except Exception:
        logger.opt(exception=True).error("Failed to inspect backup")
        return {"error": "backup inspection failed"}, 500
    finally:
        archive_path.unlink(missing_ok=True)


@system_bp.route("/backups/import", methods=["POST"])
@require_auth
async def import_backup():
    """Restore an uploaded backup and create a rollback archive before any replacement."""
    archive_path, error_response = await save_uploaded_backup()
    if error_response:
        return error_response

    try:
        result = get_backup_service().restore_backup(archive_path)
        return {
            "status": "success",
            "restored_components": result.restored_components,
            "rollback_backup": result.rollback_path.name,
            "restart_required": True,
        }
    except BackupValidationError as error:
        return {"error": str(error)}, 400
    except Exception:
        logger.opt(exception=True).error("Failed to import backup")
        return {"error": "backup import failed; existing data was preserved"}, 500
    finally:
        archive_path.unlink(missing_ok=True)


@system_bp.route("/backups/rollbacks", methods=["GET"])
@require_auth
async def list_rollback_backups():
    """List locally created rollback archives without exposing their contents."""
    rollbacks = []
    for archive_path in get_backup_service().list_rollbacks():
        stat_result = archive_path.stat()
        rollbacks.append(
            {
                "name": archive_path.name,
                "size": stat_result.st_size,
                "modified_at": stat_result.st_mtime,
            }
        )
    return {"rollbacks": rollbacks}


@system_bp.route("/backups/rollbacks/<backup_name>", methods=["GET"])
@require_auth
async def download_rollback_backup(backup_name: str):
    """Download one locally generated rollback archive by its safe file name."""
    try:
        archive_path = get_backup_service().get_rollback(backup_name)
    except BackupValidationError:
        return {"error": "rollback backup not found"}, 404
    return await send_file(
        archive_path,
        as_attachment=True,
        attachment_filename=archive_path.name,
        mimetype="application/zip",
    )


@system_bp.route("/status", methods=["GET"])
@require_auth
async def get_system_status():
    """获取系统状态"""
    im_manager: IMManager = g.container.resolve(IMManager)
    llm_manager: LLMManager = g.container.resolve(LLMManager)
    plugin_loader: PluginLoader = g.container.resolve(PluginLoader)
    workflow_registry: WorkflowRegistry = g.container.resolve(WorkflowRegistry)

    # 计算运行时间
    uptime = time.time() - start_time

    # 获取活跃的适配器数量
    active_adapters = len(
        [adapter for adapter in im_manager.adapters.values() if adapter.is_running]
    )

    # 获取活跃的LLM后端数量
    active_backends = len(llm_manager.active_backends)

    # 获取已加载的插件数量
    loaded_plugins = len(plugin_loader.plugins)

    # 获取工作流数量
    workflow_count = len(workflow_registry.snapshot_builders())

    # 获取系统资源使用情况
    memory_usage = get_memory_usage()
    cpu_usage = get_cpu_usage()
    
    # 检测代理服务
    has_proxy = bool(os.environ.get('HTTP_PROXY') or os.environ.get('HTTPS_PROXY') or 
                    os.environ.get('http_proxy') or os.environ.get('https_proxy'))

    # 获取CPU信息
    cpu_info = get_cpu_info()

    # 获取Python版本
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # 获取平台信息
    platform_info = f"{sys.platform}"

    status = SystemStatus(
        uptime=uptime,
        active_adapters=active_adapters,
        active_backends=active_backends,
        loaded_plugins=loaded_plugins,
        workflow_count=workflow_count,
        memory_usage=memory_usage,
        cpu_usage=cpu_usage,
        version=get_installed_version(),
        platform=platform_info,
        cpu_info=cpu_info,
        python_version=python_version,
        has_proxy=has_proxy,
    )

    return SystemStatusResponse(status=status).model_dump()


@system_bp.route("/check-update", methods=["GET"])
@require_auth
async def check_update():
    """检查系统更新"""
    config: GlobalConfig = g.container.resolve(GlobalConfig)
    npm_registry = config.update.npm_registry
    
    current_backend_version = get_installed_version()
    latest_backend_version, backend_download_url = await get_latest_pypi_version("kirara-ai")
    
    # 获取前端最新版本信息，但不判断是否需要更新
    # 与自动安装保持一致，取 beta 标签：npm 的 latest (0.1.0) 不兼容 3.3 的
    # ModelConfig 对象格式，会导致模型列表空白
    latest_webui_version, webui_download_url = await get_latest_npm_version(
        "kirara-ai-webui", npm_registry, dist_tag=WEBUI_DIST_TAG
    )
    
    # 只判断后端是否需要更新
    backend_update_available = version.parse(latest_backend_version) > version.parse(current_backend_version)
    
    return UpdateCheckResponse(
        current_backend_version=current_backend_version,
        latest_backend_version=latest_backend_version,
        backend_update_available=backend_update_available,
        backend_download_url=backend_download_url,
        latest_webui_version=latest_webui_version,
        webui_download_url=webui_download_url
    ).model_dump()


@system_bp.route("/update", methods=["POST"])
@require_auth
async def perform_update():
    """执行更新操作"""
    data = await request.get_json()
    update_backend = data.get("update_backend", False)
    update_webui = data.get("update_webui", False)
    temp_dir = tempfile.mkdtemp()
    
    try:
        if update_backend:
            backend_url = data["backend_download_url"]
            backend_file, backend_hash = await download_file(backend_url, temp_dir)
            # 安装后端
            subprocess.run([sys.executable, "-m", "pip", "install", backend_file], check=True)
        
        if update_webui:
            webui_url = data["webui_download_url"]
            webui_file, webui_hash = await download_file(webui_url, temp_dir)
            # 解压并安装前端
            static_dir = current_app.static_folder or "web"
            with tarfile.open(webui_file, "r:gz") as tar:
                # 解压 package/dist 里的所有文件到 web 目录
                for member in tar.getmembers():
                    if member.name.startswith("package/dist/"):
                        # 去掉 "package/dist/" 前缀
                        member.name = member.name[len("package/dist/"):]
                        # 解压到 static 目录
                        tar.extract(member, path=static_dir)
        
        return {"status": "success", "message": "更新完成"}
    
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500
    
    finally:
        shutil.rmtree(temp_dir)


@system_bp.route("/restart", methods=["POST"])
@require_auth
async def restart_system():
    """重启系统"""
    # 记录重启日志，会通过WebSocket发送给所有客户端
    logger.warning("服务器即将重启，请稍候...")
    
    # 设置重启标志
    set_restart_flag()
    shutdown_event.set()
    return {"status": "success", "message": "重启请求已发送"}
