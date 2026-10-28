"""首页初始化进度：把分散的配置状态聚成一张 checklist，全绿即隐藏。"""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person
from ..db import get_db
from ..models import DevGroup, Person, Project, RunnerNode

router = APIRouter(prefix="/api/setup", tags=["setup"])


@router.get("")
async def setup_status(db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)) -> dict:
    groups = (await db.scalars(select(DevGroup))).all()
    projects = (await db.scalars(select(Project))).all()
    people = (await db.scalars(select(Person))).all()
    # 判活拉回 Python 比：SQLite 读回 naive、PG 读回 aware（曾踩过回归），统一剥 tz
    now = datetime.datetime.utcnow()
    beats = (await db.scalars(select(RunnerNode.last_heartbeat))).all()
    runner_ok = any(
        b and (now - b.replace(tzinfo=None)).total_seconds() <= 90 for b in beats
    )
    return {
        "gitlab": any(g.gitlab_base_url and g.gitlab_token_enc for g in groups),
        "project": len(projects) > 0,
        "am": any(p.am_team_id for p in projects) or any(g.am_team_id for g in groups),
        "runner": runner_ok,
        "people": any(p.gitlab_username for p in people),
    }
