"""调试试跑：缓存开关。

Revision ID: qa0007debugcache
Revises: qa0006debugrun

试跑默认命中跨 run 共享的 AI 缓存；关掉时本次执行不读也不写缓存。
"""

import sqlalchemy as sa
from alembic import op

revision = "qa0007debugcache"
down_revision = "qa0006debugrun"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "qa_runs",
        sa.Column(
            "debug_cache",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )


def downgrade() -> None:
    op.drop_column("qa_runs", "debug_cache")