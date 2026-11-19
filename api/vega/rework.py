"""方案返工回路：CRG 初审判「需修改」时，由方案阶段的 AM 专家出修订稿。

    初审 modify → 方案专家修订 → Goldfish 复审 → 无阻塞项则定稿并重新派发
    ↑                                                        │
    └──────────────── 复审仍有阻塞项且未达上限 ←──────────────┘

只接 CRG 的 `modify`（代码要改）。`failed` 多半是执行器/网络问题，不该触发返工。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .crypto import decrypt
from .llm import am_user_key, build_claude_env, resolve_llm
from .models import (
    DevGroup,
    DevTask,
    GroupMember,
    Person,
    PlanMessage,
    PlanReview,
    PlanVersion,
    Requirement,
    RequirementDoc,
    System,
)
from .notify import merge_mentions, notify, project_owners
from .numbering import gen_session_id, next_task_code
from .queue import enqueue

log = logging.getLogger(__name__)

MAX_ROUNDS = 2
_PLAN_BLOCK = re.compile(
    r"<!--\s*vega:plan:begin\s*-->(.*?)<!--\s*vega:plan:end\s*-->", re.DOTALL
)


async def _notify(db: AsyncSession, req: Requirement, head: str, detail: str = "") -> None:
    project = await db.get(System, req.system_id)
    group = await db.get(DevGroup, project.group_id) if project else None
    if not group:
        return
    claimant = await db.get(Person, req.claimant_id) if req.claimant_id else None
    await notify(
        db,
        group,
        f"{head}：{req.code}《{req.title}》" + (f"\n{detail}" if detail else ""),
        reason="plan_rework",
        mention=merge_mentions(claimant, *(await project_owners(db, project.id))),
    )


async def _plan_expert(db: AsyncSession, req: Requirement, project: System, group: DevGroup):
    """方案阶段的 AM 专家：优先用当时方案对话里选定的 team/agent，否则系统/组默认。"""
    last = await db.scalar(
        select(PlanMessage)
        .where(
            PlanMessage.requirement_id == req.id,
            PlanMessage.mode == "plan",
            PlanMessage.agent_id.is_not(None),
        )
        .order_by(PlanMessage.id.desc())
        .limit(1)
    )
    claimant = await db.get(Person, req.claimant_id) if req.claimant_id else None
    member = (
        await db.scalar(
            select(GroupMember).where(
                GroupMember.group_id == group.id,
                GroupMember.person_id == claimant.id,
            )
        )
        if claimant
        else None
    )
    return (
        (last.team_id if last else None) or project.am_team_id or group.am_team_id,
        (last.agent_id if last else None) or project.am_agent_id or (member.am_agent_id if member else None),
    )


async def _enqueue_rework_turn(db: AsyncSession, req: Requirement, prompt: str) -> None:
    project = await db.get(System, req.system_id)
    group = await db.get(DevGroup, project.group_id)
    claimant = await db.get(Person, req.claimant_id) if req.claimant_id else None
    env = build_claude_env(
        resolve_llm(claimant, group),
        team_id=req.rework_team_id,
        agent_id=req.rework_agent_id,
        user_key=am_user_key(claimant),
        conversation_id=f"vega-rework-{req.id}-{req.rework_round}",
    )
    docs = list(
        (
            await db.scalars(
                select(RequirementDoc).where(RequirementDoc.requirement_id == req.id)
            )
        ).all()
    )
    docs = [d for d in docs if d.is_final] or docs
    payload = {
        "requirement_id": req.id,
        "session_id": gen_session_id(),
        "resume": False,
        "prompt": prompt,
        "mode": "plan",
        "docs": [{"id": d.id, "filename": d.filename} for d in docs],
        "attachments": [],
        # 平台据此在轮次结束时自动成版 + 送复审，不需要人再点「采用」
        "rework": True,
        "rework_round": req.rework_round,
    }
    if req.plan_runner_id:
        payload["sticky_runner_id"] = req.plan_runner_id
    secrets: dict[str, object] = {"claude_env": env}
    token = decrypt(get_settings().secret_key, group.gitlab_token_enc)
    if group.gitlab_base_url and token:
        payload["git_http_url"] = f"{group.gitlab_base_url.rstrip('/')}/{project.path}.git"
        payload["default_branch"] = project.default_branch
        secrets["git_token"] = token
    await enqueue(
        db,
        type="plan_turn",
        payload=payload,
        secrets=secrets,
        requirement_id=req.id,
        status="queued",
    )


def _prompt(req: Requirement, findings: str) -> str:
    return f"""你是这个需求的技术方案负责人。上一版方案已经实现并提交，但代码评审（CRG 影响面审计）
判定不可通过。请基于评审发现出一份**修订版方案**。

## 需求
{req.code}《{req.title}》

## 上一版方案
{req.plan_md or '（无）'}

## 评审发现
{findings}

要求：
1. 逐条回应评审发现，说明这次方案怎么改；评审没提到的部分不要重写。
2. 修订后的完整方案必须放在 `<!-- vega:plan:begin -->` 与 `<!-- vega:plan:end -->` 之间，
   平台会用它自动成版并送 Goldfish 复审。
3. 方案要能被开发直接执行：改动点、涉及文件、验证方式写清楚。
"""


async def start_rework(
    db: AsyncSession, requirement_id: int, findings: str
) -> bool:
    """CRG 初审 modify → 启动一轮方案返工。已有返工在进行时忽略。"""
    req = await db.get(Requirement, requirement_id)
    if req is None or req.rework_state:
        return False
    if req.rework_round >= MAX_ROUNDS:
        await _notify(
            db, req, "⚠️ 返工次数已用尽",
            f"CRG 再次判定不可通过，已到 {MAX_ROUNDS} 轮上限，转人工处理。",
        )
        await db.commit()
        return False
    project = await db.get(System, req.system_id)
    group = await db.get(DevGroup, project.group_id)
    req.rework_round += 1
    req.rework_state = "planning"
    req.rework_team_id, req.rework_agent_id = await _plan_expert(db, req, project, group)
    await _enqueue_rework_turn(db, req, _prompt(req, findings))
    await db.commit()
    await _notify(
        db, req, "🔁 方案返工",
        f"CRG 初审不可通过，已请方案专家出修订稿（第 {req.rework_round} 轮）。",
    )
    return True


async def on_plan_turn_done(
    db: AsyncSession, req: Requirement, text: str, payload: dict
) -> bool:
    """返工轮产出：抽方案块 → 成版 → 自动送 Goldfish 复审。"""
    if not payload.get("rework"):
        return False
    match = _PLAN_BLOCK.search(text or "")
    if not match:
        req.rework_state = None
        await _notify(db, req, "⚠️ 方案返工未产出方案块", "已停止自动链路，请人工检查。")
        return False
    content = match.group(1).strip()
    nxt = (
        await db.scalar(
            select(func.max(PlanVersion.version)).where(
                PlanVersion.requirement_id == req.id
            )
        )
        or 0
    ) + 1
    version = PlanVersion(requirement_id=req.id, version=nxt, content=content)
    db.add(version)
    await db.flush()
    req.rework_state = "reviewing"
    # 延迟导入：requirements 依赖本模块调用的同一批服务，模块级导入会成环
    from .routers.requirements import _enqueue_plan_review

    await _enqueue_plan_review(db, req, version, trigger="rework")
    return True


async def _finalize_and_dispatch(db: AsyncSession, req: Requirement, version: PlanVersion) -> None:
    """复审通过：定稿 + 建任务 + 派发（与人工「定稿」同一条下游）。"""
    req.plan_md = version.content
    req.status = "confirmed"
    for item in (
        await db.scalars(
            select(PlanVersion).where(PlanVersion.requirement_id == req.id)
        )
    ).all():
        item.is_final = item.id == version.id
    task = DevTask(
        requirement_id=req.id,
        code=await next_task_code(db, req),
        status="pending",
        creator_id=req.claimant_id,
        title=req.title,
        rework_round=req.rework_round,
    )
    db.add(task)
    await db.flush()
    project = await db.get(System, req.system_id)
    group = await db.get(DevGroup, project.group_id)
    from .routers.tasks import enqueue_dev_task

    await enqueue_dev_task(db, task, req, project, group)


async def on_review_done(db: AsyncSession, review: PlanReview) -> None:
    """Goldfish 复审收尾：无阻塞项 → 定稿派发；仍有 → 再来一轮（受上限约束）。"""
    req = await db.get(Requirement, review.requirement_id)
    if req is None or req.rework_state != "reviewing":
        return
    if review.status != "done":
        req.rework_state = None
        await _notify(db, req, "⚠️ 方案复审未完成", "自动链路已停止，请人工处理。")
        return
    if not review.blocker_count:
        version = await db.get(PlanVersion, review.plan_version_id)
        if version is None:
            req.rework_state = None
            return
        await _finalize_and_dispatch(db, req, version)
        req.rework_state = None
        await _notify(
            db, req, "✅ 方案返工完成",
            f"第 {req.rework_round} 轮修订通过复审，已重新派发开发任务。",
        )
        return
    if req.rework_round >= MAX_ROUNDS:
        req.rework_state = None
        await _notify(
            db, req, "⚠️ 返工次数已用尽",
            f"复审仍有 {review.blocker_count} 个阻塞项，转人工处理。",
        )
        return
    req.rework_round += 1
    req.rework_state = "planning"
    findings = (review.report_md or "")[:12000]
    await _enqueue_rework_turn(db, req, _prompt(req, findings))
    await _notify(
        db, req, "🔁 方案返工（复审仍有阻塞项）",
        f"进入第 {req.rework_round} 轮修订。",
    )