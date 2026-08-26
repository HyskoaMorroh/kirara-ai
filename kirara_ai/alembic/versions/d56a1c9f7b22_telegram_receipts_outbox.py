"""Add persistent Telegram receipts and outbound delivery queue.

Revision ID: d56a1c9f7b22
Revises: c42d8e7f1a55
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d56a1c9f7b22"
down_revision: Union[str, None] = "c42d8e7f1a55"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_inbound_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("adapter_instance", sa.String(length=128), nullable=False),
        sa.Column("update_id", sa.String(length=64), nullable=False),
        sa.Column("chat_key", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "adapter_instance",
            "update_id",
            name="uq_telegram_inbound_adapter_update",
        ),
    )
    op.create_index(
        "idx_telegram_inbound_status",
        "telegram_inbound_receipts",
        ["adapter_instance", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_telegram_inbound_receipts_status"),
        "telegram_inbound_receipts",
        ["status"],
        unique=False,
    )

    op.create_table(
        "telegram_outbox_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=False),
        sa.Column("logical_delivery_id", sa.String(length=64), nullable=False),
        sa.Column("adapter_instance", sa.String(length=128), nullable=False),
        sa.Column("recipient_key", sa.String(length=256), nullable=False),
        sa.Column("recipient_sequence", sa.Integer(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("upstream_accepted", sa.Boolean(), nullable=False),
        sa.Column("client_received", sa.Boolean(), nullable=True),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("ambiguous_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id"),
        sa.UniqueConstraint(
            "recipient_key",
            "recipient_sequence",
            name="uq_telegram_outbox_recipient_sequence",
        ),
    )
    for name, columns in (
        ("idx_telegram_outbox_recipient_status_sequence", ["recipient_key", "status", "recipient_sequence"]),
        ("idx_telegram_outbox_status", ["status", "created_at"]),
    ):
        op.create_index(name, "telegram_outbox_deliveries", columns, unique=False)
    for column in ("delivery_id", "logical_delivery_id", "adapter_instance", "recipient_key", "status"):
        op.create_index(
            op.f(f"ix_telegram_outbox_deliveries_{column}"),
            "telegram_outbox_deliveries",
            [column],
            unique=(column == "delivery_id"),
        )


def downgrade() -> None:
    for column in ("status", "recipient_key", "adapter_instance", "logical_delivery_id", "delivery_id"):
        op.drop_index(
            op.f(f"ix_telegram_outbox_deliveries_{column}"),
            table_name="telegram_outbox_deliveries",
        )
    op.drop_index("idx_telegram_outbox_status", table_name="telegram_outbox_deliveries")
    op.drop_index(
        "idx_telegram_outbox_recipient_status_sequence",
        table_name="telegram_outbox_deliveries",
    )
    op.drop_table("telegram_outbox_deliveries")
    op.drop_index(op.f("ix_telegram_inbound_receipts_status"), table_name="telegram_inbound_receipts")
    op.drop_index("idx_telegram_inbound_status", table_name="telegram_inbound_receipts")
    op.drop_table("telegram_inbound_receipts")
