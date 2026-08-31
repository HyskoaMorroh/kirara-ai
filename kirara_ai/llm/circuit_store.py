"""Durable circuit-breaker state across process restarts.

The breaker itself lives in memory (`kirara_ai/llm/resilience.py`), which is
correct for the hot path — it is consulted on every request and must not touch
disk there. What was missing is durability *around* it: a restart wiped every
open breaker, so a provider that had just been isolated for repeated failures was
immediately retried as if healthy, and the next request paid the timeout again.

This store persists only what is needed to re-open a breaker that was open when
the process stopped:

- the breaker state and when it opened;
- consecutive failure count and recovery progress.

It deliberately does **not** persist the outcome ring buffer. Error *rate* over a
sliding window describes recent live traffic; replaying a window from before a
restart would let stale samples trip a breaker on a provider that is now fine.
After a restart the rate window starts empty and the consecutive-failure
threshold does the work — the conservative choice.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping, Optional

from kirara_ai.llm.resilience import CircuitBreaker, CircuitState

#: 超过这个时长的记录视为过期：进程停了很久之后，旧的熔断状态不再描述当前上游。
_MAX_AGE_SECONDS = 24 * 60 * 60

_FORMAT_VERSION = 1


class CircuitBreakerStore:
    """Persist and restore breaker state for one process's providers."""

    def __init__(self, path: str | Path, *, clock=time.time) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._clock = clock

    def save(self, breakers: Mapping[str, CircuitBreaker]) -> None:
        """Write the durable part of every breaker's state atomically."""
        payload: dict[str, Any] = {
            "format_version": _FORMAT_VERSION,
            "saved_at": self._clock(),
            "breakers": {},
        }
        for name, breaker in breakers.items():
            snapshot = breaker.durable_state()
            if snapshot is None:
                continue
            payload["breakers"][str(name)] = snapshot
        if not payload["breakers"] and not self.path.exists():
            # 没有任何值得保存的状态时不创建空文件。
            return
        self._atomic_write(payload)

    def load(self) -> dict[str, dict[str, Any]]:
        """Return the stored per-provider state, dropping anything unusable."""
        with self._lock:
            if not self.path.is_file():
                return {}
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # 损坏的状态文件不应让进程起不来；丢弃即可，最坏情况是少一次恢复。
                return {}
        if not isinstance(raw, dict) or raw.get("format_version") != _FORMAT_VERSION:
            return {}
        saved_at = raw.get("saved_at")
        if not isinstance(saved_at, (int, float)):
            return {}
        if self._clock() - float(saved_at) > _MAX_AGE_SECONDS:
            return {}
        breakers = raw.get("breakers")
        if not isinstance(breakers, dict):
            return {}
        result: dict[str, dict[str, Any]] = {}
        for name, state in breakers.items():
            if isinstance(name, str) and isinstance(state, dict):
                result[name] = state
        return result

    def forget(self, provider_name: str) -> bool:
        """Drop one provider's stored state. Returns whether anything was removed.

        手动重置一个 Provider 的熔断器时必须一并删掉它的持久化记录，否则下一次
        进程启动（或同一进程内的一次状态重建）会把刚被重置的隔离原样恢复回来，
        表现为「重置按钮没有作用」，而日志里既没有错误也没有重置痕迹。

        只删指定的那一个：顺手清空整个文件等于把「重置一家」变成「取消所有隔离」，
        而那是一次没人要求的放行——其余上游可能正因为真实故障被隔离着。
        """
        with self._lock:
            if not self.path.is_file():
                return False
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                # 文件已经不可读，`load()` 本来就会整体丢弃它，无需再改写。
                return False
            if not isinstance(raw, dict):
                return False
            breakers = raw.get("breakers")
            if not isinstance(breakers, dict) or str(provider_name) not in breakers:
                return False
            breakers.pop(str(provider_name), None)
        # 保留 format_version 与 saved_at：重写成新的时间戳会把其余 Provider 的
        # 「已经开了多久」清零，让一个本该很快进半开的熔断器重新等满整个恢复窗口。
        raw["breakers"] = breakers
        self._atomic_write(raw)
        return True

    def restore(
        self,
        breakers: Mapping[str, CircuitBreaker],
        *,
        stored: Optional[Mapping[str, Mapping[str, Any]]] = None,
    ) -> int:
        """Apply stored state to live breakers. Returns how many were restored."""
        records = stored if stored is not None else self.load()
        restored = 0
        for name, breaker in breakers.items():
            state = records.get(str(name))
            if not isinstance(state, Mapping):
                continue
            if breaker.restore_durable_state(state, elapsed_seconds=self._elapsed(records)):
                restored += 1
        return restored

    def _elapsed(self, records: Mapping[str, Any]) -> float:
        """Seconds between the save and now, used to age an open breaker forward."""
        saved_at = None
        if isinstance(records, dict):
            saved_at = records.get("__saved_at__")
        if not isinstance(saved_at, (int, float)):
            with self._lock:
                if not self.path.is_file():
                    return 0.0
                try:
                    raw = json.loads(self.path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    return 0.0
            saved_at = raw.get("saved_at") if isinstance(raw, dict) else None
        if not isinstance(saved_at, (int, float)):
            return 0.0
        return max(0.0, float(self._clock()) - float(saved_at))

    def _atomic_write(self, payload: dict[str, Any]) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".circuit-state-",
                suffix=".tmp",
                dir=self.path.parent,
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                    json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_path, self.path)
            finally:
                temporary_path.unlink(missing_ok=True)


__all__ = ["CircuitBreakerStore", "CircuitState"]
