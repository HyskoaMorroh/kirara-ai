from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kirara_ai.config.global_config import GlobalConfig, LLMBackendConfig, ModelConfig
from kirara_ai.config.config_loader import ConfigLoader
from kirara_ai.events.event_bus import EventBus
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.plugin_manager.extension_host import ExtensionLifecycleHost
from kirara_ai.plugin_manager.models import (
    ExtensionCapabilities,
    ExtensionManifest,
    LifecycleHook,
)
from kirara_ai.plugin_manager.plugin_event_bus import PluginEventBus
from kirara_ai.scheduler.scheduler import TaskScheduler


class _AutoDetectAdapter:
    async def auto_detect_models(self):
        return ["new-model"]


class _DelayedAutoDetectAdapter:
    def __init__(self):
        self.started = __import__("asyncio").Event()
        self.release = __import__("asyncio").Event()

    async def auto_detect_models(self):
        self.started.set()
        await self.release.wait()
        return ["stale-model"]


class _MutableManager:
    def __init__(self, adapter):
        self.backends = {"test-backend": adapter}
        self.reload_calls = 0

    def get(self, backend_name):
        return self.backends.get(backend_name)

    async def reload_backend(self, backend_name):
        self.reload_calls += 1


class _ReloadFailureManager:
    def __init__(self):
        self.adapter = _AutoDetectAdapter()
        self.backends = {"test-backend": self.adapter}
        self.reload_calls = 0
        self.unload_calls = 0
        self.load_calls = 0

    def get(self, backend_name):
        return self.backends.get(backend_name)

    async def reload_backend(self, backend_name):
        self.reload_calls += 1
        self.backends.pop(backend_name, None)
        raise RuntimeError("updated catalog cannot be loaded")

    async def unload_backend(self, backend_name):
        self.unload_calls += 1
        self.backends.pop(backend_name, None)

    def load_backend(self, backend_name):
        self.load_calls += 1
        self.backends[backend_name] = self.adapter


class _Container:
    def __init__(self, manager, config, extension_host=None):
        self.manager = manager
        self.config = config
        self.extension_host = extension_host

    def has(self, dependency):
        return dependency is ExtensionLifecycleHost and self.extension_host is not None

    def resolve(self, dependency):
        if dependency is LLMManager:
            return self.manager
        if dependency is GlobalConfig:
            return self.config
        if dependency is ExtensionLifecycleHost and self.extension_host is not None:
            return self.extension_host
        raise LookupError(dependency)


@pytest.mark.asyncio
async def test_auto_detect_reload_failure_restores_previous_loaded_backend():
    """更新目录失败后仍需让旧目录对应的后端恢复可用。"""
    old_models = [ModelConfig(id="old-model", ability=0)]
    backend = SimpleNamespace(name="test-backend", models=old_models)
    config = SimpleNamespace(llms=SimpleNamespace(api_backends=[backend]))
    manager = _ReloadFailureManager()
    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler.container = _Container(manager, config)
    scheduler.logger = MagicMock()

    assert await scheduler._detect_backend("test-backend") is False
    assert backend.models == old_models
    assert manager.reload_calls == 1
    assert manager.unload_calls == 0
    assert manager.load_calls == 1
    assert "test-backend" in manager.backends


def _scheduler(manager, backend):
    config = SimpleNamespace(llms=SimpleNamespace(api_backends=[backend]))
    scheduler = TaskScheduler.__new__(TaskScheduler)
    scheduler.container = _Container(manager, config)
    scheduler.logger = MagicMock()
    scheduler._state = {}
    return scheduler


@pytest.mark.asyncio
async def test_auto_detect_discards_result_from_a_replaced_adapter():
    adapter = _DelayedAutoDetectAdapter()
    manager = _MutableManager(adapter)
    old_models = [ModelConfig(id="manual-model", ability=1)]
    backend = LLMBackendConfig(
        name="test-backend",
        adapter="openai",
        config={"endpoint": "https://old.example.test"},
        models=old_models,
    )
    scheduler = _scheduler(manager, backend)

    task = __import__("asyncio").create_task(
        scheduler._detect_backend("test-backend")
    )
    await adapter.started.wait()
    manager.backends["test-backend"] = _AutoDetectAdapter()
    adapter.release.set()

    assert await task is False
    assert backend.models == old_models
    assert manager.reload_calls == 0


@pytest.mark.asyncio
async def test_auto_detect_discards_result_after_backend_config_is_edited():
    adapter = _DelayedAutoDetectAdapter()
    manager = _MutableManager(adapter)
    old_models = [ModelConfig(id="manual-model", ability=1)]
    backend = LLMBackendConfig(
        name="test-backend",
        adapter="openai",
        config={"endpoint": "https://old.example.test", "api_key": "old"},
        models=old_models,
    )
    scheduler = _scheduler(manager, backend)

    task = __import__("asyncio").create_task(
        scheduler._detect_backend("test-backend")
    )
    await adapter.started.wait()
    backend.config = {
        "endpoint": "https://new.example.test",
        "api_key": "new",
    }
    adapter.release.set()

    assert await task is False
    assert backend.models == old_models
    assert manager.reload_calls == 0


@pytest.mark.asyncio
async def test_auto_detect_updates_only_models_on_a_matching_backend(monkeypatch):
    backend = LLMBackendConfig(
        name="test-backend",
        adapter="openai",
        config={"endpoint": "https://example.test", "api_key": "keep"},
        enable=True,
        models=[ModelConfig(id="old", ability=1)],
        auto_detect_interval_days=9,
    )
    manager = _MutableManager(_AutoDetectAdapter())
    scheduler = _scheduler(manager, backend)
    before = backend.model_dump(exclude={"models"})
    monkeypatch.setattr(
        ConfigLoader,
        "save_config_with_backup",
        lambda *_args, **_kwargs: None,
    )

    assert await scheduler._detect_backend("test-backend") is True

    assert backend.model_dump(exclude={"models"}) == before
    assert [model.id for model in backend.models] == ["new-model"]
    assert manager.reload_calls == 1


@pytest.mark.asyncio
async def test_auto_detect_emits_sanitized_model_catalog_lifecycle(monkeypatch):
    backend = LLMBackendConfig(
        name="test-backend",
        adapter="openai",
        config={"endpoint": "https://example.test"},
        models=[ModelConfig(id="private-old-model", ability=1)],
    )
    manager = _MutableManager(_AutoDetectAdapter())
    received = []
    host = ExtensionLifecycleHost()
    plugin_bus = PluginEventBus(
        EventBus(),
        manifest=ExtensionManifest(
            name="catalog-observer",
            version="1",
            capabilities=ExtensionCapabilities(lifecycle_hooks=True),
            hooks=[LifecycleHook(name="model_catalog_refreshed")],
        ),
    )
    plugin_bus.register_lifecycle_hook("model_catalog_refreshed", received.append)
    host.register(plugin_bus)
    scheduler = _scheduler(manager, backend)
    scheduler.container.extension_host = host
    monkeypatch.setattr(
        ConfigLoader,
        "save_config_with_backup",
        lambda *_args, **_kwargs: None,
    )

    assert await scheduler._detect_backend("test-backend") is True
    assert received == [
        {
            "component": "scheduler",
            "backend": "test-backend",
            "old_model_count": 1,
            "new_model_count": 1,
            "status": "updated",
        }
    ]
    serialized = repr(received)
    assert "private-old-model" not in serialized
    assert "new-model" not in serialized


@pytest.mark.asyncio
async def test_run_once_rolls_back_catalog_when_config_save_fails(monkeypatch):
    old_models = [ModelConfig(id="manual-model", ability=1)]
    backend = LLMBackendConfig(
        name="test-backend",
        adapter="openai",
        config={"endpoint": "https://example.test"},
        enable=True,
        models=old_models,
        auto_detect_interval_days=1,
    )
    manager = _MutableManager(_AutoDetectAdapter())
    scheduler = _scheduler(manager, backend)
    scheduler._save_state = MagicMock(return_value=True)

    def fail_save(*_args, **_kwargs):
        raise OSError("config disk unavailable")

    monkeypatch.setattr(ConfigLoader, "save_config_with_backup", fail_save)

    assert await scheduler.run_once(force=True) == {"test-backend": False}
    assert backend.models == old_models
    assert manager.reload_calls == 2
    assert scheduler._state == {}
    scheduler._save_state.assert_not_called()


@pytest.mark.asyncio
async def test_run_once_restores_retry_state_when_state_save_fails(monkeypatch):
    backend = LLMBackendConfig(
        name="test-backend",
        adapter="openai",
        config={"endpoint": "https://example.test"},
        enable=True,
        models=[ModelConfig(id="old-model", ability=1)],
        auto_detect_interval_days=1,
    )
    manager = _MutableManager(_AutoDetectAdapter())
    scheduler = _scheduler(manager, backend)
    scheduler._state = {"other-backend": "2026-08-01T00:00:00"}
    scheduler._save_state = MagicMock(return_value=False)
    monkeypatch.setattr(
        ConfigLoader,
        "save_config_with_backup",
        lambda *_args, **_kwargs: None,
    )

    assert await scheduler.run_once(force=True) == {"test-backend": True}
    assert [model.id for model in backend.models] == ["new-model"]
    assert scheduler._state == {"other-backend": "2026-08-01T00:00:00"}
    scheduler._save_state.assert_called_once()
