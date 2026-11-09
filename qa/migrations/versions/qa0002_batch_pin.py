"""执行记录冻结标记。

Revision ID: qa0002batchpin
Revises: qa0001baseline
"""

import sqlalchemy as sa
from alembic import op

revision = "qa0002batchpin"
down_revision = "qa0001baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "qa_run_batches",
        sa.Column("pinned", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("qa_run_batches", "pinned", server_default=None)


def downgrade() -> None:
    op.drop_column("qa_run_batches", "pinned")
