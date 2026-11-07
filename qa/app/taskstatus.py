"""任务状态的聚合规则（唯一一份）。

`test_tasks.status` 是**派生值**：由它下面的 runs 聚合而来，不单独维护。
worker 收尾、API 取消、worker 重启恢复都要按同一套规则重算 —— 之前这套逻辑只在
worker 里，API 那条路（取消排队中的 run）没人刷新，任务就会停在「排队中」。
"""

import uuid
from collections.abc import Iterable

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Run, TestTask

ACTIVE_RUN_STATUSES = {"queued", "preparing", "running", "parsing"}


def aggregate(statuses: Iterable[str]) -> str:
    values = list(statuses)
    if not values:
        return "draft"
    if any(s in ACTIVE_RUN_STATUSES for s in values):
        return "running"
    if any(s == "succeeded" for s in values):
        return "completed"
    if all(s == "cancelled" for s in values):
        return "cancelled"
    return "failed"


async def refresh_task_status(db: AsyncSession, task_id: uuid.UUID | None) -> None:
    if task_id is None:
        return
    statuses = list(
        (await db.execute(select(Run.status).where(Run.task_id == task_id))).scalars()
    )
    if not statuses:
        return
    await db.execute(
        update(TestTask).where(TestTask.id == task_id).values(status=aggregate(statuses))
    )
