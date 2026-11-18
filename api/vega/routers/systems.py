from __future__ import annotations

import logging
from datetime import timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person, require_admin
from ..config import get_settings
from ..crypto import decrypt
from ..db import get_db
from ..gitlab import GitlabClient, client_ctx
from ..llm import am_user_key, build_claude_env, resolve_llm
from ..models import (
    DevGroup,
    EgmReport,
    Person,
    Requirement,
    System,
    SystemOwner,
    SystemPM,
    SystemQaOwner,
    utcnow,
)
from ..numbering import gen_session_id
from ..person_links import person_ids, set_persons
from ..queue import enqueue
from ..schemas import EgmJobIn, EgmReportOut, SystemIn, SystemOut, SystemUpd

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/systems", tags=["systems"])


def system_to_out(
    p: System,
    tech_lead_ids: list[int],
    owner_ids: list[int],
    qa_owner_ids: list[int],
) -> dict:
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
        "qa_owner_person_ids": qa_owner_ids,
        "audit_prefixes": p.audit_prefixes,
        "auto_approve_prefixes": p.auto_approve_prefixes,
        "deploy_env_map": p.deploy_env_map,
        "deploy_refs": p.deploy_refs,
        "am_team_id": p.am_team_id,
        "am_agent_id": p.am_agent_id,
    }


@router.get("", response_model=list[SystemOut])
async def list_projects(
    group_id: int | None = None,
    project_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[SystemOut]:
    q = select(System).order_by(System.id)
    if group_id:
        q = q.where(System.group_id == group_id)
    if project_id:
        q = q.where(
            System.group_id.in_(select(DevGroup.id).where(DevGroup.project_id == project_id))
        )
    rows = (await db.scalars(q)).all()
    out = []
    for p in rows:
        out.append(
            system_to_out(
                p,
                await person_ids(db, SystemPM, system_id=p.id),
                await person_ids(db, SystemOwner, system_id=p.id),
                await person_ids(db, SystemQaOwner, system_id=p.id),
            )
        )
    return out


@router.post("", response_model=SystemOut)
async def create_project(
    body: SystemIn, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> SystemOut:
    dup = await db.scalar(
        select(System).where(
            System.group_id == body.group_id, System.gitlab_project_id == body.gitlab_project_id
        )
    )
    if dup:
        raise HTTPException(400, "该系统已在该研发组下")
    if body.run_egm_bootstrap:
        group = await db.get(DevGroup, body.group_id)
        if not group or not (
            group.gitlab_base_url and decrypt(get_settings().secret_key, group.gitlab_token_enc)
        ):
            raise HTTPException(400, "研发组未配置 GitLab，无法自动生成 EGM 文档树")
    project = System(
        group_id=body.group_id,
        gitlab_project_id=body.gitlab_project_id,
        path=body.path,
        name=body.name,
        code_prefix=body.code_prefix.upper(),
        default_branch=body.default_branch,
        mr_target_branch=body.mr_target_branch,
        audit_prefixes=body.audit_prefixes,
        auto_approve_prefixes=body.auto_approve_prefixes,
        deploy_env_map=body.deploy_env_map,
        deploy_refs=body.deploy_refs,
    )
    db.add(project)
    await db.flush()
    await set_persons(db, SystemPM, body.tech_lead_person_ids, "技术负责人", system_id=project.id)
    await set_persons(db, SystemOwner, body.owner_person_ids, "系统负责人", system_id=project.id)
    await set_persons(
        db, SystemQaOwner, body.qa_owner_person_ids, "测试负责人", system_id=project.id
    )
    await db.commit()
    await db.refresh(project)
    out = system_to_out(
        project,
        body.tech_lead_person_ids,
        body.owner_person_ids,
        body.qa_owner_person_ids,
    )
    if body.run_egm_bootstrap:
        # 项目已建成：投递失败不能让建项目失败，最多日志里留一笔
        try:
            await enqueue_project_egm_job(db, project, "bootstrap", admin)
            log.info("项目 %s 创建后自动投递 EGM 文档树", project.id)
        except HTTPException as e:
            log.warning("项目 %s 自动文档树跳过：%s", project.id, e.detail)
        except Exception:  # noqa: BLE001
            await db.rollback()
            log.exception("项目 %s 自动文档树投递失败", project.id)
    return out


@router.put("/{system_id}", response_model=SystemOut)
async def update_project(
    system_id: int,
    body: SystemUpd,
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> SystemOut:
    project = await db.get(System, system_id)
    if not project:
        raise HTTPException(404, "系统不存在")
    data = body.model_dump(exclude_unset=True)
    for k in ("path", "name", "default_branch", "mr_target_branch"):
        if data.get(k):
            setattr(project, k, data[k])
    if data.get("code_prefix"):
        project.code_prefix = data["code_prefix"].upper()
    if data.get("tech_lead_person_ids") is not None:
        await set_persons(db, SystemPM, data["tech_lead_person_ids"], "技术负责人", system_id=project.id)
    if data.get("owner_person_ids") is not None:
        await set_persons(db, SystemOwner, data["owner_person_ids"], "系统负责人", system_id=project.id)
    if data.get("qa_owner_person_ids") is not None:
        await set_persons(
            db, SystemQaOwner, data["qa_owner_person_ids"], "测试负责人", system_id=project.id
        )
    # 前缀两兄弟：显式非 None 才更新（空数组 = 清空配置，None = 不动）
    for k in (
        "audit_prefixes",
        "auto_approve_prefixes",
        "deploy_env_map",
        "deploy_refs",
        "am_team_id",
        "am_agent_id",
    ):
        if data.get(k) is not None:
            setattr(project, k, data[k])
    await db.commit()
    return system_to_out(
        project,
        await person_ids(db, SystemPM, system_id=project.id),
        await person_ids(db, SystemOwner, system_id=project.id),
        await person_ids(db, SystemQaOwner, system_id=project.id),
    )


@router.get("/{system_id}/gitlab-environments")
async def list_gitlab_environments(
    system_id: int,
    db: AsyncSession = Depends(get_db),
    _me: Person = Depends(current_person),
) -> list[str]:
    """读 GitLab 项目环境名，供「部署触发测试」的环境映射选择。"""
    project = await db.get(System, system_id)
    if not project:
        raise HTTPException(404, "系统不存在")
    group = await db.get(DevGroup, project.group_id)
    token = decrypt(get_settings().secret_key, group.gitlab_token_enc) if group else None
    if not group or not token:
        return []
    gl = GitlabClient(group.gitlab_base_url, token)
    try:
        async with client_ctx() as client:
            rows = await gl.list_environments(client, project.gitlab_project_id)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"读取 GitLab 环境失败：{e}") from e
    return [r["name"] for r in rows if r.get("name")]


# ---------- EGM 项目级任务 ----------


def _report_out(r: EgmReport) -> dict:
    return {
        "id": r.id,
        "system_id": r.system_id,
        "kind": r.kind,
        "status": r.status,
        "report_md": r.report_md,
        "branch": r.branch,
        "mr_url": r.mr_url,
        "created_at": str(r.created_at) if r.created_at else None,
        "updated_at": str(r.updated_at) if r.updated_at else None,
    }


async def _require_egm_writer(db: AsyncSession, me: Person, system_id: int) -> None:
    if me.is_admin:
        return
    ids = await person_ids(db, SystemOwner, system_id=system_id) + await person_ids(
        db, SystemPM, system_id=system_id
    )
    if me.id not in ids:
        raise HTTPException(403, "只有系统负责人或技术负责人可操作")


@router.post("/{system_id}/egm-jobs", response_model=EgmReportOut)
async def create_egm_job(
    system_id: int,
    body: EgmJobIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """项目级 EGM 任务：bootstrap 生成 README 树（推分支 + MR），drift 出设计-代码漂移报告。"""
    project = await db.get(System, system_id)
    if not project:
        raise HTTPException(404, "系统不存在")
    await _require_egm_writer(db, me, project.id)
    rep = await enqueue_project_egm_job(db, project, body.kind, me)
    return _report_out(rep)


async def enqueue_project_egm_job(
    db: AsyncSession, project: System, kind: str, person: Person | None = None
) -> EgmReport:
    """建报告并入队项目级 EGM 任务；手动按钮与每周自动漂移共用。

    person 为发起人：手动任务用发起人的 LLM / agent-memory key，自动任务不传（组 → 兜底）。
    """
    group = await db.get(DevGroup, project.group_id)
    token = decrypt(get_settings().secret_key, group.gitlab_token_enc)
    if not (group.gitlab_base_url and token):
        raise HTTPException(400, "研发组未配置 GitLab，EGM 系统级任务需要仓库")
    running = await db.scalar(
        select(EgmReport.id)
        .where(EgmReport.system_id == project.id, EgmReport.status == "running")
        .limit(1)
    )
    if running:
        raise HTTPException(400, "该系统已有 EGM 任务在跑，请等它结束")

    designs: list[dict] = []
    if kind == "drift":
        reqs = (
            await db.scalars(
                select(Requirement)
                .where(Requirement.system_id == project.id, Requirement.plan_md.is_not(None))
                .order_by(Requirement.id.desc())
                .limit(10)
            )
        ).all()
        if not reqs:
            raise HTTPException(400, "没有定稿方案，无法做设计-代码漂移对比")
        designs = [{"code": r.code, "content": r.plan_md} for r in reqs]

    env = build_claude_env(
        resolve_llm(person, group),
        team_id=project.am_team_id or group.am_team_id,
        agent_id=project.am_agent_id,
        user_key=am_user_key(person),
        conversation_id=gen_session_id(),
    )
    rep = EgmReport(system_id=project.id, kind=kind, status="running")
    db.add(rep)
    await db.flush()
    payload = {
        "system_id": project.id,
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
            select(Requirement.system_id)
            .where(
                Requirement.plan_md.is_not(None),
                Requirement.updated_at >= utcnow() - timedelta(days=30),
            )
            .distinct()
        )
    ).all()
    queued = 0
    for pid in project_ids:
        project = await db.get(System, pid)
        if not project:
            continue
        recent = await db.scalar(
            select(EgmReport.id)
            .where(
                EgmReport.system_id == pid,
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


@router.get("/{system_id}/egm-reports", response_model=list[EgmReportOut])
async def list_egm_reports(
    system_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[dict]:
    project = await db.get(System, system_id)
    if not project:
        raise HTTPException(404, "系统不存在")
    rows = (
        await db.scalars(
            select(EgmReport)
            .where(EgmReport.system_id == system_id)
            .order_by(EgmReport.id.desc())
            .limit(20)
        )
    ).all()
    return [_report_out(r) for r in rows]


@router.delete("/{system_id}/egm-reports/{report_id}")
async def delete_egm_report(
    system_id: int,
    report_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """删一条报告：执行中的不能删（结果要回写），runner_task / GitLab 分支不动。"""
    project = await db.get(System, system_id)
    if not project:
        raise HTTPException(404, "系统不存在")
    await _require_egm_writer(db, me, system_id)
    rep = await db.get(EgmReport, report_id)
    if not rep or rep.system_id != system_id:
        raise HTTPException(404, "报告不存在")
    if rep.status == "running":
        raise HTTPException(409, "任务还在执行，结束后再删")
    await db.delete(rep)
    await db.commit()
    return {"ok": True}


@router.delete("/{system_id}/egm-reports")
async def clear_egm_reports(
    system_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    project = await db.get(System, system_id)
    if not project:
        raise HTTPException(404, "系统不存在")
    await _require_egm_writer(db, me, system_id)
    running = await db.scalar(
        select(EgmReport.id)
        .where(EgmReport.system_id == system_id, EgmReport.status == "running")
        .limit(1)
    )
    if running:
        raise HTTPException(409, "有任务还在执行，结束后再清空")
    rows = (await db.scalars(select(EgmReport).where(EgmReport.system_id == system_id))).all()
    for r in rows:
        await db.delete(r)
    await db.commit()
    return {"deleted": len(rows)}


@router.delete("/{system_id}")
async def delete_project(
    system_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> dict:
    project = await db.get(System, system_id)
    if not project:
        raise HTTPException(404, "系统不存在")
    await db.delete(project)
    await db.commit()
    return {"ok": True}
