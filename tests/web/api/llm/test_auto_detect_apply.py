"""界面上的「自动检测」必须能把结果**存下来**（需求 7）。

第 7 条原文的前半句是「模型管理无法实现自动定期监测更新模型**并保存配置**」。
它是两个独立事实：

- 后台调度器 `TaskScheduler._detect_backend()` 这条路径是完整的——指纹校验、
  写 `backend_config.models`、`reload_backend`、`save_config_with_backup`，
  每一步失败都回滚。
- 而界面上那个「自动检测」按钮打的
  `GET /llm/backends/<name>/auto-detect-models` **只把结果 return**：
  它从不写 `backend_config.models`，也从不落盘。

于是用户点一下、看到模型列表刷出来、以为已经保存了——重启进程后全没了。
这与本项目反复出现的那一类缺陷同形：界面显示成功，后端什么都没落盘。

**修法刻意不是「让 GET 顺手保存」。** 两条理由：
GET 不该有副作用（缓存、预取、重试都会变成静默改配置）；
而 21.1 要求保持公共 API 兼容，把既有 GET 改成 POST 会破坏现有前端调用点。
因此保留 GET 只读，另加一条显式的
`POST /llm/backends/<name>/auto-detect-models/apply`——用户看到的是
「检测 → 预览 → 保存」，而不是「点一下悄悄改了配置」。

这一组测试钉住那条保存链路：真的落盘、真的重载、并发与失败都不留下半套状态。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from kirara_ai.config.global_config import (GlobalConfig, LLMBackendConfig,
                                            ModelConfig)
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.adapter import AutoDetectModelsProtocol
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService

AUTH = {"Authorization": "Bearer mock_token"}


class _DetectingAdapter(AutoDetectModelsProtocol):
    """An adapter whose discovered catalog is under the test's control."""

    def __init__(self, models: list[str]) -> None:
        self.models = models
        self.calls = 0

    async def auto_detect_models(self) -> list[str]:
        self.calls += 1
        return list(self.models)


def _api(tmp_path: Path, adapter: Any, *, saved_models: list[str] | None = None):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    container.register(AuthService, MockAuthService())

    config = GlobalConfig()
    config.llms.api_backends = [
        LLMBackendConfig(
            name="deepseek-official",
            adapter="openai",
            config={"api_key": "test-key"},
            enable=True,
            # `ability` 是必填位掩码；14 与 `normalize_detected_models` 给
            # 自动检测结果填的值一致（TextChat 等能力位），因此夹具与产品同口径。
            models=[
                ModelConfig(id=model, type="llm", ability=14)
                for model in (saved_models or ["old-model"])
            ],
        )
    ]
    container.register(GlobalConfig, config)

    manager = MagicMock(spec=LLMManager)
    manager.get.return_value = adapter
    manager.reload_backend = AsyncMock()
    container.register(LLMManager, manager)

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), config, manager


@pytest.mark.asyncio
async def test_the_read_only_route_stays_read_only(tmp_path, monkeypatch):
    """GET 仍然只读：缓存、预取与重试都不该变成静默改配置。"""
    saved: list[Any] = []
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.ConfigLoader.save_config_with_backup",
        lambda *args, **kwargs: saved.append(args),
    )
    client, config, _ = _api(tmp_path, _DetectingAdapter(["new-a", "new-b"]))

    response = await client.get(
        "/api/llm/backends/deepseek-official/auto-detect-models",
        headers=AUTH,
    )

    assert response.status_code == 200
    assert not saved, "GET 不得写配置"
    assert [model.id for model in config.llms.api_backends[0].models] == ["old-model"]


@pytest.mark.asyncio
async def test_apply_persists_the_detected_catalog(tmp_path, monkeypatch):
    """这是第 7 条要的那一步：检测结果真的落盘。"""
    saved: list[Any] = []
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.ConfigLoader.save_config_with_backup",
        lambda *args, **kwargs: saved.append(args),
    )
    client, config, manager = _api(tmp_path, _DetectingAdapter(["new-a", "new-b"]))

    response = await client.post(
        "/api/llm/backends/deepseek-official/auto-detect-models/apply",
        headers=AUTH,
        json={"confirmed": True},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["saved"] is True
    assert [model["id"] for model in payload["models"]] == ["new-a", "new-b"]
    assert [model.id for model in config.llms.api_backends[0].models] == [
        "new-a",
        "new-b",
    ]
    assert saved, "检测结果没有落盘"
    # 重载后端，否则新目录不会进入 active_backends——界面显示已保存，
    # 而下一次对话仍然按旧目录解析模型。
    manager.reload_backend.assert_awaited_once_with("deepseek-official")


@pytest.mark.asyncio
async def test_apply_requires_confirmation(tmp_path):
    """这个动作改写 `data/config.yaml`，不接受「顺手点一下」。

    与熔断重置同一条口径（`POST .../circuit/reset` 也要 `confirmed`）：
    写配置的动作必须由调用方明示意图。
    """
    client, config, _ = _api(tmp_path, _DetectingAdapter(["new-a"]))

    response = await client.post(
        "/api/llm/backends/deepseek-official/auto-detect-models/apply",
        headers=AUTH,
        json={},
    )

    assert response.status_code == 400
    assert [model.id for model in config.llms.api_backends[0].models] == ["old-model"]


@pytest.mark.asyncio
async def test_apply_requires_authentication(tmp_path):
    client, _, _ = _api(tmp_path, _DetectingAdapter(["new-a"]))

    response = await client.post(
        "/api/llm/backends/deepseek-official/auto-detect-models/apply",
        json={"confirmed": True},
    )

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_apply_on_an_unknown_backend_is_404(tmp_path):
    client, _, _ = _api(tmp_path, _DetectingAdapter(["new-a"]))

    response = await client.post(
        "/api/llm/backends/nope/auto-detect-models/apply",
        headers=AUTH,
        json={"confirmed": True},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_apply_rejects_an_adapter_without_auto_detect(tmp_path):
    class _Plain:
        pass

    client, _, _ = _api(tmp_path, _Plain())

    response = await client.post(
        "/api/llm/backends/deepseek-official/auto-detect-models/apply",
        headers=AUTH,
        json={"confirmed": True},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_an_empty_detection_result_never_wipes_the_saved_catalog(tmp_path, monkeypatch):
    """上游临时返回空目录时不能把已保存的模型清空。

    与调度器同一条判断（`if not models: skip update`）：一次网络抖动或权限
    变更会让上游回一个空列表，照它写回等于让这个后端在工作流里彻底不可选，
    而界面上这一步是「成功」。
    """
    saved: list[Any] = []
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.ConfigLoader.save_config_with_backup",
        lambda *args, **kwargs: saved.append(args),
    )
    client, config, _ = _api(tmp_path, _DetectingAdapter([]))

    response = await client.post(
        "/api/llm/backends/deepseek-official/auto-detect-models/apply",
        headers=AUTH,
        json={"confirmed": True},
    )

    assert response.status_code == 409
    assert not saved
    assert [model.id for model in config.llms.api_backends[0].models] == ["old-model"]


@pytest.mark.asyncio
async def test_a_failed_save_rolls_the_catalog_back(tmp_path, monkeypatch):
    """落盘失败必须回滚内存里的目录。

    不回滚的话进程内是新目录、磁盘上是旧目录，重启之后「保存成功」的那次改动
    凭空消失——而两者之间的所有对话都按新目录跑过。
    """
    def explode(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.ConfigLoader.save_config_with_backup", explode
    )
    client, config, _ = _api(tmp_path, _DetectingAdapter(["new-a"]))

    response = await client.post(
        "/api/llm/backends/deepseek-official/auto-detect-models/apply",
        headers=AUTH,
        json={"confirmed": True},
    )

    assert response.status_code == 500
    assert [model.id for model in config.llms.api_backends[0].models] == ["old-model"]


@pytest.mark.asyncio
async def test_a_failed_reload_rolls_the_catalog_back(tmp_path, monkeypatch):
    """重载失败同样回滚，且**不落盘**。

    先落盘再重载失败会留下一份「磁盘上是新目录、运行中是旧目录」的现场，
    下次重启就静默切到一个从未验证过的目录上。
    """
    saved: list[Any] = []
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.ConfigLoader.save_config_with_backup",
        lambda *args, **kwargs: saved.append(args),
    )
    client, config, manager = _api(tmp_path, _DetectingAdapter(["new-a"]))
    manager.reload_backend = AsyncMock(side_effect=RuntimeError("adapter broken"))

    response = await client.post(
        "/api/llm/backends/deepseek-official/auto-detect-models/apply",
        headers=AUTH,
        json={"confirmed": True},
    )

    assert response.status_code == 500
    assert not saved, "重载失败时不得落盘"
    assert [model.id for model in config.llms.api_backends[0].models] == ["old-model"]


@pytest.mark.asyncio
async def test_applying_an_unchanged_catalog_reports_no_change(tmp_path, monkeypatch):
    """目录没变时如实说「没变」，而不是报告一次成功的保存。

    报成功会让运维以为刚才那次操作改了什么，从而去别处找原因。
    """
    saved: list[Any] = []
    monkeypatch.setattr(
        "kirara_ai.web.api.llm.routes.ConfigLoader.save_config_with_backup",
        lambda *args, **kwargs: saved.append(args),
    )
    client, _, _ = _api(
        tmp_path, _DetectingAdapter(["same-model"]), saved_models=["same-model"]
    )

    response = await client.post(
        "/api/llm/backends/deepseek-official/auto-detect-models/apply",
        headers=AUTH,
        json={"confirmed": True},
    )

    assert response.status_code == 200
    payload = await response.get_json()
    assert payload["saved"] is False
    assert payload["changed"] is False
    assert not saved
