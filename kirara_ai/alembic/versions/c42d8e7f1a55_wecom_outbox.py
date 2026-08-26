"""Add persistent WeCom receipts and outbox.

Revision ID: c42d8e7f1a55
Revises: b31e7c2a9f44
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c42d8e7f1a55"
down_revision: Union[str, None] = "b31e7c2a9f44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wecom_inbound_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.String(length=128), nullable=False),
        sa.Column("source", sa.String(length=256), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("passive_reply", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("message_id"),
    )
    op.create_index(
        op.f("ix_wecom_inbound_receipts_message_id"),
        "wecom_inbound_receipts",
        ["message_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_wecom_inbound_receipts_status"),
        "wecom_inbound_receipts",
        ["status"],
        unique=False,
    )

    op.create_table(
        "wecom_outbox_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=False),
        sa.Column("recipient_key", sa.String(length=256), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
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
    )
    op.create_index(
        op.f("ix_wecom_outbox_deliveries_delivery_id"),
        "wecom_outbox_deliveries",
        ["delivery_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_wecom_outbox_deliveries_recipient_key"),
        "wecom_outbox_deliveries",
        ["recipient_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_wecom_outbox_deliveries_status"),
        "wecom_outbox_deliveries",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_wecom_outbox_deliveries_status"), table_name="wecom_outbox_deliveries")
    op.drop_index(op.f("ix_wecom_outbox_deliveries_recipient_key"), table_name="wecom_outbox_deliveries")
    op.drop_index(op.f("ix_wecom_outbox_deliveries_delivery_id"), table_name="wecom_outbox_deliveries")
    op.drop_table("wecom_outbox_deliveries")
    op.drop_index(op.f("ix_wecom_inbound_receipts_status"), table_name="wecom_inbound_receipts")
    op.drop_index(op.f("ix_wecom_inbound_receipts_message_id"), table_name="wecom_inbound_receipts")
    op.drop_table("wecom_inbound_receipts")
