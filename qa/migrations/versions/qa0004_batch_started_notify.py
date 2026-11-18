"""批次开始通知的幂等认领列。

Revision ID: qa0004startnotify
Revises: qa0003projectenv

一个批次会按系统/分片拆成多个 run，并行槽位可能同时认领第一个 run；
「测试开始」通知只能发一次，靠这条列的条件更新认领（同 completed_notified_at）。
"""

import sqlalchemy as sa
from alembic import op

revision = "qa0004startnotify"
down_revision = "qa0003projectenv"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "qa_run_batches", sa.Column("started_notified_at", sa.DateTime(timezone=True))
    )


def downgrade() -> None:
    op.drop_column("qa_run_batches", "started_notified_at")