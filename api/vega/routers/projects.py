from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person, require_admin
from ..db import get_db
from ..models import Person, Project, ProjectOwner, ProjectPM
from ..person_links import person_ids, set_persons
from ..schemas import ProjectIn, ProjectOut, ProjectUpd

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
    return project_to_out(project, body.tech_lead_person_ids, body.owner_person_ids)


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
