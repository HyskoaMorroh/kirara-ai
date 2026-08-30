"""「未标注」维度必须能被显式筛选（需求 22.2 的筛选项）。

统计接口按 provider / model / usage_source 等维度分组，其中 `null` 那一组在界面上
显示为「未标注」。但筛选参数层把空串当成「没填」丢掉，因此前端无法用
`provider=""` 表达「只看没有 provider 的记录」——用户选了「未标注」却拿到全量数据。

这比没有这个选项更糟：它给出一个**错误的答案**而不是拒绝回答。因此用一组独立的
`*_unset` 参数表达「该列为 NULL」，并保证统计接口与请求日志接口用同一套语义，
否则同一个筛选条件在两个页面会得到不同的结果集。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from kirara_ai.config.global_config import GlobalConfig
from kirara_ai.database import DatabaseManager
from kirara_ai.events.event_bus import EventBus
from kirara_ai.ioc.container import DependencyContainer
from kirara_ai.tracing import LLMTracer
from kirara_ai.tracing.models import LLMRequestTrace


@pytest.fixture()
def tracer(tmp_path):
    container = DependencyContainer()
    container.register(DependencyContainer, container)
    config = GlobalConfig()
    config.system.timezone = "UTC"
    container.register(GlobalConfig, config)
    database = DatabaseManager(
        container,
        database_url=f"sqlite:///{(tmp_path / 'traces.db').as_posix()}",
    )
    database.initialize()
    container.register(DatabaseManager, database)
    container.register(EventBus, EventBus())
    instance = LLMTracer(container)
    instance.initialize()
    return instance


@pytest.fixture()
def seeded(tracer: LLMTracer) -> LLMTracer:
    """两条 provider 已知、一条 provider 为 NULL。"""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rows = [
        ("t-1", "openai", 100),
        ("t-2", "openai", 200),
        ("t-3", None, 300),
    ]
    with tracer.db_manager.get_session() as session:
        for index, (trace_id, provider, tokens) in enumerate(rows):
            session.add(
                LLMRequestTrace(
                    trace_id=trace_id,
                    model_id="gpt-4o",
                    backend_name="b",
                    provider=provider,
                    request_time=now - timedelta(minutes=index),
                    status="success",
                    total_tokens=tokens,
                )
            )
        session.commit()
    return tracer


def test_statistics_can_isolate_the_rows_without_a_provider(seeded: LLMTracer):
    """`provider__is_null` 只返回 provider 为 NULL 的那一条。"""
    stats = seeded.get_statistics(filters={"provider__is_null": True})

    assert stats["overview"]["total_requests"] == 1
    assert stats["overview"]["total_tokens"] == 300


def test_statistics_without_the_flag_still_counts_every_row(seeded: LLMTracer):
    """对照组：不带该筛选时是全量，证明差异来自筛选而不是数据。"""
    stats = seeded.get_statistics(filters={})

    assert stats["overview"]["total_requests"] == 3


def test_an_explicit_provider_filter_excludes_the_null_row(seeded: LLMTracer):
    stats = seeded.get_statistics(filters={"provider": "openai"})

    assert stats["overview"]["total_requests"] == 2


def test_the_request_log_uses_the_same_null_semantics(seeded: LLMTracer):
    """列表页与统计页必须同一语义，否则同一筛选在两处给出不同结果集。"""
    records, total = seeded.get_traces(
        filters={"provider__is_null": True},
        page=1,
        page_size=50,
    )

    assert total == 1
    assert [record.trace_id for record in records] == ["t-3"]


def test_an_unknown_is_null_column_is_ignored_rather_than_crashing(seeded: LLMTracer):
    """未知列名不得让整个请求 500——筛选参数来自外部输入。"""
    stats = seeded.get_statistics(filters={"nonexistent__is_null": True})

    assert stats["overview"]["total_requests"] == 3


@pytest.mark.parametrize(
    "raw",
    ["1", "true", "TRUE", "yes", True],
)
def test_the_route_layer_accepts_the_usual_truthy_spellings(raw):
    """查询串里的布尔值有多种写法，`?provider_unset=true` 与 `=1` 都要生效。"""
    import asyncio

    from kirara_ai.web.api.tracing import routes

    options, error = asyncio.run(
        routes._trace_request_options({"provider_unset": raw}, GlobalConfig())
    )

    assert error is None
    assert options is not None
    assert options["filters"].get("provider__is_null") is True


@pytest.mark.parametrize("raw", ["0", "false", "", None])
def test_a_falsy_flag_adds_no_filter(raw):
    import asyncio

    from kirara_ai.web.api.tracing import routes

    options, error = asyncio.run(
        routes._trace_request_options({"provider_unset": raw}, GlobalConfig())
    )

    assert error is None
    assert options is not None
    assert "provider__is_null" not in options["filters"]


def test_combining_the_flag_with_an_explicit_value_is_rejected():
    """同时要求「provider 等于 openai」和「provider 为空」是矛盾条件。

    静默丢掉其中一个会让用户以为筛选生效了；这里直接 400 并说明原因。
    """
    import asyncio

    from quart import Quart

    from kirara_ai.web.api.tracing import routes

    app = Quart(__name__)

    async def resolve():
        # `jsonify` 需要应用上下文；错误分支会构造响应体。
        async with app.app_context():
            return await routes._trace_request_options(
                {"provider": "openai", "provider_unset": "1"}, GlobalConfig()
            )

    options, error = asyncio.run(resolve())

    assert options is None
    assert error is not None
    assert error[1] == 400
