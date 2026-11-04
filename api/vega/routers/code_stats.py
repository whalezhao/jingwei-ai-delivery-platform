"""代码统计页数据（只读 DailyStat / FileStat，由采集任务落库）与手动补采。"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person, require_admin
from ..db import get_db
from ..jobs import collect_day
from ..models import DevGroup, Person
from ..reports import aggregate, report_range

router = APIRouter(prefix="/api/code-stats", tags=["code-stats"])


@router.get("")
async def code_stats(
    group_id: int | None = None,
    system_id: int | None = None,
    person_id: int | None = None,
    start: date | None = None,
    end: date | None = None,
    db: AsyncSession = Depends(get_db),
    _me: Person = Depends(current_person),
) -> dict:
    """默认近 7 天（截止昨天）；带 start/end 按区间查，带 person_id 切人员视角。"""
    if not end:
        _, end = report_range("daily")
    if not start:
        start = end - timedelta(days=6)
    data = await aggregate(
        db, group_id=group_id, system_id=system_id, person_id=person_id, start=start, end=end
    )
    return {"start": str(start), "end": str(end), **data}


@router.post("/collect")
async def collect(
    group_id: int,
    start: date,
    end: date,
    system_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _admin: Person = Depends(require_admin),
) -> dict:
    """把区间内每天的数据重新采集入库，不推送报表（看当天数据、补历史都走这里）。"""
    group = await db.get(DevGroup, group_id)
    if not group:
        raise HTTPException(404, "研发组不存在")
    if end < start or (end - start).days > 30:
        raise HTTPException(400, "区间需在 31 天以内")
    rows = files = days = 0
    for i in range((end - start).days + 1):
        res = await collect_day(db, group, start + timedelta(days=i), system_id)
        if "error" in res:
            raise HTTPException(400, res["error"])
        rows += res["rows"]
        files += res["files"]
        days += 1
    return {"days": days, "rows": rows, "files": files}
