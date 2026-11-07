import uuid

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, func, select

from ..deps import CurrentPerson, DbSession, require_system_access
from ..environments import resolve_run_environments
from ..models import (
    DevGroup,
    Feature,
    Person,
    Requirement,
    Run,
    RunBatch,
    System,
    TaskCase,
    TaskFeature,
    TestCase,
    TestTask,
)
from ..schemas import (
    CaseIdList,
    FeatureIdList,
    TaskCreate,
    TaskIdList,
    TaskListOut,
    TaskOut,
    TaskUpdate,
)
from ..taskstatus import aggregate
from .batches import derive_status
from .task_common import (
    active_task_ids,
    attach_features,
    delete_tasks,
    purge_artifacts,
    require_task,
    spawn_runs,
)

router = APIRouter(tags=["tasks"])


async def _enrich_tasks(db, tasks: list[TestTask]) -> list[TaskOut]:
    """给任务列表补上功能数 / 用例数 / 最近一次执行。"""
    if not tasks:
        return []
    ids = [t.id for t in tasks]
    feature_counts = dict(
        (
            await db.execute(
                select(TaskFeature.task_id, func.count())
                .where(TaskFeature.task_id.in_(ids))
                .group_by(TaskFeature.task_id)
            )
        ).all()
    )
    case_counts = dict(
        (
            await db.execute(
                select(TaskCase.task_id, func.count())
                .where(TaskCase.task_id.in_(ids))
                .group_by(TaskCase.task_id)
            )
        ).all()
    )

    last_batches = (
        await db.execute(
            select(RunBatch.task_id, RunBatch.id, RunBatch.created_at)
            .where(RunBatch.task_id.in_(ids))
            .distinct(RunBatch.task_id)
            .order_by(RunBatch.task_id, RunBatch.created_at.desc())
        )
    ).all()
    batch_by_task = {row[0]: (row[1], row[2]) for row in last_batches}

    batch_status: dict[uuid.UUID, str] = {}
    if batch_by_task:
        agg = (
            await db.execute(
                select(
                    Run.batch_id,
                    func.array_agg(Run.status),
                    func.coalesce(func.sum(Run.failed), 0),
                    func.count(Run.id).filter(Run.failure_kind.is_not(None)),
                    func.coalesce(
                        func.sum(Run.failed).filter(Run.failure_kind.is_not(None)), 0
                    ),
                )
                .where(Run.batch_id.in_([b[0] for b in batch_by_task.values()]))
                .group_by(Run.batch_id)
            )
        ).all()
        batch_status = {
            row[0]: derive_status(
                list(row[1]),
                real_failed=int(row[2]) - int(row[4]),
                excluded_runs=int(row[3]),
            )
            for row in agg
        }

    out: list[TaskOut] = []
    # 自愈：worker 万一在收尾提交前崩掉，任务状态会停在 queued/running 而它的 run 其实
    # 都结束了。发现 drift 就顺手纠正 —— 只在真的要改时写库。
    run_status_by_task: dict[uuid.UUID, list[str]] = {}
    for row_task_id, row_status in (
        await db.execute(select(Run.task_id, Run.status).where(Run.task_id.in_(ids)))
    ).all():
        if row_task_id is not None:
            run_status_by_task.setdefault(row_task_id, []).append(row_status)
    repaired = 0
    for task in tasks:
        statuses = run_status_by_task.get(task.id)
        if not statuses or task.status not in {"queued", "running"}:
            continue
        expected = aggregate(statuses)
        if expected != task.status:
            task.status = expected
            repaired += 1
    if repaired:
        await db.commit()

    for task in tasks:
        item = TaskOut.model_validate(task)
        item.feature_count = int(feature_counts.get(task.id, 0))
        item.case_count = int(case_counts.get(task.id, 0))
        if task.id in batch_by_task:
            batch_id, created_at = batch_by_task[task.id]
            item.last_batch_id = batch_id
            item.last_batch_status = batch_status.get(batch_id)
            item.last_run_at = created_at
        out.append(item)
    return out


# ------------------------------------------------------------------------- 任务 CRUD


@router.get("/api/systems/{system_id}/tasks", response_model=list[TaskOut])
async def list_tasks(
    system_id: int,
    user: CurrentPerson,
    db: DbSession,
    task_status: str | None = Query(default=None, alias="status"),
    task_type: str | None = Query(default=None, alias="type"),
    name: str | None = None,
    limit: int = Query(default=100, le=500),
) -> list[TaskOut]:
    await require_system_access(db, system_id, user)
    stmt = select(TestTask).where(TestTask.system_id == system_id)
    if task_status:
        stmt = stmt.where(TestTask.status == task_status)
    if task_type:
        stmt = stmt.where(TestTask.type == task_type)
    if name:
        stmt = stmt.where(TestTask.name.ilike(f"%{name}%"))
    stmt = stmt.order_by(TestTask.created_at.desc()).limit(limit)
    return await _enrich_tasks(db, list((await db.execute(stmt)).scalars()))


@router.get("/api/tasks", response_model=TaskListOut)
async def list_all_tasks(
    user: CurrentPerson,
    db: DbSession,
    system_id: int | None = None,
    group_id: int | None = None,
    project_id: int | None = None,
    task_status: str | None = Query(default=None, alias="status"),
    task_type: str | None = Query(default=None, alias="type"),
    q: str | None = None,
    limit: int = Query(default=20, le=200),
    offset: int = Query(default=0, ge=0),
) -> TaskListOut:
    """跨系统任务检索；研发组 / 系统就是顶栏的全局过滤条件。"""
    if system_id is not None:
        await require_system_access(db, system_id, user)

    stmt = select(TestTask)
    if group_id is not None:
        stmt = stmt.join(System, System.id == TestTask.system_id).where(
            System.group_id == group_id
        )
    if system_id is not None:
        stmt = stmt.where(TestTask.system_id == system_id)
    if project_id is not None:
        stmt = stmt.where(
            TestTask.system_id.in_(
                select(System.id)
                .join(DevGroup, DevGroup.id == System.group_id)
                .where(DevGroup.project_id == project_id)
            )
        )
    if task_status:
        stmt = stmt.where(TestTask.status == task_status)
    if task_type:
        stmt = stmt.where(TestTask.type == task_type)
    if q:
        stmt = stmt.where(TestTask.name.ilike(f"%{q}%"))

    total = int(
        (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    )
    tasks = list(
        (
            await db.execute(
                stmt.order_by(TestTask.created_at.desc()).limit(limit).offset(offset)
            )
        ).scalars()
    )
    items = await _enrich_tasks(db, tasks)
    if not items:
        return TaskListOut(items=[], total=total)

    # 批量补展示字段，避免逐行查询
    systems = {
        row[0]: (row[1], row[2])
        for row in (
            await db.execute(
                select(System.id, System.name, System.group_id).where(
                    System.id.in_({t.system_id for t in tasks})
                )
            )
        ).all()
    }
    # 跨系统任务的系统列要列出全部归属系统，而不只是任务挂靠的那个
    task_systems: dict[uuid.UUID, list[str]] = {}
    for row in (
        await db.execute(
            select(TaskFeature.task_id, System.name)
            .join(Feature, Feature.id == TaskFeature.feature_id)
            .join(System, System.id == Feature.system_id)
            .where(TaskFeature.task_id.in_([t.id for t in tasks]))
            .distinct()
            .order_by(TaskFeature.task_id, System.name)
        )
    ).all():
        task_systems.setdefault(row[0], []).append(row[1])
    reqs: dict[int, Requirement] = {}
    req_ids = {t.requirement_id for t in tasks if t.requirement_id is not None}
    if req_ids:
        reqs = {
            r.id: r
            for r in (
                await db.execute(select(Requirement).where(Requirement.id.in_(req_ids)))
            ).scalars()
        }
    names: dict[int, str] = {}
    person_ids = {t.created_by for t in tasks if t.created_by is not None}
    if person_ids:
        names = dict(
            (
                await db.execute(
                    select(Person.id, Person.name).where(Person.id.in_(person_ids))
                )
            ).all()
        )

    for item, task in zip(items, tasks):
        item.system_name, item.group_id = systems.get(task.system_id, (None, None))
        if system_names := task_systems.get(task.id):
            item.system_name = "、".join(system_names)
        req = reqs.get(task.requirement_id) if task.requirement_id is not None else None
        if req is not None:
            item.requirement_code = req.code
            item.requirement_title = req.title
        item.created_by_name = names.get(task.created_by) if task.created_by is not None else None
    return TaskListOut(items=items, total=total)


async def _create_task(
    db, user: Person, system_id: int, system_ids: set[int], body: TaskCreate
) -> dict:
    if body.type not in {"case_run", "feature_regression", "adhoc"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法任务类型")

    task = TestTask(
        system_id=system_id,
        requirement_id=body.requirement_id,
        created_by=user.id,
        name=body.name,
        type=body.type,
        description=body.description,
        retry_count=body.retry_count,
        environment_name=(body.environment_name or "").strip() or None,
        case_selection_mode="snapshot",
        status="draft",
    )
    db.add(task)
    await db.flush()

    if task.environment_name:
        try:
            await resolve_run_environments(
                db, task, system_ids, environment_name=task.environment_name
            )
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    case_count = await attach_features(db, task, body.feature_ids)

    batch_id: str | None = None
    run_ids: list[str] = []
    if body.run_now:
        batch, runs = await spawn_runs(db, task, user, trigger="manual")
        batch_id = str(batch.id)
        run_ids = [str(r.id) for r in runs]

    await db.commit()
    return {
        **TaskOut.model_validate(task).model_dump(mode="json"),
        "case_count": case_count,
        "feature_count": len(body.feature_ids),
        "batch_id": batch_id,
        "run_ids": run_ids,
    }


@router.post(
    "/api/systems/{system_id}/tasks", status_code=status.HTTP_201_CREATED
)
async def create_task(
    system_id: int, body: TaskCreate, user: CurrentPerson, db: DbSession
) -> dict:
    """建任务。可以直接带上要回归的功能，一次调用就把任务建好并（可选）触发执行。"""
    await require_system_access(db, system_id, user, "member")
    features = []
    if body.feature_ids:
        features = list(
            (
                await db.execute(
                    select(Feature).where(
                        Feature.id.in_(body.feature_ids), Feature.system_id == system_id
                    )
                )
            ).scalars()
        )
        if missing := set(body.feature_ids) - {f.id for f in features}:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{len(missing)} 个功能不存在或不属于该系统"
            )

    return await _create_task(db, user, system_id, {system_id}, body)


@router.post("/api/tasks", status_code=status.HTTP_201_CREATED)
async def create_cross_system_task(
    body: TaskCreate, user: CurrentPerson, db: DbSession
) -> dict:
    """跨系统建任务：功能可以来自多个系统，权限要求覆盖每个系统。

    任务归属最小 system_id；执行时 spawn_runs 仍按功能所属系统拆成多个 run。
    """
    if not body.feature_ids:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "请至少选择一个功能")
    features = list(
        (
            await db.execute(select(Feature).where(Feature.id.in_(body.feature_ids)))
        ).scalars()
    )
    if missing := set(body.feature_ids) - {f.id for f in features}:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"{len(missing)} 个功能不存在"
        )
    system_ids = {f.system_id for f in features}
    for sid in sorted(system_ids):
        await require_system_access(db, sid, user, "member")

    return await _create_task(db, user, min(system_ids), system_ids, body)


@router.get("/api/tasks/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> TaskOut:
    task = await require_task(db, task_id, user)
    return (await _enrich_tasks(db, [task]))[0]


@router.patch("/api/tasks/{task_id}", response_model=TaskOut)
async def update_task(
    task_id: uuid.UUID, body: TaskUpdate, user: CurrentPerson, db: DbSession
) -> TestTask:
    task = await require_task(db, task_id, user, "member")
    data = body.model_dump(exclude_unset=True)
    if "environment_name" in data:
        data["environment_name"] = (data["environment_name"] or "").strip() or None
        if data["environment_name"]:
            try:
                await resolve_run_environments(
                    db,
                    task,
                    {task.system_id},
                    environment_name=data["environment_name"],
                )
            except ValueError as exc:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    for field, value in data.items():
        setattr(task, field, value)
    await db.commit()
    await db.refresh(task)
    return task


@router.delete("/api/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_task(task_id: uuid.UUID, user: CurrentPerson, db: DbSession) -> None:
    task = await require_task(db, task_id, user, "admin")
    if task_id in await active_task_ids(db, [task_id]):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "该任务有执行正在进行或排队中，请先取消再删除"
        )
    dirs, midscene_ids = await delete_tasks(db, [task])
    await db.commit()
    purge_artifacts(dirs, midscene_ids)


@router.post("/api/tasks/bulk-delete")
async def bulk_delete_tasks(
    body: TaskIdList, user: CurrentPerson, db: DbSession
) -> dict[str, object]:
    """批量删除任务，连带删掉它们的执行记录。

    正在跑的任务跳过而不是整批失败 —— 跳过的会单独报回来。
    """
    tasks = list(
        (
            await db.execute(select(TestTask).where(TestTask.id.in_(body.task_ids)))
        ).scalars()
    )
    for task in tasks:
        await require_task(db, task.id, user, "admin")

    active = await active_task_ids(db, [t.id for t in tasks])
    deletable = [t for t in tasks if t.id not in active]
    dirs, midscene_ids = (
        await delete_tasks(db, deletable) if deletable else ([], [])
    )
    await db.commit()
    purge_artifacts(dirs, midscene_ids)
    return {
        "deleted": len(deletable),
        "skipped": [
            {"id": str(t.id), "name": t.name, "reason": "有执行正在进行或排队中"}
            for t in tasks
            if t.id in active
        ],
    }


# ------------------------------------------------------------- 任务的用例 / 功能


@router.get("/api/tasks/{task_id}/cases")
async def list_task_cases(
    task_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> list[dict]:
    await require_task(db, task_id, user)
    stmt = (
        select(TestCase, Feature, TaskCase.source_feature_id)
        .join(TaskCase, TaskCase.case_id == TestCase.id)
        .join(Feature, Feature.id == TestCase.feature_id)
        .where(TaskCase.task_id == task_id)
        .order_by(TaskCase.position, TestCase.name)
    )
    return [
        {
            "id": str(case.id),
            "name": case.name,
            "priority": case.priority,
            "defect_ids": case.defect_ids,
            "is_active": case.is_active,
            "effective_stage": case.stage_override or feature.stage,
            "feature_id": str(feature.id),
            "feature_name": feature.name,
            "feature_requirement_id": feature.requirement_id,
            "system_id": feature.system_id,
            "source_feature_id": str(source) if source else None,
        }
        for case, feature, source in (await db.execute(stmt)).all()
    ]


@router.put("/api/tasks/{task_id}/cases")
async def set_task_cases(
    task_id: uuid.UUID, body: CaseIdList, user: CurrentPerson, db: DbSession
) -> dict[str, int]:
    task = await require_task(db, task_id, user, "member")
    await db.execute(delete(TaskCase).where(TaskCase.task_id == task_id))
    if body.case_ids:
        # 必须连系统一起过滤：只用 id 查会让别的系统的用例挂到本任务上执行
        rows = (
            await db.execute(
                select(TestCase.id, TestCase.feature_id)
                .join(Feature, Feature.id == TestCase.feature_id)
                .where(
                    TestCase.id.in_(body.case_ids),
                    Feature.system_id == task.system_id,
                )
            )
        ).all()
        found = {row[0] for row in rows}
        if missing := set(body.case_ids) - found:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{len(missing)} 个用例不存在或不属于该系统"
            )
        for position, (case_id, feature_id) in enumerate(rows):
            db.add(
                TaskCase(
                    task_id=task_id,
                    case_id=case_id,
                    source_feature_id=feature_id,
                    position=position,
                )
            )
    await db.commit()
    return {"count": len(body.case_ids)}


@router.get("/api/tasks/{task_id}/features")
async def list_task_features(
    task_id: uuid.UUID, user: CurrentPerson, db: DbSession
) -> list[dict]:
    """任务由哪些功能组合而成 —— 任务详情的「功能」页签要用。"""
    await require_task(db, task_id, user)
    stmt = (
        select(Feature)
        .join(TaskFeature, TaskFeature.feature_id == Feature.id)
        .where(TaskFeature.task_id == task_id)
        .order_by(Feature.name)
    )
    return [
        {
            "id": str(feature.id),
            "name": feature.name,
            "stage": feature.stage,
            "system_id": feature.system_id,
            "requirement_id": feature.requirement_id,
        }
        for feature in (await db.execute(stmt)).scalars()
    ]


@router.put("/api/tasks/{task_id}/features")
async def set_task_features(
    task_id: uuid.UUID, body: FeatureIdList, user: CurrentPerson, db: DbSession
) -> dict[str, int]:
    task = await require_task(db, task_id, user, "member")
    await db.execute(delete(TaskFeature).where(TaskFeature.task_id == task_id))
    if body.feature_ids:
        found = set(
            (
                await db.execute(
                    select(Feature.id).where(
                        Feature.id.in_(body.feature_ids),
                        Feature.system_id == task.system_id,
                    )
                )
            ).scalars()
        )
        if missing := set(body.feature_ids) - found:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, f"{len(missing)} 个功能不存在或不属于该系统"
            )
        for feature_id in body.feature_ids:
            db.add(TaskFeature(task_id=task_id, feature_id=feature_id))
    await db.commit()
    return {"count": len(body.feature_ids)}