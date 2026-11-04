"""MR 自动放行引擎（hermes mr_autoflow 移植版，适配 vega 的 MrReview 状态机）。

分工口径：LLM 只判内容（初审 approve），本模块用脚本硬门禁判事实——
放行前缀 fail-closed（未配置/任一文件不命中 = 不放行）、CI 必须绿、
目标分支必须匹配组配置、MR head sha 必须还是初审那个（防新推送偷渡）。
一切自动动作（approve / merge / 评论 / 冲突 @人）都留痕。
"""

from __future__ import annotations

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .gitlab import GitlabClient, client_ctx
from .jobs import _gitlab, mark_requirement_online
from .models import DevGroup, DevTask, MrReview, Person, System, Requirement
from .notify import merge_mentions, notify, project_owners
from .person_links import person_by_gitlab

log = logging.getLogger(__name__)

# 13.12 的 FF-only 仓库合并不了会 405 + "fast-forward"；文本里含这些标记按冲突处理
_CONFLICT_MARKERS = ("conflict", "409", "405", "fast-forward", "cannot be merged")


def _prefix_ok(prefixes: list | None, files: list) -> bool:
    """fail-closed：未配置或任一改动文件不命中 → 不放行。"""
    if not prefixes or not files:
        return False
    return all(any(f.startswith(p) for p in prefixes) for f in files)


async def _author_person(db: AsyncSession, group: DevGroup, author: dict) -> Person | None:
    return await person_by_gitlab(
        db, group, email=author.get("email"), username=author.get("username")
    )


async def try_autoflow(
    db: AsyncSession,
    group: DevGroup,
    project: System,
    review: MrReview,
    gl: GitlabClient,
    client: httpx.AsyncClient,
) -> None:
    """对一个初审 approve 的评审走全链放行检查；不可放行就地 return（不记死，下轮 sweep 再试）。"""
    if review.flow_state in ("merged", "conflict_notified"):  # 终态幂等：不再碰 GitLab
        return
    if review.verdict != "approve" or not review.commented:
        return
    if not _prefix_ok(project.auto_approve_prefixes, review.changed_files or []):
        return

    mr = await gl.mr_detail(client, project.gitlab_project_id, review.mr_iid)
    if mr.get("state") != "opened" or mr.get("draft") or mr.get("work_in_progress"):
        return
    if mr.get("target_branch") != project.mr_target_branch:
        return
    sha = mr.get("sha") or ""
    if not sha.startswith(review.head_sha[:12]):  # 初审后又有新推送 → 等新 sha 的初审，绝不放旧代码
        return

    author_p = await _author_person(db, group, mr.get("author") or {})
    if mr.get("merge_status") == "cannot_be_merged" or mr.get("can_be_merged") is False:
        await _conflict(db, group, project, review, gl, client, author_p, "GitLab 判定当前不可自动合并")
        return

    if review.gate:  # 初审时 CI 未绿 → 复查本 sha 的最新 pipeline，转绿才放（否则 return 等下轮）
        pipes = await gl.mr_pipelines(client, project.gitlab_project_id, review.mr_iid)
        mine = [x for x in pipes if str(x.get("sha") or "").startswith(review.head_sha[:12])]
        if mine:
            latest = max(mine, key=lambda x: x.get("id") or 0)
            review.pipeline_url = latest.get("web_url")
            if latest.get("status") != "success":
                review.gate = f"ci:{latest.get('status', 'unknown')}"
                await db.commit()
                return
        review.gate = ""

    if not await gl.approve_mr(client, project.gitlab_project_id, review.mr_iid):
        log.info("approve !%s 被拒（已批/未开 approvals/自批），继续尝试合并", review.mr_iid)
    try:
        await gl.merge_mr(client, project.gitlab_project_id, review.mr_iid, sha)
    except httpx.HTTPError as e:
        if any(m in str(e).lower() for m in _CONFLICT_MARKERS):
            await _conflict(db, group, project, review, gl, client, author_p, f"合并被拒：{e}")
        else:
            # 暂时性拒绝（pipeline pending/限流等）→ 非终态，下轮 sweep 重试
            review.flow_state = "hold_not_mergeable"
            await db.commit()
        return

    review.flow_state = "merged"
    await db.commit()
    # 任务侧同步置 done（merge_poll 的状态守卫据此不再重复通知）+ octo @plan 负责人毕业通知
    row = (
        await db.execute(
            select(DevTask, Requirement)
            .join(Requirement, DevTask.requirement_id == Requirement.id)
            .where(Requirement.system_id == project.id, DevTask.mr_iid == review.mr_iid)
        )
    ).first()
    if row:
        dev, req = row
        creator = await db.get(Person, dev.creator_id)
        owners = await project_owners(db, project.id)
        dev.status = "done"
        await mark_requirement_online(db, req)
        await db.commit()
        await notify(
            db,
            group,
            f"✅ 任务 {dev.code} 已上线：MR !{review.mr_iid} 已自动放行合并到 {project.mr_target_branch}\n"
            f"需求 {req.code}《{req.title}》",
            reason="mr_merged",
            mention=merge_mentions(creator, *owners),
        )
    try:
        await gl.add_mr_note(
            client,
            project.gitlab_project_id,
            review.mr_iid,
            f"🤖 Vega 自动放行：改动全部命中放行前缀、CI 已绿、初审可批准，已按 `{review.head_sha[:8]}` 自动合并。",
        )
    except Exception:  # noqa: BLE001 合并已成，评论失败只留日志
        log.warning("autoflow merged-note failed for !%s", review.mr_iid, exc_info=True)


async def _conflict(
    db: AsyncSession,
    group: DevGroup,
    project: System,
    review: MrReview,
    gl: GitlabClient,
    client: httpx.AsyncClient,
    person: Person | None,
    reason: str,
) -> None:
    """终态：冲突只能人工解。MR 留评论 + 群里 @作者，不强推不重试。"""
    review.flow_state = "conflict_notified"
    await db.commit()
    try:
        await gl.add_mr_note(
            client, project.gitlab_project_id, review.mr_iid, f"⚠️ Vega 自动放行中止：{reason}，需人工处理。"
        )
    except Exception:  # noqa: BLE001
        log.warning("autoflow conflict-note failed for !%s", review.mr_iid, exc_info=True)
    await notify(
        db,
        group,
        f"⛔ 自动放行冲突：{project.path} !{review.mr_iid}《{review.title}》\n{reason}\n请人工介入处理",
        reason="autoflow_conflict",
        mention=[person] if person else None,
    )


async def mr_autoflow_sweep(db: AsyncSession, group: DevGroup) -> dict:
    """兜底扫描：approve+已评论+待处理/hold 的评审逐个再试（CI 转绿即放行）。"""
    gl = _gitlab(group)
    if not gl:
        return {"error": "研发组未配置 GitLab"}
    rows = (
        await db.execute(
            select(MrReview, System)
            .join(System, MrReview.system_id == System.id)
            .where(
                MrReview.group_id == group.id,
                MrReview.verdict == "approve",
                MrReview.commented.is_(True),
                MrReview.flow_state.in_(["", "hold_not_mergeable"]),
                System.auto_approve_prefixes.isnot(None),
            )
        )
    ).all()
    tried = merged = 0
    async with client_ctx() as client:
        for review, project in rows:
            if not _prefix_ok(project.auto_approve_prefixes, review.changed_files or []):
                continue
            before = review.flow_state
            try:
                await try_autoflow(db, group, project, review, gl, client)
            except Exception:  # noqa: BLE001 单个失败不拖垮整轮
                log.exception("autoflow failed for !%s", review.mr_iid)
                continue
            tried += 1
            if review.flow_state == "merged" and before != "merged":
                merged += 1
    return {"tried": tried, "merged": merged}
