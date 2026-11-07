"""AI 相关的只读/按需接口：执行评估查询。

原则：AI 只给建议，不阻断任何操作；系统没配模型时明确报「没配」，不静默降级。
"""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..ai_review import latest_check
from ..deps import CurrentPerson, DbSession, require_system_access
from ..models import AiCheck, RunBatch
from ..schemas import AiCheckOut

router = APIRouter(tags=["ai"])


def _out(check: AiCheck | None) -> AiCheckOut:
    if check is None:
        return AiCheckOut(status="none")
    detail = check.detail or {}
    return AiCheckOut(
        status=check.status,
        verdict=check.verdict,
        summary=check.summary,
        reasons=list(detail.get("reasons") or []),
        advice=list(detail.get("advice") or []),
        diagnoses=list(detail.get("diagnoses") or []),
        model_name=check.model_name,
        error=check.error,
        created_at=check.created_at,
    )


@router.get("/api/batches/{batch_id}/ai-check", response_model=AiCheckOut)
async def get_batch_check(
    batch_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> AiCheckOut:
    """批次最新一次的 AI 评估。pending 时前端轮询，done 后展示结论。"""
    batch = await db.get(RunBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "批次不存在")
    await require_system_access(db, batch.system_id, user)
    return _out(await latest_check(db, "batch", batch_id))


@router.post("/api/batches/{batch_id}/ai-check", response_model=AiCheckOut)
async def rerun_batch_check(
    batch_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> AiCheckOut:
    """重新评估一次（异步）。已经在排队中的不重复入队。"""
    batch = await db.get(RunBatch, batch_id)
    if batch is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "批次不存在")
    await require_system_access(db, batch.system_id, user, "member")

    pending = (
        await db.execute(
            select(AiCheck.id).where(
                AiCheck.entity_type == "batch",
                AiCheck.entity_id == batch_id,
                AiCheck.status == "pending",
            )
        )
    ).first()
    if pending is None:
        db.add(AiCheck(entity_type="batch", entity_id=batch_id, status="pending"))
        await db.commit()
    return _out(await latest_check(db, "batch", batch_id))