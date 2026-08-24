from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_previous_schema_upgrades_with_onebot_outbox(tmp_path: Path):
    database_path = tmp_path / "legacy.db"
    url = f"sqlite:///{database_path.as_posix()}"
    engine = create_engine(url)
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", url)

    command.upgrade(config, "7f6a1c9e2b11")
    command.upgrade(config, "head")

    inspector = inspect(engine)
    assert "onebot_outbox_deliveries" in inspector.get_table_names()
    columns = {
        column["name"]
        for column in inspector.get_columns("onebot_outbox_deliveries")
    }
    assert {
        "delivery_id",
        "logical_delivery_id",
        "recipient_sequence",
        "status",
        "upstream_accepted",
        "client_received",
    } <= columns
