"""环境从系统级搬到项目级。

Revision ID: qa0003projectenv
Revises: qa0002batchpin

一个项目下多个系统原本各配一套同名环境；迁移后同一项目的同名环境合并成一条，
变量按 keeper 优先合并，历史 run 重新指向 keeper。
"""

import sqlalchemy as sa
from alembic import op

revision = "qa0003projectenv"
down_revision = "qa0002batchpin"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.rename_table("qa_system_environments", "qa_project_environments")
    op.rename_table("qa_system_environment_variables", "qa_project_environment_variables")
    op.add_column(
        "qa_project_environments", sa.Column("project_id", sa.Integer(), nullable=True)
    )

    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            UPDATE qa_project_environments e SET project_id = g.project_id
            FROM dev_system s JOIN dev_group g ON g.id = s.group_id
            WHERE s.id = e.system_id
            """
        )
    )
    # 没有归属项目的系统留下的环境无处可挂，直接丢弃（run 外键会置空）
    conn.execute(
        sa.text("DELETE FROM qa_project_environments WHERE project_id IS NULL")
    )

    rows = conn.execute(
        sa.text(
            """
            SELECT id, project_id, name, base_url
            FROM qa_project_environments
            ORDER BY is_default DESC, created_at, id
            """
        )
    ).all()
    keepers: dict[tuple[int, str], object] = {}
    for env_id, project_id, name, base_url in rows:
        keeper = keepers.setdefault((project_id, name), env_id)
        if keeper == env_id:
            continue
        conn.execute(
            sa.text(
                "UPDATE qa_runs SET environment_id = :keeper WHERE environment_id = :dup"
            ),
            {"keeper": keeper, "dup": env_id},
        )
        conn.execute(
            sa.text(
                """
                INSERT INTO qa_project_environment_variables
                    (id, environment_id, key, value, is_secret, description,
                     created_at, updated_at)
                SELECT v.id, :keeper, v.key, v.value, v.is_secret, v.description,
                       v.created_at, v.updated_at
                FROM qa_project_environment_variables v
                WHERE v.environment_id = :dup AND NOT EXISTS (
                    SELECT 1 FROM qa_project_environment_variables k
                    WHERE k.environment_id = :keeper AND k.key = v.key
                )
                """
            ),
            {"keeper": keeper, "dup": env_id},
        )
        if base_url:
            conn.execute(
                sa.text(
                    """
                    UPDATE qa_project_environments
                    SET base_url = :base_url
                    WHERE id = :keeper AND base_url IS NULL
                    """
                ),
                {"keeper": keeper, "base_url": base_url},
            )
        conn.execute(
            sa.text(
                "DELETE FROM qa_project_environment_variables WHERE environment_id = :dup"
            ),
            {"dup": env_id},
        )
        conn.execute(
            sa.text("DELETE FROM qa_project_environments WHERE id = :dup"),
            {"dup": env_id},
        )

    op.drop_index("uq_qa_system_environments_default", table_name="qa_project_environments")
    op.drop_index("ix_qa_system_environments_system", table_name="qa_project_environments")
    op.drop_constraint(
        "uq_qa_system_environments_name", "qa_project_environments", type_="unique"
    )
    op.drop_column("qa_project_environments", "system_id")
    op.alter_column("qa_project_environments", "project_id", nullable=False)
    op.create_foreign_key(
        "qa_project_environments_project_id_fkey",
        "qa_project_environments",
        "project",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_qa_project_environments_project", "qa_project_environments", ["project_id"]
    )
    op.create_unique_constraint(
        "uq_qa_project_environments_name",
        "qa_project_environments",
        ["project_id", "name"],
    )
    op.create_index(
        "uq_qa_project_environments_default",
        "qa_project_environments",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    conn.execute(
        sa.text(
            "ALTER INDEX ix_qa_system_environment_variables_env "
            "RENAME TO ix_qa_project_environment_variables_env"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE qa_project_environment_variables RENAME CONSTRAINT "
            "uq_qa_system_environment_variables_key "
            "TO uq_qa_project_environment_variables_key"
        )
    )


def downgrade() -> None:
    import uuid

    conn = op.get_bind()
    op.add_column(
        "qa_project_environments", sa.Column("system_id", sa.Integer(), nullable=True)
    )
    envs = conn.execute(
        sa.text(
            """
            SELECT id, project_id, name, base_url, is_default, created_by,
                   created_at, updated_at
            FROM qa_project_environments
            """
        )
    ).all()
    for env_id, project_id, name, base_url, is_default, created_by, created_at, updated_at in envs:
        systems = list(
            conn.execute(
                sa.text(
                    """
                    SELECT s.id FROM dev_system s
                    JOIN dev_group g ON g.id = s.group_id
                    WHERE g.project_id = :project_id
                    ORDER BY s.id
                    """
                ),
                {"project_id": project_id},
            ).scalars()
        )
        if not systems:
            conn.execute(
                sa.text("DELETE FROM qa_project_environments WHERE id = :id"),
                {"id": env_id},
            )
            continue
        conn.execute(
            sa.text("UPDATE qa_project_environments SET system_id = :sid WHERE id = :id"),
            {"sid": systems[0], "id": env_id},
        )
        variables = conn.execute(
            sa.text(
                """
                SELECT key, value, is_secret, description
                FROM qa_project_environment_variables WHERE environment_id = :id
                """
            ),
            {"id": env_id},
        ).all()
        for system_id in systems[1:]:
            copy_id = uuid.uuid4()
            conn.execute(
                sa.text(
                    """
                    INSERT INTO qa_project_environments
                        (id, project_id, system_id, name, base_url, is_default,
                         created_by, created_at, updated_at)
                    VALUES (:id, :project_id, :system_id, :name, :base_url,
                            :is_default, :created_by, :created_at, :updated_at)
                    """
                ),
                {
                    "id": copy_id,
                    "project_id": project_id,
                    "system_id": system_id,
                    "name": name,
                    "base_url": base_url,
                    "is_default": is_default,
                    "created_by": created_by,
                    "created_at": created_at,
                    "updated_at": updated_at,
                },
            )
            for key, value, is_secret, description in variables:
                conn.execute(
                    sa.text(
                        """
                        INSERT INTO qa_project_environment_variables
                            (id, environment_id, key, value, is_secret, description)
                        VALUES (:id, :environment_id, :key, :value, :is_secret, :description)
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "environment_id": copy_id,
                        "key": key,
                        "value": value,
                        "is_secret": is_secret,
                        "description": description,
                    },
                )

    op.drop_index("uq_qa_project_environments_default", table_name="qa_project_environments")
    op.drop_index("ix_qa_project_environments_project", table_name="qa_project_environments")
    op.drop_constraint(
        "uq_qa_project_environments_name", "qa_project_environments", type_="unique"
    )
    op.alter_column("qa_project_environments", "system_id", nullable=False)
    op.create_index(
        "ix_qa_system_environments_system", "qa_project_environments", ["system_id"]
    )
    op.create_unique_constraint(
        "uq_qa_system_environments_name",
        "qa_project_environments",
        ["system_id", "name"],
    )
    op.create_index(
        "uq_qa_system_environments_default",
        "qa_project_environments",
        ["system_id"],
        unique=True,
        postgresql_where=sa.text("is_default"),
    )
    op.drop_constraint(
        "qa_project_environments_project_id_fkey",
        "qa_project_environments",
        type_="foreignkey",
    )
    op.drop_column("qa_project_environments", "project_id")
    op.rename_table("qa_project_environments", "qa_system_environments")
    op.rename_table("qa_project_environment_variables", "qa_system_environment_variables")
    conn.execute(
        sa.text(
            "ALTER INDEX ix_qa_project_environment_variables_env "
            "RENAME TO ix_qa_system_environment_variables_env"
        )
    )
    conn.execute(
        sa.text(
            "ALTER TABLE qa_system_environment_variables RENAME CONSTRAINT "
            "uq_qa_project_environment_variables_key "
            "TO uq_qa_system_environment_variables_key"
        )
    )
