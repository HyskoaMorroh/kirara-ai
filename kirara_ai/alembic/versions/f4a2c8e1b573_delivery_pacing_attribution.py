"""Split the send phase into pacing wait and real upstream time.

需求 19.5 点名五种原因不能混成一个「QQ 慢」，其中一项是**发送限流**。
而 ``send_seconds`` 是 ``send_started → send_succeeded`` 的整段墙钟时间，
里面同时含着两件性质相反的事：我们为防刷屏**主动等**的时间，和上游**真的慢**
的时间。两者的处置正好相反——前者调 ``send_pacing`` 配置，后者查上游。
混成一个数字时，一条十页回复因节流等了 20 秒会显示成「平台发送 20 秒」，
运维去查 QQ 而 QQ 什么问题都没有。

``send_seconds`` 保留不动：它回答「用户等了多久」，那是一个独立且必要的问题。
新增的两列回答「该去查谁」。

两列都可空，且**空与 0 含义不同**：空是「这条链路没有测量节流」
（没有节流概念的 Telegram / WeCom、以及第三方适配器），
0 是「测了，这一次没等」（单页回复）。把前者写成 0 会让运维排除掉一个
其实没有被测量的原因。

Revision ID: f4a2c8e1b573
Revises: d8b4e6f2c917
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f4a2c8e1b573"
down_revision: Union[str, None] = "d8b4e6f2c917"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 历史行不回填。
#:
#: 回填需要一个「当时节流等了多久」的值，而那个值从来没有被记录过。
#: 按配置重算是编数据：`send_pacing` 可能在这之间改过，抖动本身也是随机的。
#: 留 NULL 是唯一诚实的选择，它在界面上显示成「未测到」——
#: 与「这次没等」（0）区分开，读者因此知道这一段无从判断。
_COLUMNS = ("send_pacing_seconds", "send_upstream_seconds")


def _existing_columns(table: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    if table not in inspector.get_table_names():
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    existing = _existing_columns("im_delivery_timings")
    if not existing:
        # 表还不存在（全新库会由更早的迁移建好）：没有可加列的对象。
        return
    for name in _COLUMNS:
        if name in existing:
            continue
        op.add_column(
            "im_delivery_timings", sa.Column(name, sa.Float(), nullable=True)
        )


def downgrade() -> None:
    existing = _existing_columns("im_delivery_timings")
    for name in _COLUMNS:
        if name in existing:
            op.drop_column("im_delivery_timings", name)
