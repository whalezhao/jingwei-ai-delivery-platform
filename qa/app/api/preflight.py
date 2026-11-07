"""执行前预检路由。"""

import uuid

from fastapi import APIRouter

from ..deps import CurrentPerson, DbSession
from ..preflight import run_preflight
from ..schemas import PreflightOut, TaskPreflightIn
from .task_common import require_task

router = APIRouter(tags=["preflight"])


@router.post("/api/tasks/{task_id}/preflight", response_model=PreflightOut)
async def preflight(
    task_id: uuid.UUID, user: CurrentPerson, db: DbSession, body: TaskPreflightIn | None = None
) -> dict:
    task = await require_task(db, task_id, user)
    return await run_preflight(
        db, task, environment_name=body.environment_name if body else None
    )
