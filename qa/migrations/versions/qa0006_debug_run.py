"""调试试跑：run 上直接挂用例 YAML，并用标记与正式执行隔离。

Revision ID: qa0006debugrun
Revises: qa0005deploytrigger

试跑不落 TestCase：内容存在 debug_yaml 上，结果只用于调试，不进入统计。
"""

import sqlalchemy as sa
from alembic import op

revision = "qa0006debugrun"
down_revision = "qa0005deploytrigger"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "qa_runs",
        sa.Column("is_debug", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("qa_runs", sa.Column("debug_yaml", sa.Text(), nullable=True))
    op.add_column("qa_runs", sa.Column("debug_step_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("qa_runs", "debug_step_limit")
    op.drop_column("qa_runs", "debug_yaml")
    op.drop_column("qa_runs", "is_debug")