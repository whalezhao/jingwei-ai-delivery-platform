"""任务的共享操作：权限、快照、拆 run、删除、产物清理。"""

import uuid

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select

from ..config import get_settings
from ..deps import require_system_access
from ..environments import DEFAULT_ENVIRONMENT, resolve_run_environments
from ..modelconfig import assert_systems_configured
from ..models import (
    AiCheck,
    AppSetting,
    CaseResult,
    Feature,
    SystemModelConfig,
    Run,
    RunBatch,
    TaskCase,
    TaskFeature,
    TestCase,
    TestTask,
)
from ..quota import ensure_quota
from ..runner import staging
from ..taskstatus import ACTIVE_RUN_STATUSES


async def require_task(db, task_id: uuid.UUID, user, minimum: str = "view") -> TestTask:
    task = await db.get(TestTask, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    await require_system_access(db, task.system_id, user, minimum)
    return task


async def resolve_run_groups(db, task: TestTask) -> dict[int, int]:
    """任务下的用例按系统分组。

    这不是优化而是硬约束：YAML 里的 gotoUrl / ${BASE_URL} 是系统级的，跨系统的用例
    没法在一次 midscene-test 里跑，所以跨系统的任务必须拆成多个 run。
    """
    stmt = (
        select(Feature.system_id, func.count(TestCase.id))
        .join(TestCase, TestCase.feature_id == Feature.id)
        .join(TaskCase, TaskCase.case_id == TestCase.id)
        .where(TaskCase.task_id == task.id, TestCase.is_active.is_(True))
        .group_by(Feature.system_id)
    )
    return {row[0]: row[1] for row in (await db.execute(stmt)).all()}


async def _cases_by_system(db, task: TestTask) -> dict[int, list[uuid.UUID]]:
    """按系统列出任务快照里的用例 id，顺序沿用任务里的 position。"""
    rows = (
        await db.execute(
            select(Feature.system_id, TestCase.id)
            .join(TestCase, TestCase.feature_id == Feature.id)
            .join(TaskCase, TaskCase.case_id == TestCase.id)
            .where(TaskCase.task_id == task.id, TestCase.is_active.is_(True))
            .order_by(TaskCase.position, TestCase.name)
        )
    ).all()
    grouped: dict[int, list[uuid.UUID]] = {}
    for system_id, case_id in rows:
        grouped.setdefault(system_id, []).append(case_id)
    return grouped


async def attach_features(
    db, task: TestTask, feature_ids: list[uuid.UUID]
) -> int:
    """把功能、以及它们下面启用中的用例，**快照**进任务。返回用例数。

    快照而非动态引用：保证历史回归可复现 —— 功能后续新增的用例不会偷偷混进
    一个已经定稿的回归计划里。
    """
    if not feature_ids:
        return 0
    cases = (
        await db.execute(
            select(TestCase.id, TestCase.feature_id).where(
                TestCase.feature_id.in_(feature_ids), TestCase.is_active.is_(True)
            )
        )
    ).all()
    for feature_id in feature_ids:
        db.add(TaskFeature(task_id=task.id, feature_id=feature_id))
    for position, (case_id, feature_id) in enumerate(cases):
        db.add(
            TaskCase(
                task_id=task.id,
                case_id=case_id,
                source_feature_id=feature_id,
                position=position,
            )
        )
    return len(cases)


def split_shards(case_ids: list[uuid.UUID], shard_size: int) -> list[list[uuid.UUID]]:
    """按 shard_size 切分用例；0 表示不分片。"""
    if not case_ids:
        return []
    if shard_size <= 0:
        return [case_ids]
    return [
        case_ids[index : index + shard_size]
        for index in range(0, len(case_ids), shard_size)
    ]


async def _ensure_base_url_available(
    db, environments: dict, cases_by_project: dict[uuid.UUID, list[uuid.UUID]]
) -> None:
    """用例引用 ${BASE_URL} 时，环境或项目必须给得出地址。

    否则 midscene 在收集阶段就报 Undefined project variable，整批用例一条都不跑；
    拦在入队前，用户才能看到一句能照着改的提示。
    """
    case_ids = [cid for ids in cases_by_project.values() for cid in ids]
    yaml_by_id = dict(
        (
            await db.execute(
                select(TestCase.id, TestCase.yaml_content).where(TestCase.id.in_(case_ids))
            )
        ).all()
    )

    problems: list[str] = []
    for system_id, ids in cases_by_project.items():
        env = environments.get(system_id)
        if env is not None and env.base_url:
            continue
        if any("${BASE_URL}" in (yaml_by_id.get(cid) or "") for cid in ids):
            env_name = env.name if env is not None else DEFAULT_ENVIRONMENT
            problems.append(f"系统 {system_id} 的环境「{env_name}」没有配置被测地址")
    if problems:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "；".join(problems)
            + "，但用例里引用了 ${BASE_URL}；请到项目环境里补上地址后再执行",
        )


async def spawn_runs(
    db,
    task: TestTask,
    user=None,
    trigger: str = "manual",
    *,
    case_ids: list[uuid.UUID] | None = None,
    parent_batch_id: uuid.UUID | None = None,
    environment_name: str | None = None,
    created_by_id: int | None = None,
) -> tuple[RunBatch, list[Run]]:
    """为一次执行建批次，并按系统拆出多个 run。

    批次是用户视角的「一次执行」，run 是技术粒度（一次 midscene 进程只能跑一个系统）。
    `case_ids` 用于「只重跑失败用例」：只跑指定用例，仍然按系统拆 run。
    """
    await ensure_batch_slot(db, task.id)
    cases_by_project = await _cases_by_system(db, task)
    if case_ids is not None:
        wanted = set(case_ids)
        cases_by_project = {
            system_id: [cid for cid in ids if cid in wanted]
            for system_id, ids in cases_by_project.items()
        }
        cases_by_project = {pid: ids for pid, ids in cases_by_project.items() if ids}
    groups = {pid: len(ids) for pid, ids in cases_by_project.items()}
    if not groups:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "任务下没有可执行的用例")
    # 执行门禁：系统必须明确配过模型才允许跑。所有执行路径都汇到这里，拦一处就够。
    await assert_systems_configured(db, set(groups))
    # 额度门禁：超限就不让跑 —— 报错里带已用/限额，管理员调高即可
    for system_id in groups:
        try:
            await ensure_quota(
                db, system_id, await db.get(SystemModelConfig, system_id)
            )
        except RuntimeError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    env_name = (environment_name or task.environment_name or "").strip() or DEFAULT_ENVIRONMENT
    try:
        environments = await resolve_run_environments(
            db, task, set(groups), environment_name=env_name
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    await _ensure_base_url_available(db, environments, cases_by_project)

    batch = RunBatch(
        task_id=task.id,
        system_id=task.system_id,
        # 存任务名快照：任务改名或删除后历史记录还要能显示
        name=task.name,
        trigger=trigger,
        parent_batch_id=parent_batch_id,
        environment_name=env_name,
        created_by=user.id if user else created_by_id,
    )
    db.add(batch)
    await db.flush()

    settings = await db.get(AppSetting, 1)
    shard_size = max(0, int(settings.run_shard_size if settings else 4))

    runs: list[Run] = []
    for system_id in sorted(groups, key=str):
        project_case_ids = cases_by_project.get(system_id, [])
        if not project_case_ids:
            continue
        # 一个系统内的 YAML 默认串行；按 shard_size 切片后每个 run 一个浏览器，
        # 可以分发到不同槽位/worker 并行跑。0 表示不分片（保持老行为）。
        chunks = split_shards(project_case_ids, shard_size)
        shard_count = len(chunks)
        env = environments[system_id]
        for chunk_index, chunk in enumerate(chunks):
            run = Run(
                task_id=task.id,
                batch_id=batch.id,
                system_id=system_id,
                environment_id=env.id,
                environment_name=env.name,
                environment_base_url=env.base_url,
                created_by=user.id if user else created_by_id,
                status="queued",
                # 分片或显式指定子集时必须落 case_ids，否则 worker 会跑整个任务
                case_ids=(
                    [str(case_id) for case_id in chunk]
                    if shard_count > 1 or case_ids is not None
                    else None
                ),
                shard_index=chunk_index + 1 if shard_count > 1 else None,
                shard_count=shard_count if shard_count > 1 else None,
            )
            db.add(run)
            runs.append(run)
    if not runs:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "任务下没有可执行的用例")
    task.status = "queued"
    # flush 才能拿到主键 —— id 的默认值是 Python 侧 uuid4，构造时还没赋值
    await db.flush()
    return batch, runs


async def failed_case_ids(
    db, *, batch_id: uuid.UUID | None = None, run_id: uuid.UUID | None = None
) -> tuple[list[uuid.UUID], int]:
    """返回 (可重跑的失败用例 id, 无法归属的失败结果数)。"""
    stmt = (
        select(CaseResult.test_case_id)
        .join(Run, Run.id == CaseResult.run_id)
        .where(
            CaseResult.status.not_in(("success", "passed")),
            # 模型/网络/额度导致的失败不重跑：环境没恢复时再跑一遍只会再红一次
            Run.failure_kind.is_(None),
        )
    )
    if batch_id is not None:
        stmt = stmt.where(Run.batch_id == batch_id)
    if run_id is not None:
        stmt = stmt.where(CaseResult.run_id == run_id)
    rows = list((await db.execute(stmt)).scalars())
    resolvable = sorted({row for row in rows if row is not None}, key=str)
    unresolved = sum(1 for row in rows if row is None)
    return resolvable, unresolved


async def delete_batches(db, batch_ids: list[uuid.UUID]) -> tuple[list[str], list[str]]:
    """删批次及其 run / AI 结论，返回待清理的 (工作目录, midscene runId)。"""
    if not batch_ids:
        return [], []
    run_ids = set(
        (await db.execute(select(Run.id).where(Run.batch_id.in_(batch_ids)))).scalars()
    )
    dirs: list[str] = []
    midscene_ids: list[str] = []
    if run_ids:
        rows = (
            await db.execute(
                select(Run.work_dir, Run.midscene_run_id).where(Run.id.in_(run_ids))
            )
        ).all()
        dirs = [work_dir for work_dir, _ in rows if work_dir]
        midscene_ids = [mid for _, mid in rows if mid]
        await db.execute(delete(Run).where(Run.id.in_(run_ids)))
    await db.execute(
        delete(AiCheck).where(
            AiCheck.entity_type == "batch", AiCheck.entity_id.in_(batch_ids)
        )
    )
    await db.execute(delete(RunBatch).where(RunBatch.id.in_(batch_ids)))
    return dirs, midscene_ids


async def delete_tasks(db, tasks: list[TestTask]) -> tuple[list[str], list[str]]:
    """删任务并**连带删掉它的执行记录**，返回待清理的 (工作目录, midscene runId)。

    runs.task_id / run_batches.task_id 的外键是 SET NULL —— 只删任务的话，这些执行
    记录会变成「没有任务归属的残留」，继续占着执行记录页。所以按 task_id 显式清掉；
    case_results / ai_calls 由数据库上的 CASCADE 跟着 run 一起走。
    """
    task_ids = [t.id for t in tasks]
    batch_ids = list(
        (
            await db.execute(select(RunBatch.id).where(RunBatch.task_id.in_(task_ids)))
        ).scalars()
    )
    dirs, midscene_ids = await delete_batches(db, batch_ids)

    # 没有批次的老 run（batch_id 为空）也要跟着任务删
    run_ids = set(
        (await db.execute(select(Run.id).where(Run.task_id.in_(task_ids)))).scalars()
    )
    if run_ids:
        rows = (
            await db.execute(
                select(Run.work_dir, Run.midscene_run_id).where(Run.id.in_(run_ids))
            )
        ).all()
        dirs += [work_dir for work_dir, _ in rows if work_dir]
        midscene_ids += [mid for _, mid in rows if mid]
        await db.execute(delete(Run).where(Run.id.in_(run_ids)))
    await db.execute(
        delete(AiCheck).where(
            AiCheck.entity_type == "task", AiCheck.entity_id.in_(task_ids)
        )
    )
    await db.execute(delete(TestTask).where(TestTask.id.in_(task_ids)))
    return dirs, midscene_ids


def purge_artifacts(dirs: list[str], midscene_ids: list[str]) -> None:
    """删掉执行在本机留下的产物（私有工作目录 + 散落在全局目录的报告）。"""
    staging.purge_run_artifacts(
        runner_root=get_settings().runner_path,
        work_dirs=dirs,
        midscene_run_ids=midscene_ids,
    )


BATCH_KEEP = 10


async def _active_batch_ids(db, batch_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not batch_ids:
        return set()
    return set(
        (
            await db.execute(
                select(Run.batch_id)
                .where(
                    Run.batch_id.in_(batch_ids),
                    Run.status.in_(ACTIVE_RUN_STATUSES),
                )
                .distinct()
            )
        ).scalars()
    )


async def ensure_batch_slot(db, task_id: uuid.UUID) -> None:
    """新批次会把最旧的挤出去；可能被挤掉的都冻结了就提前拦住并提示。"""
    rows = (
        await db.execute(
            select(RunBatch.id, RunBatch.pinned)
            .where(RunBatch.task_id == task_id)
            .order_by(RunBatch.created_at.desc())
        )
    ).all()
    if len(rows) < BATCH_KEEP:
        return
    # 新建一条后，最新的 BATCH_KEEP 条保留，其余都是候选
    if not [row for row in rows[BATCH_KEEP - 1 :] if not row.pinned]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"执行记录最多保留 {BATCH_KEEP} 条，多出的记录都已冻结；请先解冻一条再执行",
        )


async def trim_task_batches(db, task_id: uuid.UUID) -> None:
    """调用方 commit 新批次后裁剪：只留最近 BATCH_KEEP 条，冻结和还在跑的不动。"""
    rows = (
        await db.execute(
            select(RunBatch.id, RunBatch.pinned)
            .where(RunBatch.task_id == task_id)
            .order_by(RunBatch.created_at.desc())
        )
    ).all()
    victims = [row[0] for row in rows[BATCH_KEEP:] if not row[1]]
    active = await _active_batch_ids(db, victims)
    victims = [batch_id for batch_id in victims if batch_id not in active]
    if not victims:
        return
    dirs, midscene_ids = await delete_batches(db, victims)
    await db.commit()
    purge_artifacts(dirs, midscene_ids)


async def active_task_ids(db, task_ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """有执行正在排队/跑着的任务 —— 这些不能删：worker 手里还攥着那个 run。"""
    if not task_ids:
        return set()
    return set(
        (
            await db.execute(
                select(Run.task_id)
                .where(
                    Run.task_id.in_(task_ids), Run.status.in_(ACTIVE_RUN_STATUSES)
                )
                .distinct()
            )
        ).scalars()
    )

