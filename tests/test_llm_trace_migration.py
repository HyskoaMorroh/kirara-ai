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
    command.upgrade(config, "head")

    columns = {column["name"] for column in inspect(engine).get_columns("llm_request_traces")}
    assert {
        "cache_write_tokens",
        "usage_source",
        "ttft_ms",
        "attempt_count",
        "attempts_json",
        "cost_snapshot_json",
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

    assert migrated.provider == "legacy-provider"
    assert migrated.error_category == "unknown"
    assert migrated.usage_source == "unknown"
