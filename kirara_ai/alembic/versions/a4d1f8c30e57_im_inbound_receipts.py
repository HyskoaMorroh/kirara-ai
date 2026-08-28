"""Add shared inbound dedup receipts for every IM channel.

Telegram already had ``telegram_inbound_receipts``; OneBot and QQBot had no
inbound dedup at all, so an upstream that redelivered an event after a reconnect
made the whole workflow run again (duplicate model cost, duplicate reply).
This table is channel-scoped so all adapters share one inbound contract.

Revision ID: a4d1f8c30e57
Revises: f3c8d5e2a7b0
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a4d1f8c30e57"
down_revision: Union[str, None] = "f3c8d5e2a7b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "im_inbound_receipts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("adapter_instance", sa.String(length=128), nullable=False),
        sa.Column("event_key", sa.String(length=128), nullable=False),
        sa.Column("chat_key", sa.String(length=256), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "channel",
            "adapter_instance",
            "event_key",
            name="uq_im_inbound_channel_adapter_event",
        ),
    )
    op.create_index(
        "idx_im_inbound_status",
        "im_inbound_receipts",
        ["channel", "adapter_instance", "status"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_inbound_receipts_status"),
        "im_inbound_receipts",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_im_inbound_receipts_status"), table_name="im_inbound_receipts")
    op.drop_index("idx_im_inbound_status", table_name="im_inbound_receipts")
    op.drop_table("im_inbound_receipts")
