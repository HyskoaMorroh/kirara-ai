"""Add persistent QQ Bot delivery outbox.

Revision ID: b31e7c2a9f44
Revises: a14c3f98d2e7
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b31e7c2a9f44"
down_revision: Union[str, None] = "a14c3f98d2e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "qqbot_outbox_deliveries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("delivery_id", sa.String(length=64), nullable=False),
        sa.Column("logical_delivery_id", sa.String(length=64), nullable=False),
        sa.Column("recipient_key", sa.String(length=256), nullable=False),
        sa.Column("recipient_sequence", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("params_json", sa.Text(), nullable=False),
        sa.Column("media_file_type", sa.Integer(), nullable=True),
        sa.Column("media_data", sa.LargeBinary(), nullable=True),
        sa.Column("media_response_json", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("upload_attempt_count", sa.Integer(), nullable=False),
        sa.Column("upstream_accepted", sa.Boolean(), nullable=False),
        sa.Column("client_received", sa.Boolean(), nullable=True),
        sa.Column("response_json", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(), nullable=True),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("ambiguous_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("delivery_id"),
        sa.UniqueConstraint(
            "recipient_key",
            "recipient_sequence",
            name="uq_qqbot_outbox_recipient_sequence",
        ),
    )
    op.create_index(
        "idx_qqbot_outbox_recipient_status_sequence",
        "qqbot_outbox_deliveries",
        ["recipient_key", "status", "recipient_sequence"],
        unique=False,
    )
    op.create_index(
        "idx_qqbot_outbox_status_next_attempt",
        "qqbot_outbox_deliveries",
        ["status", "next_attempt_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_qqbot_outbox_deliveries_delivery_id"),
        "qqbot_outbox_deliveries",
        ["delivery_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_qqbot_outbox_deliveries_logical_delivery_id"),
        "qqbot_outbox_deliveries",
        ["logical_delivery_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_qqbot_outbox_deliveries_recipient_key"),
        "qqbot_outbox_deliveries",
        ["recipient_key"],
        unique=False,
    )
    op.create_index(
        op.f("ix_qqbot_outbox_deliveries_status"),
        "qqbot_outbox_deliveries",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_qqbot_outbox_deliveries_status"), table_name="qqbot_outbox_deliveries")
    op.drop_index(op.f("ix_qqbot_outbox_deliveries_recipient_key"), table_name="qqbot_outbox_deliveries")
    op.drop_index(op.f("ix_qqbot_outbox_deliveries_logical_delivery_id"), table_name="qqbot_outbox_deliveries")
    op.drop_index(op.f("ix_qqbot_outbox_deliveries_delivery_id"), table_name="qqbot_outbox_deliveries")
    op.drop_index("idx_qqbot_outbox_status_next_attempt", table_name="qqbot_outbox_deliveries")
    op.drop_index("idx_qqbot_outbox_recipient_status_sequence", table_name="qqbot_outbox_deliveries")
    op.drop_table("qqbot_outbox_deliveries")
