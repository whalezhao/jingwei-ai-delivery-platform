"""工作台：需求 → 任务 → MR/CI/合并 全链路快照（流程图数据源，前端 5s 轮询）。

页面只显示实时工作的需求：所有任务都到终态（合并 / 失败 / MR 关闭）就退场，
不再提供已毕业名册——全部合入的需求状态会被置为 online（已上线），在需求池里查。"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person
from ..db import get_db
from ..jobs import MR_CLOSED_MARK
from ..models import (
    DevGroup,
    DevTask,
    DevTaskEvent,
    GroupMember,
    MrReview,
    Person,
    PlanMessage,
    PlanVersion,
    System,
    SystemOwner,
    Requirement,
    RequirementDoc,
)
from .requirements import _can_open
from .tasks import _runner_names

router = APIRouter(prefix="/api/workbench", tags=["workbench"])


def _iso(dt: datetime | None) -> str | None:
    """SQLite 读回的是不带时区的 UTC；补上 UTC 后前端才能按浏览器时区显示。"""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def task_outcome(t: DevTask, m: MrReview | None) -> str | None:
    """单任务终态：merged / failed / closed；None=还在流水线上。"""
    if t.status == "failed":
        return "failed"
    if t.status == "done":
        if (t.error or "").startswith(MR_CLOSED_MARK):
            return "closed"
        return "merged"
    return None


def requirement_graduation(outcomes: list[str | None]) -> str | None:
    """全部任务终态 → 需求收尾（不再上流程图）；全 merged=done，有 failed=failed，否则 closed。"""
    if not outcomes or any(o is None for o in outcomes):
        return None
    if all(o == "merged" for o in outcomes):
        return "done"
    if "failed" in outcomes:
        return "failed"
    return "closed"


@router.get("")
async def workbench(
    group_id: int | None = None,
    system_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    _me: Person = Depends(current_person),
) -> dict:
    # 登录人所在研发组（多组取默认序第一个）→ 前端首进页面的默认筛选
    default_group_id = await db.scalar(
        select(GroupMember.group_id)
        .join(DevGroup, GroupMember.group_id == DevGroup.id)
        .where(GroupMember.person_id == _me.id)
        .order_by(DevGroup.id)
    )

    proj_q = select(System.id)
    if group_id:
        proj_q = proj_q.where(System.group_id == group_id)
    if system_id:
        proj_q = proj_q.where(System.id == system_id)
    pids = list((await db.scalars(proj_q)).all())
    if not pids:
        return {"requirements": [], "default_group_id": default_group_id}

    # 跟踪初态是「需求定稿」：没有定稿文档的需求还没进流程，不展示
    final_req_ids = select(RequirementDoc.requirement_id).where(RequirementDoc.is_final.is_(True))
    reqs = (
        await db.scalars(
            select(Requirement)
            .where(Requirement.system_id.in_(pids), Requirement.id.in_(final_req_ids))
            .order_by(Requirement.id.desc())
            .limit(50)
        )
    ).all()
    projects = {
        p.id: p
        for p in (await db.scalars(select(System).where(System.id.in_([r.system_id for r in reqs])))).all()
    }
    group_ids = {p.group_id for p in projects.values()}
    groups = {g.id: g for g in (await db.scalars(select(DevGroup).where(DevGroup.id.in_(group_ids)))).all()}
    claimant_ids = {r.claimant_id for r in reqs if r.claimant_id}
    claimants = {
        p.id: p.name
        for p in (await db.scalars(select(Person).where(Person.id.in_(claimant_ids)))).all()
    } if claimant_ids else {}

    # 项目负责人（octo 通知对象）：前端「通知项目负责人」卡片直接展示名单
    owners: dict[int, list[str]] = {}
    if projects:
        rows = (
            await db.execute(
                select(SystemOwner.system_id, Person.name)
                .join(Person, SystemOwner.person_id == Person.id)
                .where(SystemOwner.system_id.in_(list(projects)))
                .order_by(SystemOwner.id)
            )
        ).all()
        for pid, name in rows:
            owners.setdefault(pid, []).append(name)

    # 定稿文档版本 + 定稿方案版本 + 定稿时间：阶段卡片的必要信息（不读 content 大字段）
    docs: dict[int, dict] = {}
    plan_versions: dict[int, dict] = {}
    if reqs:
        rid_list = [r.id for r in reqs]
        rows = (
            await db.execute(
                select(
                    RequirementDoc.requirement_id,
                    RequirementDoc.version,
                    RequirementDoc.filename,
                    RequirementDoc.created_at,
                )
                .where(RequirementDoc.requirement_id.in_(rid_list), RequirementDoc.is_final.is_(True))
                .order_by(RequirementDoc.id)
            )
        ).all()
        for rid, version, filename, created in rows:
            docs[rid] = {"version": version, "filename": filename, "time": _iso(created)}
        rows = (
            await db.execute(
                select(PlanVersion.requirement_id, PlanVersion.version, PlanVersion.created_at)
                .where(PlanVersion.requirement_id.in_(rid_list), PlanVersion.is_final.is_(True))
                .order_by(PlanVersion.id)
            )
        ).all()
        for rid, version, created in rows:
            plan_versions[rid] = {"version": version, "time": _iso(created)}

    tasks = (
        await db.scalars(
            select(DevTask)
            .where(DevTask.requirement_id.in_([r.id for r in reqs]))
            .order_by(DevTask.id)
        )
    ).all()
    by_req: dict[int, list[DevTask]] = {}
    for t in tasks:
        by_req.setdefault(t.requirement_id, []).append(t)
    runners = await _runner_names(db, [t.id for t in tasks])

    # 专家归属：两条讨论各取最后一条用户消息（显式选择优先），空则回落到项目默认，
    # 再退到认领人在该组的默认 agent——与 requirements.post_plan_message 的解析顺序一致。
    latest_pick: dict[tuple[int, str], str | None] = {}
    if reqs:
        rows = (
            await db.execute(
                select(PlanMessage.requirement_id, PlanMessage.mode, PlanMessage.agent_id)
                .where(
                    PlanMessage.requirement_id.in_([r.id for r in reqs]),
                    PlanMessage.role == "user",
                    PlanMessage.mode.in_(("requirement", "plan")),
                )
                .order_by(PlanMessage.id)
            )
        ).all()
        for rid, mode, aid in rows:
            latest_pick[(rid, mode)] = aid

    member_agent: dict[tuple[int, int], str] = {}  # (组, 人) → 默认 agent
    agent_name: dict[tuple[int, str], str] = {}  # (组, agent) → 人名
    agent_name_any: dict[str, str] = {}
    if group_ids:
        rows = (
            await db.execute(
                select(GroupMember.group_id, GroupMember.person_id, GroupMember.am_agent_id, Person.name)
                .join(Person, GroupMember.person_id == Person.id)
                .where(GroupMember.group_id.in_(group_ids))
            )
        ).all()
        for gid, pid, aid, name in rows:
            if aid:
                member_agent[(gid, pid)] = aid
                agent_name[(gid, aid)] = name
                agent_name_any.setdefault(aid, name)

    def expert(r: Requirement, p: System, mode: str) -> dict | None:
        if (r.id, mode) not in latest_pick:
            return None  # 该模式还没聊过，不硬凑专家
        aid = latest_pick[(r.id, mode)] or p.am_agent_id
        if not aid and r.claimant_id:
            aid = member_agent.get((p.group_id, r.claimant_id))
        if not aid:
            return None
        return {"id": aid, "name": agent_name.get((p.group_id, aid)) or agent_name_any.get(aid) or aid}


    # 多 agent 协作统计：活跃任务的事件里按 parent 分泳道 → 子 agent 个数（去重）/ 事件数
    live_ids = [t.id for t in tasks if t.status in ("queued", "running", "pushed")]
    agents: dict[int, dict] = {}
    if live_ids:
        rows = (
            await db.execute(
                select(DevTaskEvent.dev_task_id, DevTaskEvent.payload).where(DevTaskEvent.dev_task_id.in_(live_ids))
            )
        ).all()
        subs: dict[int, set[str]] = {}
        for tid, payload in rows:
            a = agents.setdefault(tid, {"sub": 0, "events": 0})
            a["events"] += 1
            parent = (payload or {}).get("parent")
            if parent:
                subs.setdefault(tid, set()).add(parent)
        for tid, ids in subs.items():
            agents[tid]["sub"] = len(ids)

    # MR 初审/放行：取每个 (project, iid) 最新一条（同 MR 多 sha 重推时旧结论作废）
    mr_keys = {
        (projects[r.system_id].id, t.mr_iid)
        for r in reqs
        for t in by_req.get(r.id, [])
        if t.mr_iid
    }
    mrs: dict[tuple[int, int], MrReview] = {}
    if mr_keys:
        rows = (
            await db.scalars(
                select(MrReview)
                .where(tuple_(MrReview.system_id, MrReview.mr_iid).in_(mr_keys))
                .order_by(MrReview.id)
            )
        ).all()
        for m in rows:
            mrs[(m.system_id, m.mr_iid)] = m

    out = []
    for r in reqs:
        p = projects[r.system_id]
        group = groups[p.group_id]
        req_open = _can_open(r, _me)
        task_items = []
        outcomes = []
        for t in by_req.get(r.id, []):
            m = mrs.get((p.id, t.mr_iid)) if t.mr_iid else None
            outcome = task_outcome(t, m)
            outcomes.append(outcome)
            task_items.append(
                {
                    "id": t.id,
                    "code": t.code,
                    "title": t.title,
                    "status": t.status,
                    # 详情页含需求文档/方案正文：与需求池同一可见性，前端据此禁用入口
                    "can_open": req_open,
                    "branch": t.branch,
                    "runner_name": runners.get(t.id),
                    "mr_iid": t.mr_iid,
                    "mr_url": t.mr_url,
                    "error": t.error,
                    "outcome": outcome,
                    "agents": agents.get(t.id),
                    "created_at": _iso(t.created_at),
                    "updated_at": _iso(t.updated_at),
                    "mr": {
                        "verdict": m.verdict,
                        "gate": m.gate,
                        "pipeline_url": m.pipeline_url,
                        "flow_state": m.flow_state,
                        "target_branch": m.target_branch or p.mr_target_branch,
                        "summary": m.summary,
                        "issues": len(m.issues or []),
                        "error": m.error,
                        "created_at": _iso(m.created_at),
                        "updated_at": _iso(m.updated_at),
                    } if m else None,
                }
            )
        if requirement_graduation(outcomes):
            continue  # 已收尾的需求退场，页面只留实时工作的
        out.append(
            {
                "id": r.id,
                "code": r.code,
                "title": r.title,
                "status": r.status,
                "can_open": req_open,
                "claimant_name": claimants.get(r.claimant_id),
                "created_at": _iso(r.created_at),
                "system_id": p.id,
                "system_name": p.name,
                "group_id": p.group_id,
                "doc_final": True,  # 只有定稿需求才会走到这里（跟踪初态）
                "plan_final": r.status in ("confirmed", "archived", "online"),  # 方案定稿节点
                "doc": docs.get(r.id),  # 定稿文档版本/文件名/定稿时间（需求定稿卡片的核心信息）
                "plan": plan_versions.get(r.id),  # 定稿方案版本号 + 定稿时间
                # 轨道按项目配置裁剪：没配就没有这两段，终点改为 octo 通知负责人
                "review_on": bool(p.audit_prefixes),
                "merge_on": bool(p.auto_approve_prefixes),
                "owners": owners.get(p.id, []),
                "doc_agent": expert(r, p, "requirement"),  # 协助写需求的专家
                "plan_agent": expert(r, p, "plan"),  # 共同编写方案的专家
                "tasks": task_items,
            }
        )
    return {"requirements": out, "default_group_id": default_group_id}
