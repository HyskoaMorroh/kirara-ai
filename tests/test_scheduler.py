from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kirara_ai.config.global_config import GlobalConfig, ModelConfig
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.scheduler.scheduler import TaskScheduler


class _AutoDetectAdapter:
    async def auto_detect_models(self):
        return ["new-model"]


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
    def __init__(self, manager, config):
        self.manager = manager
        self.config = config

    def resolve(self, dependency):
        if dependency is LLMManager:
            return self.manager
        if dependency is GlobalConfig:
            return self.config
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
