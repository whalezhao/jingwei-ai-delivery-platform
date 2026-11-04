"""开发任务：列表 / 详情 / 派发（入队 dev_task）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person
from ..config import get_settings
from ..crypto import decrypt
from ..db import get_db
from ..llm import am_user_key, build_claude_env, resolve_llm
from ..models import (
    DevGroup,
    DevTask,
    DevTaskEvent,
    GroupMember,
    Person,
    PersonGitlabAccount,
    System,
    Requirement,
    RequirementDoc,
)
from ..numbering import gen_session_id
from ..queue import enqueue
from ..schemas import DevTaskOut
from .requirements import _can_open  # 需求详情可见性：任务详情复用同一口径

router = APIRouter(prefix="/api/dev-tasks", tags=["dev-tasks"])


async def _get_open_task(db: AsyncSession, task_id: int, me: Person) -> DevTask:
    """任务详情含需求文档与方案正文，必须和需求详情同权：非认领人 403。

    可见性直接用需求池那套 _can_open，避免两处口径漂移。
    """
    from .requirements import _can_open

    task = await db.get(DevTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    req = await db.get(Requirement, task.requirement_id)
    if not req or not _can_open(req, me):
        raise HTTPException(403, "需求已被他人认领，无权查看")
    return task


def task_to_out(t: DevTask, runner_name: str | None = None, can_open: bool = True) -> dict:
    return DevTaskOut(
        id=t.id,
        requirement_id=t.requirement_id,
        code=t.code,
        title=t.title,
        scope=t.scope,
        status=t.status,
        creator_id=t.creator_id,
        branch=t.branch,
        mr_iid=t.mr_iid,
        mr_url=t.mr_url,
        runner_name=runner_name,
        error=t.error,
        created_at=str(t.created_at) if t.created_at else None,
        can_open=can_open,
    ).model_dump()


async def _runner_names(db: AsyncSession, task_ids: list[int]) -> dict[int, str]:
    """dev_task_id → 抢单节点名（取最新一次认领）。"""
    if not task_ids:
        return {}
    from ..models import RunnerNode, RunnerTask

    rows = (
        await db.scalars(
            select(RunnerTask).where(RunnerTask.dev_task_id.in_(task_ids)).order_by(RunnerTask.id)
        )
    ).all()
    latest = {r.dev_task_id: r.runner_id for r in rows if r.dev_task_id and r.runner_id}
    names = {
        n.id: n.name
        for n in (
            await db.scalars(select(RunnerNode).where(RunnerNode.id.in_(list(set(latest.values())))))
        ).all()
    }
    return {tid: names[rid] for tid, rid in latest.items() if rid in names}


@router.get("", response_model=list[DevTaskOut])
async def list_tasks(
    requirement_id: int | None = None,
    system_id: int | None = None,
    group_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[DevTaskOut]:
    stmt = select(DevTask).order_by(DevTask.id.desc())
    if requirement_id:
        stmt = stmt.where(DevTask.requirement_id == requirement_id)
    if system_id:
        stmt = stmt.where(DevTask.requirement_id.in_(select(Requirement.id).where(Requirement.system_id == system_id)))
    if group_id:
        stmt = stmt.where(
            DevTask.requirement_id.in_(
                select(Requirement.id)
                .join(System, Requirement.system_id == System.id)
                .where(System.group_id == group_id)
            )
        )
    if status:
        stmt = stmt.where(DevTask.status == status)
    term = (q or "").strip().lower()
    if term:
        # autoescape：用户输入里的 % / _ 按字面匹配，不当通配符
        stmt = stmt.where(
            or_(
                func.lower(DevTask.code).contains(term, autoescape=True),
                func.lower(DevTask.title).contains(term, autoescape=True),
            )
        )
    rows = (await db.scalars(stmt)).all()
    runners = await _runner_names(db, [t.id for t in rows])
    reqs = {
        r.id: r
        for r in (
            await db.scalars(
                select(Requirement).where(Requirement.id.in_([t.requirement_id for t in rows] or [0]))
            )
        ).all()
    }
    # can_open 让前端把「详情」入口按需求可见性灰掉，而不是点进去才 403
    return [
        task_to_out(t, runners.get(t.id), t.requirement_id in reqs and _can_open(reqs[t.requirement_id], me))
        for t in rows
    ]


@router.get("/{task_id}", response_model=DevTaskOut)
async def get_task(
    task_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> DevTaskOut:
    task = await db.get(DevTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    from .requirements import _can_open

    req = await db.get(Requirement, task.requirement_id)
    return task_to_out(task, can_open=bool(req) and _can_open(req, me))


@router.get("/{task_id}/detail")
async def task_detail(
    task_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> dict:
    """任务详情页聚合：定稿需求（文档定稿版本）+ 同需求拆解任务 + 定稿方案 md + SSE 过滤用 runner 任务号。"""
    from ..models import PlanVersion, RunnerTask

    task = await _get_open_task(db, task_id, me)
    req = await db.get(Requirement, task.requirement_id)
    project = await db.get(System, req.system_id)

    siblings = (
        await db.scalars(select(DevTask).where(DevTask.requirement_id == req.id).order_by(DevTask.id))
    ).all()
    runners = await _runner_names(db, [t.id for t in siblings])

    final_doc = await db.scalar(
        select(RequirementDoc)
        .where(RequirementDoc.requirement_id == req.id, RequirementDoc.is_final.is_(True))
        .order_by(RequirementDoc.id.desc())
    )
    final_plan = await db.scalar(
        select(PlanVersion)
        .where(PlanVersion.requirement_id == req.id, PlanVersion.is_final.is_(True))
        .order_by(PlanVersion.id.desc())
    )
    rt_ids = list(
        (
            await db.scalars(
                select(RunnerTask.id).where(RunnerTask.dev_task_id == task.id).order_by(RunnerTask.id)
            )
        ).all()
    )
    return {
        "task": task_to_out(task, runners.get(task.id)),
        "requirement": {
            "id": req.id,
            "code": req.code,
            "title": req.title,
            "description": req.description,
            "status": req.status,
            "plan_md": req.plan_md,
        },
        "system": {"id": project.id, "name": project.name} if project else None,
        "final_doc": (
            {"id": final_doc.id, "filename": final_doc.filename, "version": final_doc.version}
            if final_doc
            else None
        ),
        # 老需求没有版本表时退回 req.plan_md（version 0 表示非版本化）
        "final_plan": (
            {"version": final_plan.version, "content": final_plan.content}
            if final_plan
            else ({"version": 0, "content": req.plan_md} if req.plan_md else None)
        ),
        "tasks": [task_to_out(t, runners.get(t.id)) for t in siblings],
        "runner_task_ids": rt_ids,
    }


@router.get("/{task_id}/events")
async def task_events(
    task_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> list[dict]:
    """AI 干活过程回放：runner 留痕的思考/文本/工具调用/结果，按序返回。

    长任务事件数可上万，超量时取最近的：回放要看的是「现在做到哪」，不是开头。
    """
    # 过程事件里有 AI 读到的文档/代码内容，同样按需求可见性收紧
    await _get_open_task(db, task_id, me)
    rows = (
        await db.scalars(
            select(DevTaskEvent)
            .where(DevTaskEvent.dev_task_id == task_id)
            .order_by(DevTaskEvent.seq.desc())
            .limit(5000)
        )
    ).all()
    return [{"seq": r.seq, "kind": r.kind, **(r.payload or {})} for r in reversed(rows)]


GIT_RULES = """## Git 门禁（必须遵守）
- 只在当前分支上提交，不要切换/创建其他分支，不要 push
- 提交信息按 Angular 规范：<type>(<scope>): <subject>，scope 用需求编号，如 feat(REQ-XXX-2610-001): 实现xx
- 主题用中文、命令式、句尾无句号
- 完成后 git commit 即可，MR 与 push 由系统执行
"""


async def _creator_gitlab(
    db: AsyncSession, group: DevGroup, creator: Person
) -> tuple[str | None, PersonGitlabAccount | None, str | None]:
    """本人在本组绑定的 GitLab 身份：返回 (token, account, 错误原因)。

    组机器账号只用于平台级只读操作；开发任务的 push/MR 必须用本人账号，
    否则多组之间没有权限隔离、提交也会记到机器账号头上。
    """
    if not group.gitlab_base_url:
        return None, None, "研发组未配置 GitLab"
    gm = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.person_id == creator.id)
    )
    if not gm or not gm.gitlab_account_id:
        return None, None, "该成员未绑定本研发组的 GitLab 账号"
    acc = await db.get(PersonGitlabAccount, gm.gitlab_account_id)
    if not acc:
        return None, None, "绑定的 GitLab 账号不存在"
    if acc.base_url.rstrip("/") != group.gitlab_base_url.rstrip("/"):
        return None, None, "绑定的 GitLab 账号不属于本组的 GitLab 实例"
    token = decrypt(get_settings().secret_key, acc.token_enc)
    if not token:
        return None, None, "绑定的 GitLab 账号缺少 token"
    return token, acc, None


async def enqueue_dev_task(
    db: AsyncSession, task: DevTask, req: Requirement, project: System, group: DevGroup
) -> str | None:
    """构造每任务独立的 prompt/分支并入队（confirm 自动入池与手工派发共用）。
    组 GitLab 未配置时返回 None（任务留 pending，配置好后手工派发）。"""
    creator = await db.get(Person, task.creator_id)
    gm = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.person_id == creator.id)
    )
    token, acc, _err = await _creator_gitlab(db, group, creator)
    if not token:
        return None

    username = (acc.username if acc else None) or creator.gitlab_username or creator.name
    # 每任务独立分支（t<n}）：同需求多任务并行 push 不会互相覆盖
    n = task.code.rsplit("-", 1)[-1]
    branch = f"feature/{project.code_prefix.lower()}/{username}/{req.code}/t{n}"
    all_docs = (
        await db.scalars(select(RequirementDoc).where(RequirementDoc.requirement_id == req.id))
    ).all()
    # 多版本文档只下发定稿版（历史版本是过程稿，不进 AI 上下文）；无定稿标记的旧数据全量兜底
    docs = [d for d in all_docs if d.is_final] or all_docs

    scope_lines = f"任务范围：{task.scope}\n" if task.scope else ""
    boundary = (
        "\n本任务只负责上述范围；同一需求的其他任务由其他 AI 并行完成，不要实现它们的范围。\n"
        if task.scope
        else ""
    )
    prompt = f"""你是开发工程师，负责实现以下开发任务。

{GIT_RULES}
## 任务
任务编号：{task.code}
任务：{task.title or req.title}
需求编号：{req.code}《{req.title}》
项目：{project.path}（默认分支 {project.default_branch}，工作分支 {branch}，已为你准备好 worktree）
{scope_lines}{boundary}
## 已确认的执行计划
{req.plan_md or '（无）'}

请按计划实现，写完代码后提交 commit（遵守上面的 Git 门禁）。不要 push。
"""
    target = resolve_llm(creator, group)
    env = build_claude_env(
        target,
        team_id=project.am_team_id or group.am_team_id,
        agent_id=project.am_agent_id or (gm.am_agent_id if gm else None),
        user_key=am_user_key(creator),
        conversation_id=f"vega-dev-{task.id}",
    )

    payload = {
        "dev_task_id": task.id,
        "requirement_id": req.id,
        "session_id": gen_session_id(),
        "git_http_url": f"{group.gitlab_base_url.rstrip('/')}/{project.path}.git",
        "default_branch": project.default_branch,
        "branch": branch,
        "prompt": prompt,
        # 文档只传清单：runner 下载原始文件到节点，claude 自行读取解析
        "docs": [{"id": d.id, "filename": d.filename} for d in docs],
    }
    task.branch = branch
    task.status = "queued"
    await enqueue(
        db,
        type="dev_task",
        payload=payload,
        secrets={"git_token": token, "claude_env": env},
        dev_task_id=task.id,
        requirement_id=req.id,
    )
    return branch


@router.post("/{task_id}/dispatch")
async def dispatch_task(
    task_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> dict:
    task = await db.get(DevTask, task_id)
    if not task:
        raise HTTPException(404, "任务不存在")
    if task.status not in ("pending", "failed"):
        raise HTTPException(400, f"任务状态 {task.status} 不可派发")
    if task.creator_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有下发人可派发")

    req = await db.get(Requirement, task.requirement_id)
    project = await db.get(System, req.system_id)
    group = await db.get(DevGroup, project.group_id)
    creator = await db.get(Person, task.creator_id)
    _token, _acc, err = await _creator_gitlab(db, group, creator)
    if err:
        raise HTTPException(400, err)

    branch = await enqueue_dev_task(db, task, req, project, group)
    await db.commit()
    return {"ok": True, "branch": branch}
