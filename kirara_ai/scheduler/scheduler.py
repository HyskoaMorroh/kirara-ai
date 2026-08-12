import asyncio
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional

from kirara_ai.config.config_loader import CONFIG_FILE, ConfigLoader
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.adapter import AutoDetectModelsProtocol
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.logger import get_logger

# 记录每个后端上次自动检测时间的状态文件
STATE_FILE = "data/auto_detect_state.json"

# 后台循环的检查周期（秒），每 24 小时检查一次是否有到期的后端
CHECK_INTERVAL_SECONDS = 86400


class TaskScheduler:
    """
    后台定时任务调度器。

    目前负责按照每个 LLM 后端配置的 auto_detect_interval_days，
    定期调用 auto_detect_models() 刷新模型列表并写回配置文件，
    免除用户在 WebUI 手动点击「自动检测」和「保存配置」。
    """

    def __init__(self, container: DependencyContainer):
        self.container = container
        self.logger = get_logger("TaskScheduler")
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()
        self._state: Dict[str, Any] = self._load_state()

    def _load_state(self) -> Dict[str, Any]:
        """读取上次检测时间的记录"""
        if not os.path.exists(STATE_FILE):
            return {}
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            self.logger.warning(f"Failed to load auto-detect state: {e}")
            return {}

    def _save_state(self) -> None:
        """保存上次检测时间的记录"""
        try:
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.warning(f"Failed to save auto-detect state: {e}")

    def _is_due(self, backend_name: str, interval_days: int) -> bool:
        """判断某个后端是否到了该检测的时间"""
        if interval_days <= 0:
            return False

        last_run = self._state.get(backend_name)
        if not last_run:
            # 从未检测过，立即执行一次
            return True

        try:
            last_time = datetime.fromisoformat(last_run)
        except Exception:
            return True

        elapsed_days = (datetime.now() - last_time).total_seconds() / 86400
        return elapsed_days >= interval_days

    async def _detect_backend(self, backend_name: str) -> bool:
        """
        对单个后端执行自动检测并写回配置。
        :return: 是否成功更新了模型列表
        """
        llm_manager: LLMManager = self.container.resolve(LLMManager)
        config: GlobalConfig = self.container.resolve(GlobalConfig)

        adapter = llm_manager.get(backend_name)
        if not adapter:
            self.logger.warning(f"Backend {backend_name} not loaded, skip auto-detect")
            return False

        if not isinstance(adapter, AutoDetectModelsProtocol):
            self.logger.debug(f"Backend {backend_name} does not support auto-detect, skip")
            return False

        try:
            models = await adapter.auto_detect_models()
        except Exception as e:
            self.logger.error(f"Auto-detect failed for backend {backend_name}: {e}")
            return False

        if not models:
            self.logger.warning(f"Auto-detect returned empty model list for {backend_name}, skip update")
            return False

        backend_config = next(
            (b for b in config.llms.api_backends if b.name == backend_name), None
        )
        if not backend_config:
            self.logger.warning(f"Backend {backend_name} not found in config, skip update")
            return False

        old_models = list(backend_config.models)
        new_models = sorted(set(models))

        if old_models == new_models:
            self.logger.info(f"Backend {backend_name} model list unchanged ({len(new_models)} models)")
            return True

        backend_config.models = new_models
        self.logger.info(
            f"Backend {backend_name} model list updated: "
            f"{len(old_models)} -> {len(new_models)} models"
        )

        # 重新加载后端，让新模型列表进入 active_backends
        try:
            await llm_manager.reload_backend(backend_name)
        except Exception as e:
            self.logger.error(f"Failed to reload backend {backend_name} after auto-detect: {e}")

        return True

    async def run_once(self, force: bool = False) -> Dict[str, bool]:
        """
        遍历所有启用了自动检测的后端，执行一轮检测。
        :param force: 忽略间隔时间，强制对所有配置了间隔的后端执行
        :return: 每个后端的执行结果
        """
        config: GlobalConfig = self.container.resolve(GlobalConfig)
        results: Dict[str, bool] = {}
        config_changed = False

        for backend in list(config.llms.api_backends):
            interval_days = getattr(backend, "auto_detect_interval_days", 0) or 0
            if interval_days <= 0:
                continue
            if not backend.enable:
                continue
            if not force and not self._is_due(backend.name, interval_days):
                continue

            self.logger.info(f"Running auto-detect for backend: {backend.name}")
            success = await self._detect_backend(backend.name)
            results[backend.name] = success

            if success:
                self._state[backend.name] = datetime.now().isoformat()
                config_changed = True

        if config_changed:
            self._save_state()
            try:
                ConfigLoader.save_config_with_backup(CONFIG_FILE, config)
                self.logger.info("Configuration saved after auto-detect")
            except Exception as e:
                self.logger.error(f"Failed to save config after auto-detect: {e}")

        return results

    async def _loop(self) -> None:
        """后台循环，定期检查是否有后端到期"""
        # 启动后先等一会儿，避免和应用启动流程抢资源
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=60)
            return
        except asyncio.TimeoutError:
            pass

        while not self._stop_event.is_set():
            try:
                await self.run_once()
            except Exception as e:
                self.logger.opt(exception=e).error(f"Auto-detect scheduler loop error: {e}")

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=CHECK_INTERVAL_SECONDS
                )
                return
            except asyncio.TimeoutError:
                continue

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        """启动后台调度任务"""
        if self._task is not None and not self._task.done():
            return
        self._stop_event.clear()
        self._task = loop.create_task(self._loop())
        self.logger.info("Auto-detect scheduler started")

    def stop(self) -> None:
        """停止后台调度任务"""
        self._stop_event.set()
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self.logger.info("Auto-detect scheduler stopped")

    def get_status(self) -> Dict[str, Any]:
        """返回各后端的自动检测状态，供 WebUI 展示"""
        config: GlobalConfig = self.container.resolve(GlobalConfig)
        status = []
        for backend in config.llms.api_backends:
            interval_days = getattr(backend, "auto_detect_interval_days", 0) or 0
            last_run = self._state.get(backend.name)
            status.append(
                {
                    "name": backend.name,
                    "interval_days": interval_days,
                    "last_run": last_run,
                    "model_count": len(backend.models),
                }
            )
        return {"running": self._task is not None and not self._task.done(), "backends": status}
