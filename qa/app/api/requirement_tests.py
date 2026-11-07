"""需求维度的测试入口：发起、进度、历史。

测试在需求所有开发任务合入（Vega `status == "online"`）后开放，管理员可强制提前。
"""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, select

from ..config import get_settings
from ..deps import CurrentPerson, DbSession, is_qa, require_system_access
from ..models import (
    Feature,
    Requirement,
    Run,
    RunBatch,
    TaskCase,
    TestTask,
    WorkerHeartbeat,
)
from ..schemas import RequirementRunIn, RequirementTestSummaryOut
from ..taskstatus import ACTIVE_RUN_STATUSES
from .batches import _run_aggregates, derive_status
from .task_common import attach_features, spawn_runs

router = APIRouter(tags=["requirement-tests"])


async def _requirement_or_404(db, requirement_id: int) -> Requirement:
    requirement = await db.get(Requirement, requirement_id)
    if requirement is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "需求不存在")
    return requirement


async def _worker_online(db) -> bool:
    cutoff = datetime.now(UTC) - timedelta(seconds=get_settings().worker_stale_seconds)
    return (
        await db.execute(
            select(WorkerHeartbeat.id).where(WorkerHeartbeat.heartbeat_at >= cutoff)
        )
    ).first() is not None


@router.post("/api/requirements/{requirement_id}/run", status_code=201)
async def run_requirement_test(
    requirement_id: int,
    user: CurrentPerson,
    db: DbSession,
    body: RequirementRunIn | None = None,
) -> dict:
    requirement = await _requirement_or_404(db, requirement_id)
    await require_system_access(db, requirement.system_id, user)
    if requirement.status != "online" and not user.is_admin:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "需求的所有开发任务尚未全部合入，暂不能发起测试",
        )
    # 发起 = 维护级动作；需求认领人可以发起自己的需求
    if not (user.is_admin or is_qa(user) or requirement.claimant_id == user.id):
        await require_system_access(db, requirement.system_id, user, "member")

    feature_ids = list(
        (
            await db.execute(
                select(Feature.id).where(
                    Feature.requirement_id == requirement_id,
                    Feature.system_id == requirement.system_id,
                )
            )
        ).scalars()
    )
    if not feature_ids:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "该需求还没有关联功能；请先在系统的「功能测试」里建功能并关联需求",
        )

    active = (
        await db.execute(
            select(Run.id)
            .join(TestTask, TestTask.id == Run.task_id)
            .where(
                TestTask.requirement_id == requirement_id,
                Run.status.in_(ACTIVE_RUN_STATUSES),
            )
            .limit(1)
        )
    ).first()
    if active is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "该需求已有测试在进行中")
    if not await _worker_online(db):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "测试 Worker 离线，任务会一直排队；请先启动 Worker"
        )

    environment_name = (
        (body.environment_name or "").strip() or None if body else None
    )
    task = TestTask(
        system_id=requirement.system_id,
        requirement_id=requirement_id,
        name=f"{requirement.code} {requirement.title}"[:128],
        type="requirement_test",
        environment_name=environment_name,
        case_selection_mode="snapshot",
        created_by=user.id,
        status="draft",
    )
    db.add(task)
    await db.flush()
    case_count = await attach_features(db, task, feature_ids)
    if case_count == 0:
        await db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "关联功能下没有启用中的用例"
        )

    batch, runs = await spawn_runs(
        db,
        task,
        user,
        trigger="requirement",
        environment_name=environment_name,
    )
    await db.commit()
    return {
        "task_id": str(task.id),
        "batch_id": str(batch.id),
        "run_ids": [str(run.id) for run in runs],
        "case_count": case_count,
    }


async def _latest_batches(db, requirement_ids: list[int]) -> dict[int, RunBatch]:
    if not requirement_ids:
        return {}
    rows = (
        await db.execute(
            select(RunBatch, TestTask.requirement_id)
            .join(TestTask, TestTask.id == RunBatch.task_id)
            .where(TestTask.requirement_id.in_(requirement_ids))
            .distinct(TestTask.requirement_id)
            .order_by(TestTask.requirement_id, RunBatch.created_at.desc())
        )
    ).all()
    return {requirement_id: batch for batch, requirement_id in rows}


async def _task_case_counts(db, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    if not task_ids:
        return {}
    rows = (
        await db.execute(
            select(TaskCase.task_id, func.count())
            .where(TaskCase.task_id.in_(task_ids))
            .group_by(TaskCase.task_id)
        )
    ).all()
    return {task_id: int(count) for task_id, count in rows}


def _summary(
    requirement_id: int,
    batch: RunBatch | None,
    agg: dict,
    case_count: int,
) -> RequirementTestSummaryOut:
    if batch is None:
        return RequirementTestSummaryOut(requirement_id=requirement_id)
    statuses = agg.get("statuses", [])
    status_value = derive_status(
        statuses,
        real_failed=agg.get("failed", 0) - agg.get("excluded_failed", 0),
        excluded_runs=agg.get("excluded_runs", 0),
    )
    total = max(int(agg.get("total", 0)), case_count)
    passed = int(agg.get("passed", 0))
    failed = int(agg.get("failed", 0))
    remaining = max(0, total - passed - failed)
    running = remaining if any(s in ACTIVE_RUN_STATUSES for s in statuses) else 0
    return RequirementTestSummaryOut(
        requirement_id=requirement_id,
        status=status_value,
        task_id=batch.task_id,
        batch_id=batch.id,
        total=total,
        passed=passed,
        failed=failed,
        running=running,
        not_run=0 if running else remaining,
        pass_rate=(passed / (passed + failed)) if passed + failed else None,
        started_at=agg.get("started_at"),
        ended_at=agg.get("ended_at"),
        updated_at=batch.created_at,
    )


@router.get(
    "/api/requirements/{requirement_id}/test-summary",
    response_model=RequirementTestSummaryOut,
)
async def requirement_test_summary(
    requirement_id: int, user: CurrentPerson, db: DbSession
) -> RequirementTestSummaryOut:
    requirement = await _requirement_or_404(db, requirement_id)
    await require_system_access(db, requirement.system_id, user)
    batch = (await _latest_batches(db, [requirement_id])).get(requirement_id)
    if batch is None:
        return RequirementTestSummaryOut(requirement_id=requirement_id)
    agg = (await _run_aggregates(db, [batch.id])).get(batch.id, {})
    counts = await _task_case_counts(db, [batch.task_id] if batch.task_id else [])
    return _summary(requirement_id, batch, agg, counts.get(batch.task_id, 0))


@router.get("/api/requirement-test-summaries")
async def requirement_test_summaries(
    user: CurrentPerson,
    db: DbSession,
    ids: str = Query(default=""),
) -> list[RequirementTestSummaryOut]:
    """需求列表批量查状态；一次取一页，避免逐行请求。"""
    requirement_ids = sorted(
        {int(item) for item in ids.split(",") if item.strip().isdigit()}
    )
    if not requirement_ids:
        return []
    rows = list(
        (
            await db.execute(
                select(Requirement.id, Requirement.system_id).where(
                    Requirement.id.in_(requirement_ids)
                )
            )
        ).all()
    )
    # 查看权限与需求列表一致：任意登录用户；只返回存在的需求
    for system_id in {row[1] for row in rows}:
        await require_system_access(db, system_id, user)
    existing_ids = [row[0] for row in rows]
    batches = await _latest_batches(db, existing_ids)
    aggregates = await _run_aggregates(db, [b.id for b in batches.values()])
    counts = await _task_case_counts(
        db, [b.task_id for b in batches.values() if b.task_id]
    )
    return [
        _summary(
            requirement_id,
            batches.get(requirement_id),
            aggregates.get(batches[requirement_id].id, {})
            if requirement_id in batches
            else {},
            counts.get(batches[requirement_id].task_id, 0)
            if requirement_id in batches and batches[requirement_id].task_id
            else 0,
        )
        for requirement_id in existing_ids
    ]


@router.get("/api/requirements/{requirement_id}/tests")
async def requirement_tests(
    requirement_id: int,
    user: CurrentPerson,
    db: DbSession,
    limit: int = Query(default=30, le=100),
) -> list[dict]:
    """该需求的历史测试（批次维度，新到旧）。"""
    requirement = await _requirement_or_404(db, requirement_id)
    await require_system_access(db, requirement.system_id, user)
    rows = (
        await db.execute(
            select(RunBatch, TestTask)
            .join(TestTask, TestTask.id == RunBatch.task_id)
            .where(TestTask.requirement_id == requirement_id)
            .order_by(RunBatch.created_at.desc())
            .limit(limit)
        )
    ).all()
    aggregates = await _run_aggregates(db, [batch.id for batch, _ in rows])
    return [
        {
            "batch_id": str(batch.id),
            "task_id": str(batch.task_id) if batch.task_id else None,
            "name": batch.name,
            "trigger": batch.trigger,
            "environment_name": batch.environment_name,
            "created_at": batch.created_at.isoformat() if batch.created_at else None,
            "started_at": (
                aggregates[batch.id]["started_at"].isoformat()
                if aggregates.get(batch.id, {}).get("started_at")
                else None
            ),
            "ended_at": (
                aggregates[batch.id]["ended_at"].isoformat()
                if aggregates.get(batch.id, {}).get("ended_at")
                else None
            ),
            "status": derive_status(
                aggregates.get(batch.id, {}).get("statuses", []),
                real_failed=(
                    aggregates.get(batch.id, {}).get("failed", 0)
                    - aggregates.get(batch.id, {}).get("excluded_failed", 0)
                ),
                excluded_runs=aggregates.get(batch.id, {}).get("excluded_runs", 0),
            ),
            "total": aggregates.get(batch.id, {}).get("total", 0),
            "passed": aggregates.get(batch.id, {}).get("passed", 0),
            "failed": aggregates.get(batch.id, {}).get("failed", 0),
        }
        for batch, _task in rows
    ]