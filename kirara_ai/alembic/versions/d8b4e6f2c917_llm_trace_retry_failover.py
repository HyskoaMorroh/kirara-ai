"""Split attempt counts into retries and failovers.

Revision ID: d8b4e6f2c917
Revises: c7f1b3a9d204

需求 22.1 把「重试」与「故障转移」列为两项，而 `attempt_count` 分不开它们：
同一家重试 3 次与切换 3 家各试 1 次都是 3，处置却完全相反（前者调超时与退避，
后者查供应商健康与熔断）。

回填用 `attempts_json` 里已有的 `provider` 序列完成：相邻两次 provider 相同算
一次重试，不同算一次故障转移。取不出序列的历史行保持 NULL——「没有数据」
与「确实没重试过」是两件事，把前者写成 0 会让人以为链路一直很干净。
"""

import json
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d8b4e6f2c917"
down_revision: Union[str, None] = "c7f1b3a9d204"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _split(attempts_json: object) -> tuple[object, object]:
    """Return ``(retry_count, failover_count)`` or ``(None, None)``."""
    if not attempts_json:
        return None, None
    try:
        attempts = json.loads(attempts_json)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(attempts, list) or not attempts:
        return None, None
    providers = [
        str((item or {}).get("provider") or "") if isinstance(item, dict) else ""
        for item in attempts
    ]
    retries = 0
    failovers = 0
    for previous, current in zip(providers, providers[1:]):
        if current == previous:
            retries += 1
        else:
            failovers += 1
    return retries, failovers


def upgrade() -> None:
    op.add_column(
        "llm_request_traces",
        sa.Column("retry_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "llm_request_traces",
        sa.Column("failover_count", sa.Integer(), nullable=True),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, attempts_json FROM llm_request_traces "
            "WHERE attempts_json IS NOT NULL"
        )
    ).fetchall()
    for row_id, attempts_json in rows:
        retries, failovers = _split(attempts_json)
        if retries is None:
            continue
        connection.execute(
            sa.text(
                "UPDATE llm_request_traces "
                "SET retry_count = :retries, failover_count = :failovers "
                "WHERE id = :row_id"
            ),
            {"retries": retries, "failovers": failovers, "row_id": row_id},
        )


def downgrade() -> None:
    op.drop_column("llm_request_traces", "failover_count")
    op.drop_column("llm_request_traces", "retry_count")
