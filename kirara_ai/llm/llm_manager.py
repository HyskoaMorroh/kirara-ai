import random
import queue
import inspect
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Union

from typing_extensions import deprecated

from kirara_ai.config.global_config import GlobalConfig, ModelConfig
from kirara_ai.events.event_bus import EventBus
from kirara_ai.events.llm import LLMAdapterLoaded, LLMAdapterUnloaded
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.ioc.inject import Inject
from kirara_ai.llm.adapter import LLMBackendAdapter, LLMChatStreamProtocol
from kirara_ai.llm.attribution import strip_ai_attribution
from kirara_ai.llm.llm_registry import LLMBackendRegistry
from kirara_ai.llm.model_types import ModelAbility, ModelType
from kirara_ai.llm.format.message import LLMChatTextContent
from kirara_ai.llm.format.request import LLMChatRequest
from kirara_ai.llm.format.response import LLMChatResponse, Message
from kirara_ai.llm.pricing import CostSnapshot, PriceCatalog, calculate_cost_snapshot
from kirara_ai.llm.resilience import (ChatExecutionResult, CircuitBreaker, CircuitState, ErrorCategory,
                                      FailoverExecutionError, ProviderAttempt, RequestCancelledError,
                                      RETRYABLE_ERROR_CATEGORIES, StreamExecutionResult,
                                      StreamInterruptedError, classify_llm_error, sanitize_error_summary)
from kirara_ai.logger import get_logger
from kirara_ai.tracing import LLMTracer
from kirara_ai.tracing.decorator import (
    attach_estimated_usage,
    mark_provider_usage,
    suppress_llm_chat_tracing,
)



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
        created: list[str] = []
        for backend in self.config.llms.api_backends:
            if backend.name not in self._resilience_breakers:
                created.append(backend.name)
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
        if created:
            # 重启后恢复「停机前处于熔断/半开」的 Provider：否则刚被隔离的上游
            # 会被立刻当作健康重试，下一个请求再付一次超时。
            self._restore_circuit_state({name: self._resilience_breakers[name] for name in created})
        self._resilience_initialized = True

    def _circuit_store(self):
        """Return the durable breaker store, or ``None`` when no data path is set."""
        store = getattr(self, "_resilience_store", None)
        if store is not None:
            return store
        try:
            from kirara_ai.config import DATA_PATH
            from kirara_ai.llm.circuit_store import CircuitBreakerStore
        except Exception:  # noqa: BLE001 - 缺少可选依赖不应影响请求路径
            return None
        store = CircuitBreakerStore(Path(DATA_PATH) / "llm" / "circuit-state.json")
        self._resilience_store = store
        return store

    def _restore_circuit_state(self, breakers: Dict[str, CircuitBreaker]) -> None:
        store = self._circuit_store()
        if store is None or not breakers:
            return
        try:
            restored = store.restore(breakers)
        except Exception as error:  # noqa: BLE001 - 恢复失败只损失一次保护
            self.logger.warning(f"熔断状态恢复失败：{error}")
            return
        if restored:
            self.logger.info(f"已恢复 {restored} 个 Provider 的熔断状态")

    def _persist_circuit_state(self) -> None:
        """Persist open/half-open breakers so a restart does not clear them."""
        store = self._circuit_store()
        if store is None:
            return
        try:
            store.save(self._resilience_breakers)
        except Exception as error:  # noqa: BLE001 - 持久化失败不得影响请求
            self.logger.debug(f"熔断状态持久化失败：{error}")

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
        # 卸载一个后端时中止它所有在途请求：留着不管，它们会继续跑到自然结束
        # 并继续计费，而这个后端已经从模型表里摘掉、结果无人接收。
        cancel_all = getattr(backend_adapter, "cancel_all_pending_requests", None)
        if callable(cancel_all):
            try:
                aborted = cancel_all()
                if aborted:
                    self.logger.info(
                        f"卸载 {backend_name} 时中止了 {aborted} 个在途请求"
                    )
            except Exception as error:  # noqa: BLE001 - 清理失败不得阻断卸载
                self.logger.debug(f"中止在途请求失败：{error}")
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
        返回指定模型优先级最高的活跃适配器实例
        :param model_id: 模型ID
        :return: LLM适配器实例,如果没有找到则返回None

        历史实现在活跃后端里 ``random.choice``，与 ``get_provider_candidates`` 建立的
        确定性优先级队列互相矛盾：同一个模型两次调用可能落到不同 Provider，配置里的
        ``priority`` 对这条入口完全无效。现在复用同一套排序，只在队列为空时（例如所有
        候选都被 ``participate_in_failover=False`` 排除）回退到原有的活跃后端列表，
        保证「只配了一个不参与故障转移的后端」这类既有配置仍然可用。
        """
        if model_id not in self.active_backends:
            return None

        backends = self.active_backends[model_id]
        if not backends:
            return None

        prioritized = self.get_provider_candidates(model_id)
        if prioritized:
            return prioritized[0]
        return backends[0]

    def get_provider_candidates(
        self,
        model_id: str,
        provider_allowlist: Optional[Iterable[str]] = None,
    ) -> List[LLMBackendAdapter]:
        """Return enabled providers in deterministic priority order."""
        allowed = {
            str(provider).strip()
            for provider in (provider_allowlist or ())
            if str(provider).strip()
        }
        candidates = []
        for index, adapter in enumerate(self.active_backends.get(model_id, [])):
            backend_name = getattr(adapter, "backend_name", None)
            if allowed and backend_name not in allowed:
                continue
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

    def _request_for_provider(
        self, request: LLMChatRequest, backend
    ) -> LLMChatRequest:
        """Apply per-provider request policy without mutating the caller's object.

        `reasoning_effort` 与整流开关都配在**每个供应商**上，而同一个模型可以由
        多个供应商提供：队列里 P1 是自建高强度推理网关、P2 是不支持思考的兼容接口
        时，两者必须各自按自己的配置发请求。就地改写请求对象会让 P1 的设置泄漏到
        P2，而 P2 收到未知字段可能直接 400——一次本可成功的故障转移变成两连败。

        调用方在请求上显式给出的值优先：否则「这一次想快一点」「这一次不要改我的
        请求」都无法表达。
        """
        if backend is None:
            return request
        updates: dict[str, Any] = {}
        effort = getattr(backend, "reasoning_effort", None)
        if effort and request.reasoning_effort is None:
            updates["reasoning_effort"] = effort
        if request.rectifier is None:
            builder = getattr(backend, "build_rectifier_config", None)
            if callable(builder):
                updates["rectifier"] = builder()
        if not updates:
            return request
        return request.model_copy(update=updates)

    @staticmethod
    def _apply_response_policy(response: LLMChatResponse, backend) -> LLMChatResponse:
        """Apply per-provider response policy to the text the user will see.

        目前只有一项：`hide_ai_attribution`。三条边界都在这里体现——

        * **只改文本片段。** 工具调用参数是给程序读的，改写会让工具收到被篡改的
          输入；因此 `LLMToolCallContent` 与其余片段原样保留。
        * **不动用量与成本。** 署名是上游已经生成并计费的 token，
          把它从展示里去掉不等于没花那笔钱；改写 usage 会让账单对不上。
        * **文本没有变化时返回原对象。** 避免每条回复都白拷一份消息树。
        """
        if backend is None or not getattr(backend, "hide_ai_attribution", False):
            return response
        parts = list(response.message.content)
        changed = False
        for index, part in enumerate(parts):
            if not isinstance(part, LLMChatTextContent):
                continue
            cleaned = strip_ai_attribution(part.text, enabled=True)
            if cleaned != part.text:
                parts[index] = LLMChatTextContent(text=cleaned)
                changed = True
        if not changed:
            return response
        return response.model_copy(
            update={"message": response.message.model_copy(update={"content": parts})}
        )

    def apply_response_policy_for_attempts(
        self, response: LLMChatResponse, attempts: Sequence[ProviderAttempt]
    ) -> LLMChatResponse:
        """Apply per-provider response policy to an aggregated streaming reply.

        流式路径**不能**逐分片清理：一句「本回复由 AI 生成。」很可能被切成
        ``本回复由 `` / ``AI 生成。`` 两片，逐片判断两片都不像署名，于是整句原样
        漏出去——结果取决于上游怎么切分片，这是最难复现的一类缺陷。因此分片原样
        交付（同时保证首字节时刻不失真），聚合完成后在这里按**真正成交的那家**
        供应商的配置执行一次。

        没有成功尝试时原样返回：宁可不清理，也不能按别人的配置改写。
        """
        provider = next(
            (
                attempt.provider
                for attempt in reversed(list(attempts))
                if attempt.success
            ),
            None,
        )
        if provider is None:
            return response
        return self._apply_response_policy(response, self._backend_config(provider))

    def execute_chat(
        self,
        request: LLMChatRequest,
        *,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        deadline_seconds: Optional[float] = None,
        cancellation_event: Optional[threading.Event] = None,
        provider_allowlist: Optional[Iterable[str]] = None,
        correlation_id: Optional[str] = None,
    ) -> ChatExecutionResult:
        candidates = self.get_provider_candidates(
            request.model or "",
            provider_allowlist=provider_allowlist,
        )
        tracer = self._get_llm_tracer()
        trace_id = self._start_logical_trace(
            tracer, request, candidates, correlation_id=correlation_id
        )
        requested_at = datetime.now(timezone.utc)
        initial_provider = self._candidate_provider(candidates)
        try:
            result = self._execute_chat(
                request,
                max_retries=max_retries,
                retry_delay=retry_delay,
                deadline_seconds=deadline_seconds,
                cancellation_event=cancellation_event,
                trace_id=trace_id,
                candidates=candidates,
            )
        except RequestCancelledError as error:
            self._fail_logical_trace(
                tracer,
                trace_id,
                request,
                error,
                error.attempts,
                backend_name=self._final_provider(error.attempts, initial_provider),
                correlation_id=correlation_id,
            )
            raise
        except Exception as error:
            attempts = getattr(error, "attempts", ())
            self._fail_logical_trace(
                tracer,
                trace_id,
                request,
                error,
                attempts,
                backend_name=self._final_provider(attempts, initial_provider),
                correlation_id=correlation_id,
            )
            raise
        response = mark_provider_usage(result.response)
        # 供应商完全没给 usage 时补一个明确标记为估算的值。这一步在装饰器
        # (`trace_llm_chat`) 里也有，但本方法通过 `suppress_llm_chat_tracing()`
        # 让装饰器整体短路，所以必须在这条主链路上单独调用；否则这类请求会以
        # 「0 token、0 成本」落库，被读成一次免费调用。
        response = attach_estimated_usage(request, response)
        if response is not result.response:
            result.response = response

        final_provider = self._final_provider(result.attempts, initial_provider)
        self._complete_logical_trace(
            tracer,
            trace_id,
            request,
            response,
            result.attempts,
            backend_name=final_provider,
            cost_snapshot=self._calculate_cost_snapshot(
                response,
                provider=final_provider,
                requested_at=requested_at,
            ),
            correlation_id=correlation_id,
        )
        return result

    def _execute_chat(
        self,
        request: LLMChatRequest,
        *,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        deadline_seconds: Optional[float] = None,
        cancellation_event: Optional[threading.Event] = None,
        trace_id: Optional[str] = None,
        candidates: Optional[List[LLMBackendAdapter]] = None,
        provider_allowlist: Optional[Iterable[str]] = None,
    ) -> ChatExecutionResult:
        """Execute one synchronous chat request through a bounded provider queue."""
        model_id = request.model or ""
        trace_id = trace_id or uuid.uuid4().hex
        if self._is_cancelled(cancellation_event):
            raise RequestCancelledError(trace_id=trace_id)
        if candidates is None:
            candidates = self.get_provider_candidates(
                model_id,
                provider_allowlist=provider_allowlist,
            )
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
            # 每个供应商按自己的配置发请求；副本而非就地改写，避免设置泄漏到下一家。
            provider_request = self._request_for_provider(request, backend)
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
                        provider_request,
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
                    # 署名清理按**该供应商**的配置执行：队列里 P1 开了、P2 没开时，
                    # 「哪条回复被改写过」不能取决于故障转移走到了第几家。
                    response = self._apply_response_policy(response, backend)
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
                    # 打开熔断的那一刻就落盘：进程随后被重启时，这个 Provider
                    # 不会因为状态丢失而被立刻当作健康重试。
                    self._persist_circuit_state()
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

    def stream_supports_tool_calls(
        self,
        model_id: str,
        provider_allowlist: Optional[Iterable[str]] = None,
    ) -> bool:
        """这个模型的**每一个**候选供应商是否都能在流式下交出工具调用。

        要求「每一个」而不是「至少一个」：故障转移会在候选之间切换，如果只有第一家
        支持而第二家不支持，那么一次转移之后工具调用就静默消失了——而用户看到的是
        一次成功的纯文本回复。能力必须按最弱的候选算。

        没有候选时返回 ``False``：那时走非流式让既有的错误路径给出可读原因，
        比在流式路径上失败更容易排查。
        """
        candidates = self.get_provider_candidates(
            model_id, provider_allowlist=provider_allowlist
        )
        if not candidates:
            return False
        return all(
            bool(getattr(adapter, "supports_stream_tool_calls", False))
            for adapter in candidates
        )

    def execute_stream(
        self,
        request: LLMChatRequest,
        *,
        max_retries: Optional[int] = None,
        retry_delay: Optional[float] = None,
        deadline_seconds: Optional[float] = None,
        cancellation_event: Optional[threading.Event] = None,
        provider_allowlist: Optional[Iterable[str]] = None,
        correlation_id: Optional[str] = None,
    ) -> StreamExecutionResult:
        """Execute a stream without joining output from different providers."""
        model_id = request.model or ""
        attempts: List[ProviderAttempt] = []
        candidates = self.get_provider_candidates(
            model_id,
            provider_allowlist=provider_allowlist,
        )
        tracer = self._get_llm_tracer()
        trace_id = self._start_logical_trace(
            tracer, request, candidates, correlation_id=correlation_id
        )
        requested_at = datetime.now(timezone.utc)
        initial_provider = self._candidate_provider(candidates)
        try:
            if self._is_cancelled(cancellation_event):
                raise RequestCancelledError(trace_id=trace_id)
            if not candidates:
                raise FailoverExecutionError(
                    f"No eligible providers for model {model_id}", attempts=attempts
                )

            started = time.monotonic()
            configured_deadline = deadline_seconds
            if configured_deadline is None:
                first_config = self._backend_config(getattr(candidates[0], "backend_name", ""))
                configured_deadline = self._stream_timeout(first_config)
            deadline = started + max(0.0, configured_deadline)
            provider_iterator = self._execute_stream_iterator(
                request=request,
                candidates=candidates,
                trace_id=trace_id,
                attempts=attempts,
                deadline=deadline,
                max_retries=max_retries,
                retry_delay=retry_delay,
                cancellation_event=cancellation_event,
            )
        except Exception as error:
            self._fail_logical_trace(
                tracer,
                trace_id,
                request,
                error,
                getattr(error, "attempts", attempts),
                backend_name=initial_provider,
                correlation_id=correlation_id,
            )
            raise
        iterator = self._trace_stream_iterator(
            provider_iterator,
            tracer=tracer,
            trace_id=trace_id,
            request=request,
            attempts=attempts,
            initial_provider=initial_provider,
            requested_at=requested_at,
            correlation_id=correlation_id,
        )
        return StreamExecutionResult(
            iterator,
            trace_id=trace_id,
            attempts=attempts,
            on_close=lambda: self._fail_logical_trace(
                tracer,
                trace_id,
                request,
                "LLM stream closed before iteration",
                attempts,
                backend_name=initial_provider,
                correlation_id=correlation_id,
            ),
        )

    def _trace_stream_iterator(
        self,
        iterator: Iterator[Any],
        *,
        tracer,
        trace_id: str,
        request: LLMChatRequest,
        attempts: List[ProviderAttempt],
        initial_provider: str,
        requested_at: datetime,
        correlation_id: Optional[str] = None,
    ) -> Iterator[Any]:
        responses: List[LLMChatResponse] = []
        try:
            for chunk in iterator:
                if isinstance(chunk, LLMChatResponse):
                    chunk = mark_provider_usage(chunk)
                    responses.append(chunk)
                yield chunk
        except GeneratorExit:
            self._fail_logical_trace(
                tracer,
                trace_id,
                request,
                "LLM stream closed before completion",
                attempts,
                backend_name=self._final_provider(attempts, initial_provider),
                correlation_id=correlation_id,
            )
            raise
        except Exception as error:
            error_attempts = getattr(error, "attempts", attempts)
            self._fail_logical_trace(
                tracer,
                trace_id,
                request,
                error,
                error_attempts,
                backend_name=self._final_provider(error_attempts, initial_provider),
                correlation_id=correlation_id,
            )
            raise
        else:
            response = self._combine_stream_responses(responses, request)
            # 与同步路径保持同一口径：聚合后仍然没有 usage 的，补估算值并标记来源。
            response = attach_estimated_usage(request, response)
            final_provider = self._final_provider(attempts, initial_provider)

            self._complete_logical_trace(
                tracer,
                trace_id,
                request,
                response,
                attempts,
                backend_name=final_provider,
                cost_snapshot=self._calculate_cost_snapshot(
                    response,
                    provider=final_provider,
                    requested_at=requested_at,
                ),
                correlation_id=correlation_id,
            )

    def _execute_stream_iterator(
        self,
        *,
        request: LLMChatRequest,
        candidates: List[LLMBackendAdapter],
        trace_id: str,
        attempts: List[ProviderAttempt],
        deadline: float,
        max_retries: Optional[int],
        retry_delay: Optional[float],
        cancellation_event: Optional[threading.Event],
    ) -> Iterator[Any]:
        model_id = request.model or ""
        last_error: Optional[BaseException] = None
        stop_failover = False

        for provider_index, adapter in enumerate(candidates):
            if self._is_cancelled(cancellation_event):
                raise RequestCancelledError(trace_id=trace_id, attempts=attempts)
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

            provider_retries = max(
                0,
                max_retries
                if max_retries is not None
                else (backend.max_retries if backend else 0),
            )
            base_delay = max(
                0.0,
                retry_delay
                if retry_delay is not None
                else (backend.retry_backoff_seconds if backend else 0.5),
            )
            max_delay = backend.retry_backoff_max_seconds if backend else 5.0
            first_byte_timeout = (
                backend.stream_first_byte_timeout_seconds if backend else 15.0
            )
            idle_timeout = backend.stream_idle_timeout_seconds if backend else 30.0
            # 流式路径与非流式同一处翻译：供应商级推理强度必须在两条路径上
            # 表现一致，否则同一个 Provider 的行为取决于是否开了流式。
            provider_request = self._request_for_provider(request, backend)

            for retry_index in range(provider_retries + 1):
                attempt_started = time.monotonic()
                if attempt_started >= deadline:
                    stop_failover = True
                    break
                first_byte_at: Optional[float] = None
                try:
                    for chunk, received_at in self._stream_adapter_events(
                        adapter,
                        provider_request,
                        deadline=deadline,
                        first_byte_timeout=first_byte_timeout,
                        idle_timeout=idle_timeout,
                        cancellation_event=cancellation_event,
                    ):
                        if first_byte_at is None:
                            first_byte_at = received_at
                        yield chunk
                    completed = time.monotonic()
                    breaker.record_success(completed)
                    successful_attempt = ProviderAttempt(
                        trace_id=trace_id,
                        model=model_id,
                        provider=provider_name,
                        attempt=len(attempts) + 1,
                        retry_index=retry_index,
                        success=True,
                        started_at=attempt_started,
                        first_byte_at=first_byte_at,
                        completed_at=completed,
                        partial_output=False,
                    )
                    attempts.append(successful_attempt)
                    self._record_attempt(successful_attempt)
                    return
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
                        first_byte_at=first_byte_at,
                        completed_at=completed,
                        partial_output=first_byte_at is not None,
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
                    # 打开熔断的那一刻就落盘：进程随后被重启时，这个 Provider
                    # 不会因为状态丢失而被立刻当作健康重试。
                    self._persist_circuit_state()
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
                        first_byte_at=first_byte_at,
                        completed_at=completed,
                        partial_output=first_byte_at is not None,
                    )
                    attempts.append(failed_attempt)
                    self._record_attempt(failed_attempt)
                    if first_byte_at is not None:
                        raise StreamInterruptedError(
                            f"LLM stream interrupted after partial output (trace_id={trace_id})",
                            trace_id=trace_id,
                            attempts=attempts,
                            cause=error,
                        ) from error
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
            f"LLM stream failed (trace_id={trace_id}): {summary}",
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
                    # 上游报告的限额余量（需求 9）。
                    #
                    # 与熔断状态放在同一行，因为两者回答的是同一个问题的两面：
                    # 「这家现在能不能用」。熔断说的是「它已经坏了」，
                    # 余量说的是「它还剩多少、多久后会坏」——后者是唯一能在
                    # 撞上限之前给出信号的东西。
                    #
                    # 上游从不报这些头时为 `None`，界面据此显示「未上报」。
                    # 填一组 0 会造出一个不存在的紧急情况（0 = 余量用完）。
                    "rate_limit": (
                        snapshot.to_dict()
                        if (snapshot := getattr(adapter, "last_rate_limit", None))
                        else None
                    ),
                    "recent_error_category": next(
                        (item.error_category for item in reversed(self._resilience_attempts.get(provider_name, [])) if not item.success),
                        None,
                    ),
                    # 熔断的**触发与恢复证据**（需求 21.3）。快照只回答「现在什么
                    # 状态」；轮询间隔内发生的 open → half-open → closed 全部不可见，
                    # 于是「昨天下午它被隔离过吗、隔了多久」只能靠恰好抓到那次轮询。
                    # 只取最近 10 条：这里是运维面板，不是完整审计流。
                    "recent_transitions": list(breaker.transitions()[-10:]),
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
                            "partial_output": item.partial_output,
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
    def _stream_timeout(backend) -> float:
        """Return the stream deadline, preferring the stream key over the legacy one.

        The synchronous path already honored ``non_stream_timeout_seconds``; the
        stream path read ``request_timeout_seconds`` directly, so a backend that
        configured only the newer stream keys still ran streams on the 60 second
        legacy default.
        """
        if backend is None:
            return 60.0
        configured_fields = getattr(backend, "model_fields_set", set())
        if "stream_total_timeout_seconds" in configured_fields:
            return backend.stream_total_timeout_seconds
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

    def _get_llm_tracer(self):
        container = getattr(self, "container", None)
        if container is None:
            return None
        try:
            tracer = container.resolve(LLMTracer)
        except KeyError:
            return None
        return tracer if all(callable(getattr(tracer, name, None)) for name in (
            "start_request_tracking", "complete_request_tracking", "fail_request_tracking"
        )) else None

    def _get_price_catalog(self) -> Optional[PriceCatalog]:
        container = getattr(self, "container", None)
        if container is None:
            return None
        try:
            catalog = container.resolve(PriceCatalog)
        except KeyError:
            return None
        return catalog if isinstance(catalog, PriceCatalog) else None

    @staticmethod
    def _candidate_provider(candidates) -> str:
        return getattr(candidates[0], "backend_name", "unknown") if candidates else "unknown"

    @staticmethod
    def _final_provider(attempts, fallback: str) -> str:
        attempts = list(attempts or ())
        successful = next((item.provider for item in reversed(attempts) if item.success), None)
        return successful or (attempts[-1].provider if attempts else fallback)

    @staticmethod
    def _combine_stream_responses(
        responses: List[LLMChatResponse],
        request: LLMChatRequest,
    ) -> LLMChatResponse:
        if not responses:
            return LLMChatResponse(
                model=request.model,
                message=Message(role="assistant", content=[]),
            )
        if len(responses) == 1:
            return responses[0].model_copy(deep=True)

        content = []
        tool_calls = []
        model = request.model
        usage = None
        finish_reason = None
        role = "assistant"
        for response in responses:
            model = response.model or model
            role = response.message.role
            content.extend(part.model_copy(deep=True) for part in response.message.content)
            if response.message.tool_calls:
                tool_calls.extend(call.model_copy(deep=True) for call in response.message.tool_calls)
            usage = response.usage or usage
            finish_reason = response.message.finish_reason or finish_reason
        return LLMChatResponse(
            model=model,
            usage=usage.model_copy(deep=True) if usage is not None else None,
            message=Message(
                role=role,
                content=content,
                tool_calls=tool_calls or None,
                finish_reason=finish_reason,
            ),
        )

    def _calculate_cost_snapshot(
        self,
        response: LLMChatResponse,
        *,
        provider: str,
        requested_at: datetime,
    ) -> Optional[CostSnapshot]:
        if response.usage is None:
            return None
        catalog = self._get_price_catalog()
        if catalog is None:
            return None
        model = response.model
        if not model:
            return None
        try:
            price = catalog.resolve(provider, model, requested_at)
            return calculate_cost_snapshot(
                response.usage,
                price,
                requested_at=requested_at,
                provider=provider,
                model=model,
            )
        except (LookupError, ValueError):
            return None

    @staticmethod
    def _start_logical_trace(
        tracer,
        request,
        candidates,
        *,
        correlation_id: Optional[str] = None,
    ) -> str:
        fallback = uuid.uuid4().hex
        if tracer is None:
            return fallback
        provider = getattr(candidates[0], "backend_name", "unknown") if candidates else "unknown"
        try:
            start = tracer.start_request_tracking
            return start(
                provider,
                request,
                **LLMManager._supported_trace_kwargs(
                    start,
                    correlation_id=correlation_id,
                ),
            )
        except Exception:
            return fallback

    @staticmethod
    def _complete_logical_trace(
        tracer,
        trace_id,
        request,
        response,
        attempts,
        *,
        backend_name: str,
        cost_snapshot: Optional[CostSnapshot] = None,
        correlation_id: Optional[str] = None,
    ) -> None:
        if tracer is None:
            return
        complete = tracer.complete_request_tracking
        try:
            complete(
                trace_id,
                request,
                response,
                **LLMManager._supported_trace_kwargs(
                    complete,
                    attempts=attempts,
                    backend_name=backend_name,
                    cost_snapshot=cost_snapshot,
                    correlation_id=correlation_id,
                ),
            )
        except Exception:
            return

    @staticmethod
    def _fail_logical_trace(
        tracer,
        trace_id,
        request,
        error,
        attempts,
        *,
        backend_name: str,
        correlation_id: Optional[str] = None,
    ) -> None:
        if tracer is None:
            return
        fail = tracer.fail_request_tracking
        try:
            fail(
                trace_id,
                request,
                error,
                **LLMManager._supported_trace_kwargs(
                    fail,
                    attempts=attempts,
                    backend_name=backend_name,
                    correlation_id=correlation_id,
                ),
            )
        except Exception:
            return

    @staticmethod
    def _supported_trace_kwargs(method, **metadata) -> Dict[str, Any]:
        """Filter optional trace metadata without retrying a failed method call."""
        try:
            parameters = inspect.signature(method).parameters
        except (TypeError, ValueError):
            return metadata
        if any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        ):
            return metadata
        return {name: value for name, value in metadata.items() if name in parameters}

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
                with suppress_llm_chat_tracing():
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

    @classmethod
    def _stream_adapter_events(
        cls,
        adapter: LLMBackendAdapter,
        request: LLMChatRequest,
        *,
        deadline: float,
        first_byte_timeout: float,
        idle_timeout: float,
        cancellation_event=None,
    ) -> Iterator[tuple[Any, float]]:
        """Bridge a blocking stream iterator through a bounded cancellation-aware wait."""
        events: queue.Queue = queue.Queue()
        stopped = threading.Event()

        def publish(kind: str, value: Any = None) -> bool:
            if stopped.is_set():
                return False
            events.put((kind, value, time.monotonic()))
            return True

        def invoke() -> None:
            try:
                with suppress_llm_chat_tracing():
                    if isinstance(adapter, LLMChatStreamProtocol):
                        stream = adapter.stream_chat(request)
                        for chunk in stream:
                            if not publish("chunk", chunk):
                                return
                    else:
                        if not publish("chunk", adapter.chat(request)):
                            return
                publish("done")
            except BaseException as error:
                publish("error", error)

        worker = threading.Thread(target=invoke, name="kirara-llm-stream", daemon=True)
        worker.start()
        waiting_for_first = True
        activity_deadline = min(deadline, time.monotonic() + first_byte_timeout)
        try:
            while True:
                if cls._is_cancelled(cancellation_event):
                    cls._cancel_adapter_request(adapter, request)
                    raise RequestCancelledError()
                remaining = min(deadline, activity_deadline) - time.monotonic()
                if remaining <= 0:
                    cls._cancel_adapter_request(adapter, request)
                    phase = "first chunk" if waiting_for_first else "stream idle"
                    raise TimeoutError(f"LLM {phase} timeout exceeded")
                try:
                    kind, value, received_at = events.get(timeout=min(0.05, remaining))
                except queue.Empty:
                    continue
                if cls._is_cancelled(cancellation_event):
                    cls._cancel_adapter_request(adapter, request)
                    raise RequestCancelledError()
                if received_at > deadline:
                    cls._cancel_adapter_request(adapter, request)
                    raise TimeoutError("LLM request deadline exceeded")
                if kind == "error":
                    raise value
                if kind == "done":
                    return
                waiting_for_first = False
                activity_deadline = min(deadline, received_at + idle_timeout)
                yield value, received_at
        finally:
            stopped.set()

    @staticmethod
    def _wait_for_backoff(delay: float, cancellation_event=None) -> bool:
        if cancellation_event is not None:
            return cancellation_event.wait(delay)
        time.sleep(delay)
        return False

    def reset_provider_circuit(self, provider_name: str) -> None:
        """把一个 Provider 的熔断器手动清回 closed，并撤销它的持久化隔离。

        必须先删持久化记录再重建内存状态。反过来做的话，`_initialize_resilience_state`
        会把「字典里没有这个名字」当成新建，紧接着从状态文件里把刚被重置的
        open / half-open **原地恢复**回来——重置不需要等到重启就已经失效，
        而调用方拿到的是成功返回。那种失败最难查：界面说已重置，
        下一个请求仍然跳过这个 Provider，日志里既没有错误也没有重置痕迹。
        """
        store = self._circuit_store()
        if store is not None:
            try:
                store.forget(provider_name)
            except Exception as error:  # noqa: BLE001 - 撤销失败不该让重置整体失败
                # 仍然继续清内存：本进程内立即生效，最坏情况是下次启动又恢复一次。
                self.logger.warning(f"熔断状态撤销失败：{error}")
        self._resilience_breakers.pop(provider_name, None)
        self._resilience_attempts.pop(provider_name, None)
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
