"""任务执行相关路由：手动执行。"""

import uuid

from fastapi import APIRouter, HTTPException, status

from ..deps import CurrentPerson, DbSession
from ..schemas import TaskRunIn
from .task_common import require_task, spawn_runs, trim_task_batches

router = APIRouter(tags=["tasks"])


@router.post("/api/tasks/{task_id}/run")
async def run_task(
    task_id: uuid.UUID,
    user: CurrentPerson,
    db: DbSession,
    body: TaskRunIn | None = None,
) -> dict:
    task = await require_task(db, task_id, user, "member")
    if task.status in {"queued", "running"}:
        raise HTTPException(status.HTTP_409_CONFLICT, "该任务已有执行在进行中")
    # 一个环境一条执行记录（批次）：批次是用户视角的一次执行
    names: list[str | None] = []
    for name in (body.environment_names if body else []):
        cleaned = name.strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    batches = []
    for env_name in names or [None]:
        batch, runs = await spawn_runs(
            db, task, user, trigger="manual", environment_name=env_name
        )
        batches.append(
            {
                "batch_id": str(batch.id),
                "environment_name": batch.environment_name,
                "run_ids": [str(r.id) for r in runs],
            }
        )
    await db.commit()
    await trim_task_batches(db, task.id)
    return {
        "task_id": str(task.id),
        "batch_id": batches[0]["batch_id"],
        "run_ids": batches[0]["run_ids"],
        "batches": batches,
    }