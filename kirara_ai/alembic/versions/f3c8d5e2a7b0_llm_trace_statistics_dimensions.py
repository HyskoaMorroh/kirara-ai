"""Add indexed LLM trace statistics dimensions.

Revision ID: f3c8d5e2a7b0
Revises: e2b7c4d1f6a9
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f3c8d5e2a7b0"
down_revision: Union[str, None] = "e2b7c4d1f6a9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_request_traces",
        sa.Column("provider", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "llm_request_traces",
        sa.Column("error_category", sa.String(length=32), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE llm_request_traces "
            "SET provider = backend_name "
            "WHERE provider IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE llm_request_traces "
            "SET error_category = 'unknown' "
            "WHERE status = 'failed' AND error_category IS NULL"
        )
    )
    op.execute(
        sa.text(
            "UPDATE llm_request_traces "
            "SET usage_source = 'unknown' "
            "WHERE usage_source IS NULL"
        )
    )
    op.create_index(
        "idx_provider_time",
        "llm_request_traces",
        ["provider", "request_time"],
        unique=False,
    )
    op.create_index(
        "idx_error_category_time",
        "llm_request_traces",
        ["error_category", "request_time"],
        unique=False,
    )
    op.create_index(
        "idx_usage_source_time",
        "llm_request_traces",
        ["usage_source", "request_time"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_usage_source_time", table_name="llm_request_traces")
    op.drop_index("idx_error_category_time", table_name="llm_request_traces")
    op.drop_index("idx_provider_time", table_name="llm_request_traces")
    op.drop_column("llm_request_traces", "error_category")
    op.drop_column("llm_request_traces", "provider")
