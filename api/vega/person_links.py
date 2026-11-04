"""项目/研发组的人员多选关联（SystemPM / SystemOwner / GroupTechLead 共用）。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import DevGroup, GroupMember, Person, PersonGitlabAccount


async def person_ids(db: AsyncSession, model, **owner_filter: int) -> list[int]:
    stmt = select(model.person_id)
    for k, v in owner_filter.items():
        stmt = stmt.where(getattr(model, k) == v)
    return list((await db.scalars(stmt)).all())


async def set_persons(db: AsyncSession, model, ids: list[int], role: str, **owner_filter: int) -> None:
    """整组替换：先删旧关联再逐条写入，person 不存在直接 400。"""
    stmt = model.__table__.delete()
    for k, v in owner_filter.items():
        stmt = stmt.where(getattr(model, k) == v)
    await db.execute(stmt)
    for pid in ids:
        if not await db.get(Person, pid):
            raise HTTPException(400, f"{role} {pid} 不存在")
        db.add(model(person_id=pid, **owner_filter))


async def person_by_gitlab(
    db: AsyncSession, group: DevGroup, *, email: str | None = None, username: str | None = None
) -> Person | None:
    """GitLab 提交人/MR 作者 → 本组成员：账号表优先，旧 person.gitlab_* 兜底。"""
    base = (group.gitlab_base_url or "").rstrip("/")
    q = (
        select(Person)
        .join(PersonGitlabAccount, PersonGitlabAccount.person_id == Person.id)
        .join(GroupMember, GroupMember.person_id == Person.id)
        .where(GroupMember.group_id == group.id)
    )
    if base:
        q = q.where(PersonGitlabAccount.base_url == base)
    if email:
        row = await db.scalar(q.where(PersonGitlabAccount.email == email))
        if row:
            return row
    if username:
        row = await db.scalar(q.where(PersonGitlabAccount.username == username))
        if row:
            return row
    if email:
        row = await db.scalar(select(Person).where(Person.gitlab_email == email))
        if row:
            return row
    if username:
        return await db.scalar(select(Person).where(Person.gitlab_username == username))
    return None


async def gitlab_username_for(db: AsyncSession, group: DevGroup, person: Person) -> str | None:
    """这个人在本组 GitLab 上的用户名（MR 审核人等多处需要）。"""
    acc = await db.scalar(
        select(PersonGitlabAccount)
        .join(GroupMember, GroupMember.gitlab_account_id == PersonGitlabAccount.id)
        .where(GroupMember.group_id == group.id, GroupMember.person_id == person.id)
    )
    return (acc.username if acc else None) or person.gitlab_username
