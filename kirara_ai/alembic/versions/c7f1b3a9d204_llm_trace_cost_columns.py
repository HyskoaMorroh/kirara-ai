"""Project LLM cost snapshots onto summable columns.

Revision ID: c7f1b3a9d204
Revises: b5e2c94a17d8

成本一直存在 ``cost_snapshot_json`` 这个 Text 列里（历史账单必须沿用请求当时的
定价快照，拿现价重算是错的）。代价是汇总无法用 ``SUM``：统计要把筛选后的每一行
取回 Python 逐条 ``json.loads``，已有的六个复合索引在这条路径上完全没用，
而请求日志有分页保护、统计页没有——一年几十万条追踪时打开统计页就是一次全表物化。

这里把快照里的总成本与币种投影成两个专用列。快照仍是权威来源，两列只是它的
只读投影。回填用 SQLite / PostgreSQL 都支持的 JSON 文本切分完成，不引入
数据库特有的 JSON 函数：``total_cost`` 在快照里是一个带引号的十进制字符串。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c7f1b3a9d204"
down_revision: Union[str, None] = "b5e2c94a17d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "llm_request_traces",
        sa.Column("total_cost", sa.Numeric(20, 8), nullable=True),
    )
    op.add_column(
        "llm_request_traces",
        sa.Column("cost_currency", sa.String(length=3), nullable=True),
    )
    # 回填历史行。逐行解析要拉全表，所以在 SQL 里按固定的快照格式切分：
    # `"total_cost": "0.5"` / `"currency": "USD"`。切不出来的行保持 NULL——
    # 「没有定价证据」不能写成 0，写成 0 会让历史账单凭空变小。
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT id, cost_snapshot_json FROM llm_request_traces "
            "WHERE cost_snapshot_json IS NOT NULL"
        )
    ).fetchall()
    for row_id, snapshot in rows:
        cost, currency = _parse_snapshot(snapshot)
        if cost is None:
            continue
        connection.execute(
            sa.text(
                "UPDATE llm_request_traces "
                "SET total_cost = :cost, cost_currency = :currency "
                "WHERE id = :row_id"
            ),
            {"cost": cost, "currency": currency, "row_id": row_id},
        )
    op.create_index(
        "idx_cost_currency_time",
        "llm_request_traces",
        ["cost_currency", "request_time"],
        unique=False,
    )


def _parse_snapshot(snapshot: object) -> tuple[object, object]:
    """Return ``(total_cost, currency)`` from one snapshot, or ``(None, None)``."""
    import json

    if not snapshot:
        return None, None
    try:
        payload = json.loads(snapshot)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(payload, dict):
        return None, None
    raw_cost = payload.get("total_cost")
    if raw_cost is None:
        return None, None
    currency = payload.get("currency")
    return str(raw_cost), (str(currency)[:3] if currency else None)


def downgrade() -> None:
    op.drop_index("idx_cost_currency_time", table_name="llm_request_traces")
    op.drop_column("llm_request_traces", "cost_currency")
    op.drop_column("llm_request_traces", "total_cost")
