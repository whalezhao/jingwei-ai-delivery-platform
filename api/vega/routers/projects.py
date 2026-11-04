"""项目（顶层）：公司按项目分割团队，项目下挂多个研发组。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import current_person, require_admin
from ..db import get_db
from ..models import DevGroup, NotifyLog, Person, Project, ProjectLeader
from ..person_links import person_ids, set_persons
from ..schemas import ProjectIn, ProjectOut, ProjectUpd

router = APIRouter(prefix="/api/projects", tags=["projects"])


async def _project_to_out(db: AsyncSession, p: Project) -> dict:
    groups = (await db.scalars(select(DevGroup.id).where(DevGroup.project_id == p.id))).all()
    return {
        "id": p.id,
        "name": p.name,
        "code": p.code,
        "octo_group_no": p.octo_group_no,
        "leader_person_ids": await person_ids(db, ProjectLeader, project_id=p.id),
        "group_ids": list(groups),
    }


@router.get("", response_model=list[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> list[ProjectOut]:
    rows = (await db.scalars(select(Project).order_by(Project.id))).all()
    return [await _project_to_out(db, p) for p in rows]


@router.post("", response_model=ProjectOut)
async def create_project(
    body: ProjectIn, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> ProjectOut:
    if not body.name.strip():
        raise HTTPException(400, "项目名不能为空")
    if await db.scalar(select(Project).where(Project.name == body.name.strip())):
        raise HTTPException(400, "项目名已存在")
    project = Project(name=body.name.strip(), code=body.code, octo_group_no=body.octo_group_no)
    db.add(project)
    await db.flush()
    await set_persons(db, ProjectLeader, body.leader_person_ids, "项目总负责人", project_id=project.id)
    await db.commit()
    return await _project_to_out(db, project)


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
    if data.get("name"):
        name = data["name"].strip()
        dup = await db.scalar(select(Project).where(Project.name == name, Project.id != project.id))
        if dup:
            raise HTTPException(400, "项目名已存在")
        project.name = name
    for k in ("code", "octo_group_no"):
        if k in data:
            setattr(project, k, data[k])
    if data.get("leader_person_ids") is not None:
        await set_persons(db, ProjectLeader, data["leader_person_ids"], "项目总负责人", project_id=project.id)
    await db.commit()
    return await _project_to_out(db, project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> dict:
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    # 外键没有级联：先解绑，避免 PG 直接 500
    bound = await db.scalar(select(DevGroup.id).where(DevGroup.project_id == project.id).limit(1))
    if bound:
        raise HTTPException(400, "项目下还有研发组，先解绑再删")
    # 通知留痕与负责人关联没有级联：先清掉/置空，避免外键把删除挡成 500
    await db.execute(update(NotifyLog).where(NotifyLog.project_id == project.id).values(project_id=None))
    await db.execute(delete(ProjectLeader).where(ProjectLeader.project_id == project.id))
    await db.delete(project)
    await db.commit()
    return {"ok": True}