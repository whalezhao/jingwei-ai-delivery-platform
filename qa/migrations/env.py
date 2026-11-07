"""Alembic 环境：直接复用 app 的模型元数据和数据库地址。

连接串优先取环境变量 DATABASE_URL（便于在临时库上跑 autogenerate），
否则用后端配置里的值。异步驱动走 async 模板。
"""

import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

from app.config import get_settings
from app.models import Base

config = context.config

if config.config_file_name is not None:
    # disable_existing_loggers 默认是 True —— 会把 app 已经建好的 logger 全部禁用，
    # 启动迁移之后 API 的请求日志就静默消失了
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

config.set_main_option(
    "sqlalchemy.url",
    os.environ.get("DATABASE_URL") or get_settings().database_url,
)

# 与 Vega 共库：只认领 qa_* 表，版本表也单独命名，绝不碰 Vega 的表和它自己的迁移记录
VERSION_TABLE = "qa_alembic_version"


def include_object(object, name, type_, reflected, compare_to):
    if type_ == "table":
        return name.startswith("qa_")
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        version_table=VERSION_TABLE,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    # compare_type：改字段类型时也要能 autogenerate 出来
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        version_table=VERSION_TABLE,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
