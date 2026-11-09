"""认证与授权依赖。这是信任边界，校验要严。

只认 Vega 签发的 HMAC token（`person_id.exp.sig`，与本服务共库、共享
`VEGA_SECRET_KEY`）。人员、组织、需求都直接读 Vega 的表，不再有本地用户/团队。
"""

import hashlib
import hmac
import time
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_db
from .models import (
    DevGroup,
    Feature,
    GroupTechLead,
    Person,
    Project,
    System,
    SystemOwner,
    TestCase,
    TestTask,
)

bearer = HTTPBearer(auto_error=False)

DbSession = Annotated[AsyncSession, Depends(get_db)]


def parse_token(token: str) -> int | None:
    """与 Vega `api/vega/auth.py` 保持一致；两边密钥必须相同。"""
    key = get_settings().vega_secret_key
    if not key:
        return None
    try:
        pid_s, exp_s, sig = token.split(".")
        body = f"{pid_s}.{exp_s}"
        expect = hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(sig, expect) or int(exp_s) < time.time():
            return None
        return int(pid_s)
    except (ValueError, TypeError):
        return None


async def current_person(
    db: DbSession,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Person:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "缺少凭证")
    pid = parse_token(creds.credentials)
    if pid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "凭证无效或已过期")
    person = await db.get(Person, pid)
    if person is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "用户不存在")
    return person


CurrentPerson = Annotated[Person, Depends(current_person)]


def is_qa(person: Person) -> bool:
    return "qa" in (person.roles or [])


async def require_admin(person: CurrentPerson) -> Person:
    if not person.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")
    return person


async def writable_system_ids(db: AsyncSession, person: Person) -> set[int] | None:
    """有写权限的系统集合；None 表示管理员/QA，可写全部系统。"""
    if person.is_admin or is_qa(person):
        return None
    ids = set(
        (
            await db.execute(
                select(SystemOwner.system_id).where(SystemOwner.person_id == person.id)
            )
        ).scalars()
    )
    ids.update(
        (
            await db.execute(
                select(System.id)
                .join(GroupTechLead, GroupTechLead.group_id == System.group_id)
                .where(GroupTechLead.person_id == person.id)
            )
        ).scalars()
    )
    return ids


async def _can_write_system(db: AsyncSession, system: System, person: Person) -> bool:
    ids = await writable_system_ids(db, person)
    return ids is None or system.id in ids


async def require_system_access(
    db: AsyncSession, system_id: int, person: Person, minimum: str = "view"
) -> System:
    system = await db.get(System, system_id)
    if system is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "系统不存在")
    if minimum == "view":
        return system
    if await _can_write_system(db, system, person):
        return system
    raise HTTPException(status.HTTP_403_FORBIDDEN, "需要系统负责人、研发组负责人或 QA 权限")


async def project_system_ids(db: AsyncSession, project_id: int) -> list[int]:
    return list(
        (
            await db.execute(
                select(System.id)
                .join(DevGroup, DevGroup.id == System.group_id)
                .where(DevGroup.project_id == project_id)
            )
        ).scalars()
    )


async def require_project_access(
    db: AsyncSession, project_id: int, person: Person, minimum: str = "view"
) -> Project:
    """项目级写权限 = 能写项目下任一系统（负责人/研发组负责人/QA/管理员）。"""
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "项目不存在")
    if minimum == "view":
        return project
    writable = await writable_system_ids(db, person)
    if writable is None:
        return project
    if set(await project_system_ids(db, project_id)) & writable:
        return project
    raise HTTPException(status.HTTP_403_FORBIDDEN, "需要系统负责人、研发组负责人或 QA 权限")


async def require_feature_access(
    db: AsyncSession, feature_id, person: Person, minimum: str = "view"
) -> Feature:
    feature = await db.get(Feature, feature_id)
    if feature is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "功能不存在")
    await require_system_access(db, feature.system_id, person, minimum)
    return feature


async def require_case_access(
    db: AsyncSession, case_id, person: Person, minimum: str = "view"
) -> TestCase:
    case = await db.get(TestCase, case_id)
    if case is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用例不存在")
    feature = await db.get(Feature, case.feature_id)
    if feature is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用例所属功能不存在")
    await require_system_access(db, feature.system_id, person, minimum)
    # effective_stage 派生自 feature，序列化前必须已加载，否则触发异步懒加载报错
    case.feature = feature
    return case


async def require_task_access(
    db: AsyncSession, task_id, person: Person, minimum: str = "view"
) -> TestTask:
    task = await db.get(TestTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    await require_system_access(db, task.system_id, person, minimum)
    return task