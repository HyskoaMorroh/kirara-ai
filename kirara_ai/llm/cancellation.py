"""Real cancellation for in-flight upstream HTTP requests.

`llm_manager` 在超时、取消信号与 deadline 三处调用
``adapter.cancel_pending_request(request)``，但此前**没有任何适配器实现过它**
（全仓库对它的引用只有 `getattr` 那一处）。后果不是「取消没做」，而是
「取消看起来做了」——日志写着已取消、等待循环也确实松手了，可 HTTP 连接还在，
上游继续生成、继续计费，承载请求的 daemon 线程跑到自然结束。

这里提供适配器共用的最小实现。三个刻意的选择：

- **按 `id(request)` 登记**，不按请求内容。`LLMChatRequest` 是 pydantic 模型，
  未声明 frozen 因此不可哈希；而且两条内容完全相同的并发请求必须能分别取消，
  按内容做键会让取消打到错误的那一条上。
- **登记表必须自己清空。** 只登记不移除等于一个按请求数增长的 map；
  一个长期运行的部署会把它变成内存泄漏，而症状（内存缓慢上涨）
  与取消功能毫无表面关联。因此登记走上下文管理器，`finally` 里一定移除。
- **`close()` 是唯一真正断开连接的动作。** 只设一个布尔标记、等下一次循环去看，
  对上游毫无作用：它已经在生成了。取消必须直接关掉响应对象。
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from kirara_ai.llm.rate_limit import RateLimitSnapshot, parse_rate_limit_headers


class CancellableRequestMixin:
    """Track in-flight HTTP responses so a caller can abort them.

    适配器只需在发出请求后用 :meth:`_track_response` 包住响应的生命周期，
    :meth:`cancel_pending_request` 就能真正中止它。
    """

    #: 惰性初始化：适配器的 `__init__` 各不相同，不要求它们记得调 super()。
    _pending_responses: dict[int, Any]
    _pending_lock: threading.Lock

    def _pending_registry(self) -> tuple[dict[int, Any], threading.Lock]:
        registry = getattr(self, "_pending_responses", None)
        if registry is None:
            # 两个线程同时首次进入时可能各建一份表；用类级锁把初始化串起来。
            with _INIT_LOCK:
                registry = getattr(self, "_pending_responses", None)
                if registry is None:
                    registry = {}
                    self._pending_responses = registry
                    self._pending_lock = threading.Lock()
        lock = getattr(self, "_pending_lock", None)
        if lock is None:
            with _INIT_LOCK:
                lock = getattr(self, "_pending_lock", None)
                if lock is None:
                    lock = threading.Lock()
                    self._pending_lock = lock
        return registry, lock

    @contextmanager
    def _track_response(self, request: Any, response: Any) -> Iterator[Any]:
        """Register ``response`` as the in-flight response for ``request``."""
        registry, lock = self._pending_registry()
        key = id(request)
        with lock:
            registry[key] = response
        # 顺手采集上游限额余量（需求 9）。
        #
        # 放在这里而不是各适配器里：这个上下文管理器是四家适配器**唯一都会经过**
        # 的收口，一处采集覆盖全部；写在适配器里就是四份重复代码，
        # 而新增第五家适配器时必然漏掉。
        #
        # 采集在 `yield` 之前：失败路径（429）那次的响应头恰恰是最有价值的一次
        # ——它带着 `retry-after`。等到 `finally` 或成功之后再读就正好错过它。
        self._capture_rate_limit(response)
        try:
            yield response
        finally:
            with lock:
                # 用 `pop(key, None)`：取消路径可能已经把它移除了。
                registry.pop(key, None)

    def _capture_rate_limit(self, response: Any) -> None:
        """Record the upstream rate-limit headroom reported by ``response``.

        采集失败绝不影响请求：限额头是上游给的，不能假定它存在或可解析，
        而一个解析异常会让整条本已成功的请求失败——那比少一个数字严重得多。

        上游没报这些头时保持 ``None``（而不是一组 0）：0 表示「余量用完」，
        是最该报警的状态；把「没上报」显示成 0 会造出一个不存在的紧急情况。
        """
        try:
            headers = getattr(response, "headers", None)
            snapshot = parse_rate_limit_headers(headers)
        except Exception:  # noqa: BLE001 - 观测不得影响主链路
            return
        if snapshot is not None:
            self.last_rate_limit = snapshot

    @property
    def last_rate_limit(self) -> Optional[RateLimitSnapshot]:
        """最近一次上游报告的限额余量；上游从不报时为 ``None``。

        只保留最近一次而不是全部历史：余量是**当下**的状态，
        十分钟前的余量对「现在能不能发」没有意义。历史趋势由追踪表负责。
        """
        return getattr(self, "_last_rate_limit", None)

    @last_rate_limit.setter
    def last_rate_limit(self, snapshot: Optional[RateLimitSnapshot]) -> None:
        self._last_rate_limit = snapshot

    def cancel_pending_request(self, request: Any) -> None:
        """Abort the upstream response for ``request`` if one is still in flight.

        对一个从未发出或已经结束的请求调用它是无操作——`llm_manager` 在多个
        位置调用（超时、取消、deadline），其中有些位置请求可能还没真正发出，
        清理动作抛异常会把一次超时变成一次崩溃。
        """
        registry, lock = self._pending_registry()
        with lock:
            response = registry.pop(id(request), None)
        if response is None:
            return
        close = getattr(response, "close", None)
        if not callable(close):
            return
        try:
            close()
        except Exception:  # noqa: BLE001 - 中止失败不得掩盖真正的失败原因
            pass

    def cancel_all_pending_requests(self) -> int:
        """Abort every in-flight response. Returns how many were closed.

        卸载一个后端时用：留着在途请求不管，它们会继续计费到自然结束。
        """
        registry, lock = self._pending_registry()
        with lock:
            responses = list(registry.values())
            registry.clear()
        closed = 0
        for response in responses:
            close = getattr(response, "close", None)
            if not callable(close):
                continue
            try:
                close()
                closed += 1
            except Exception:  # noqa: BLE001
                continue
        return closed

    def _pending_response(self, request: Any) -> Optional[Any]:
        """Return the in-flight response for ``request``, for tests and diagnostics."""
        registry, lock = self._pending_registry()
        with lock:
            return registry.get(id(request))


_INIT_LOCK = threading.Lock()


__all__ = ["CancellableRequestMixin"]
