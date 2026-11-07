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
from .models import System, SystemEnvironment, SystemEnvironmentVariable, TestTask

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
    db: AsyncSession, system_id: int, *, created_by: int | None = None
) -> SystemEnvironment:
    row = (
        await db.execute(
            select(SystemEnvironment).where(
                SystemEnvironment.system_id == system_id,
                SystemEnvironment.is_default.is_(True),
            )
        )
    ).scalar_one_or_none()
    if row is not None:
        return row
    if await db.get(System, system_id) is None:
        raise ValueError("系统不存在")
    row = SystemEnvironment(
        system_id=system_id,
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
                select(SystemEnvironment).where(
                    SystemEnvironment.system_id == system_id,
                    SystemEnvironment.is_default.is_(True),
                )
            )
        ).scalar_one()
    return row


async def resolve_run_environments(
    db: AsyncSession,
    task: TestTask,
    system_ids: set[int],
    *,
    environment_name: str | None = None,
) -> dict[int, SystemEnvironment]:
    """按任务的环境名解析每个系统的环境；缺一个就整体拒绝。"""
    if not system_ids:
        return {}
    env_name = (environment_name or task.environment_name or "").strip() or DEFAULT_ENVIRONMENT
    rows = list(
        (
            await db.execute(
                select(SystemEnvironment).where(
                    SystemEnvironment.system_id.in_(system_ids),
                    SystemEnvironment.name == env_name,
                )
            )
        ).scalars()
    )
    found = {row.system_id: row for row in rows}

    if env_name == DEFAULT_ENVIRONMENT:
        for system_id in system_ids - set(found):
            found[system_id] = await ensure_default_environment(db, system_id)

    missing = sorted(system_ids - set(found), key=str)
    if missing:
        names = list(
            (
                await db.execute(select(System.name).where(System.id.in_(missing)))
            ).scalars()
        )
        raise EnvironmentResolutionError(
            f"环境「{env_name}」在以下系统下不存在：{'、'.join(names) or '未知系统'}"
        )
    return found


async def load_environment_variables(
    db: AsyncSession, environment_id: uuid.UUID
) -> tuple[dict[str, str], dict[str, str]]:
    """返回 (普通变量, 密钥变量)。密钥只在这里解密，调用方不得记录其值。"""
    rows = list(
        (
            await db.execute(
                select(SystemEnvironmentVariable).where(
                    SystemEnvironmentVariable.environment_id == environment_id
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
