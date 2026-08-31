"""熔断器重置必须有接口，否则一个被误隔离的上游只能靠重启放回去。

`LLMManager.reset_provider_circuit` 早就存在，但仓库里没有任何路由调用它——
它是一个只能从 Python 交互式会话里碰到的方法。实际后果：熔断被一次上游抖动
打开之后，运维在界面上看得到 `open`，却没有任何动作能把它清掉；
恢复窗口是配置里的固定值，等不起的时候唯一办法是重启整个进程，
而那会一并中断所有正在进行的对话。

这条路由与依赖安装同一边界：它改变服务器接受流量的方式（把一个刚被判定
不健康的上游重新放回队列），因此要求创建者身份，不是「有 scope 就行」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


class _RecordingManager:
    """记录重置调用，并给出一份可断言的健康快照。"""

    def __init__(self) -> None:
        self.reset_calls: list[str] = []
        self.known = {"provider-a"}

    def reset_provider_circuit(self, provider_name: str) -> None:
        if provider_name not in self.known:
            raise KeyError(provider_name)
        self.reset_calls.append(provider_name)

    def get_resilience_status(self) -> list[dict[str, object]]:
        return [{"name": "provider-a", "circuit_state": "closed"}]

    def load_backend(self, name: str) -> None:
        return None

    async def unload_backend(self, name: str) -> None:
        return None


def _make_api(tmp_path: Path, *, creator: bool = True):
    from kirara_ai.llm.llm_manager import LLMManager

    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(name="provider-a", adapter="stub", enable=True, models=["m"]),
    ]
    container.register(GlobalConfig, config)
    container.register(AuthService, MockAuthService(creator=creator))
    container.register(LLMBackendRegistry, LLMBackendRegistry())
    container.register(ResourceLifecycleService, ResourceLifecycleService(tmp_path / "runtime"))
    manager = _RecordingManager()
    container.register(LLMManager, manager)

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), manager


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


@pytest.mark.asyncio
async def test_reset_route_exists_and_calls_the_manager(tmp_path: Path):
    client, manager = _make_api(tmp_path)

    response = await client.post(
        "/api/llm/backends/provider-a/circuit/reset",
        headers=_headers(),
        json={"confirmed": True},
    )

    # 回归点：路由不存在时这里是 404，而 `reset_provider_circuit` 永远不被调用。
    assert response.status_code == 200
    assert manager.reset_calls == ["provider-a"]


@pytest.mark.asyncio
async def test_reset_requires_confirmation(tmp_path: Path):
    client, manager = _make_api(tmp_path)

    response = await client.post(
        "/api/llm/backends/provider-a/circuit/reset",
        headers=_headers(),
        json={},
    )

    # 把一个刚被判定不健康的上游放回队列会立刻影响真实流量，
    # 因此与依赖安装同一口径：要显式确认，不接受空请求体。
    assert response.status_code == 400
    assert manager.reset_calls == []


@pytest.mark.asyncio
async def test_reset_rejects_unsupported_fields(tmp_path: Path):
    client, manager = _make_api(tmp_path)

    response = await client.post(
        "/api/llm/backends/provider-a/circuit/reset",
        headers=_headers(),
        json={"confirmed": True, "recovery_timeout_seconds": 0},
    )

    # 不接受顺带改配置：那是 `PUT /backends/<name>` 的职责，
    # 混在重置里等于一个没有审计记录的配置写入。
    assert response.status_code == 400
    assert manager.reset_calls == []


@pytest.mark.asyncio
async def test_reset_of_an_unknown_backend_is_404_not_500(tmp_path: Path):
    client, manager = _make_api(tmp_path)

    response = await client.post(
        "/api/llm/backends/does-not-exist/circuit/reset",
        headers=_headers(),
        json={"confirmed": True},
    )

    # 「没有这个后端」是客户端问题，不是服务器故障。
    assert response.status_code == 404
    assert manager.reset_calls == []


@pytest.mark.asyncio
async def test_reset_response_carries_the_refreshed_status(tmp_path: Path):
    client, _ = _make_api(tmp_path)

    response = await client.post(
        "/api/llm/backends/provider-a/circuit/reset",
        headers=_headers(),
        json={"confirmed": True},
    )

    body = await response.get_json()
    # 界面按钮点完要立刻看到新状态，否则只能再发一次 status 请求，
    # 而那两次请求之间的窗口足够让人以为重置没生效。
    assert body["data"][0]["circuit_state"] == "closed"


@pytest.mark.asyncio
async def test_non_creator_is_refused(tmp_path: Path):
    client, manager = _make_api(tmp_path, creator=False)

    response = await client.post(
        "/api/llm/backends/provider-a/circuit/reset",
        headers=_headers(),
        json={"confirmed": True},
    )

    # 403 而不是 401：token 有效，缺的是身份。与依赖安装同一口径——
    # 仅凭 scope 判定不够，默认签发的 token 带 `["*"]`。
    assert response.status_code == 403
    assert manager.reset_calls == []
