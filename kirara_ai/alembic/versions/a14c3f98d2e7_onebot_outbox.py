"""Add persistent OneBot delivery outbox.

Revision ID: a14c3f98d2e7
Revises: 7f6a1c9e2b11
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a14c3f98d2e7"
down_revision: Union[str, None] = "7f6a1c9e2b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "onebot_outbox_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=False),
        sa.Column("logical_delivery_id", sa.String(length=64), nullable=False),
        sa.Column("recipient_key", sa.String(length=256), nullable=False),
        sa.Column("recipient_sequence", sa.Integer(), nullable=False),
        sa.Column("page_index", sa.Integer(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=False),
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
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("ambiguous_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id"),
        sa.UniqueConstraint(
            "recipient_key",
            "recipient_sequence",
            name="uq_onebot_outbox_recipient_sequence",
        ),
    )
    op.create_index(
        "idx_onebot_outbox_recipient_status_sequence",
        "onebot_outbox_deliveries",
        ["recipient_key", "status", "recipient_sequence"],
        unique=False,
    )
    op.create_index(
        "idx_onebot_outbox_status_next_attempt",
        "onebot_outbox_deliveries",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_onebot_outbox_deliveries_delivery_id"),
        "onebot_outbox_deliveries",
        ["delivery_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_onebot_outbox_deliveries_logical_delivery_id"),
        "onebot_outbox_deliveries",
        ["logical_delivery_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_onebot_outbox_deliveries_recipient_key"),
        "onebot_outbox_deliveries",
        ["recipient_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_onebot_outbox_deliveries_status"),
        "onebot_outbox_deliveries",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_onebot_outbox_deliveries_status"),
        table_name="onebot_outbox_deliveries",
    )
    op.drop_index(
        op.f("ix_onebot_outbox_deliveries_recipient_key"),
        table_name="onebot_outbox_deliveries",
    )
    op.drop_index(
        op.f("ix_onebot_outbox_deliveries_logical_delivery_id"),
        table_name="onebot_outbox_deliveries",
    )
    op.drop_index(
        op.f("ix_onebot_outbox_deliveries_delivery_id"),
        table_name="onebot_outbox_deliveries",
    )
    op.drop_index(
        "idx_onebot_outbox_status_next_attempt",
        table_name="onebot_outbox_deliveries",
    )
    op.drop_index(
        "idx_onebot_outbox_recipient_status_sequence",
        table_name="onebot_outbox_deliveries",
    )
    op.drop_table("onebot_outbox_deliveries")
