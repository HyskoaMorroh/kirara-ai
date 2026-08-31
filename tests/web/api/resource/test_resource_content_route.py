"""资源正文必须能在界面上看到，而不只能靠 SSH 进容器翻目录。

需求 10 点名「Claude 提示词管理、会话管理、提示词管理」。当前 prompt / skill /
hook 等类型只能走通用的安装 / 启用 / 停用 / 版本 / 备份生命周期——**没有任何
地方能看到那份正文**。而 prompt 这个类型的全部内容就是正文：一个看不到正文的
「提示词管理」回答不了它唯一要回答的问题「现在生效的提示词到底写了什么」。

`ResourceLifecycleService.read_entry_metadata()` 早就存在且返回的正是这些
（entry 路径、正文、已校验摘要、来源、权限），但**零调用点**——与
`UsageSource.ESTIMATED` 当初完全同一形态：有定义、有测试、主链路上没人用。

## 为什么是只读，不是编辑器

安装后的资源正文不能就地改：`content_sha256` 把清单与文件绑在一起，
`read_entry` 每次读取都重新校验摘要。就地编辑会让下一次载入直接失败——
不是「改了没生效」，而是那个资源彻底不可用。

因此正确的模型是「看正文 + 装新版本」，而版本机制已经在那里（`POST
/resources/<id>/versions`，要求版本号递增、自动备份、装完保持停用等待确认）。
提供一个会破坏完整性契约的编辑框，比不提供更糟。

## 四条边界

1. **只读，且只读已注册的版本。** 版本号必须在 `versions` 里，
   否则一个拼错的版本号会变成任意路径读取。
2. **摘要一起返回。** 用户看到的正文与运行时载入的是同一份，
   这一点必须可自证，而不是靠信任。
3. **不返回宿主机路径。** `entry` 是包内相对路径，不是容器/宿主绝对路径。
4. **`resources.read` 即可。** 它不写盘、不执行任何东西；
   要求创建者身份会让「看一眼提示词写了什么」变成一个需要提权的动作。
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from kirara_ai.config.global_config import GlobalConfig, WebConfig
from kirara_ai.im.manager import IMManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.plugin_manager.plugin_loader import PluginLoader
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.web.app import WebServer
from kirara_ai.workflow.core.block.registry import BlockRegistry
from kirara_ai.workflow.core.workflow import WorkflowRegistry
from tests.utils.auth_test_utils import auth_headers, setup_auth_service  # noqa

TEST_SECRET_KEY = "test-secret-key"

PROMPT_BODY = "你是我的研究型办公助手。回答先给结论，再给依据。"


def _manifest(
    resource_id: str = "office.prompt",
    version: str = "1.0.0",
    *,
    body: str = PROMPT_BODY,
    resource_type: str = "prompt",
) -> tuple[dict, dict[str, bytes]]:
    files = {"prompt.md": body.encode("utf-8")}
    file_records = [
        {
            "path": path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for path, content in files.items()
    ]
    content_hash = hashlib.sha256(
        b"".join(
            f"{item['path']}:{item['size']}:{item['sha256']}\n".encode("ascii")
            for item in sorted(file_records, key=lambda item: item["path"])
        )
    ).hexdigest()
    return (
        {
            "resource_id": resource_id,
            "type": resource_type,
            "version": version,
            "source": "local-test-source",
            "entry": "prompt.md",
            "permissions": ["workflow.read"],
            "files": file_records,
            "content_sha256": content_hash,
        },
        files,
    )


def _write_archive(path: Path, manifest: dict, files: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for member, content in files.items():
            archive.writestr(member, content)
    return path


@pytest.fixture
def container(tmp_path: Path) -> DependencyContainer:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.web = WebConfig(
        secret_key=TEST_SECRET_KEY, password_file="test_password.hash"
    )
    container.register(GlobalConfig, config)
    setup_auth_service(container)
    container.register(BlockRegistry, BlockRegistry())

    im_manager = MagicMock(spec=IMManager)
    im_manager.adapters = {}
    container.register(IMManager, im_manager)
    llm_manager = MagicMock(spec=LLMManager)
    llm_manager.active_backends = {}
    container.register(LLMManager, llm_manager)
    plugin_loader = MagicMock(spec=PluginLoader)
    plugin_loader.plugins = []
    container.register(PluginLoader, plugin_loader)
    workflow_registry = MagicMock(spec=WorkflowRegistry)
    workflow_registry.snapshot_builders.return_value = ()
    container.register(WorkflowRegistry, workflow_registry)

    lifecycle = ResourceLifecycleService(
        tmp_path / "data", workflow_registry=None, container=container
    )
    container.register(ResourceLifecycleService, lifecycle)

    web_server = WebServer(container)
    container.register(WebServer, web_server)
    return container


@pytest.fixture
def test_client(container) -> TestClient:
    return TestClient(container.resolve(WebServer).app)


@pytest.fixture
def installed(container, tmp_path: Path):
    lifecycle = container.resolve(ResourceLifecycleService)
    manifest, files = _manifest()
    archive = _write_archive(tmp_path / "prompt.zip", manifest, files)
    return lifecycle.install_archive(archive)


class TestContentIsReadable:
    def test_content_route_returns_the_entry_body(
        self, test_client, auth_headers, installed
    ):
        response = test_client.get(
            "/backend-api/api/resources/office.prompt/content", headers=auth_headers
        )

        assert response.status_code == 200
        payload = response.json()
        # 「提示词管理」唯一要回答的问题：现在生效的提示词到底写了什么。
        assert payload["content"] == PROMPT_BODY
        assert payload["version"] == "1.0.0"
        assert payload["entry"] == "prompt.md"

    def test_response_carries_the_verified_digest(
        self, test_client, auth_headers, installed
    ):
        payload = test_client.get(
            "/backend-api/api/resources/office.prompt/content", headers=auth_headers
        ).json()

        # 用户看到的正文与运行时载入的是同一份，这一点必须可自证而不是靠信任。
        assert payload["content_sha256"] == installed["content_sha256"]
        assert payload["permissions"] == ["workflow.read"]

    def test_response_does_not_leak_host_paths(
        self, test_client, auth_headers, installed, tmp_path
    ):
        body = test_client.get(
            "/backend-api/api/resources/office.prompt/content", headers=auth_headers
        ).text

        # `entry` 是包内相对路径。返回容器或宿主绝对路径等于把部署布局泄出去。
        assert str(tmp_path) not in body
        assert "/app/data" not in body


class TestVersionPinning:
    def test_an_explicit_registered_version_can_be_read(
        self, test_client, auth_headers, container, installed, tmp_path
    ):
        lifecycle = container.resolve(ResourceLifecycleService)
        manifest, files = _manifest(version="2.0.0", body="第二版正文")
        lifecycle.update_archive(
            _write_archive(tmp_path / "v2.zip", manifest, files),
            expected_resource_id="office.prompt",
        )

        first = test_client.get(
            "/backend-api/api/resources/office.prompt/content?version=1.0.0",
            headers=auth_headers,
        ).json()
        current = test_client.get(
            "/backend-api/api/resources/office.prompt/content", headers=auth_headers
        ).json()

        # 回退前想先看看旧版写了什么，是这个入口最实际的用途之一。
        assert first["content"] == PROMPT_BODY
        assert current["content"] == "第二版正文"
        assert current["version"] == "2.0.0"

    def test_an_unregistered_version_is_rejected(
        self, test_client, auth_headers, installed
    ):
        response = test_client.get(
            "/backend-api/api/resources/office.prompt/content?version=9.9.9",
            headers=auth_headers,
        )

        # 拼错的版本号不能变成任意路径读取。
        assert response.status_code in {400, 404}

    def test_a_traversal_version_is_rejected(
        self, test_client, auth_headers, installed
    ):
        response = test_client.get(
            "/backend-api/api/resources/office.prompt/content?version=../../secrets",
            headers=auth_headers,
        )

        assert response.status_code in {400, 404}

    def test_an_unknown_resource_is_404(self, test_client, auth_headers):
        response = test_client.get(
            "/backend-api/api/resources/nope/content", headers=auth_headers
        )
        assert response.status_code == 404


class TestAuthorization:
    def test_read_scope_is_enough(self, test_client, auth_headers, installed):
        # 看一眼提示词写了什么不该是一个需要提权的动作：它不写盘、不执行任何东西。
        response = test_client.get(
            "/backend-api/api/resources/office.prompt/content", headers=auth_headers
        )
        assert response.status_code == 200

    def test_unauthenticated_request_is_rejected(self, test_client, installed):
        response = test_client.get("/backend-api/api/resources/office.prompt/content")
        assert response.status_code in {401, 403}


class TestNoInPlaceEditing:
    """就地改正文会让该资源彻底不可用，因此不提供写入路由。

    `content_sha256` 把清单与文件绑在一起，`read_entry` 每次读取都重新校验。
    就地编辑的后果不是「改了没生效」，而是下一次载入直接失败。改正文的正确
    路径是装一个新版本（`POST /resources/<id>/versions`）——它要求版本号递增、
    自动备份、装完保持停用等待确认。提供一个会破坏完整性契约的编辑框
    比不提供更糟。

    用真实请求探测而不是读路由表：路由表在 Quart blueprint 与 FastAPI 之间
    经过一层适配，形状不是一对一的；而「打过去会不会被接受」才是用户
    能观察到的事实。
    """

    def test_get_is_accepted(self, test_client, auth_headers, installed):
        response = test_client.get(
            "/backend-api/api/resources/office.prompt/content", headers=auth_headers
        )
        # 先钉住只读入口真的在：否则下面几条会因为「压根没有 /content」而虚假通过。
        assert response.status_code == 200

    @pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
    def test_write_methods_are_not_accepted(
        self, test_client, auth_headers, installed, method
    ):
        # 用 `request()` 而不是 `client.delete(json=...)`：DELETE 的便捷方法
        # 不接受 body 参数，而这里要探的正是「带 body 的写入会不会被接受」。
        response = test_client.request(
            method,
            "/backend-api/api/resources/office.prompt/content",
            headers=auth_headers,
            json={"content": "试图就地改写"},
        )

        # 405（方法不允许）或 404（该路径下没有这个方法）都可接受；
        # 2xx 不行——那意味着正文可以被就地改写。
        assert response.status_code >= 400, (
            f"{method.upper()} 被接受了，正文可以就地改写"
        )
        assert response.status_code != 200

    def test_the_version_upload_route_is_the_supported_path(
        self, test_client, auth_headers, installed
    ):
        # 改正文有一条受支持的路径，缺它的话上面那些拒绝就变成了「功能缺失」。
        response = test_client.post(
            "/backend-api/api/resources/office.prompt/versions", headers=auth_headers
        )
        # 没带文件，因此必然失败；这里只确认这条路由存在（不是 404/405）。
        assert response.status_code not in {404, 405}
