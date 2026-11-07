"""qa_* 表的基线。

测试域的表都建在 Vega 的 PostgreSQL 库里，只碰 `qa_` 前缀；Vega 自己的
`person` / `dev_group` / `dev_system` / `requirement` 由 Vega 建，这里只引用。

旧 aster 库不迁移（保留为归档），所以这是一条全新的基线，而不是从旧链末尾接续。

Revision ID: qa0001baseline
Revises:
"""

from alembic import op

from app.models import Base

revision = "qa0001baseline"
down_revision = None
branch_labels = None
depends_on = None


def _qa_tables():
    return [
        table
        for name, table in Base.metadata.tables.items()
        if name.startswith("qa_")
    ]


def upgrade() -> None:
    # Vega 的 dev_system / person / requirement 必须先存在；create_all 只建
    # qa_* 表，外键指向已存在的 Vega 表。
    Base.metadata.create_all(bind=op.get_bind(), tables=_qa_tables())


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), tables=_qa_tables())