"""项目环境与变量解析。

约定：
- 任务只存环境名；执行时在每个项目下找同名环境。
- 非密钥变量进 midscene config.variables，YAML 用 `${KEY}`。
- 密钥进子进程环境，YAML 用 `${{KEY}}`；绝不写进磁盘配置。
"""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from .crypto import decrypt_secret
from .models import (
    DevGroup,
    Project,
    ProjectEnvironment,
    ProjectEnvironmentVariable,
    System,
    TestTask,
)

DEFAULT_ENVIRONMENT = "默认"
_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RESERVED_KEYS = {"BASE_URL"}
# 这些 key 一旦进子进程环境，等于把代码执行/加载路径交给配置者
FORBIDDEN_SECRET_KEYS = {
    "PATH",
    "HOME",
    "SHELL",
    "USER",
    "LOGNAME",
    "PWD",
    "TMPDIR",
    "NODE_OPTIONS",
    "NODE_PATH",
    "BASH_ENV",
    "ENV",
    "PYTHONPATH",
    "PYTHONHOME",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "DYLD_INSERT_LIBRARIES",
    "DYLD_LIBRARY_PATH",
    "MIDSCENE_RUN_DIR",
    "SECRET_KEY",
    "DATABASE_URL",
    "POSTGRES_PASSWORD",
}
FORBIDDEN_SECRET_PREFIXES = ("MIDSCENE_", "OPENAI_", "LD_", "DYLD_", "PYTHON", "npm_", "NODE_")


class EnvironmentResolutionError(ValueError):
    """任务环境在部分系统下不存在。消息直接给用户看。"""


def validate_variable_key(key: str, *, is_secret: bool) -> str:
    key = (key or "").strip()
    if not _KEY_RE.match(key) or len(key) > 64:
        raise ValueError("变量名只能包含字母、数字、下划线，且不能以数字开头")
    if key in RESERVED_KEYS:
        raise ValueError(f"{key} 由平台注入，不能再配置")
    if is_secret and (
        key in FORBIDDEN_SECRET_KEYS or key.startswith(FORBIDDEN_SECRET_PREFIXES)
    ):
        raise ValueError(f"{key} 是执行器保留变量，不能作为密钥注入")
    return key


async def ensure_default_environment(
    db: AsyncSession, project_id: int, *, created_by: int | None = None
) -> ProjectEnvironment:
    row = (
        await db.execute(
            select(ProjectEnvironment).where(
                ProjectEnvironment.project_id == project_id,
                ProjectEnvironment.is_default.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    if await db.get(Project, project_id) is None:
        raise ValueError("项目不存在")
    row = ProjectEnvironment(
        project_id=project_id,
        name=DEFAULT_ENVIRONMENT,
        is_default=True,
        created_by=created_by,
    )
    db.add(row)
    try:
        async with db.begin_nested():
            await db.flush()
    except IntegrityError:
        # 并发创建：另一个请求已经建好了，回滚这个 savepoint 后重查
        row = (
            await db.execute(
                select(ProjectEnvironment).where(
                    ProjectEnvironment.project_id == project_id,
                    ProjectEnvironment.is_default.is_(True),
                )
            )
        ).scalar_one()
    return row


async def _project_by_system(db: AsyncSession, system_ids: set[int]) -> dict[int, int]:
    """系统 → 项目。没有归属项目的系统不在结果里。"""
    rows = (
        await db.execute(
            select(System.id, DevGroup.project_id)
            .join(DevGroup, DevGroup.id == System.group_id)
            .where(System.id.in_(system_ids))
        )
    ).all()
    return {system_id: project_id for system_id, project_id in rows if project_id}


async def resolve_run_environments(
    db: AsyncSession,
    task: TestTask | None,
    system_ids: set[int],
    *,
    environment_name: str | None = None,
) -> dict[int, ProjectEnvironment]:
    """按任务的环境名，解析每个系统所属项目的同名环境；缺一个就整体拒绝。

    同一项目下的多个系统共用一份环境（地址 + 变量），所以返回值仍按系统索引，
    但同一项目里的系统拿到的是同一条记录。
    """
    if not system_ids:
        return {}
    env_name = (
        environment_name or (task.environment_name if task else None) or ""
    ).strip() or DEFAULT_ENVIRONMENT
    project_by_system = await _project_by_system(db, system_ids)
    no_project = sorted(system_ids - set(project_by_system), key=str)
    if no_project:
        names = list(
            (
                await db.execute(select(System.name).where(System.id.in_(no_project)))
            ).scalars()
        )
        raise EnvironmentResolutionError(
            f"以下系统还没有归属项目，无法解析环境：{'、'.join(names) or '未知系统'}"
        )

    project_ids = set(project_by_system.values())
    rows = list(
        (
            await db.execute(
                select(ProjectEnvironment).where(
                    ProjectEnvironment.project_id.in_(project_ids),
                    ProjectEnvironment.name == env_name,
                )
            )
        ).scalars()
    )
    found = {row.project_id: row for row in rows}

    if env_name == DEFAULT_ENVIRONMENT:
        for project_id in project_ids - set(found):
            found[project_id] = await ensure_default_environment(db, project_id)

    missing = sorted(project_ids - set(found))
    if missing:
        names = list(
            (
                await db.execute(select(Project.name).where(Project.id.in_(missing)))
            ).scalars()
        )
        raise EnvironmentResolutionError(
            f"环境「{env_name}」在以下项目下不存在：{'、'.join(names) or '未知项目'}"
        )
    return {
        system_id: found[project_id]
        for system_id, project_id in project_by_system.items()
    }


async def environment_for_system(
    db: AsyncSession, system_id: int, *, environment_name: str | None = None
) -> ProjectEnvironment | None:
    """单个系统用的环境（按它所属项目解析），解析不到返回 None。"""
    try:
        envs = await resolve_run_environments(
            db, None, {system_id}, environment_name=environment_name
        )
    except EnvironmentResolutionError:
        return None
    return envs.get(system_id)


async def load_environment_variables(
    db: AsyncSession, environment_id: uuid.UUID
) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (普通变量, 密钥变量)。密钥只在这里解密，调用方不得记录其值。"""
    rows = list(
        (
            await db.execute(
                select(ProjectEnvironmentVariable).where(
                    ProjectEnvironmentVariable.environment_id == environment_id
                )
            )
        ).scalars()
    )
    plain: dict[str, str] = {}
    secret: dict[str, str] = {}
    for row in rows:
        value = row.value or ""
        if row.is_secret:
            secret[row.key] = decrypt_secret(value) if value else ""
        else:
            plain[row.key] = value
    return plain, secret
