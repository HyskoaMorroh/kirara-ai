"""Store replayable Telegram Update payloads for inbound recovery.

Revision ID: d9f6a2c1e8b4
Revises: d56a1c9f7b22
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d9f6a2c1e8b4"
down_revision: Union[str, None] = "d56a1c9f7b22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_inbound_receipts",
        sa.Column("payload_json", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("telegram_inbound_receipts", "payload_json")
