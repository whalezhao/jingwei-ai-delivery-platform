"""全局运行状态：底部状态栏数据源（执行器在线数、任务队列、agent-memory 开关）。

只做聚合计数，查询量固定；前端 15s 轮询一次，不随任务量增长。
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person
from ..db import get_db
from ..models import Person, RunnerNode, RunnerTask, utcnow
from .am import am_enabled
from .nodes import HEARTBEAT_OFFLINE_S

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("")
async def status(
    db: AsyncSession = Depends(get_db), _me: Person = Depends(current_person)
) -> dict:
    cutoff = utcnow() - timedelta(seconds=HEARTBEAT_OFFLINE_S)
    counts = dict(
        (
            await db.execute(
                select(RunnerTask.status, func.count()).group_by(RunnerTask.status)
            )
        ).all()
    )
    return {
        "runners": {
            "online": await db.scalar(
                select(func.count())
                .select_from(RunnerNode)
                .where(RunnerNode.last_heartbeat > cutoff)
            )
            or 0,
            "total": await db.scalar(select(func.count()).select_from(RunnerNode)) or 0,
        },
        "queue": {
            "running": counts.get("claimed", 0) + counts.get("running", 0),
            "queued": counts.get("queued", 0),
        },
        "am": await am_enabled(db),
    }
