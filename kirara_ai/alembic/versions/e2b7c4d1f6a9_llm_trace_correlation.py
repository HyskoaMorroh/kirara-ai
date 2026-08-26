"""Correlate LLM traces that belong to one Agent turn.

Revision ID: e2b7c4d1f6a9
Revises: d9f6a2c1e8b4
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e2b7c4d1f6a9"
down_revision: Union[str, None] = "d9f6a2c1e8b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_request_traces",
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_llm_request_traces_correlation_id",
        "llm_request_traces",
        ["correlation_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_llm_request_traces_correlation_id",
        table_name="llm_request_traces",
    )
    op.drop_column("llm_request_traces", "correlation_id")
