"""执行批次 —— 用户视角的「一次执行」。

一次执行可能拆成多个 midscene 进程（run），但用户看到的是「我点了一次」。
这一层把两者对上：执行记录按批次展示，每行带各功能的通过情况，点进去再下钻到
各 run → 用例结果 → midscene 报告（叶子节点）。
"""

import json
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from ..deps import CurrentPerson, DbSession, require_system_access
from ..models import (
    AiCheck,
    CaseResult,
    Feature,
    Person,
    System,
    Run,
    RunBatch,
    TaskCase,
    TaskFeature,
    TestCase,
    TestTask,
)
from ..schemas import (
    BatchDetailOut,
    BatchListOut,
)
from .runs import _resolve_data_path
from .task_common import active_task_ids, failed_case_ids, spawn_runs

router = APIRouter(prefix="/api/batches", tags=["batches"])

ACTIVE_RUN_STATUSES = ("queued", "preparing", "running", "parsing")


def derive_status(statuses: list[str], real_failed: int, excluded_runs: int) -> str:
    """批次状态由它的 runs 现算，不落库 —— 存一份就多一处会不一致的地方。

    刻意区分 completed 与 completed_with_failures：执行跑完了和用例通过是两件事。
    real_failed 已经扣掉了「非用例原因」（模型额度/配置/网络）导致的失败。
    """
    if not statuses:
        return "empty"
    if any(s in ACTIVE_RUN_STATUSES for s in statuses):
        return "running"
    if all(s == "cancelled" for s in statuses):
        return "cancelled"

    succeeded = sum(1 for s in statuses if s == "succeeded")
    if succeeded == 0:
        return "excluded" if excluded_runs == len(statuses) else "failed"
    if succeeded < len(statuses):
        return "partial"
    return "completed_with_failures" if real_failed else "completed"


def _scope(
    *,
    system_id: int | None,
    task_id: uuid.UUID | None,
    trigger: str | None,
    name: str | None,
) -> list:
    conditions = []
    if system_id is not None:
        conditions.append(RunBatch.system_id == system_id)
    if task_id is not None:
        conditions.append(RunBatch.task_id == task_id)
    if trigger:
        conditions.append(RunBatch.trigger == trigger)
    if name:
        conditions.append(RunBatch.name.ilike(f"%{name}%"))
    return conditions


async def _run_aggregates(db, batch_ids: list[uuid.UUID]) -> dict:
    """按批次聚合 runs。"""
    if not batch_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Run.batch_id,
                func.count(Run.id),
                func.coalesce(func.sum(Run.total), 0),
                func.coalesce(func.sum(Run.passed), 0),
                func.coalesce(func.sum(Run.failed), 0),
                func.min(Run.started_at),
                func.max(Run.ended_at),
                func.coalesce(func.sum(Run.duration_ms), 0),
                func.array_agg(Run.status),
                func.count(Run.id).filter(Run.failure_kind.is_not(None)),
                func.coalesce(
                    func.sum(Run.failed).filter(Run.failure_kind.is_not(None)), 0
                ),
            )
            .where(Run.batch_id.in_(batch_ids))
            .group_by(Run.batch_id)
        )
    ).all()
    return {
        row[0]: {
            "runs": int(row[1]),
            "total": int(row[2]),
            "passed": int(row[3]),
            "failed": int(row[4]),
            "started_at": row[5],
            "ended_at": row[6],
            # 批次耗时是墙钟跨度，不是各 run 耗时之和 —— 分片并行时求和会重复计算
            "duration_ms": (
                int((row[6] - row[5]).total_seconds() * 1000)
                if row[5] is not None and row[6] is not None
                else int(row[7])
            ),
            "statuses": list(row[8]),
            "excluded_runs": int(row[9]),
            "excluded_failed": int(row[10]),
        }
        for row in rows
    }


async def _feature_breakdown(db, batch_ids: list[uuid.UUID]) -> dict:
    """按「批次 × 功能」聚合用例结果 —— 执行记录每行要显示的多个功能状态。"""
    if not batch_ids:
        return {}
    rows = (
        await db.execute(
            select(
                Run.batch_id,
                Feature.id,
                Feature.name,
                Feature.stage,
                func.count(CaseResult.id),
                func.count(CaseResult.id).filter(CaseResult.status == "success"),
                func.count(CaseResult.id).filter(CaseResult.status == "failed"),
            )
            .join(Run, Run.id == CaseResult.run_id)
            .join(TestCase, TestCase.id == CaseResult.test_case_id)
            .join(Feature, Feature.id == TestCase.feature_id)
            .where(Run.batch_id.in_(batch_ids))
            .group_by(Run.batch_id, Feature.id, Feature.name, Feature.stage)
            .order_by(Feature.name)
        )
    ).all()
    out: dict[uuid.UUID, list[dict]] = {}
    for batch_id, fid, fname, fstage, total, passed, failed in rows:
        out.setdefault(batch_id, []).append(
            {
                "feature_id": str(fid),
                "name": fname,
                "stage": fstage,
                "total": int(total),
                "passed": int(passed),
                "failed": int(failed),
            }
        )
    return out


async def _task_info(db, task_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict]:
    """批次对应的上游测试任务。"""
    if not task_ids:
        return {}
    tasks = list(
        (await db.execute(select(TestTask).where(TestTask.id.in_(task_ids)))).scalars()
    )
    if not tasks:
        return {}
    feature_counts = dict(
        (
            await db.execute(
                select(TaskFeature.task_id, func.count())
                .where(TaskFeature.task_id.in_(task_ids))
                .group_by(TaskFeature.task_id)
            )
        ).all()
    )
    case_counts = dict(
        (
            await db.execute(
                select(TaskCase.task_id, func.count())
                .where(TaskCase.task_id.in_(task_ids))
                .group_by(TaskCase.task_id)
            )
        ).all()
    )
    return {
        task.id: {
            "task_name": task.name,
            "task_type": task.type,
            "task_status": task.status,
            "task_feature_count": int(feature_counts.get(task.id, 0)),
            "task_case_count": int(case_counts.get(task.id, 0)),
        }
        for task in tasks
    }


async def _ai_verdicts(
    db, batch_ids: list[uuid.UUID]
) -> dict[uuid.UUID, tuple[str, str | None]]:
    """每个批次最新一次的 AI 结论 —— 执行记录列表上直接能看到 AI 的判断。"""
    if not batch_ids:
        return {}
    rows = (
        await db.execute(
            select(AiCheck.entity_id, AiCheck.status, AiCheck.verdict)
            .where(AiCheck.entity_type == "batch", AiCheck.entity_id.in_(batch_ids))
            .order_by(AiCheck.entity_id, AiCheck.created_at.desc())
        )
    ).all()
    out: dict[uuid.UUID, tuple[str, str | None]] = {}
    for entity_id, status, verdict in rows:
        out.setdefault(entity_id, (status, verdict))
    return out


def _batch_item(batch: RunBatch, agg: dict, task: dict, ai: tuple) -> dict:
    statuses = agg.get("statuses", [])
    return {
        "id": batch.id,
        "task_id": batch.task_id,
        "task_name": task.get("task_name"),
        "task_type": task.get("task_type"),
        "task_status": task.get("task_status"),
        "task_feature_count": task.get("task_feature_count", 0),
        "task_case_count": task.get("task_case_count", 0),
        "ai_status": ai[0],
        "ai_verdict": ai[1],
        "system_id": batch.system_id,
        "name": batch.name,
        "trigger": batch.trigger,
        "environment_name": batch.environment_name,
        "parent_batch_id": batch.parent_batch_id,
        "created_at": batch.created_at,
        "started_at": agg.get("started_at"),
        "ended_at": agg.get("ended_at"),
        "duration_ms": agg.get("duration_ms", 0),
        "runs": agg.get("runs", 0),
        "total": agg.get("total", 0),
        "passed": agg.get("passed", 0),
        "failed": agg.get("failed", 0),
        "excluded_runs": agg.get("excluded_runs", 0),
        "status": derive_status(
            statuses,
            real_failed=agg.get("failed", 0) - agg.get("excluded_failed", 0),
            excluded_runs=agg.get("excluded_runs", 0),
        ),
    }


@router.get("", response_model=BatchListOut)
async def list_batches(
    user: CurrentPerson,
    db: DbSession,
    system_id: int | None = None,
    task_id: uuid.UUID | None = None,
    trigger: str | None = None,
    name: str | None = None,
    limit: int = Query(default=50, le=200),
    offset: int = 0,
) -> BatchListOut:
    if system_id is not None:
        await require_system_access(db, system_id, user)
    elif not user.is_admin:
        raise HTTPException(403, "请指定系统")
    conditions = _scope(
        system_id=system_id, task_id=task_id, trigger=trigger, name=name
    )
    total = (
        await db.execute(select(func.count(RunBatch.id)).where(*conditions))
    ).scalar_one()
    batches = list(
        (
            await db.execute(
                select(RunBatch)
                .where(*conditions)
                .order_by(RunBatch.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
    )

    ids = [b.id for b in batches]
    aggregates = await _run_aggregates(db, ids)
    features = await _feature_breakdown(db, ids)
    task_info = await _task_info(db, [b.task_id for b in batches if b.task_id])
    ai_verdicts = await _ai_verdicts(db, ids)

    items = []
    for batch in batches:
        item = _batch_item(
            batch,
            aggregates.get(batch.id, {}),
            task_info.get(batch.task_id, {}) if batch.task_id else {},
            ai_verdicts.get(batch.id, (None, None)),
        )
        item["features"] = features.get(batch.id, [])
        items.append(item)
    return BatchListOut(items=items, total=total)


@router.get("/{batch_id}", response_model=BatchDetailOut)
async def get_batch(
    batch_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> BatchDetailOut:
    batch = await db.get(RunBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "执行记录不存在")
    await require_system_access(db, batch.system_id, user)

    runs = list(
        (
            await db.execute(
                select(Run, System.name)
                .outerjoin(System, System.id == Run.system_id)
                .where(Run.batch_id == batch_id)
                .order_by(Run.created_at)
            )
        ).all()
    )
    agg = (await _run_aggregates(db, [batch_id])).get(batch_id, {})
    features = await _feature_breakdown(db, [batch_id])
    task = (await _task_info(db, [batch.task_id] if batch.task_id else [])).get(
        batch.task_id, {}
    )
    ai = (await _ai_verdicts(db, [batch_id])).get(batch_id, (None, None))
    creator = await db.get(Person, batch.created_by) if batch.created_by else None

    item = _batch_item(batch, agg, task, ai)
    return BatchDetailOut(
        **item,
        created_by_name=creator.name if creator else None,
        features=features.get(batch_id, []),
        run_items=[
            {
                "id": run.id,
                "system_id": run.system_id,
                "system_name": system_name,
                "status": run.status,
                "total": run.total,
                "passed": run.passed,
                "failed": run.failed,
                "duration_ms": run.duration_ms,
                "model_name": run.model_name,
                "failure_kind": run.failure_kind,
                "environment_name": run.environment_name,
                "report_available": bool(run.report_html_path),
                "started_at": run.started_at,
                "shard_index": run.shard_index,
                "shard_count": run.shard_count,
            }
            for run, system_name in runs
        ],
    )


# ------------------------------------------------------------- 重跑失败用例


async def _rerun_failed(
    db,
    user,
    task: TestTask,
    case_ids: list[uuid.UUID],
    *,
    unresolved: int,
    parent_batch_id: uuid.UUID | None,
    environment_name: str | None,
) -> dict:
    if unresolved:
        raise HTTPException(
            409,
            f"有 {unresolved} 条失败结果无法归属到用例（历史数据或用例已删除），不能精确重跑",
        )
    if not case_ids:
        raise HTTPException(400, "没有可重跑的失败用例")
    available = set(
        (
            await db.execute(
                select(TaskCase.case_id)
                .join(TestCase, TestCase.id == TaskCase.case_id)
                .where(
                    TaskCase.task_id == task.id,
                    TaskCase.case_id.in_(case_ids),
                    TestCase.is_active.is_(True),
                )
            )
        ).scalars()
    )
    missing = set(case_ids) - available
    if missing:
        raise HTTPException(
            409, f"有 {len(missing)} 个失败用例已被移出任务或停用，不能重跑"
        )
    if await active_task_ids(db, [task.id]):
        raise HTTPException(409, "任务正在执行，请等当前批次结束再重跑")
    batch, runs = await spawn_runs(
        db,
        task,
        user,
        trigger="rerun",
        case_ids=sorted(available, key=str),
        parent_batch_id=parent_batch_id,
        environment_name=environment_name,
    )
    await db.commit()
    return {
        "batch_id": str(batch.id),
        "run_ids": [str(run.id) for run in runs],
        "case_count": len(available),
        "parent_batch_id": str(parent_batch_id) if parent_batch_id else None,
    }


@router.post("/{batch_id}/rerun-failed")
async def rerun_batch_failed(
    batch_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> dict:
    batch = await db.get(RunBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "执行记录不存在")
    await require_system_access(db, batch.system_id, user, "member")
    statuses = list(
        (await db.execute(select(Run.status).where(Run.batch_id == batch.id))).scalars()
    )
    if any(status in ACTIVE_RUN_STATUSES for status in statuses):
        raise HTTPException(409, "批次还没结束，不能重跑")
    if batch.task_id is None:
        raise HTTPException(400, "源任务已删除，无法重跑")
    task = await db.get(TestTask, batch.task_id)
    if task is None:
        raise HTTPException(400, "源任务已删除，无法重跑")
    case_ids, unresolved = await failed_case_ids(db, batch_id=batch.id)
    return await _rerun_failed(
        db,
        user,
        task,
        case_ids,
        unresolved=unresolved,
        parent_batch_id=batch.id,
        environment_name=batch.environment_name,
    )


# ------------------------------------------------------------- 任务级聚合报告


async def _batch_report_dir(db: DbSession, batch_id: uuid.UUID) -> Path | None:
    """聚合报告放在批次里某个 run 的工作目录下，按新到旧找第一个存在的。"""
    runs = list(
        (
            await db.execute(
                select(Run).where(Run.batch_id == batch_id).order_by(Run.created_at.desc())
            )
        ).scalars()
    )
    for run in runs:
        if not run.work_dir:
            continue
        work_dir = _resolve_data_path(run.work_dir)
        if work_dir is None:
            continue
        candidate = work_dir / "batch-report" / "markdown"
        if candidate.is_dir():
            return candidate
    return None


@router.get("/{batch_id}/report-structured")
async def get_batch_report_structured(
    batch_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> dict[str, object]:
    """任务级聚合报告：批次下所有 run 的报告合并成一份（用例 → 步骤 → 关键帧）。"""
    batch = await db.get(RunBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "执行记录不存在")
    await require_system_access(db, batch.system_id, user)

    report_dir = await _batch_report_dir(db, batch_id)
    if report_dir is not None:
        json_path = report_dir / "report.json"
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = None
        if isinstance(data, dict) and data.get("version") == 2:
            return {"available": True, "data": data, "fallback_run_id": None}

    # 老批次没有聚合报告：只有单 run 时可以直接退回那份 run 的报告
    runs = list(
        (
            await db.execute(
                select(Run).where(Run.batch_id == batch_id).order_by(Run.created_at.desc())
            )
        ).scalars()
    )
    fallback = None
    if len(runs) == 1:
        report = _resolve_data_path(runs[0].report_html_path)
        if report is not None and report.exists():
            fallback = str(runs[0].id)
    return {"available": False, "data": None, "fallback_run_id": fallback}


@router.get("/{batch_id}/report-assets/{relative:path}")
async def serve_batch_report_asset(
    batch_id: uuid.UUID, relative: str, db: DbSession
) -> FileResponse:
    """聚合报告的截图等静态资源。

    访问控制和单 run 报告一致：路径里带批次 UUID（不可猜），不再单独做鉴权；
    只允许读 `batch-report/markdown/` 下面，`../` 一律拒绝。
    """
    batch = await db.get(RunBatch, batch_id)
    if batch is None:
        raise HTTPException(404, "执行记录不存在")
    report_dir = await _batch_report_dir(db, batch_id)
    if report_dir is None:
        raise HTTPException(404, "聚合报告不存在")

    root = report_dir.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(403, "非法路径")
    if not target.is_file():
        raise HTTPException(404, "文件不存在")
    return FileResponse(target)