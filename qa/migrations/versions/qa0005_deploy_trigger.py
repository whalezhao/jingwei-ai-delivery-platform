"""部署触发测试：批次来源枚举 + 任务的幂等键。

Revision ID: qa0005deploytrigger
Revises: qa0004startnotify

部署事件会为同一需求重复回调；qa_test_tasks 上的 (deploy_event_id, requirement_id)
部分唯一索引保证「同一次部署、同一需求」只建一个测试任务。
"""

import sqlalchemy as sa
from alembic import op

revision = "qa0005deploytrigger"
down_revision = "qa0004startnotify"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_qa_run_batches_trigger", "qa_run_batches", type_="check")
    op.create_check_constraint(
        "ck_qa_run_batches_trigger",
        "qa_run_batches",
        "trigger IN ('manual','regression','rerun','requirement','deployment')",
    )
    op.add_column("qa_test_tasks", sa.Column("deploy_event_id", sa.Integer(), nullable=True))
    op.create_index(
        "uq_qa_test_tasks_deploy_req",
        "qa_test_tasks",
        ["deploy_event_id", "requirement_id"],
        unique=True,
        postgresql_where=sa.text("deploy_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_qa_test_tasks_deploy_req", table_name="qa_test_tasks")
    op.drop_column("qa_test_tasks", "deploy_event_id")
    op.drop_constraint("ck_qa_run_batches_trigger", "qa_run_batches", type_="check")
    op.create_check_constraint(
        "ck_qa_run_batches_trigger",
        "qa_run_batches",
        "trigger IN ('manual','regression','rerun','requirement')",
    )