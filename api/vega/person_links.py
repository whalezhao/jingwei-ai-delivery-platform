"""项目/研发组的人员多选关联（ProjectPM / ProjectOwner / GroupTechLead 共用）。"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Person


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
