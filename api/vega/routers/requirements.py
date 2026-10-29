from __future__ import annotations

import asyncio
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..auth import current_person
from ..bus import bus
from ..config import get_settings
from ..crypto import decrypt
from ..db import get_db, sessionmaker
from ..llm import am_user_key, build_claude_env, resolve_llm
from ..models import (
    DevGroup,
    DevTask,
    DevTaskEvent,
    GroupMember,
    Person,
    PlanEvent,
    PlanMessage,
    PlanVersion,
    Project,
    Requirement,
    RequirementDoc,
    RunnerNode,
    RunnerTask,
    utcnow,
)
from ..numbering import gen_session_id, next_requirement_code, next_task_code
from ..queue import enqueue
from ..schemas import (
    DevTaskOut,
    PlanClearIn,
    PlanMessageIn,
    PlanVersionIn,
    RequirementDocIn,
    RequirementIn,
    RequirementOut,
    RequirementTagsIn,
    RequirementUpdate,
)
from . import person_to_out

router = APIRouter(prefix="/api/requirements", tags=["requirements"])


def _split_tags(raw: str | None) -> list[str]:
    return [t for t in (raw or "").split(",") if t]


def _join_tags(tags: list[str]) -> str | None:
    """去空白、去重、单个 ≤32 字符、最多 10 个。逗号是存储分隔符，含逗号的标签会被拆开。"""
    clean: list[str] = []
    for t in tags:
        t = " ".join(str(t).split())[:32]
        if t and t not in clean:
            clean.append(t)
    return ",".join(clean[:10]) or None


def req_to_out(
    r: Requirement,
    claimant: Person | None = None,
    project: Project | None = None,
    proposer: Person | None = None,
) -> dict:
    return {
        "id": r.id,
        "project_id": r.project_id,
        "project_name": project.name if project else None,
        "code": r.code,
        "title": r.title,
        "description": r.description,
        "status": r.status,
        # 迭代中 → 已上线自动转正式；不落库，避免与 status 不一致
        "iteration_status": "formal" if r.status == "online" else "iterating",
        "tags": _split_tags(r.tags),
        "claimant_id": r.claimant_id,
        "claimant_name": claimant.name if claimant else None,
        "proposer_id": r.proposer_id,
        "proposer_name": proposer.name if proposer else None,
        "plan_md": r.plan_md,
        "created_at": str(r.created_at) if r.created_at else None,
    }


async def _get_req(db: AsyncSession, req_id: int) -> Requirement:
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "需求不存在")
    return req


def _can_claim(req: Requirement, me: Person, has_final_doc: bool) -> bool:
    """当前登录人此刻能不能认领：池中 + 该阶段对应的角色（管理员不设限）。"""
    if req.status != "pool":
        return False
    if me.is_admin:
        return True
    return ("dev" if has_final_doc else "product") in (me.roles or [])


def _can_open(req: Requirement, me: Person) -> bool:
    """详情可见性：被认领之后只有认领人自己能打开。

    未认领的不锁（池中谁都能看才谈得上认领）；但已由产品定稿移交、等待开发接手时
    只对开发开放——产品此时已交出认领权，不应该还能翻详情。
    """
    if me.is_admin or req.claimant_id == me.id:
        return True
    if req.claimant_id is None:
        return req.proposer_id is None or "dev" in (me.roles or [])
    return False


async def _get_req_open(db: AsyncSession, req_id: int, me: Person) -> Requirement:
    req = await _get_req(db, req_id)
    if not _can_open(req, me):
        raise HTTPException(403, "需求已被他人认领，无权查看详情")
    return req


@router.get("", response_model=list[RequirementOut])
async def list_requirements(
    project_id: int | None = None,
    group_id: int | None = None,
    status: str | None = None,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[RequirementOut]:
    q = select(Requirement).order_by(Requirement.id.desc())
    if project_id:
        q = q.where(Requirement.project_id == project_id)
    if group_id:
        q = q.join(Project, Requirement.project_id == Project.id).where(Project.group_id == group_id)
    if status:
        q = q.where(Requirement.status == status)
    rows = (await db.scalars(q)).all()
    projects = {
        p.id: p
        for p in (
            await db.scalars(select(Project).where(Project.id.in_([r.project_id for r in rows])))
        ).all()
    }
    # 一次取全量定稿文档的需求 id，避免 N 次查
    finalized = set(
        await db.scalars(
            select(RequirementDoc.requirement_id).where(RequirementDoc.is_final.is_(True))
        )
    )
    out = []
    for r in rows:
        claimant = await db.get(Person, r.claimant_id) if r.claimant_id else None
        proposer = await db.get(Person, r.proposer_id) if r.proposer_id else None
        # 列表照旧全量可见（只读摘要）；can_open / can_claim 告诉前端能不能点进详情、能不能认领
        out.append(
            {
                **req_to_out(r, claimant, projects.get(r.project_id), proposer),
                "can_open": _can_open(r, me),
                "can_claim": _can_claim(r, me, r.id in finalized),
            }
        )
    return out


@router.get("/{req_id}", response_model=RequirementOut)
async def get_requirement(
    req_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> RequirementOut:
    req = await _get_req_open(db, req_id, me)
    claimant = await db.get(Person, req.claimant_id) if req.claimant_id else None
    proposer = await db.get(Person, req.proposer_id) if req.proposer_id else None
    project = await db.get(Project, req.project_id)
    return {
        **req_to_out(req, claimant, project, proposer),
        "can_open": True,
        "can_claim": _can_claim(req, me, await _has_final_doc(db, req.id)),
    }


@router.post("", response_model=RequirementOut)
async def create_requirement(
    body: RequirementIn, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> RequirementOut:
    project = await db.get(Project, body.project_id)
    if not project:
        raise HTTPException(404, "项目不存在")
    code = await next_requirement_code(db, project.code_prefix)
    req = Requirement(
        project_id=project.id,
        code=code,
        title=body.title,
        description=body.description,
    )
    db.add(req)
    await db.commit()
    await db.refresh(req)
    # 新建的需求必在池中、必无定稿文档：能不能认领只看角色
    return {**req_to_out(req), "can_claim": _can_claim(req, me, False)}


@router.put("/{req_id}", response_model=RequirementOut)
async def update_requirement(
    req_id: int,
    body: RequirementUpdate,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> RequirementOut:
    req = await _get_req(db, req_id)
    if req.status in ("confirmed", "archived", "online"):
        raise HTTPException(400, "需求已定稿，进入开发阶段，不能再修改")
    # 文档已定稿、等开发承接（claimant 已空出）时同样不许改：需求内容已定
    if req.claimant_id != me.id and (req.claimant_id is not None or req.proposer_id is not None) and not me.is_admin:
        raise HTTPException(403, "只有认领人或管理员可修改需求")

    data = body.model_dump(exclude_unset=True)
    if "title" in data:
        title = (data.pop("title") or "").strip()
        if not title:
            raise HTTPException(400, "标题不能为空")
        req.title = title
    if "description" in data:
        req.description = (data.pop("description") or "").strip() or None

    await db.commit()
    await db.refresh(req)
    claimant = await db.get(Person, req.claimant_id) if req.claimant_id else None
    project = await db.get(Project, req.project_id)
    return req_to_out(req, claimant, project)


@router.put("/{req_id}/tags", response_model=RequirementOut)
async def update_requirement_tags(
    req_id: int,
    body: RequirementTagsIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> RequirementOut:
    """迭代版本标签：已定稿/已上线的需求也能改（发布后仍要按版本归档）。"""
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and (req.claimant_id is not None or req.proposer_id is not None) and not me.is_admin:
        raise HTTPException(403, "只有认领人或管理员可修改标签")
    req.tags = _join_tags(body.tags)
    await db.commit()
    await db.refresh(req)
    claimant = await db.get(Person, req.claimant_id) if req.claimant_id else None
    project = await db.get(Project, req.project_id)
    return req_to_out(req, claimant, project)


@router.delete("/{req_id}")
async def delete_requirement(
    req_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> dict:
    """删除需求及其全部过程数据（文档、方案、会话、事件、任务）。

    只允许删还没进开发流程的：任务一旦推到 GitLab 就带着 MR 关联，删了需求会让
    webhook 按 (project, mr_iid) 找不到 DevTask 而静默 return，那条 MR 永远毕不了业。
    """
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and (req.claimant_id is not None or req.proposer_id is not None) and not me.is_admin:
        raise HTTPException(403, "只有认领人或管理员可删除需求")

    tasks = (await db.scalars(select(DevTask).where(DevTask.requirement_id == req.id))).all()
    if any(t.mr_iid or t.status not in ("pending", "failed") for t in tasks):
        raise HTTPException(400, "需求下已有任务进入开发/评审流程，不能删除")

    running = await db.scalar(
        select(RunnerTask.id)
        .where(
            RunnerTask.requirement_id == req.id,
            RunnerTask.status.in_(("queued", "claimed", "running")),
        )
        .limit(1)
    )
    if running:
        raise HTTPException(400, "本轮还在生成，请先停止再删除")

    # 子表没有 ondelete cascade，且 dev_task.requirement_id 是 NOT NULL，逐个显式删更可控
    task_ids = [t.id for t in tasks]
    msg_ids = list(await db.scalars(select(PlanMessage.id).where(PlanMessage.requirement_id == req.id)))
    if task_ids:
        await db.execute(delete(DevTaskEvent).where(DevTaskEvent.dev_task_id.in_(task_ids)))
        await db.execute(delete(DevTask).where(DevTask.id.in_(task_ids)))
    # plan_event 同时挂 requirement 和 runner_task：按任一维度删都会漏，先按需求删干净
    await db.execute(delete(PlanEvent).where(PlanEvent.requirement_id == req.id))
    if msg_ids:
        await db.execute(delete(PlanMessage).where(PlanMessage.id.in_(msg_ids)))
    await db.execute(delete(PlanVersion).where(PlanVersion.requirement_id == req.id))
    await db.execute(delete(RequirementDoc).where(RequirementDoc.requirement_id == req.id))
    await db.execute(delete(RunnerTask).where(RunnerTask.requirement_id == req.id))
    await db.delete(req)
    await db.commit()
    return {"ok": True, "code": req.code}


@router.post("/{req_id}/docs")
async def upload_doc(
    req_id: int,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    req = await _get_req(db, req_id)
    # 池中没人认领过的需求允许直接上传（录需求的一种方式）；已被认领或已由产品定稿
    # 移交出去之后，只能认领人自己传——否则别人可以往别人手里的需求塞文档
    if req.claimant_id != me.id and (req.claimant_id is not None or req.proposer_id is not None) and not me.is_admin:
        raise HTTPException(403, "只有认领人可上传需求文档")
    name = (file.filename or "doc").replace("\\", "/").rsplit("/", 1)[-1]
    if not name.endswith((".md", ".txt", ".markdown", ".docx")):
        raise HTTPException(400, "仅支持 .md / .txt / .docx")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "空文件")
    nxt = (await db.scalar(select(func.max(RequirementDoc.version)).where(RequirementDoc.requirement_id == req.id)) or 0) + 1
    first = nxt == 1
    db.add(
        RequirementDoc(
            requirement_id=req.id, filename=name, content=raw, version=nxt, is_final=first
        )
    )
    # 第一版即定稿（上传原型就是定稿需求）：同样要把认领权交给开发，
    # 否则会出现「有定稿文档但认领人仍是产品」的中间态，谁都走不下去
    if first:
        await _hand_over_to_dev(db, req)
    await db.commit()
    return {"ok": True, "filename": name, "size": len(raw), "version": nxt, "is_final": first}


@router.post("/{req_id}/docs/{doc_id}/finalize")
async def finalize_doc(
    req_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """把某个版本定为需求定稿版本（同需求唯一，其余自动取消）。

    定稿 = 产品这一段交卷：认领权交还需求池，等开发重新认领做方案。产品留痕为
    「需求提出人」，此后不再能打开详情。身兼开发的定稿人不必来回交接，认领权留在自己手上。
    """
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有认领人可定稿文档")
    if req.status in ("confirmed", "archived", "online"):
        raise HTTPException(400, "需求已定稿")
    doc = await db.get(RequirementDoc, doc_id)
    if not doc or doc.requirement_id != req_id:
        raise HTTPException(404, "文档不存在")
    rows = (await db.scalars(select(RequirementDoc).where(RequirementDoc.requirement_id == req_id))).all()
    for d in rows:
        d.is_final = d.id == doc_id
    await _hand_over_to_dev(db, req)
    await db.commit()
    return {"ok": True, "final_version": doc.version}


async def _hand_over_to_dev(db: AsyncSession, req: Requirement) -> None:
    """需求文档定稿后把认领权交还需求池（只有开发能再认领）。"""
    claimant = await db.get(Person, req.claimant_id) if req.claimant_id else None
    if not claimant or claimant.is_admin or "dev" in (claimant.roles or []):
        return
    req.proposer_id = req.proposer_id or claimant.id
    req.claimant_id = None
    req.status = "pool"


@router.post("/{req_id}/docs/{doc_id}/unfinalize")
async def unfinalize_doc(
    req_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """撤回需求文档定稿：仅在方案未定稿（pool / claimed）时可回退，回到需求讨论。

    定稿后认领权已交还池子：提出人自己（或管理员）可以撤回，撤回即把认领权还给他——
    需求讨论本来就是产品那一段的活。
    """
    req = await _get_req(db, req_id)
    if me.id not in (req.claimant_id, req.proposer_id) and not me.is_admin:
        raise HTTPException(403, "只有认领人或提出人可撤回定稿")
    if req.status not in ("pool", "claimed"):
        raise HTTPException(400, "方案已定稿，需求文档不能撤回")
    doc = await db.get(RequirementDoc, doc_id)
    if not doc or doc.requirement_id != req_id:
        raise HTTPException(404, "文档不存在")
    rows = (await db.scalars(select(RequirementDoc).where(RequirementDoc.requirement_id == req_id))).all()
    for d in rows:
        d.is_final = False
    if req.proposer_id:
        req.claimant_id, req.proposer_id, req.status = req.proposer_id, None, "claimed"
    await db.commit()
    return {"ok": True}


@router.post("/{req_id}/docs/from-ai")
async def create_doc_from_ai(
    req_id: int,
    body: RequirementDocIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """人点「采用为文档」：把需求讨论里 AI 给出的正文落成新版本（非定稿）。"""
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有认领人可采用文档")
    if req.status in ("confirmed", "archived", "online"):
        raise HTTPException(400, "需求已定稿")
    if await _has_final_doc(db, req.id):
        raise HTTPException(400, "需求文档已定稿，不能再修改")
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "文档内容为空")
    raw = content.encode()
    docs = (
        await db.scalars(
            select(RequirementDoc)
            .where(RequirementDoc.requirement_id == req.id)
            .order_by(RequirementDoc.version)
        )
    ).all()
    latest = docs[-1] if docs else None
    # 连点两下且内容与最新版完全相同：复用该版，不重复快照
    if latest and latest.content == raw:
        return {
            "id": latest.id,
            "filename": latest.filename,
            "version": latest.version,
            "is_final": latest.is_final,
            "created": False,
        }
    nxt = (latest.version if latest else 0) + 1
    # AI 产出的是 Markdown：统一 .md 后缀，预览/节点解析都走文本路径
    stem = latest.filename.rsplit(".", 1)[0] if latest else "需求文档"
    filename = f"{stem}.md"
    doc = RequirementDoc(
        requirement_id=req.id, filename=filename, content=raw, version=nxt, is_final=False
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return {
        "id": doc.id,
        "filename": filename,
        "version": nxt,
        "is_final": False,
        "created": True,
    }


@router.get("/{req_id}/docs/{doc_id}/download")
async def download_doc(
    req_id: int,
    doc_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> Response:
    """UI 下载/预览原始字节（docx 预览靠它在前端转 html）。"""
    await _get_req_open(db, req_id, me)
    doc = await db.get(RequirementDoc, doc_id)
    if not doc or doc.requirement_id != req_id:
        raise HTTPException(404, "文档不存在")
    from urllib.parse import quote

    return Response(
        doc.content,
        media_type="application/octet-stream",
        headers={"content-disposition": f"attachment; filename*=UTF-8''{quote(doc.filename)}"},
    )


@router.get("/{req_id}/docs")
async def list_docs(
    req_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> list[dict]:
    await _get_req_open(db, req_id, me)
    rows = (
        await db.scalars(
            select(RequirementDoc).where(RequirementDoc.requirement_id == req_id).order_by(RequirementDoc.id)
        )
    ).all()
    return [
        {
            "id": d.id,
            "filename": d.filename,
            "size": len(d.content),
            "version": d.version,
            "is_final": d.is_final,
        }
        for d in rows
    ]


@router.get("/{req_id}/docs/{doc_id}")
async def get_doc(
    req_id: int, doc_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> dict:
    await _get_req_open(db, req_id, me)
    doc = await db.get(RequirementDoc, doc_id)
    if not doc or doc.requirement_id != req_id:
        raise HTTPException(404, "文档不存在")
    # 文本格式给预览；docx 是原始字节，预览无意义（claude 在节点上自行解析）
    if doc.filename.endswith((".md", ".txt", ".markdown")):
        content = doc.content.decode("utf-8", errors="replace")
    else:
        content = ""
    return {"id": doc.id, "filename": doc.filename, "content": content}


@router.get("/{req_id}/plan-versions")
async def list_plan_versions(
    req_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> list[dict]:
    """方案版本史：人在对话里点「采用此方案」才成版；finalize 标记定稿。"""
    await _get_req_open(db, req_id, me)
    rows = (
        await db.scalars(
            select(PlanVersion).where(PlanVersion.requirement_id == req_id).order_by(PlanVersion.id)
        )
    ).all()
    return [
        {
            "id": v.id,
            "version": v.version,
            "is_final": v.is_final,
            "created_at": str(v.created_at),
            "content": v.content,
        }
        for v in rows
    ]


@router.post("/{req_id}/plan-versions")
async def create_plan_version(
    req_id: int,
    body: PlanVersionIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """人点「采用此方案」：把对话里标记块的正文落成一个方案版本。

    连点两下且内容与最新版完全相同（且未定稿）时复用同一版，不重复快照。
    """
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有认领人可采用方案")
    if req.status in ("confirmed", "archived", "online"):
        raise HTTPException(400, "需求已定稿")
    content = body.content.strip()
    if not content:
        raise HTTPException(400, "方案内容为空")
    latest = await db.scalar(
        select(PlanVersion)
        .where(PlanVersion.requirement_id == req.id)
        .order_by(PlanVersion.version.desc())
        .limit(1)
    )
    if latest and latest.content == content and not latest.is_final:
        return {"id": latest.id, "version": latest.version, "created": False}
    nxt = (await db.scalar(select(func.max(PlanVersion.version)).where(PlanVersion.requirement_id == req.id)) or 0) + 1
    version = PlanVersion(requirement_id=req.id, version=nxt, content=content)
    db.add(version)
    await db.commit()
    await db.refresh(version)
    return {"id": version.id, "version": version.version, "created": True}


@router.post("/{req_id}/claim", response_model=RequirementOut)
async def claim(
    req_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> RequirementOut:
    req = await _get_req(db, req_id)
    if req.status not in ("pool",):
        raise HTTPException(400, f"需求当前状态 {req.status} 不可认领")
    project = await db.get(Project, req.project_id)
    gm = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == project.group_id, GroupMember.person_id == me.id)
    )
    if not gm and not me.is_admin:
        raise HTTPException(403, "只有该研发组成员可认领")

    # 两段式：需求文档没定稿前是产品的活（写需求），定稿之后交回池子给开发（做方案开发）
    if not me.is_admin:
        need, why = (
            ("dev", "需求已定稿，只能由开发认领")
            if await _has_final_doc(db, req.id)
            else ("product", "需求未定稿，只能由产品认领")
        )
        if need not in (me.roles or []):
            raise HTTPException(403, why)

    req.claimant_id = me.id
    req.status = "claimed"
    await db.commit()
    return req_to_out(req, me)


# ---------- plan 会话 ----------


_PLAN_RULE = """[输出约定]
1) 给出方案时，把方案正文（完整、可独立阅读的 Markdown）放在下面两行标记之间，标记各占一行：
<!-- vega:plan:begin -->
…方案内容…
<!-- vega:plan:end -->
2) 需要用户在几个选项之间做决定时（只在真正需要拍板时），输出一个提问块，选项 2~4 个：
<!-- vega:ask:begin -->
{"question": "要问的问题", "options": [{"label": "选项名", "desc": "一句说明"}]}
<!-- vega:ask:end -->
需求文档已定稿，本轮只讨论方案，不再修改需求文档。
讨论、确认、反问不要写进方案标记；本轮没有方案/问题就不要写对应标记。"""


_DOC_RULE = """[输出约定]
1) 这轮讨论需求本身。需要给出/修订需求文档时，把完整的需求文档正文（可独立阅读的 Markdown）放在下面两行标记之间，标记各占一行：
<!-- vega:doc:begin -->
…完整需求文档…
<!-- vega:doc:end -->
2) 需要用户在几个选项之间做决定时（只在真正需要拍板时），输出一个提问块，选项 2~4 个：
<!-- vega:ask:begin -->
{"question": "要问的问题", "options": [{"label": "选项名", "desc": "一句说明"}]}
<!-- vega:ask:end -->
讨论、反问不要写进文档标记；本轮没有给出文档就不要写标记。"""


async def _has_final_doc(db: AsyncSession, req_id: int) -> bool:
    return bool(
        await db.scalar(
            select(RequirementDoc.id)
            .where(RequirementDoc.requirement_id == req_id, RequirementDoc.is_final.is_(True))
            .limit(1)
        )
    )


async def _plan_context(db: AsyncSession, req: Requirement) -> str:
    """首轮注入的需求上下文（项目信息）。文档不注入文本——runner 会下载原始文件到节点，
    由 claude 自行读取解析（设计约定：平台只做传递，不替模型解析）。"""
    project = await db.get(Project, req.project_id)
    parts = [
        f"需求：{req.code}《{req.title}》",
        f"项目：{project.path}（默认分支 {project.default_branch}）",
    ]
    if req.description:
        parts.append(f"描述：{req.description}")
    return "\n".join(parts)


@router.post("/{req_id}/plan/messages")
async def post_plan_message(
    req_id: int,
    body: PlanMessageIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """用户发言 → 入队 plan_turn（粘性路由到首接 runner）。"""
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有认领人可对话")
    if req.status in ("confirmed", "archived", "online"):
        raise HTTPException(400, "需求已定稿")

    # 需求文档定稿前只能聊需求（AI 可改文档）；定稿后才能聊方案
    allowed = "plan" if await _has_final_doc(db, req.id) else "requirement"
    mode = body.mode or allowed
    if mode != allowed:
        raise HTTPException(
            400,
            "需求文档已定稿，请切换到方案讨论"
            if mode == "requirement"
            else "需求文档未定稿，请先讨论并定稿需求文档",
        )

    project = await db.get(Project, req.project_id)
    group = await db.get(DevGroup, project.group_id)
    gm = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group.id, GroupMember.person_id == req.claimant_id)
    )
    person = await db.get(Person, req.claimant_id)

    # 换团队/专家 = 换上下文：新开一条会话，并把该模式之前的聊天记录整体带过去
    # （历史来自已落库的消息，前端照常显示，不删不改）
    cur_team = body.team_id or None
    cur_agent = body.agent_id or None
    prev = (
        await db.execute(
            select(PlanMessage.team_id, PlanMessage.agent_id)
            .where(
                PlanMessage.requirement_id == req.id,
                PlanMessage.mode == mode,
                PlanMessage.role == "user",
            )
            .order_by(PlanMessage.id.desc())
            .limit(1)
        )
    ).first()
    switched = prev is not None and (prev[0], prev[1]) != (cur_team, cur_agent)

    # 粘住的节点被删了（换机/重建）：自愈——解绑并把两条会话一并作废，
    # 本轮从新会话重来（下面会把该模式历史整体带上）；任务不带 sticky，
    # 任何在线节点都能认领，认领时再重新固化绑定。
    node_lost = False
    if req.plan_runner_id and await db.get(RunnerNode, req.plan_runner_id) is None:
        req.plan_runner_id = None
        req.req_session_id = None
        req.plan_session_id = None
        node_lost = True

    # 两种讨论各用一条 session；团队/专家由输入框逐轮指定，留空回退项目/研发组
    if mode == "requirement":
        first = switched or not req.req_session_id
        if first:
            req.req_session_id = gen_session_id()
        session_id = req.req_session_id
        rule = _DOC_RULE
    else:
        first = switched or not req.plan_session_id
        if first:
            req.plan_session_id = gen_session_id()
        session_id = req.plan_session_id
        rule = _PLAN_RULE
    resume = not first
    team_id = cur_team or project.am_team_id or group.am_team_id
    agent_id = cur_agent or project.am_agent_id or (gm.am_agent_id if gm else None)

    # 新会话（换团队/专家、换节点、或 session 缺失）都把该模式历史整体带过去让模型接续；
    # 之后的轮次只发输入框里的单条消息。
    history = ""
    if first:
        past = (
            await db.scalars(
                select(PlanMessage)
                .where(PlanMessage.requirement_id == req.id, PlanMessage.mode == mode)
                .order_by(PlanMessage.id)
            )
        ).all()
        history = "\n\n".join(
            f"{'用户' if m.role == 'user' else 'AI'}：{m.content}" for m in past if m.content
        )

    target = resolve_llm(person, group)
    env = build_claude_env(
        target,
        team_id=team_id,
        # 团队+专家必须首轮就一起下发：proxy 的 headerAutoSelect 只有 team/agent
        # 都在账号可见列表里命中才直接注册会话；只带 team 而该团队有多个专家时，
        # proxy 会发 AskUserQuestion 让用户选专家，claude -p 里该工具被禁用，
        # 这一轮直接报错作废（换专家后的新会话同此）
        agent_id=agent_id,
        user_key=am_user_key(person),
        conversation_id=session_id,
    )
    if first:
        parts = [await _plan_context(db, req)]
        if history:
            why = "换团队/专家" if switched else "切换运行节点" if node_lost else None
            head = f"[{why}前的聊天记录，供接续]" if why else "[此前的聊天记录，供接续]"
            parts.append(f"{head}\n{history}")
        parts.append(f"用户：{body.content}")
        prompt = "\n\n---\n".join(parts)
    else:
        prompt = body.content
    # 每轮都带：续聊与 runner「会话失效重建」的重试轮共用同一 prompt
    prompt = f"{prompt}\n\n{rule}"
    all_docs = (
        await db.scalars(
            select(RequirementDoc)
            .where(RequirementDoc.requirement_id == req.id)
            .order_by(RequirementDoc.version)
        )
    ).all()
    # 方案讨论只下发定稿版；需求讨论只给最新一版（多版同名文件在节点上会互相覆盖）
    docs = [d for d in all_docs if d.is_final] if mode == "plan" else all_docs[-1:]
    # 用户消息带 mode：前端按模式隔离历史；团队/专家一并留痕，刷新后输入框恢复上次选择
    db.add(
        PlanMessage(
            requirement_id=req.id,
            role="user",
            content=body.content,
            mode=mode,
            team_id=body.team_id or None,
            agent_id=body.agent_id or None,
        )
    )

    payload = {
        "requirement_id": req.id,
        "session_id": session_id,
        "resume": resume,
        "prompt": prompt,
        "mode": mode,
        "docs": [{"id": d.id, "filename": d.filename} for d in docs],
    }
    if req.plan_runner_id:
        payload["sticky_runner_id"] = req.plan_runner_id

    # 只读问答跑在项目基线目录（worktree 借不到按绝对路径索引的 CRG 图）。
    # 组没配 GitLab 就不下发仓库，runner 退化为只带文档。
    secrets = {"claude_env": env}
    token = decrypt(get_settings().secret_key, group.gitlab_token_enc)
    if group.gitlab_base_url and token:
        payload["git_http_url"] = f"{group.gitlab_base_url.rstrip('/')}/{project.path}.git"
        payload["default_branch"] = project.default_branch
        secrets["git_token"] = token

    task = await enqueue(
        db,
        type="plan_turn",
        payload=payload,
        secrets=secrets,
        requirement_id=req.id,
        status="queued",
    )
    await db.commit()
    return {"task_id": task.id, "session_id": session_id, "mode": mode}


@router.post("/{req_id}/plan/stop")
async def stop_plan_turn(
    req_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """停止本轮生成：立即落一条「已停止」消息，runner 轮询到 cancelled 后杀掉 claude。"""
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有认领人可操作")
    task = await db.scalar(
        select(RunnerTask)
        .where(
            RunnerTask.requirement_id == req.id,
            RunnerTask.type == "plan_turn",
            RunnerTask.status.in_(("queued", "claimed", "running")),
        )
        .order_by(RunnerTask.id.desc())
        .limit(1)
    )
    if not task:
        raise HTTPException(400, "当前没有进行中的对话")
    mode = (task.payload or {}).get("mode", "plan")
    task.status = "cancelled"
    task.error = "用户停止"
    task.finished_at = utcnow()
    # 落库而不是只发直播帧：刷新/重连后最后一条是 assistant，前端才不会一直转圈
    msg = PlanMessage(requirement_id=req.id, role="assistant", content="⏹ 已停止本轮生成", mode=mode)
    db.add(msg)
    await db.flush()
    await db.execute(
        update(PlanEvent)
        .where(PlanEvent.runner_task_id == task.id, PlanEvent.message_id.is_(None))
        .values(message_id=msg.id)
    )
    events = [
        _evt_out(e)
        for e in (
            await db.scalars(select(PlanEvent).where(PlanEvent.message_id == msg.id).order_by(PlanEvent.seq))
        ).all()
    ]
    await db.commit()
    bus.publish(
        req.id,
        {
            "type": "plan_done",
            "message": {"id": msg.id, "role": "assistant", "content": msg.content, "mode": mode, "events": events},
        },
    )
    return {"ok": True, "task_id": task.id}


@router.post("/{req_id}/plan/clear")
async def clear_plan_session(
    req_id: int,
    body: PlanClearIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> dict:
    """清除会话（类似 /clear）：置空该模式的 claude session 并删掉它的聊天记录。

    文档与方案版本不动；下一轮按首轮处理，会重新初始化会话并带上需求上下文。
    """
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有认领人可操作")
    running = await db.scalar(
        select(RunnerTask.id)
        .where(
            RunnerTask.requirement_id == req.id,
            RunnerTask.type == "plan_turn",
            RunnerTask.status.in_(("queued", "claimed", "running")),
        )
        .limit(1)
    )
    if running:
        raise HTTPException(400, "本轮还在生成，请先停止再清除会话")
    ids = list(
        await db.scalars(
            select(PlanMessage.id).where(PlanMessage.requirement_id == req.id, PlanMessage.mode == body.mode)
        )
    )
    if ids:
        await db.execute(delete(PlanEvent).where(PlanEvent.message_id.in_(ids)))
        await db.execute(delete(PlanMessage).where(PlanMessage.id.in_(ids)))
    if body.mode == "requirement":
        req.req_session_id = None
    else:
        req.plan_session_id = None
    await db.commit()
    return {"ok": True, "mode": body.mode, "cleared": len(ids)}


def _evt_out(e: PlanEvent) -> dict:
    """回放事件统一带 kind：归一化块（kind=thinking/tool_use/tool_result）payload 自带，
    system 事件（task_started / hook_* / compact_boundary …）只有 type/subtype，
    这里补上 raw 并原样塞进 event 字段，与直播帧 plan_raw 同构——
    否则前端认不出该往哪个渲染分支走，只能丢或当原始 JSON 铺出来。"""
    if (e.payload or {}).get("kind"):
        return e.payload
    return {**e.payload, "kind": "raw", "event": e.payload}


async def _events_by_message(db: AsyncSession, req_id: int, msg_ids: list[int]) -> dict[int, list[dict]]:
    if not msg_ids:
        return {}
    rows = (
        await db.scalars(
            select(PlanEvent)
            .where(PlanEvent.requirement_id == req_id, PlanEvent.message_id.in_(msg_ids))
            .order_by(PlanEvent.seq)
        )
    ).all()
    out: dict[int, list[dict]] = {}
    for e in rows:
        if e.message_id is None:
            continue
        out.setdefault(e.message_id, []).append(_evt_out(e))
    return out


@router.get("/{req_id}/plan/messages")
async def list_plan_messages(
    req_id: int,
    mode: Literal["requirement", "plan"] | None = Query(None),
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[dict]:
    await _get_req_open(db, req_id, me)
    stmt = select(PlanMessage).where(PlanMessage.requirement_id == req_id)
    if mode:
        stmt = stmt.where(PlanMessage.mode == mode)
    rows = (await db.scalars(stmt.order_by(PlanMessage.id))).all()
    events = await _events_by_message(db, req_id, [m.id for m in rows])
    return [
        {
            "id": m.id,
            "role": m.role,
            "content": m.content,
            "mode": m.mode,
            "team_id": m.team_id,
            "agent_id": m.agent_id,
            "created_at": str(m.created_at),
            # 该轮思考/工具过程：前端按消息重放思考链（刷新后不丢）
            "events": events.get(m.id, []),
        }
        for m in rows
    ]


@router.get("/{req_id}/plan/stream")
async def plan_stream(
    req_id: int,
    mode: Literal["requirement", "plan"] | None = Query(None),
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> StreamingResponse:
    """SSE：先回放历史，再实时推 runner 回传的事件。mode 用于隔离两种讨论的历史与直播。

    回放用一次性 session，查完即关：SSE 长连接能开几个小时，若整条连接都占着
    数据库连接，多开几个页面就会把连接池耗尽，所有接口一起卡住。
    """
    await _get_req_open(db, req_id, me)  # 权限在建流前判，否则前端拿到 200 才慢慢发现没数据

    async def gen():
        q = bus.subscribe(req_id)
        try:
            async with sessionmaker()() as db:
                stmt = select(PlanMessage).where(PlanMessage.requirement_id == req_id)
                if mode:
                    stmt = stmt.where(PlanMessage.mode == mode)
                rows = (await db.scalars(stmt.order_by(PlanMessage.id))).all()
                events = await _events_by_message(db, req_id, [m.id for m in rows])
                record = [
                    (m.id, m.role, m.content, m.mode, m.team_id, m.agent_id, events.get(m.id, []))
                    for m in rows
                ]
                # 断线/刷新时本轮可能还在跑：已产生的过程事件也补发，否则重连后
                # 只剩一个空加载泡，看起来像卡死
                pending: list[dict] = []
                last = rows[-1] if rows else None
                if last and last.role == "user":
                    task = await db.scalar(
                        select(RunnerTask)
                        .where(
                            RunnerTask.requirement_id == req_id,
                            RunnerTask.type == "plan_turn",
                            RunnerTask.status.in_(("claimed", "running")),
                        )
                        .order_by(RunnerTask.id.desc())
                        .limit(1)
                    )
                    if task and (not mode or (task.payload or {}).get("mode", "plan") == mode):
                        erows = (
                            await db.scalars(
                                select(PlanEvent)
                                .where(PlanEvent.runner_task_id == task.id, PlanEvent.message_id.is_(None))
                                .order_by(PlanEvent.seq)
                            )
                        ).all()
                        pending = [_evt_out(e) for e in erows]
            for mid, role, content, m_mode, m_team, m_agent, evts in record:
                yield f"data: {json.dumps({'type': 'message', 'id': mid, 'role': role, 'content': content, 'mode': m_mode, 'team_id': m_team, 'agent_id': m_agent, 'events': evts}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'ready', 'pending_events': pending}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    evt = await asyncio.wait_for(q.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                # 带 mode 且与订阅模式不符的直播帧丢弃；老事件不带 mode 时照旧透传
                if mode and evt.get("mode") and evt["mode"] != mode:
                    continue
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
                if evt.get("type") in ("plan_done", "plan_error"):
                    # 会话仍可继续多轮，流保持打开由前端按需关闭
                    pass
        finally:
            bus.unsubscribe(req_id, q)

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.post("/{req_id}/plan-versions/{version_id}/finalize", response_model=RequirementOut)
async def finalize_plan_version(
    req_id: int,
    version_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> RequirementOut:
    """方案定稿：人在预览区挑一版定稿即触发入池。与文档定稿同款交互，唯一 final。"""
    req = await _get_req(db, req_id)
    if req.claimant_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有认领人可定稿")
    if req.status not in ("pool", "claimed"):
        raise HTTPException(400, f"状态 {req.status} 不可定稿")
    version = await db.get(PlanVersion, version_id)
    if not version or version.requirement_id != req.id:
        raise HTTPException(404, "方案版本不存在")

    req.plan_md = version.content
    req.status = "confirmed"
    for v in (
        await db.scalars(select(PlanVersion).where(PlanVersion.requirement_id == req.id))
    ).all():
        v.is_final = v.id == version.id

    # 定稿 → 单任务入池：大任务的并行拆解由主 agent 在执行期自行开子 agent 完成
    # （子 agent 进展经事件的 parent 字段外露），平台不再做需求级任务拆解。
    project = await db.get(Project, req.project_id)
    group = await db.get(DevGroup, project.group_id)
    from .tasks import enqueue_dev_task

    task = DevTask(
        requirement_id=req.id,
        code=await next_task_code(db, req),
        status="pending",
        creator_id=req.claimant_id or me.id,
        title=req.title,
    )
    db.add(task)
    await db.commit()
    await enqueue_dev_task(db, task, req, project, group)  # GitLab 未配置则留 pending
    await db.commit()
    claimant = await db.get(Person, req.claimant_id) if req.claimant_id else None
    return req_to_out(req, claimant, project)


# ---------- 开发任务 ----------


@router.post("/{req_id}/tasks", response_model=DevTaskOut)
async def create_task(
    req_id: int,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> DevTaskOut:
    req = await _get_req(db, req_id)
    if req.status not in ("confirmed", "online"):
        raise HTTPException(400, "需求未确认，不能建任务")
    if req.status == "online":
        req.status = "confirmed"  # 已上线的需求追加任务 → 回到开发中
    if req.claimant_id != me.id and not me.is_admin:
        raise HTTPException(403, "只有认领人可建任务")
    code = await next_task_code(db, req)
    task = DevTask(requirement_id=req.id, code=code, status="pending", creator_id=req.claimant_id or me.id)
    db.add(task)
    await db.commit()
    await db.refresh(task)
    from .tasks import task_to_out

    return task_to_out(task)
