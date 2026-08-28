"""Add durable per-reply delivery timings.

The in-memory timeline answers "why was this reply slow" while it is in flight,
but nothing could answer "QQ felt slow last Tuesday — model or send?" a week
later. This table stores durations and counts only: no message content, and a
phase that was never measured stays NULL rather than being written as 0.

Revision ID: b5e2c94a17d8
Revises: a4d1f8c30e57
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b5e2c94a17d8"
down_revision: Union[str, None] = "a4d1f8c30e57"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "im_delivery_timings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("adapter_instance", sa.String(length=128), nullable=False),
        sa.Column("conversation_digest", sa.String(length=64), nullable=True),
        sa.Column("recorded_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("queue_seconds", sa.Float(), nullable=True),
        sa.Column("llm_first_byte_seconds", sa.Float(), nullable=True),
        sa.Column("llm_generation_seconds", sa.Float(), nullable=True),
        sa.Column("formatting_seconds", sa.Float(), nullable=True),
        sa.Column("send_seconds", sa.Float(), nullable=True),
        sa.Column("total_seconds", sa.Float(), nullable=True),
        sa.Column("segment_count", sa.Integer(), nullable=True),
        sa.Column("retry_count", sa.Integer(), nullable=True),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_im_delivery_channel_time",
        "im_delivery_timings",
        ["channel", "recorded_at"],
        unique=False,
    )
    op.create_index(
        "idx_im_delivery_status_time",
        "im_delivery_timings",
        ["status", "recorded_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_delivery_timings_recorded_at"),
        "im_delivery_timings",
        ["recorded_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_im_delivery_timings_correlation_id"),
        "im_delivery_timings",
        ["correlation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_im_delivery_timings_correlation_id"), table_name="im_delivery_timings"
    )
    op.drop_index(
        op.f("ix_im_delivery_timings_recorded_at"), table_name="im_delivery_timings"
    )
    op.drop_index("idx_im_delivery_status_time", table_name="im_delivery_timings")
    op.drop_index("idx_im_delivery_channel_time", table_name="im_delivery_timings")
    op.drop_table("im_delivery_timings")
