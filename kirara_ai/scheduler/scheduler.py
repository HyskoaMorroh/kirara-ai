import asyncio
import json
import os
import random
import tempfile
import threading
from datetime import datetime
from typing import Any, Dict, Optional

from kirara_ai.config import DATA_PATH
from kirara_ai.config.config_loader import CONFIG_FILE, ConfigLoader
from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.llm.adapter import AutoDetectModelsProtocol
from kirara_ai.llm.llm_manager import LLMManager
from kirara_ai.logger import get_logger
from kirara_ai.scheduler.model_catalog import (
    backend_config_fingerprint,
    model_catalogs_equal,
    normalize_detected_models,
)

# 记录每个后端上次自动检测时间的状态文件
STATE_FILE = os.path.join(DATA_PATH, "auto_detect_state.json")

# 后台循环的检查周期（秒），每 24 小时检查一次是否有到期的后端
CHECK_INTERVAL_SECONDS = 86400

# 启动后首次检查前的等待时间（秒）。原先固定为 60 秒，且 `_is_due` 对"从未
# 检测过"的后端一律返回 True，于是全新安装会在启动 60 秒后同时探测所有启用
# 的后端。这里加一段随机抖动，把首轮探测分散开，减少对上游接口的瞬时压力。
STARTUP_DELAY_SECONDS = 60
STARTUP_JITTER_SECONDS = 300

# 全局的配置写锁。TaskScheduler 会在后台任务里改写 config.llms.api_backends，
# 而 Web 路由同时在读同一份对象；共用一把锁让两侧的读写不会交错。
CONFIG_WRITE_LOCK = threading.RLock()
CONFIG_UPDATE_LOCK = asyncio.Lock()


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

    def _save_state(self) -> bool:
        """保存上次检测时间的记录"""
        temp_path: Optional[str] = None
        try:
            state_dir = os.path.dirname(STATE_FILE)
            os.makedirs(state_dir, exist_ok=True)
            file_descriptor, temp_path = tempfile.mkstemp(
                prefix=".auto_detect_state-", suffix=".tmp", dir=state_dir
            )
            with os.fdopen(file_descriptor, "w", encoding="utf-8") as f:
                json.dump(self._state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, STATE_FILE)
            return True
        except Exception as e:
            self.logger.warning(f"Failed to save auto-detect state: {e}")
            try:
                if temp_path and os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            return False

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

        backend_config = next(
            (b for b in config.llms.api_backends if b.name == backend_name), None
        )
        if not backend_config:
            self.logger.warning(
                f"Backend {backend_name} not found in config, skip update"
            )
            return False
        config_fingerprint = backend_config_fingerprint(backend_config)

        try:
            models = await adapter.auto_detect_models()
        except Exception as e:
            self.logger.error(f"Auto-detect failed for backend {backend_name}: {e}")
            return False

        if not models:
            self.logger.warning(f"Auto-detect returned empty model list for {backend_name}, skip update")
            return False

        # 自动检测只刷新当前后端的模型目录；它不会修改任何工作流内的
        # model_name / fallback_model_1..4。若某个旧配置已不在目录中，编辑器
        # 将把对应槽位显示为空，直到用户主动选择当前可用模型。
        new_models = normalize_detected_models(models)

        async with CONFIG_UPDATE_LOCK:
            backend_config = next(
                (b for b in config.llms.api_backends if b.name == backend_name), None
            )
            if not backend_config:
                self.logger.warning(f"Backend {backend_name} not found in config, skip update")
                return False
            current_adapter = llm_manager.get(backend_name)
            if current_adapter is not adapter:
                self.logger.warning(
                    f"Backend {backend_name} changed during auto-detect, "
                    "discarding stale model list"
                )
                return False
            if backend_config_fingerprint(backend_config) != config_fingerprint:
                self.logger.warning(
                    f"Backend {backend_name} configuration changed during "
                    "auto-detect, discarding stale model list"
                )
                return False

            old_model_count = len(backend_config.models)
            if model_catalogs_equal(backend_config.models, new_models):
                self.logger.info(f"Backend {backend_name} model list unchanged ({len(new_models)} models)")
                return True

            old_models = list(backend_config.models)
            backend_config.models = new_models
            # 重新加载后端，让新模型列表进入 active_backends
            try:
                await llm_manager.reload_backend(backend_name)
            except Exception as e:
                backend_config.models = old_models
                self.logger.error(
                    f"Failed to reload backend {backend_name} after auto-detect: {e}"
                )
                try:
                    if backend_name in llm_manager.backends:
                        await llm_manager.unload_backend(backend_name)
                    llm_manager.load_backend(backend_name)
                except Exception as rollback_error:
                    self.logger.error(
                        f"Failed to restore backend {backend_name} after auto-detect: "
                        f"{rollback_error}"
                    )
                return False

            try:
                def save_config() -> None:
                    with CONFIG_WRITE_LOCK:
                        ConfigLoader.save_config_with_backup(CONFIG_FILE, config)

                await asyncio.to_thread(save_config)
            except Exception as e:
                backend_config.models = old_models
                self.logger.error(
                    f"Failed to save config after auto-detect for {backend_name}: {e}"
                )
                try:
                    await llm_manager.reload_backend(backend_name)
                except Exception as rollback_error:
                    self.logger.error(
                        f"Failed to restore backend {backend_name} after config save failure: "
                        f"{rollback_error}"
                    )
                    try:
                        if backend_name in llm_manager.backends:
                            await llm_manager.unload_backend(backend_name)
                        llm_manager.load_backend(backend_name)
                    except Exception as restore_error:
                        self.logger.error(
                            f"Failed to reload restored backend {backend_name}: "
                            f"{restore_error}"
                        )
                return False

            self.logger.info(
                f"Backend {backend_name} model list updated: "
                f"{old_model_count} -> {len(new_models)} models"
            )

        return True

    async def run_once(self, force: bool = False) -> Dict[str, bool]:
        """
        遍历所有启用了自动检测的后端，执行一轮检测。
        :param force: 忽略间隔时间，强制对所有配置了间隔的后端执行
        :return: 每个后端的执行结果
        """
        config: GlobalConfig = self.container.resolve(GlobalConfig)
        results: Dict[str, bool] = {}
        state_before_run = dict(self._state)
        state_changed = False

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
                state_changed = True

        if state_changed and not self._save_state():
            self._state = state_before_run

        return results

    async def _loop(self) -> None:
        """后台循环，定期检查是否有后端到期"""
        # 启动后先等一会儿，避免和应用启动流程抢资源。
        # 加随机抖动：全新安装时所有后端都"从未检测过"，固定延迟会让它们
        # 在同一时刻一起发起探测。
        startup_delay = STARTUP_DELAY_SECONDS + random.uniform(0, STARTUP_JITTER_SECONDS)
        self.logger.debug(f"Auto-detect first run scheduled in {startup_delay:.0f}s")
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=startup_delay)
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
