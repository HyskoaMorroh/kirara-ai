from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from kirara_ai.database import DatabaseManager
from kirara_ai.ioc.container import DependencyContainer


QQBOT_OUTBOX_COLUMNS = {
    "delivery_id",
    "logical_delivery_id",
    "recipient_key",
    "recipient_sequence",
    "params_json",
    "media_data",
    "media_response_json",
    "status",
    "attempt_count",
    "upload_attempt_count",
    "upstream_accepted",
    "client_received",
}


def test_previous_schema_upgrades_with_qqbot_outbox(tmp_path: Path):
    database_path = tmp_path / "legacy.db"
    url = f"sqlite:///{database_path.as_posix()}"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "a14c3f98d2e7")
    database = DatabaseManager(DependencyContainer(), database_url=url)
    database.initialize()

    inspector = inspect(database.engine)
    assert "qqbot_outbox_deliveries" in inspector.get_table_names()
    columns = {
        column["name"]
        for column in inspector.get_columns("qqbot_outbox_deliveries")
    }
    assert QQBOT_OUTBOX_COLUMNS <= columns


def test_fresh_database_initialization_creates_qqbot_outbox(tmp_path: Path):
    database = DatabaseManager(
        DependencyContainer(),
        database_url=f"sqlite:///{(tmp_path / 'fresh.db').as_posix()}",
    )

    database.initialize()

    inspector = inspect(database.engine)
    assert "qqbot_outbox_deliveries" in inspector.get_table_names()
    columns = {
        column["name"]
        for column in inspector.get_columns("qqbot_outbox_deliveries")
    }
    assert QQBOT_OUTBOX_COLUMNS <= columns
