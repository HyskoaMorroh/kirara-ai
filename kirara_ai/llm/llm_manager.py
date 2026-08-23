import random
import queue
import threading
import time
import uuid
from typing import Any, Dict, List, Optional, Union

from typing_extensions import deprecated

from kirara_ai.config.global_config import GlobalConfig, ModelConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.events.llm import LLMAdapterLoaded, LLMAdapterUnloaded
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.llm.adapter import LLMBackendAdapter
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.llm.model_types import ModelAbility, ModelType
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.resilience import (ChatExecutionResult, CircuitBreaker, CircuitState, ErrorCategory,
                                      FailoverExecutionError, ProviderAttempt, RequestCancelledError,
                                      RETRYABLE_ERROR_CATEGORIES, classify_llm_error, sanitize_error_summary)
from kirara_ai.logger import get_logger


class LLMManager:
    """
    跟踪、管理和调度模型后端
    """

    container: DependencyContainer
    config: GlobalConfig
    backend_registry: LLMBackendRegistry
    active_backends: Dict[str, List[LLMBackendAdapter]]
    model_info: Dict[str, ModelConfig]  # 存储模型的配置信息
    event_bus: EventBus

    @Inject()
    def __init__(
        self,
        container: DependencyContainer,
        config: GlobalConfig,
        backend_registry: LLMBackendRegistry,
        event_bus: EventBus,
    ):
        self.container = container
        self.config = config
        self.backend_registry = backend_registry
        self.event_bus = event_bus
        self.logger = get_logger("LLMAdapter")
        self.active_backends = {}
        self.model_info = {}  # 初始化模型信息字典
        self.backends: Dict[str, LLMBackendAdapter] = {}
        self._resilience_breakers: Dict[str, CircuitBreaker] = {}
        self._resilience_attempts: Dict[str, List[ProviderAttempt]] = {}
        self._resilience_initialized = False

    def _backend_config(self, backend_name: str):
        return next((backend for backend in self.config.llms.api_backends if backend.name == backend_name), None)

    def _initialize_resilience_state(self) -> None:
        configured_names = {backend.name for backend in self.config.llms.api_backends}
        self._resilience_breakers = {
            name: breaker
            for name, breaker in self._resilience_breakers.items()
            if name in configured_names
        }
        for backend in self.config.llms.api_backends:
            self._resilience_breakers.setdefault(
                backend.name,
                CircuitBreaker(
                    failure_threshold=backend.circuit_failure_threshold,
                    error_rate_threshold=backend.circuit_error_rate_threshold,
                    min_requests=backend.circuit_min_requests,
                    recovery_timeout_seconds=backend.circuit_recovery_timeout_seconds,
                    recovery_success_threshold=backend.circuit_recovery_success_threshold,
                ),
            )
            self._resilience_attempts.setdefault(backend.name, [])
        self._resilience_initialized = True

    def load_config(self):
        """加载配置文件中的所有启用的后端"""
        self._initialize_resilience_state()
        for backend in self.config.llms.api_backends:
            if backend.enable:
                self.logger.info(f"Loading backend: {backend.name}")
                try:
                    self.load_backend(backend.name)
                except Exception as e:
                    self.logger.error(f"Failed to load backend {backend.name}: {e}")

    def load_backend(self, backend_name: str):
        """
        加载指定的后端
        :param backend_name: 后端名称
        """
        backend = next(
            (b for b in self.config.llms.api_backends if b.name == backend_name), None
        )
        if not backend:
            raise ValueError(f"Backend {backend_name} not found in config")

        if not backend.enable:
            raise ValueError(f"Backend {backend_name} is not enabled")

        if any(backend_name in adapters for adapters in self.active_backends.values()):
            raise ValueError(f"Backend {backend_name} is already loaded")

        adapter_class = self.backend_registry.get(backend.adapter)
        config_class = self.backend_registry.get_config_class(backend.adapter)

        if not adapter_class or not config_class:
            raise ValueError(f"Invalid adapter type: {backend.adapter}")

        # 创建适配器实例
        with self.container.scoped() as scoped_container:
            scoped_container.register(config_class, config_class(**backend.config))
            adapter = Inject(scoped_container).create(adapter_class)()
            adapter.backend_name = backend_name
            self.backends[backend_name] = adapter

            # 注册到每个支持的模型并记录模型信息
            for model_config in backend.models:
                # 从ModelConfig中获取模型信息
                model_id = model_config.id

                # 直接存储模型配置
                self.model_info[model_id] = model_config

                if model_id not in self.active_backends:
                    self.active_backends[model_id] = []
                self.active_backends[model_id].append(adapter)

        self.event_bus.post(LLMAdapterLoaded(adapter=adapter, backend_name=backend_name))
        self._initialize_resilience_state()
        self.logger.info(f"Backend {backend_name} loaded successfully")

    async def unload_backend(self, backend_name: str):
        """
        卸载指定的后端
        :param backend_name: 后端名称
        """
        backend = next(
            (b for b in self.config.llms.api_backends if b.name == backend_name), None
        )
        if not backend:
            raise ValueError(f"Backend {backend_name} not found in config")

        backend_adapter = self.backends.get(backend_name)

        if not backend_adapter:
            raise ValueError(f"Backend {backend_name} not found")

        # 从所有模型中移除这个后端的适配器
        all_models = list(self.active_backends.keys())
        for model in all_models:
            if backend_adapter in self.active_backends[model]:
                self.active_backends[model].remove(backend_adapter)
            if len(self.active_backends[model]) == 0:
                self.active_backends.pop(model)
                # 清理模型信息
                if model in self.model_info:
                    self.model_info.pop(model)

        backend_adapter = self.backends.pop(backend_name)
        self._resilience_breakers.pop(backend_name, None)
        self._resilience_attempts.pop(backend_name, None)
        self.event_bus.post(LLMAdapterUnloaded(backend_name=backend_name, adapter=backend_adapter))

    async def reload_backend(self, backend_name: str):
        """
        重新加载指定的后端
        :param backend_name: 后端名称
        """
        await self.unload_backend(backend_name)
        self.load_backend(backend_name)

    def is_backend_available(self, backend_name: str) -> bool:
        """
        检查后端是否可用
        :param backend_name: 后端名称
        :return: 后端是否可用
        """
        backend = next(
            (b for b in self.config.llms.api_backends if b.name == backend_name), None
        )
        if not backend:
            return False

        if not backend.enable:
            return False

        # 检查后端的所有模型是否都有可用的适配器
        for model_config in backend.models:
            model_id = model_config.id
            if model_id not in self.active_backends or len(self.active_backends[model_id]) == 0:
                return False
        return True

    def get(self, backend_name: str) -> Optional[LLMBackendAdapter]:
        """
        获取指定后端的适配器实例
        :param backend_name: 后端名称
        :return: LLM适配器实例,如果没有找到则返回None
        """
        return self.backends.get(backend_name)

    def get_llm(self, model_id: str) -> Optional[LLMBackendAdapter]:
        """
        从指定模型的活跃后端中随机返回一个适配器实例
        :param model_id: 模型ID
        :return: LLM适配器实例,如果没有找到则返回None
        """
        if model_id not in self.active_backends:
            return None

        backends = self.active_backends[model_id]
        if not backends:
            return None
        # TODO: 后续考虑支持更多的选择策略
        return random.choice(backends)

    def get_provider_candidates(self, model_id: str) -> List[LLMBackendAdapter]:
        """Return enabled providers in deterministic priority order."""
        candidates = []
        for index, adapter in enumerate(self.active_backends.get(model_id, [])):
            backend_name = getattr(adapter, "backend_name", None)
            backend = self._backend_config(backend_name) if backend_name else None
            if not backend or not backend.enable or not backend.participate_in_failover:
                continue
            candidates.append((backend.priority, index, adapter))
        candidates.sort(key=lambda item: (item[0], item[1]))
        return [adapter for _, _, adapter in candidates]

    def _record_attempt(self, attempt: ProviderAttempt) -> None:
        attempts = self._resilience_attempts.setdefault(attempt.provider, [])
        attempts.append(attempt)
        del attempts[:-20]

    def execute_chat(
        self,
        request: LLMChatRequest,
        *,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        deadline_seconds: Optional[float] = None,
        cancellation_event: Optional[threading.Event] = None,
    ) -> ChatExecutionResult:
        """Execute one synchronous chat request through a bounded provider queue."""
        model_id = request.model or ""
        trace_id = uuid.uuid4().hex
        if self._is_cancelled(cancellation_event):
            raise RequestCancelledError(trace_id=trace_id)
        candidates = self.get_provider_candidates(model_id)
        attempts: List[ProviderAttempt] = []
        if not candidates:
            raise FailoverExecutionError(
                f"No eligible providers for model {model_id}", attempts=attempts
            )

        started = time.monotonic()
        configured_deadline = deadline_seconds
        if configured_deadline is None:
            first_config = self._backend_config(getattr(candidates[0], "backend_name", ""))
            configured_deadline = self._non_stream_timeout(first_config)
        deadline = started + max(0.0, configured_deadline)
        last_error: Optional[BaseException] = None
        stop_failover = False

        for provider_index, adapter in enumerate(candidates):
            if self._is_cancelled(cancellation_event):
                raise RequestCancelledError(
                    trace_id=trace_id,
                    attempts=attempts,
                )
            provider_name = getattr(adapter, "backend_name", f"provider-{provider_index + 1}")
            backend = self._backend_config(provider_name)
            breaker = self._resilience_breakers.setdefault(
                provider_name,
                CircuitBreaker(
                    failure_threshold=backend.circuit_failure_threshold if backend else 3,
                    error_rate_threshold=backend.circuit_error_rate_threshold if backend else 0.5,
                    min_requests=backend.circuit_min_requests if backend else 10,
                    recovery_timeout_seconds=backend.circuit_recovery_timeout_seconds if backend else 30,
                    recovery_success_threshold=backend.circuit_recovery_success_threshold if backend else 2,
                ),
            )
            if not breaker.acquire():
                skipped = ProviderAttempt(
                    trace_id=trace_id,
                    model=model_id,
                    provider=provider_name,
                    attempt=len(attempts) + 1,
                    retry_index=0,
                    success=False,
                    error_category="circuit_open",
                    error_summary="provider circuit is open",
                    started_at=time.monotonic(),
                    completed_at=time.monotonic(),
                )
                attempts.append(skipped)
                self._record_attempt(skipped)
                continue

            provider_retries = max(0, max_retries if max_retries is not None else (backend.max_retries if backend else 0))
            base_delay = max(0.0, retry_delay if retry_delay is not None else (backend.retry_backoff_seconds if backend else 0.5))
            max_delay = backend.retry_backoff_max_seconds if backend else 5.0
            for retry_index in range(provider_retries + 1):
                attempt_started = time.monotonic()
                if attempt_started >= deadline:
                    stop_failover = True
                    break
                if self._is_cancelled(cancellation_event):
                    breaker.record_cancelled()
                    raise RequestCancelledError(
                        trace_id=trace_id,
                        attempts=attempts,
                    )
                try:
                    response = self._call_adapter_with_deadline(
                        adapter,
                        request,
                        max(0.0, deadline - attempt_started),
                        cancellation_event=cancellation_event,
                    )
                    completed = time.monotonic()
                    if completed > deadline:
                        raise TimeoutError("LLM request deadline exceeded")
                    breaker.record_success(completed)
                    successful_attempt = ProviderAttempt(
                        trace_id=trace_id,
                        model=model_id,
                        provider=provider_name,
                        attempt=len(attempts) + 1,
                        retry_index=retry_index,
                        success=True,
                        started_at=attempt_started,
                        first_byte_at=None,
                        completed_at=completed,
                    )
                    attempts.append(successful_attempt)
                    self._record_attempt(successful_attempt)
                    return ChatExecutionResult(response=response, trace_id=trace_id, attempts=attempts)
                except RequestCancelledError as error:
                    completed = time.monotonic()
                    breaker.record_cancelled()
                    cancelled_attempt = ProviderAttempt(
                        trace_id=trace_id,
                        model=model_id,
                        provider=provider_name,
                        attempt=len(attempts) + 1,
                        retry_index=retry_index,
                        success=False,
                        error_category=ErrorCategory.CANCELLED.value,
                        error_summary=sanitize_error_summary(error),
                        started_at=attempt_started,
                        completed_at=completed,
                    )
                    attempts.append(cancelled_attempt)
                    self._record_attempt(cancelled_attempt)
                    error.trace_id = trace_id
                    error.attempts = list(attempts)
                    raise
                except Exception as error:
                    last_error = error
                    category = classify_llm_error(error)
                    completed = time.monotonic()
                    breaker.record_failure(completed)
                    failed_attempt = ProviderAttempt(
                        trace_id=trace_id,
                        model=model_id,
                        provider=provider_name,
                        attempt=len(attempts) + 1,
                        retry_index=retry_index,
                        success=False,
                        error_category=category.value,
                        error_summary=sanitize_error_summary(error),
                        started_at=attempt_started,
                        completed_at=completed,
                    )
                    attempts.append(failed_attempt)
                    self._record_attempt(failed_attempt)
                    if category not in RETRYABLE_ERROR_CATEGORIES:
                        stop_failover = True
                        break
                    if retry_index >= provider_retries:
                        break
                    delay = min(max_delay, base_delay * (2**retry_index))
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        stop_failover = True
                        break
                    if self._wait_for_backoff(
                        delay=min(delay, remaining),
                        cancellation_event=cancellation_event,
                    ):
                        raise RequestCancelledError(
                            trace_id=trace_id,
                            attempts=attempts,
                        )
            if stop_failover:
                break

        summary = ", ".join(
            f"{attempt.provider}:{attempt.error_category or 'failed'}" for attempt in attempts
        ) or "no provider attempt"
        raise FailoverExecutionError(
            f"LLM request failed (trace_id={trace_id}): {summary}",
            attempts=attempts,
            cause=last_error,
        ) from last_error

    def get_resilience_status(self) -> List[Dict[str, Any]]:
        """Return provider health and sanitized recent attempts for operational UI."""
        if not self._resilience_initialized:
            self._initialize_resilience_state()
        rows: List[Dict[str, Any]] = []
        for model_id, adapters in self.active_backends.items():
            for adapter in adapters:
                provider_name = getattr(adapter, "backend_name", "")
                backend = self._backend_config(provider_name)
                if not backend:
                    continue
                breaker = self._resilience_breakers.setdefault(provider_name, CircuitBreaker())
                rows.append({
                    "model": model_id,
                    "provider": provider_name,
                    "priority": backend.priority,
                    **breaker.snapshot(),
                    "recent_error_category": next(
                        (item.error_category for item in reversed(self._resilience_attempts.get(provider_name, [])) if not item.success),
                        None,
                    ),
                    "recent_attempts": [
                        {
                            "trace_id": item.trace_id,
                            "provider": item.provider,
                            "model": item.model,
                            "attempt": item.attempt,
                            "retry_index": item.retry_index,
                            "success": item.success,
                            "error_category": item.error_category,
                            "started_at": item.started_at,
                            "completed_at": item.completed_at,
                            "first_byte_at": item.first_byte_at,
                        }
                        for item in self._resilience_attempts.get(provider_name, [])[-20:]
                    ],
                })
        return rows

    @staticmethod
    def _non_stream_timeout(backend) -> float:
        if backend is None:
            return 60.0
        configured_fields = getattr(backend, "model_fields_set", set())
        if "non_stream_timeout_seconds" in configured_fields:
            return backend.non_stream_timeout_seconds
        return backend.request_timeout_seconds

    @staticmethod
    def _is_cancelled(cancellation_event) -> bool:
        return cancellation_event is not None and cancellation_event.is_set()

    @staticmethod
    def _cancel_adapter_request(adapter: LLMBackendAdapter, request: LLMChatRequest) -> None:
        cancel = getattr(adapter, "cancel_pending_request", None)
        if callable(cancel):
            try:
                cancel(request)
            except Exception:
                pass

    @classmethod
    def _call_adapter_with_deadline(
        cls,
        adapter: LLMBackendAdapter,
        request: LLMChatRequest,
        timeout: float,
        *,
        cancellation_event=None,
    ):
        """Bound legacy synchronous adapters without blocking the event-loop caller."""
        if timeout <= 0:
            raise TimeoutError("LLM request deadline exceeded")
        if cls._is_cancelled(cancellation_event):
            cls._cancel_adapter_request(adapter, request)
            raise RequestCancelledError()
        result: queue.Queue = queue.Queue(maxsize=1)

        def invoke() -> None:
            try:
                result.put((True, adapter.chat(request)))
            except BaseException as error:
                result.put((False, error))

        worker = threading.Thread(target=invoke, name="kirara-llm-attempt", daemon=True)
        worker.start()
        deadline = time.monotonic() + timeout
        while True:
            if cls._is_cancelled(cancellation_event):
                cls._cancel_adapter_request(adapter, request)
                raise RequestCancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                cls._cancel_adapter_request(adapter, request)
                raise TimeoutError("LLM request deadline exceeded")
            try:
                succeeded, value = result.get(timeout=min(0.05, remaining))
            except queue.Empty:
                continue
            if cls._is_cancelled(cancellation_event):
                cls._cancel_adapter_request(adapter, request)
                raise RequestCancelledError()
            break
        if succeeded:
            return value
        raise value

    @staticmethod
    def _wait_for_backoff(delay: float, cancellation_event=None) -> bool:
        if cancellation_event is not None:
            return cancellation_event.wait(delay)
        time.sleep(delay)
        return False

    def reset_provider_circuit(self, provider_name: str) -> None:
        self._resilience_breakers.pop(provider_name, None)
        self._initialize_resilience_state()

    def get_supported_models(self, model_type: Union[ModelType, ModelAbility], ability: Optional[ModelAbility] = None) -> List[str]:
        """
        获取所有支持指定能力的模型
        :param model_type: 模型类型；为兼容 3.2.0 的单参数调用 get_supported_models(ability)，
                           当此处传入能力枚举且 ability 省略时，按 ModelType.LLM 处理
        :param ability: 指定的能力
        :return: 支持的模型ID列表
        """
        if ability is None:
            # 旧签名 get_supported_models(ability)
            ability = model_type  # type: ignore[assignment]
            model_type = ModelType.LLM
        # 注意：这里必须用“模型能力位包含所需能力位”的方向判断。
        # ModelAbility.is_capable 的实现是 self.value & ability == ability，
        # 若写成 ability.is_capable(model.ability)，含额外能力的模型
        #（例如 gpt-4o = TextChat|ImageInput|FunctionCalling = 286）会被误判为不支持 TextChat(14)。
        required = ability.value  # type: ignore[union-attr]
        return [
            model_id
            for model_id, model_config in self.model_info.items()
            if model_config.type == model_type.value  # type: ignore[union-attr]
            and (model_config.ability & required) == required
        ]

    @deprecated("请使用 get_supported_models 方法")
    def get_llm_id_by_ability(self, ability: ModelAbility) -> Optional[str]:
        """
        根据指定的能力获取一个随机符合要求的LLM模型ID
        deprecated: 请使用 get_supported_models 方法
        :param ability: 指定的能力
        :return: 符合要求的模型ID，如果没有找到则返回None
        """
        supported_models = self.get_supported_models(ModelType.LLM, ability)
        return None if not supported_models else random.choice(supported_models)

    def get_models_by_ability(self, model_type: ModelType, ability: ModelAbility) -> Optional[str]:
        """
        根据指定能力随机获取一个模型ID
        :param model_type: 模型类型
        :param ability: 指定的能力
        :return: 随机选择的模型ID，如果没有找到则返回None
        """
        supported_models = self.get_supported_models(model_type, ability)
        if not supported_models:
            return None
        return random.choice(supported_models)

    def get_models_by_type(self, model_type: ModelType) -> List[str]:
        """
        获取指定类型的所有模型
        :param model_type: 模型类型
        :return: 该类型的模型ID列表
        """
        return [
            model_id for model_id, config in self.model_info.items()
            if config.type == model_type.value
        ]
