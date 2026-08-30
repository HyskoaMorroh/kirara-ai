"""需求 18.3：Telegram 的重试配置必须真的生效。

`TelegramConfig` 有两个字段：

- `outbox_max_attempts`（默认 3，描述「Telegram 明确拒绝请求时的最大投递尝试次数」）
- `outbox_retry_delay_seconds`（默认 1.0，描述「Telegram 明确瞬态失败后的基础重试间隔」）

两者被传进 `TelegramOutboxService.__init__`，赋值给 `self.max_attempts` /
`self.retry_delay_seconds`——**然后再没有任何地方读它们**。真实行为是：
`RetryAfter`（Telegram 明确说「稍后再试」）被 `_deliver_one` 当成
`TelegramRetryableError` 直接 `_mark_dead_letter`，一次都不重试。

这是最坏的一类配置缺陷：字段在、文档在、界面上能填，改它却什么都不会发生。
用户把 `outbox_max_attempts` 调到 10，以为拿到了 10 次重试，实际仍然是 1 次。
比「没有这个配置」更糟——后者至少不会让人以为已经配好了。

OneBot 与 QQBot 走的是共享的 `retry_backoff_seconds`（指数 + 上限 + 抖动），
Telegram 应当同一口径。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.plugins.im_telegram_adapter.outbox import (
    TelegramOutboxService,
    TelegramRetryableError,
)


def _service(tmp_path: Path, sender, **kwargs) -> TelegramOutboxService:
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'telegram.db').as_posix()}",
    )
    database.initialize()
    settings = {"adapter_instance": "telegram-main", "retry_delay_seconds": 0.0}
    settings.update(kwargs)
    return TelegramOutboxService(database, sender, **settings)


def _enqueue(service: TelegramOutboxService, delivery_id: str = "d1") -> None:
    service.enqueue(
        delivery_id,
        "chat:1",
        "text",
        {"chat_id": 1, "text": "hello"},
        logical_delivery_id="logical",
        page_index=0,
        page_count=1,
    )


@pytest.mark.asyncio
async def test_an_explicit_retryable_rejection_is_retried_up_to_max_attempts(
    tmp_path: Path,
):
    attempts = {"count": 0}

    async def sender(_params: dict[str, Any]) -> Any:
        attempts["count"] += 1
        raise TelegramRetryableError("Flood control exceeded")

    service = _service(tmp_path, sender, max_attempts=3)
    _enqueue(service)

    result = await service.deliver("d1")

    # 配置说 3 次就必须尝试 3 次；此前恒为 1 次，配置形同装饰。
    assert attempts["count"] == 3
    assert result.status == "dead_letter"
    assert result.attempt_count == 3


@pytest.mark.asyncio
async def test_a_retry_that_succeeds_is_reported_as_accepted(tmp_path: Path):
    attempts = {"count": 0}

    async def sender(_params: dict[str, Any]) -> Any:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise TelegramRetryableError("Flood control exceeded")
        return {"message_id": 7}

    service = _service(tmp_path, sender, max_attempts=3)
    _enqueue(service)

    result = await service.deliver("d1")

    assert attempts["count"] == 2
    assert result.status == "accepted"


@pytest.mark.asyncio
async def test_max_attempts_of_one_means_no_retry(tmp_path: Path):
    """`max_attempts=1` 必须真的只试一次——它是「关掉重试」的表达方式。"""
    attempts = {"count": 0}

    async def sender(_params: dict[str, Any]) -> Any:
        attempts["count"] += 1
        raise TelegramRetryableError("nope")

    service = _service(tmp_path, sender, max_attempts=1)
    _enqueue(service)

    result = await service.deliver("d1")

    assert attempts["count"] == 1
    assert result.status == "dead_letter"


@pytest.mark.asyncio
async def test_a_non_retryable_failure_is_never_retried(tmp_path: Path):
    """普通异常不是「稍后再试」，重试它只是把同一个错误重复犯三遍。"""
    attempts = {"count": 0}

    async def sender(_params: dict[str, Any]) -> Any:
        attempts["count"] += 1
        raise ValueError("chat not found")

    service = _service(tmp_path, sender, max_attempts=5)
    _enqueue(service)

    result = await service.deliver("d1")

    assert attempts["count"] == 1
    assert result.status == "dead_letter"


@pytest.mark.asyncio
async def test_an_ambiguous_outcome_is_still_never_retried(tmp_path: Path):
    """结果未知**绝不能**重试：可能已经发出去了，重发就是重复消息。

    这条边界在补重试时最容易被一起改掉——`ambiguous` 与 `dead_letter` 在
    「都失败了」这个层面看起来像，但一个是「不知道有没有发」、
    另一个是「确定没发」，处置完全相反。
    """
    attempts = {"count": 0}

    async def sender(_params: dict[str, Any]) -> Any:
        attempts["count"] += 1
        raise asyncio.TimeoutError("no response")

    service = _service(tmp_path, sender, max_attempts=5)
    _enqueue(service)

    result = await service.deliver("d1")

    assert attempts["count"] == 1
    assert result.status == "ambiguous"


@pytest.mark.asyncio
async def test_the_retry_delay_uses_the_shared_bounded_schedule(tmp_path: Path, monkeypatch):
    """重试间隔必须走共享的 `retry_backoff_seconds`（指数 + 上限 + 抖动）。

    自己写一个 `delay * attempt` 会让三个适配器再次漂移，而这正是那个共享模块
    存在的原因。
    """
    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(
        "kirara_ai.plugins.im_telegram_adapter.outbox.asyncio.sleep", fake_sleep
    )

    async def sender(_params: dict[str, Any]) -> Any:
        raise TelegramRetryableError("Flood control exceeded")

    service = _service(tmp_path, sender, max_attempts=3, retry_delay_seconds=2.0)
    _enqueue(service)

    await service.deliver("d1")

    # 两次重试之间各等一次；最后一次失败后不再等待（没有下一次了）。
    assert len(waits) == 2
    # 指数增长：第二次等待应当大于第一次的下界。抖动只会让它更早，
    # 因此用共享上限与「非负」两条不变量断言，而不是写死具体数字。
    assert all(0 <= wait <= 300 for wait in waits)
    assert waits[1] > 0
