"""runner 节点管理（管理员创建节点发放 token）。"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth import require_admin
from ..db import get_db
from ..models import Person, Requirement, RunnerNode, RunnerTask, utcnow
from ..schemas import NodeCreateIn, NodeOut

router = APIRouter(prefix="/api/runner-nodes", tags=["runner-nodes"])

HEARTBEAT_OFFLINE_S = 90


def _out(n: RunnerNode) -> dict:
    return {
        "id": n.id,
        "name": n.name,
        "status": n.status,
        "last_heartbeat": str(n.last_heartbeat) if n.last_heartbeat else None,
    }


@router.get("", response_model=list[NodeOut])
async def list_nodes(db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)) -> list[NodeOut]:
    # claimed/running = 该节点正在跑的 claude 进程数（每任务一个子进程）
    counts = dict(
        (
            await db.execute(
                select(RunnerTask.runner_id, func.count())
                .where(RunnerTask.status.in_(("claimed", "running")))
                .group_by(RunnerTask.runner_id)
            )
        ).all()
    )
    rows = (await db.scalars(select(RunnerNode).order_by(RunnerNode.id))).all()
    now = utcnow()
    out = []
    for n in rows:
        # 兜底：心跳超时但状态还是 online 的按 offline 展示。
        # SQLite 读回的 DateTime 是 naive，比较前要补回 UTC 时区。
        if n.status == "online" and n.last_heartbeat:
            hb = n.last_heartbeat
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=timezone.utc)
            if (now - hb).total_seconds() > HEARTBEAT_OFFLINE_S:
                n.status = "offline"
        out.append({**_out(n), "running_tasks": counts.get(n.id, 0)})
    await db.commit()
    return out


@router.post("", response_model=dict)
async def create_node(
    body: NodeCreateIn, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> dict:
    if await db.scalar(select(RunnerNode).where(RunnerNode.name == body.name)):
        raise HTTPException(400, "同名节点已存在")
    token = secrets.token_hex(24)
    node = RunnerNode(name=body.name, token_hash=hashlib.sha256(token.encode()).hexdigest())
    db.add(node)
    await db.commit()
    await db.refresh(node)
    return {"id": node.id, "name": node.name, "token": token}  # token 只此一次返回


@router.delete("/{node_id}")
async def delete_node(
    node_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> dict:
    node = await db.get(RunnerNode, node_id)
    if not node:
        raise HTTPException(404, "节点不存在")
    # 节点删了，粘在它上面的引用必须一起解绑：sticky 路由只认原节点，
    # 否则会话消息永远卡在 queued（requeue_dead 也只认还存在的节点）。
    # claude 会话存在节点本机，节点没了会话也没了——session id 一并作废，
    # 下一条消息新开会话并带上历史（requirements.post_plan_message 的 first 分支）。
    # 已认领/在跑的任务放回队列换节点重跑；runner_id 一并置空（外键指向被删的行）。
    for req in (await db.scalars(select(Requirement).where(Requirement.plan_runner_id == node_id))).all():
        req.plan_runner_id = None
        req.req_session_id = None
        req.plan_session_id = None
    for t in (
        await db.scalars(select(RunnerTask).where(RunnerTask.status.in_(("queued", "claimed", "running"))))
    ).all():
        if (t.payload or {}).get("sticky_runner_id") == node_id:
            t.payload = {k: v for k, v in t.payload.items() if k != "sticky_runner_id"}
        if t.runner_id == node_id:
            t.runner_id = None
            t.claimed_at = None
            if t.status in ("claimed", "running"):
                t.status = "queued"
    await db.delete(node)
    await db.commit()
    return {"ok": True}
