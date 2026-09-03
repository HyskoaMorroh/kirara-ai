"""故障转移队列的顺序必须能在**看到队列的地方**改（需求 8）。

需求 8 的队列语义是「按队列优先级选择供应商（P1 优先）」。此前 `priority` 只能在
供应商编辑表单里逐个填数字：想把 P3 提到 P1，得先记住另外两家各是多少、
再算一个中间值填进去，而这三次编辑分散在三个不同的表单里。队列页
（`ResilienceView`）能看到实际次序却只读——**看的地方和改的地方分离**。

这条路由按「一次给出整条队列的新次序」工作，理由是逐个写数字会经过中间态：
把 P3 改成 1 的那一刻，队列里出现两个 1，而两个 1 的相对次序由列表下标决定
（`get_provider_candidates` 的第二排序键），也就是用户看不见的东西。

三条边界：

1. **必须给全**。只提交一部分会让未提交的那些落在哪里取决于它们原本的数字，
   而用户以为自己排的是整条队列。
2. **复用已在使用的数字**。重排只交换「谁拿哪个数字」，不发明新数字——
   同一家供应商可能同时服务多个模型，凭空抬高它的 priority 会连带改动
   另一条队列的次序，而那条队列此刻不在屏幕上。
3. **相等的数字要拆开**。全都是默认 100 时，多重集重排等于什么都没做；
   必须产出严格递增的一组值，否则保存成功而次序没变。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig, WebConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.adapter import LLMBackendAdapter, LLMChatProtocol
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.plugin_manager.resource_lifecycle import ResourceLifecycleService
from kirara_ai.web.app import create_web_api_app
from kirara_ai.web.auth.services import AuthService, MockAuthService


class _Stub(LLMBackendAdapter, LLMChatProtocol):
    def __init__(self, name: str) -> None:
        self.backend_name = name

    def chat(self, req: LLMChatRequest) -> LLMChatResponse:
        return LLMChatResponse(
            model=req.model or "model-a",
            message=Message(role="assistant", content=[LLMChatTextContent(text="ok")]),
        )


def _api(
    tmp_path: Path,
    *,
    priorities: dict[str, int],
    models: dict[str, list[str]] | None = None,
    creator: bool = True,
):
    """装一个带真实 `LLMManager` 的 API。

    `models` 给出每家供应商服务哪些模型，默认全都只服务 `model-a`——
    跨模型共享一家供应商是边界 2 的场景，需要显式构造。
    """
    models = models or {name: ["model-a"] for name in priorities}
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    container.register(EventBus, EventBus())
    config = GlobalConfig()
    config.web = WebConfig(secret_key="k", password_file=str(tmp_path / "pw.hash"))
    config.llms.api_backends = [
        LLMBackendConfig(
            name=name, adapter="stub", enable=True, models=models[name], priority=priority
        )
        for name, priority in priorities.items()
    ]
    container.register(GlobalConfig, config)
    container.register(AuthService, MockAuthService(creator=creator))
    container.register(LLMBackendRegistry, LLMBackendRegistry())
    container.register(
        ResourceLifecycleService, ResourceLifecycleService(tmp_path / "runtime")
    )
    manager = LLMManager(container)
    container.register(LLMManager, manager)
    # 手工装 active_backends：注册表里没有 `stub` 适配器，而这份测试断言的是
    # 排序与落盘，与适配器能否实例化无关。
    for name in priorities:
        adapter = _Stub(name)
        manager.backends[name] = adapter
        for model in models[name]:
            manager.active_backends.setdefault(model, []).append(adapter)
    manager._initialize_resilience_state()

    app = create_web_api_app(container)
    app.config["TESTING"] = True
    return app.test_client(), manager, config


def _headers() -> dict[str, str]:
    return {"Authorization": "Bearer mock_token"}


def _priorities(config: GlobalConfig) -> dict[str, int]:
    return {backend.name: backend.priority for backend in config.llms.api_backends}


def _queue(manager: LLMManager, model: str = "model-a") -> list[str]:
    """管理器实际会按什么次序尝试——这是唯一算「生效」的判据。"""
    return [
        getattr(adapter, "backend_name", "")
        for adapter in manager.get_provider_candidates(model)
    ]


@pytest.mark.asyncio
async def test_reordering_changes_the_order_the_manager_actually_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """断言落在 `get_provider_candidates` 上，不是落在写进去的数字上。

    数字只是手段：只断言 priority 变了，会让一次「写对了数字但排序键读的是
    别的东西」的实现通过测试。
    """
    client, manager, config = _api(tmp_path, priorities={"a": 10, "b": 20, "c": 30})
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)
    assert _queue(manager) == ["a", "b", "c"]

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["c", "a", "b"]},
    )

    assert response.status_code == 200
    assert _queue(manager) == ["c", "a", "b"]


@pytest.mark.asyncio
async def test_it_reuses_the_numbers_already_in_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """重排只交换「谁拿哪个数字」，不发明新数字。

    同一家供应商可以同时服务多个模型；凭空抬高它的 priority 会连带改动
    另一条此刻不在屏幕上的队列。
    """
    client, _, config = _api(tmp_path, priorities={"a": 10, "b": 20, "c": 30})
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["c", "a", "b"]},
    )

    assert _priorities(config) == {"c": 10, "a": 20, "b": 30}


@pytest.mark.asyncio
async def test_equal_priorities_are_split_into_a_strict_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """全是默认 100 时也必须真的排开。

    多重集重排在这种情况下是恒等变换：保存成功而次序没变，
    而那正是「新建三个供应商后拖不动」的现场表现。
    """
    client, manager, config = _api(tmp_path, priorities={"a": 100, "b": 100, "c": 100})
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["c", "b", "a"]},
    )

    assert response.status_code == 200
    assert _queue(manager) == ["c", "b", "a"]
    values = [config.llms.api_backends[i].priority for i in range(3)]
    assert len(set(values)) == 3, "相等的 priority 没被拆开，次序仍由列表下标决定"


@pytest.mark.asyncio
async def test_a_partial_queue_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """只提交一部分会让未提交的那几家落在哪里取决于旧数字。"""
    client, _, config = _api(tmp_path, priorities={"a": 10, "b": 20, "c": 30})
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["c", "a"]},
    )

    assert response.status_code == 400
    assert _priorities(config) == {"a": 10, "b": 20, "c": 30}


@pytest.mark.asyncio
async def test_an_unknown_provider_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    client, _, config = _api(tmp_path, priorities={"a": 10, "b": 20})
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["a", "nope"]},
    )

    assert response.status_code == 400
    assert _priorities(config) == {"a": 10, "b": 20}


@pytest.mark.asyncio
async def test_a_duplicated_provider_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """同一家出现两次时「它排第几」没有答案，长度校验也会被蒙过去。"""
    client, _, config = _api(tmp_path, priorities={"a": 10, "b": 20})
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["a", "a"]},
    )

    assert response.status_code == 400
    assert _priorities(config) == {"a": 10, "b": 20}


@pytest.mark.asyncio
async def test_an_unknown_model_is_404(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """拼错的模型名不能返回 200：那看起来和排序成功一模一样。"""
    client, _, config = _api(tmp_path, priorities={"a": 10})
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-zzz", "providers": ["a"]},
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_a_provider_outside_this_queue_is_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """排一条队列不得动到另一条里独有的那几家。"""
    client, manager, config = _api(
        tmp_path,
        priorities={"a": 10, "b": 20, "solo": 15},
        models={"a": ["model-a"], "b": ["model-a"], "solo": ["model-b"]},
    )
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["b", "a"]},
    )

    assert response.status_code == 200
    assert _priorities(config)["solo"] == 15
    assert _queue(manager, "model-b") == ["solo"]


@pytest.mark.asyncio
async def test_the_new_order_is_persisted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """不落盘的排序在下次启动时消失，而界面已经说过「已保存」。"""
    client, _, _ = _api(tmp_path, priorities={"a": 10, "b": 20})
    saved: list[object] = []
    monkeypatch.setattr(
        ConfigLoader,
        "save_config_with_backup",
        lambda *args, **kwargs: saved.append(args),
    )

    await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["b", "a"]},
    )

    assert saved, "排序没有写入配置文件"


@pytest.mark.asyncio
async def test_a_non_creator_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """改队列次序改变服务器接受流量的方式，与重置熔断同一边界。"""
    client, _, config = _api(tmp_path, priorities={"a": 10, "b": 20}, creator=False)
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["b", "a"]},
    )

    assert response.status_code == 403
    assert _priorities(config) == {"a": 10, "b": 20}


@pytest.mark.asyncio
async def test_the_response_carries_the_refreshed_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """返回新状态，界面不必再发一次请求才能看到新次序。"""
    client, _, _ = _api(tmp_path, priorities={"a": 10, "b": 20})
    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", lambda *a, **k: None)

    response = await client.put(
        "/api/llm/resilience/queue",
        headers=_headers(),
        json={"model": "model-a", "providers": ["b", "a"]},
    )

    payload = await response.get_json()
    rows = payload["data"]
    order = [row["provider"] for row in sorted(rows, key=lambda item: item["priority"])]
    assert order == ["b", "a"]
