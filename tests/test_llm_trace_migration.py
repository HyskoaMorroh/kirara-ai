from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_old_llm_trace_schema_upgrades_with_usage_attempt_and_cost_columns(tmp_path: Path):
    database_path = tmp_path / "legacy.db"
    url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "4a364dbb8dab")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO llm_request_traces (
                    trace_id, model_id, backend_name, request_time, status
                ) VALUES (
                    'legacy-failure', 'legacy-model', 'legacy-provider',
                    '2026-08-27 12:00:00', 'failed'
                )
                """
            )
        )
    # 停在成本投影列之前，插一条**带价格快照**的历史行，
    # 这样回填逻辑才真的被执行到——空表上跑迁移证明不了回填是对的。
    command.upgrade(config, "b5e2c94a17d8")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO llm_request_traces (
                    trace_id, model_id, backend_name, request_time, status,
                    cost_snapshot_json
                ) VALUES (
                    'legacy-priced', 'legacy-model', 'legacy-provider',
                    '2026-08-27 12:05:00', 'success',
                    '{"currency": "USD", "total_cost": "1.25", "price_version_id": "v1"}'
                )
                """
            )
        )
        # 一条带 attempts 的历史行：A → A → B 应回填成「重试 1 次、转移 1 次」。
        connection.execute(
            text(
                """
                INSERT INTO llm_request_traces (
                    trace_id, model_id, backend_name, request_time, status,
                    attempts_json
                ) VALUES (
                    'legacy-attempts', 'legacy-model', 'provider-a',
                    '2026-08-27 12:06:00', 'success',
                    '[{"provider": "provider-a"}, {"provider": "provider-a"}, {"provider": "provider-b"}]'
                )
                """
            )
        )
    command.upgrade(config, "head")

    columns = {column["name"] for column in inspect(engine).get_columns("llm_request_traces")}
    assert {
        "cache_write_tokens",
        "usage_source",
        "ttft_ms",
        "attempt_count",
        "attempts_json",
        "cost_snapshot_json",
        "total_cost",
        "cost_currency",
        "retry_count",
        "failover_count",
        "correlation_id",
        "provider",
        "error_category",
    } <= columns
    indexes = {index["name"] for index in inspect(engine).get_indexes("llm_request_traces")}
    assert {
        "ix_llm_request_traces_correlation_id",
        "idx_provider_time",
        "idx_error_category_time",
        "idx_usage_source_time",
        "idx_cost_currency_time",
    } <= indexes

    with engine.connect() as connection:
        migrated = connection.execute(
            text(
                """
                SELECT provider, error_category, usage_source
                FROM llm_request_traces
                WHERE trace_id = 'legacy-failure'
                """
            )
        ).one()
        priced = connection.execute(
            text(
                """
                SELECT total_cost, cost_currency
                FROM llm_request_traces
                WHERE trace_id = 'legacy-priced'
                """
            )
        ).one()
        unpriced = connection.execute(
            text(
                """
                SELECT total_cost, cost_currency
                FROM llm_request_traces
                WHERE trace_id = 'legacy-failure'
                """
            )
        ).one()
        counts = connection.execute(
            text(
                """
                SELECT retry_count, failover_count
                FROM llm_request_traces
                WHERE trace_id = 'legacy-attempts'
                """
            )
        ).one()
        no_attempts = connection.execute(
            text(
                """
                SELECT retry_count, failover_count
                FROM llm_request_traces
                WHERE trace_id = 'legacy-failure'
                """
            )
        ).one()

    assert migrated.provider == "legacy-provider"
    assert migrated.error_category == "unknown"
    assert migrated.usage_source == "unknown"
    # 有快照的历史行被回填成可 SUM 的金额与币种。
    assert float(priced.total_cost) == 1.25
    assert priced.cost_currency == "USD"
    # 没有快照的行保持 NULL：「没有定价证据」不是「花了 0 元」，
    # 回填成 0 会让历史账单凭空变小，而且没有任何报错。
    assert unpriced.total_cost is None
    assert unpriced.cost_currency is None
    # A → A → B 回填成「重试 1 次、转移 1 次」；`attempt_count` 分不开这两者。
    assert counts.retry_count == 1
    assert counts.failover_count == 1
    # 没有 attempts 的历史行保持 NULL，与「确实没重试过」（0）区分。
    assert no_attempts.retry_count is None
    assert no_attempts.failover_count is None
