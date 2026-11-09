"""项目环境与变量。

环境挂在项目上：一个项目一套环境（一份被测地址 + 一组变量），项目下所有系统共用。
任务只存环境名，执行时按系统所属项目解析。
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..crypto import encrypt_secret
from ..deps import (
    CurrentPerson,
    DbSession,
    project_system_ids,
    require_project_access,
    require_system_access,
)
from ..environments import (
    DEFAULT_ENVIRONMENT,
    ensure_default_environment,
    validate_variable_key,
)
from ..models import (
    DevGroup,
    Project,
    ProjectEnvironment,
    ProjectEnvironmentVariable,
    System,
    TestTask,
)
from ..schemas import (
    EnvironmentIn,
    EnvironmentOut,
    EnvironmentUpdate,
    EnvironmentVariableOut,
    EnvironmentVariablesIn,
)

router = APIRouter(tags=["environments"])


async def _require_env(
    db, environment_id: uuid.UUID, user, minimum: str = "member"
) -> ProjectEnvironment:
    env = await db.get(ProjectEnvironment, environment_id)
    if env is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "环境不存在")
    await require_project_access(db, env.project_id, user, minimum)
    return env


async def _variables(db, environment_id: uuid.UUID) -> list[EnvironmentVariableOut]:
    rows = list(
        (
            await db.execute(
                select(ProjectEnvironmentVariable)
                .where(ProjectEnvironmentVariable.environment_id == environment_id)
                .order_by(ProjectEnvironmentVariable.key)
            )
        ).scalars()
    )
    return [
        EnvironmentVariableOut(
            id=row.id,
            key=row.key,
            value=None if row.is_secret else row.value,
            is_secret=row.is_secret,
            value_set=bool(row.value),
            description=row.description,
        )
        for row in rows
    ]


async def _env_out(db, env: ProjectEnvironment) -> EnvironmentOut:
    return EnvironmentOut(
        id=env.id,
        project_id=env.project_id,
        name=env.name,
        base_url=env.base_url,
        is_default=env.is_default,
        variables=await _variables(db, env.id),
    )


async def _list_project_envs(db, project_id: int) -> list[EnvironmentOut]:
    await ensure_default_environment(db, project_id)
    await db.commit()
    rows = list(
        (
            await db.execute(
                select(ProjectEnvironment)
                .where(ProjectEnvironment.project_id == project_id)
                .order_by(
                    ProjectEnvironment.is_default.desc(), ProjectEnvironment.name
                )
            )
        ).scalars()
    )
    return [await _env_out(db, row) for row in rows]


async def _environment_in_use(db, env: ProjectEnvironment) -> bool:
    """项目下还有任务在用这个环境名。"""
    return (
        await db.execute(
            select(TestTask.id).where(
                TestTask.system_id.in_(await project_system_ids(db, env.project_id)),
                TestTask.environment_name == env.name,
            )
        )
    ).first() is not None


@router.get(
    "/api/projects/{project_id}/environments", response_model=list[EnvironmentOut]
)
async def list_project_environments(
    project_id: int, user: CurrentPerson, db: DbSession
) -> list[EnvironmentOut]:
    await require_project_access(db, project_id, user)
    return await _list_project_envs(db, project_id)


@router.get(
    "/api/systems/{system_id}/environments", response_model=list[EnvironmentOut]
)
async def list_system_environments(
    system_id: int, user: CurrentPerson, db: DbSession
) -> list[EnvironmentOut]:
    """系统视角的环境（= 它所属项目的环境），任务创建/编辑时用来选环境名。"""
    system = await require_system_access(db, system_id, user)
    project_id = (
        await db.execute(
            select(DevGroup.project_id)
            .join(System, System.group_id == DevGroup.id)
            .where(System.id == system.id)
        )
    ).scalar_one_or_none()
    if project_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "系统还没有归属项目")
    return await _list_project_envs(db, project_id)


@router.post(
    "/api/projects/{project_id}/environments",
    response_model=EnvironmentOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_environment(
    project_id: int,
    body: EnvironmentIn,
    user: CurrentPerson,
    db: DbSession,
) -> EnvironmentOut:
    await require_project_access(db, project_id, user, "member")
    name = body.name.strip()
    if name == DEFAULT_ENVIRONMENT:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "「默认」环境已存在")
    exists = (
        await db.execute(
            select(ProjectEnvironment.id).where(
                ProjectEnvironment.project_id == project_id,
                ProjectEnvironment.name == name,
            )
        )
    ).first()
    if exists is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "同名环境已存在")
    env = ProjectEnvironment(
        project_id=project_id,
        name=name,
        base_url=(body.base_url or "").strip() or None,
        is_default=False,
        created_by=user.id,
    )
    db.add(env)
    await db.commit()
    await db.refresh(env)
    return await _env_out(db, env)


@router.patch("/api/environments/{environment_id}", response_model=EnvironmentOut)
async def update_environment(
    environment_id: uuid.UUID,
    body: EnvironmentUpdate,
    user: CurrentPerson,
    db: DbSession,
) -> EnvironmentOut:
    env = await _require_env(db, environment_id, user, "member")
    if body.name is not None and body.name.strip() != env.name:
        if env.is_default:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "默认环境不能改名")
        new_name = body.name.strip()
        if await _environment_in_use(db, env):
            raise HTTPException(
                status.HTTP_409_CONFLICT, "仍有任务在使用这个环境名，不能改名"
            )
        duplicate = (
            await db.execute(
                select(ProjectEnvironment.id).where(
                    ProjectEnvironment.project_id == env.project_id,
                    ProjectEnvironment.name == new_name,
                    ProjectEnvironment.id != env.id,
                )
            )
        ).first()
        if duplicate is not None:
            raise HTTPException(status.HTTP_409_CONFLICT, "同名环境已存在")
        env.name = new_name

    if body.base_url is not None:
        env.base_url = body.base_url.strip() or None
    await db.commit()
    await db.refresh(env)
    return await _env_out(db, env)


@router.delete(
    "/api/environments/{environment_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def delete_environment(
    environment_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> None:
    env = await _require_env(db, environment_id, user, "member")
    if env.is_default:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "默认环境不能删除")
    if await _environment_in_use(db, env):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "仍有任务在使用这个环境，不能删除"
        )
    await db.delete(env)
    await db.commit()


@router.put(
    "/api/environments/{environment_id}/variables", response_model=EnvironmentOut
)
async def set_environment_variables(
    environment_id: uuid.UUID,
    body: EnvironmentVariablesIn,
    user: CurrentPerson,
    db: DbSession,
) -> EnvironmentOut:
    env = await _require_env(db, environment_id, user, "member")
    existing = {
        row.key: row
        for row in (
            await db.execute(
                select(ProjectEnvironmentVariable).where(
                    ProjectEnvironmentVariable.environment_id == env.id
                )
            )
        ).scalars()
    }
    seen: set[str] = set()
    for item in body.variables:
        try:
            key = validate_variable_key(item.key, is_secret=item.is_secret)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        if key in seen:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"变量 {key} 重复")
        seen.add(key)
        row = existing.get(key)
        if item.clear:
            if row is not None:
                await db.delete(row)
            continue
        if row is None:
            row = ProjectEnvironmentVariable(
                environment_id=env.id, key=key, is_secret=item.is_secret
            )
            db.add(row)
        # 密钥留空 = 不改；新建时必须给值，否则存一个空密钥没有意义
        if item.is_secret:
            if item.value:
                row.value = encrypt_secret(item.value)
            elif row.value is None:
                row.value = ""
            row.is_secret = True
        else:
            row.value = item.value or ""
            row.is_secret = False
        row.description = item.description
    await db.commit()
    await db.refresh(env)
    return await _env_out(db, env)
