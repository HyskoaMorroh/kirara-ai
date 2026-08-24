"""Persist LLM usage provenance, attempts and cost snapshots.

Revision ID: 7f6a1c9e2b11
Revises: 4a364dbb8dab
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "7f6a1c9e2b11"
down_revision: Union[str, None] = "4a364dbb8dab"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("llm_request_traces", sa.Column("cache_write_tokens", sa.Integer(), nullable=True))
    op.add_column("llm_request_traces", sa.Column("usage_source", sa.String(length=20), nullable=True))
    op.add_column("llm_request_traces", sa.Column("ttft_ms", sa.Integer(), nullable=True))
    op.add_column("llm_request_traces", sa.Column("attempt_count", sa.Integer(), nullable=True))
    op.add_column("llm_request_traces", sa.Column("attempts_json", sa.Text(), nullable=True))
    op.add_column("llm_request_traces", sa.Column("cost_snapshot_json", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_request_traces", "cost_snapshot_json")
    op.drop_column("llm_request_traces", "attempts_json")
    op.drop_column("llm_request_traces", "attempt_count")
    op.drop_column("llm_request_traces", "ttft_ms")
    op.drop_column("llm_request_traces", "usage_source")
    op.drop_column("llm_request_traces", "cache_write_tokens")
