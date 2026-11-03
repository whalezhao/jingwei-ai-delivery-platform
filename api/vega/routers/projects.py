from __future__ import annotations

import logging
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person, require_admin
from ..config import get_settings
from ..crypto import decrypt
from ..db import get_db
from ..llm import am_user_key, build_claude_env, resolve_llm
from ..models import DevGroup, EgmReport, Person, Project, ProjectOwner, ProjectPM, Requirement, utcnow
from ..numbering import gen_session_id
from ..person_links import person_ids, set_persons
from ..queue import enqueue
from ..schemas import EgmJobIn, EgmReportOut, ProjectIn, ProjectOut, ProjectUpd

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects", tags=["projects"])


def project_to_out(p: Project, tech_lead_ids: list[int], owner_ids: list[int]) -> dict:
    return {
        "id": p.id,
        "group_id": p.group_id,
        "gitlab_project_id": p.gitlab_project_id,
        "path": p.path,
        "name": p.name,
        "code_prefix": p.code_prefix,
        "default_branch": p.default_branch,
        "mr_target_branch": p.mr_target_branch,
        "tech_lead_person_ids": tech_lead_ids,
        "owner_person_ids": owner_ids,
        "audit_prefixes": p.audit_prefixes,
        "auto_approve_prefixes": p.auto_approve_prefixes,
        "am_team_id": p.am_team_id,
        "am_agent_id": p.am_agent_id,
    }


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    group_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[ProjectOut]:
    q = select(Project).order_by(Project.id)
    if group_id:
        q = q.where(Project.group_id == group_id)
    rows = (await db.scalars(q)).all()
    out = []
    for p in rows:
        out.append(
            project_to_out(
                p,
                await person_ids(db, ProjectPM, project_id=p.id),
                await person_ids(db, ProjectOwner, project_id=p.id),
            )
        )
    return out


@router.post("", response_model=ProjectOut)
async def create_project(
    body: ProjectIn, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> ProjectOut:
    dup = await db.scalar(
        select(Project).where(
            Project.group_id == body.group_id, Project.gitlab_project_id == body.gitlab_project_id
        )
    )
    if dup:
        raise HTTPException(400, "该项目已在该研发组下")
    if body.run_egm_bootstrap:
        group = await db.get(DevGroup, body.group_id)
        if not group or not (
            group.gitlab_base_url and decrypt(get_settings().secret_key, group.gitlab_token_enc)
        ):
            raise HTTPException(400, "研发组未配置 GitLab，无法自动生成 EGM 文档树")
    project = Project(
        group_id=body.group_id,
        gitlab_project_id=body.gitlab_project_id,
        path=body.path,
        name=body.name,
        code_prefix=body.code_prefix.upper(),
        default_branch=body.default_branch,
        mr_target_branch=body.mr_target_branch,
        audit_prefixes=body.audit_prefixes,
        auto_approve_prefixes=body.auto_approve_prefixes,
    )
    db.add(project)
    await db.flush()
    await set_persons(db, ProjectPM, body.tech_lead_person_ids, "技术负责人", project_id=project.id)
    await set_persons(db, ProjectOwner, body.owner_person_ids, "项目负责人", project_id=project.id)
    await db.commit()
    await db.refresh(project)
    out = project_to_out(project, body.tech_lead_person_ids, body.owner_person_ids)
    if body.run_egm_bootstrap:
        # 项目已建成：投递失败不能让建项目失败，最多日志里留一笔
        try:
            await enqueue_project_egm_job(db, project, "bootstrap")
            log.info("项目 %s 创建后自动投递 EGM 文档树", project.id)
        except HTTPException as e:
            log.warning("项目 %s 自动文档树跳过：%s", project.id, e.detail)
        except Exception:  # noqa: BLE001
            await db.rollback()
            log.exception("项目 %s 自动文档树投递失败", project.id)
    return out


@router.put("/{project_id}", response_model=ProjectOut)
async def update_project(
    project_id: int,
    body: ProjectUpd,
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> ProjectOut:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    data = body.model_dump(exclude_unset=True)
    for k in ("path", "name", "default_branch", "mr_target_branch"):
        if data.get(k):
            setattr(project, k, data[k])
    if data.get("code_prefix"):
        project.code_prefix = data["code_prefix"].upper()
    if data.get("tech_lead_person_ids") is not None:
        await set_persons(db, ProjectPM, data["tech_lead_person_ids"], "技术负责人", project_id=project.id)
    if data.get("owner_person_ids") is not None:
        await set_persons(db, ProjectOwner, data["owner_person_ids"], "项目负责人", project_id=project.id)
    # 前缀两兄弟：显式非 None 才更新（空数组 = 清空配置，None = 不动）
    for k in ("audit_prefixes", "auto_approve_prefixes", "am_team_id", "am_agent_id"):
        if data.get(k) is not None:
            setattr(project, k, data[k])
    await db.commit()
    return project_to_out(
        project,
        await person_ids(db, ProjectPM, project_id=project.id),
        await person_ids(db, ProjectOwner, project_id=project.id),
    )


# ---------- EGM 项目级任务 ----------


def _report_out(r: EgmReport) -> dict:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "kind": r.kind,
        "status": r.status,
        "report_md": r.report_md,
        "branch": r.branch,
        "mr_url": r.mr_url,
        "created_at": str(r.created_at) if r.created_at else None,
    }


@router.post("/{project_id}/egm-jobs", response_model=EgmReportOut)
async def create_egm_job(
    project_id: int,
    body: EgmJobIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """项目级 EGM 任务：bootstrap 生成 README 树（推分支 + MR），drift 出设计-代码漂移报告。"""
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    allowed = me.is_admin or me.id in (
        await person_ids(db, ProjectOwner, project_id=project.id)
        + await person_ids(db, ProjectPM, project_id=project.id)
    )
    if not allowed:
        raise HTTPException(403, "只有项目负责人或技术负责人可发起")
    rep = await enqueue_project_egm_job(db, project, body.kind)
    return _report_out(rep)


async def enqueue_project_egm_job(db: AsyncSession, project: Project, kind: str) -> EgmReport:
    """建报告并入队项目级 EGM 任务；手动按钮与每周自动漂移共用。"""
    group = await db.get(DevGroup, project.group_id)
    token = decrypt(get_settings().secret_key, group.gitlab_token_enc)
    if not (group.gitlab_base_url and token):
        raise HTTPException(400, "研发组未配置 GitLab，EGM 项目级任务需要仓库")
    running = await db.scalar(
        select(EgmReport.id)
        .where(EgmReport.project_id == project.id, EgmReport.status == "running")
        .limit(1)
    )
    if running:
        raise HTTPException(400, "该项目已有 EGM 任务在跑，请等它结束")

    designs: list[dict] = []
    if kind == "drift":
        reqs = (
            await db.scalars(
                select(Requirement)
                .where(Requirement.project_id == project.id, Requirement.plan_md.is_not(None))
                .order_by(Requirement.id.desc())
                .limit(10)
            )
        ).all()
        if not reqs:
            raise HTTPException(400, "没有定稿方案，无法做设计-代码漂移对比")
        designs = [{"code": r.code, "content": r.plan_md} for r in reqs]

    env = build_claude_env(
        resolve_llm(None, group),
        team_id=project.am_team_id or group.am_team_id,
        agent_id=project.am_agent_id,
        user_key=am_user_key(None),
        conversation_id=gen_session_id(),
    )
    rep = EgmReport(project_id=project.id, kind=kind, status="running")
    db.add(rep)
    await db.flush()
    payload = {
        "project_id": project.id,
        "egm_job_id": rep.id,
        "kind": kind,
        "designs": designs,
        "git_http_url": f"{group.gitlab_base_url.rstrip('/')}/{project.path}.git",
        "default_branch": project.default_branch,
    }
    task = await enqueue(
        db,
        type="egm_job",
        payload=payload,
        secrets={"claude_env": env, "git_token": token},
        status="queued",
    )
    rep.runner_task_id = task.id
    await db.commit()
    return rep


async def egm_drift_sweep(db: AsyncSession) -> int:
    """每周自动漂移：近 30 天有定稿方案、7 天内没跑过 drift 的项目各投一次。"""
    if not get_settings().egm_auto_drift:
        return 0
    project_ids = (
        await db.scalars(
            select(Requirement.project_id)
            .where(
                Requirement.plan_md.is_not(None),
                Requirement.updated_at >= utcnow() - timedelta(days=30),
            )
            .distinct()
        )
    ).all()
    queued = 0
    for pid in project_ids:
        project = await db.get(Project, pid)
        if not project:
            continue
        recent = await db.scalar(
            select(EgmReport.id)
            .where(
                EgmReport.project_id == pid,
                EgmReport.kind == "drift",
                EgmReport.created_at >= utcnow() - timedelta(days=7),
            )
            .limit(1)
        )
        if recent:
            continue
        try:
            await enqueue_project_egm_job(db, project, "drift")
            queued += 1
        except HTTPException as e:
            await db.rollback()
            log.info("项目 %s 自动漂移跳过：%s", pid, e.detail)
        except Exception:  # noqa: BLE001 单项目失败不拖垮整轮
            await db.rollback()
            log.exception("项目 %s 自动漂移投递失败", pid)
    return queued


@router.get("/{project_id}/egm-reports", response_model=list[EgmReportOut])
async def list_egm_reports(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[dict]:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    rows = (
        await db.scalars(
            select(EgmReport)
            .where(EgmReport.project_id == project_id)
            .order_by(EgmReport.id.desc())
            .limit(20)
        )
    ).all()
    return [_report_out(r) for r in rows]


@router.delete("/{project_id}")
async def delete_project(
    project_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> dict:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    await db.delete(project)
    await db.commit()
    return {"ok": True}
