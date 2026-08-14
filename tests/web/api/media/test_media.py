import collections
import os
import tempfile
import time
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from kirara_ai.config.config_loader import CONFIG_FILE
from kirara_ai.config.global_config import GlobalConfig, MediaConfig, WebConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.media.manager import MediaManager
from kirara_ai.media.metadata import MediaMetadata
from kirara_ai.media.types.media_type import MediaType
from kirara_ai.web.app import WebServer
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa

# ==================== 常量区 ====================
TEST_PASSWORD = "test-password"
TEST_SECRET_KEY = "test-secret-key"


# ==================== Fixtures ====================
@pytest.fixture(scope="module")
def temp_media_dir():
    """为媒体文件创建一个临时目录。"""
    temp_dir = tempfile.mkdtemp(prefix="kirara_test_media_api_")
    media_dir = os.path.join(temp_dir, "media")
    # 确保目录存在，因为 MediaManager 会创建它，但测试可能在之前运行
    os.makedirs(media_dir, exist_ok=True)
    print(f"Created temp media dir: {media_dir}")
    yield media_dir
    print(f"Removing temp media dir: {temp_dir}")
    # shutil.rmtree(temp_dir) # 在某些系统上可能会有权限问题，暂时注释掉


@pytest.fixture(scope="module")
def container(temp_media_dir):
    """创建一个带有模拟组件的依赖容器。"""
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())

    # 配置
    config = GlobalConfig()
    config.web = WebConfig(
        secret_key=TEST_SECRET_KEY, password_file="test_password.hash"
    )
    config.media = MediaConfig(
        cleanup_duration=7,
        auto_remove_unreferenced=True,
        last_cleanup_time=int(time.time()) - 86400,  # 昨天
    )
    container.register(GlobalConfig, config)

    # 认证服务
    setup_auth_service(container)  # 基于配置设置认证

    # 媒体管理器 (真实的，但使用临时目录)
    # 如果 MediaManager 使用 __new__，则重置单例实例以进行测试
    if hasattr(MediaManager, "_instance"):
        del MediaManager._instance
    media_manager = MediaManager(media_dir=temp_media_dir)
    container.register(MediaManager, media_manager)

    return container


@pytest.fixture(scope="module")
def app(container):
    """创建 FastAPI 应用实例。"""
    web_server = WebServer(container)
    container.register(WebServer, web_server)
    return web_server.app


@pytest.fixture(scope="module")
def test_client(app):
    """创建一个 TestClient 实例。"""
    # 使用 lifespan 管理器来确保启动和关闭事件被触发
    with TestClient(app) as client:
        yield client


# ==================== 测试用例 ====================
@pytest.mark.usefixtures("test_client", "auth_headers") # 应用 test_client 和 auth_headers
class TestMediaAPI:
    @pytest.fixture(autouse=True)
    def setup_mocks(self, container, temp_media_dir):
        """在每个测试之前设置模拟对象。"""
        # 模拟 MediaManager 方法
        self.mock_media_manager = MagicMock(spec=MediaManager)
        # 确保 mock manager 知道正确的 media_dir 以便 disk_usage 测试
        self.mock_media_manager.media_dir = temp_media_dir

        # 使用 patch 来替换路由中获取 MediaManager 的函数
        self.patcher_get_manager = patch(
            "kirara_ai.web.api.media.routes._get_media_manager",
            return_value=self.mock_media_manager,
        )
        self.mock_get_manager = self.patcher_get_manager.start()

        # 模拟 ConfigLoader 保存
        # 注意：需要模拟 routes.py 中使用的 ConfigLoader 实例或类方法
        self.patcher_save_config = patch(
            "kirara_ai.web.api.media.routes.ConfigLoader.save_config_with_backup"
        )
        self.mock_save_config = self.patcher_save_config.start()

        # 模拟 shutil.disk_usage
        self.patcher_disk_usage = patch("kirara_ai.web.api.media.routes.shutil.disk_usage")
        self.mock_disk_usage = self.patcher_disk_usage.start()

        # 模拟 time.time() 以便检查 last_cleanup_time 的更新
        self.current_time = int(time.time())
        self.patcher_time = patch("kirara_ai.web.api.media.routes.time.time", return_value=self.current_time)
        self.mock_time = self.patcher_time.start()


        yield  # 运行测试

        # 停止 patchers
        self.patcher_get_manager.stop()
        self.patcher_save_config.stop()
        self.patcher_disk_usage.stop()
        self.patcher_time.stop()

    def test_get_system_info(self, test_client, auth_headers, container):
        """测试 GET /system 端点。"""
        config: GlobalConfig = container.resolve(GlobalConfig)
        # media_manager_instance: MediaManager = container.resolve(MediaManager) # 获取真实的实例以获取路径

        # 设置模拟返回值
        mock_media_ids = ["media1", "media2"]
        mock_metadata1 = MediaMetadata(
            media_id="media1",
            media_type=MediaType.IMAGE,
            format="jpg",
            size=1024,
            references={"ref1"},
        )
        mock_metadata2 = MediaMetadata(
            media_id="media2",
            media_type=MediaType.AUDIO,
            format="mp3",
            size=2048,
            references={"ref2"},
        )
        self.mock_media_manager.get_all_media_ids.return_value = mock_media_ids
        self.mock_media_manager.get_metadata.side_effect = lambda mid: (
            mock_metadata1
            if mid == "media1"
            else (mock_metadata2 if mid == "media2" else None)
        )

        mock_disk_usage_result = collections.namedtuple(
            "usage", ["total", "used", "free"]
        )(
            total=10 * 1024 * 1024, used=3 * 1024 * 1024, free=7 * 1024 * 1024
        )
        self.mock_disk_usage.return_value = mock_disk_usage_result

        response = test_client.get("/backend-api/api/media/system", headers=auth_headers)

        assert response.status_code == 200, f"响应内容: {response.text}"
        data = response.json()

        assert data["cleanup_duration"] == config.media.cleanup_duration
        assert data["auto_remove_unreferenced"] == config.media.auto_remove_unreferenced
        assert data["last_cleanup_time"] == config.media.last_cleanup_time
        assert data["total_media_count"] == 2
        assert data["total_media_size"] == 1024 + 2048
        assert data["disk_total"] == mock_disk_usage_result.total
        assert data["disk_used"] == mock_disk_usage_result.used
        assert data["disk_free"] == mock_disk_usage_result.free

        self.mock_media_manager.get_all_media_ids.assert_called_once()
        assert self.mock_media_manager.get_metadata.call_count == 2
        # 验证 disk_usage 使用了正确的路径 (来自 mock manager)
        self.mock_disk_usage.assert_called_once_with(self.mock_media_manager.media_dir)

    def test_set_config(self, test_client, auth_headers, container):
        """测试 POST /system/config 端点。"""
        config: GlobalConfig = container.resolve(GlobalConfig)
        original_duration = config.media.cleanup_duration
        original_auto_remove = config.media.auto_remove_unreferenced

        new_config_data = {"cleanup_duration": 14, "auto_remove_unreferenced": False}

        response = test_client.post(
            "/backend-api/api/media/system/config",
            headers=auth_headers,
            json=new_config_data,
        )

        assert response.status_code == 200, f"响应内容: {response.text}"
        data = response.json()
        assert data["success"] is True

        # 验证容器中的配置对象是否已更新
        assert config.media.cleanup_duration == 14
        assert config.media.auto_remove_unreferenced is False

        # 验证模拟对象是否被调用
        self.mock_media_manager.setup_cleanup_task.assert_called_once_with(container)
        # 验证配置保存时传递了正确的参数
        self.mock_save_config.assert_called_once()
        args, kwargs = self.mock_save_config.call_args
        assert args[0] == CONFIG_FILE
        saved_config = args[1]
        assert isinstance(saved_config, GlobalConfig)
        assert saved_config.media.cleanup_duration == 14
        assert saved_config.media.auto_remove_unreferenced is False


        # 为其他测试恢复原始值（尽管 fixture 应该处理隔离）
        config.media.cleanup_duration = original_duration
        config.media.auto_remove_unreferenced = original_auto_remove

    def test_cleanup_unreferenced(self, test_client, auth_headers, container):
        """测试 POST /system/cleanup-unreferenced 端点。"""
        config: GlobalConfig = container.resolve(GlobalConfig)
        original_last_cleanup_time = config.media.last_cleanup_time

        # 设置清理的模拟返回值
        cleanup_count = 5
        self.mock_media_manager.cleanup_unreferenced.return_value = cleanup_count

        response = test_client.post(
            "/backend-api/api/media/system/cleanup-unreferenced", headers=auth_headers
        )

        assert response.status_code == 200, f"响应内容: {response.text}"
        data = response.json()
        assert data["success"] is True
        assert data["count"] == cleanup_count

        # 验证模拟对象是否被调用
        self.mock_media_manager.cleanup_unreferenced.assert_called_once()
        self.mock_save_config.assert_called_once()
        self.mock_media_manager.setup_cleanup_task.assert_called_once_with(container)

        # 验证 last_cleanup_time 是否已更新 (使用模拟的时间)
        assert config.media.last_cleanup_time == self.current_time

        # 验证保存的配置中 last_cleanup_time 也更新了
        args, kwargs = self.mock_save_config.call_args
        saved_config: GlobalConfig = args[1]
        assert saved_config.media.last_cleanup_time == self.current_time


        # 恢复原始时间（如果需要）
        config.media.last_cleanup_time = original_last_cleanup_time
