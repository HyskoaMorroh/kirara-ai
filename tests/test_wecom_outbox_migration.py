from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer


def _tables(database: DatabaseManager) -> dict[str, set[str]]:
    inspector = inspect(database.engine)
    return {
        table: {column["name"] for column in inspector.get_columns(table)}
        for table in inspector.get_table_names()
    }


def test_qqbot_schema_upgrades_to_wecom_outbox(tmp_path: Path):
    database_path = tmp_path / "legacy.db"
    url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "b31e7c2a9f44")
    database = DatabaseManager(DependencyContainer(), database_url=url)
    database.initialize()

    tables = _tables(database)
    assert {
        "message_id",
        "source",
        "status",
        "passive_reply",
        "completed_at",
    } <= tables["wecom_inbound_receipts"]
    assert {
        "delivery_id",
        "recipient_key",
        "action",
        "params_json",
        "status",
        "attempt_count",
        "upstream_accepted",
        "client_received",
    } <= tables["wecom_outbox_deliveries"]


def test_fresh_database_initialization_creates_wecom_outbox(tmp_path: Path):
    database = DatabaseManager(
        DependencyContainer(),
        database_url=f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}",
    )

    database.initialize()

    tables = _tables(database)
    assert "wecom_inbound_receipts" in tables
    assert "wecom_outbox_deliveries" in tables
