"""GitLab webhook 接收：全局单一地址 /webhooks/gitlab。

组识别靠 X-Gitlab-Token 与各组 webhook_secret 比对（单一地址没有 group_id 路径段）。
事件路由（13.12 语义）：
- Push Hook → 群播报 + @推送人
- Merge Request Hook → open 播报；update（新推送）若命中项目 audit_prefixes → 入队 AI 初审；
  merge / close 动作当场收尾任务并播报（13.12 有时不发 merge，merge_poll 轮询兜底）
- Pipeline Hook → success 静默；failed → 后台 LLM 根因分析 → @提交人
"""

from __future__ import annotations

import datetime
import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..crypto import decrypt
from ..db import get_db, sessionmaker
from ..gitlab import client_ctx
from ..jobs import MR_CLOSED_MARK, _gitlab, _notify_task_final, ci_failure_analysis, mark_requirement_online
from ..llm import am_user_key, build_claude_env, resolve_llm
from ..models import DevGroup, DevTask, GitlabEvent, MrReview, Person, Project, Requirement, utcnow
from ..notify import merge_mentions, notify, project_owners
from ..queue import enqueue

log = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks/gitlab", tags=["webhooks"])


async def _person_by(db: AsyncSession, *, email: str | None = None, username: str | None = None) -> Person | None:
    q = select(Person)
    if email:
        row = await db.scalar(q.where(Person.gitlab_email == email))
        if row:
            return row
    if username:
        return await db.scalar(q.where(Person.gitlab_username == username))
    return None


def _project_id(payload: dict) -> int | None:
    """MR / Pipeline hook 没有顶层 project_id：项目 id 在 project.id（13.12 实测）。
    只认顶层会让项目查不到 → MR 播报显示「?」，AI 初审也永远不入队。"""
    return payload.get("project_id") or (payload.get("project") or {}).get("id")


@router.post("")
async def gitlab_webhook(
    request: Request,
    background: BackgroundTasks,
    x_gitlab_event: str = Header(default=""),
    x_gitlab_token: str = Header(default=""),
    db: AsyncSession = Depends(get_db),
) -> dict:
    group = await db.scalar(select(DevGroup).where(DevGroup.webhook_secret == x_gitlab_token))
    if not group or not x_gitlab_token:
        raise HTTPException(403, "webhook token 校验失败")

    payload = await request.json()
    event = GitlabEvent(group_id=group.id, event_type=x_gitlab_event, payload=payload)
    db.add(event)

    try:
        if x_gitlab_event == "Push Hook":
            await _on_push(db, group, payload)
        elif x_gitlab_event == "Merge Request Hook":
            await _on_mr(db, group, payload, background)
        elif x_gitlab_event == "Pipeline Hook":
            await _on_pipeline(db, group, payload, background, event.id)
    except Exception:  # noqa: BLE001 事件处理失败不影响 200 返回（GitLab 会重试打爆）
        log.exception("webhook handler failed: %s", x_gitlab_event)

    await db.commit()
    return {"ok": True}


async def _on_push(db: AsyncSession, group: DevGroup, payload: dict) -> None:
    n = payload.get("total_commits_count", 0)
    if n == 0 and payload.get("after", "").rstrip("0") == "":
        return  # 纯分支删除/空推送静默
    user_email = payload.get("user_email")
    person = await _person_by(db, email=user_email, username=payload.get("user_name"))
    project = await db.scalar(
        select(Project).where(
            Project.group_id == group.id,
            Project.gitlab_project_id == _project_id(payload),
        )
    )
    pname = project.path if project else (payload.get("project") or {}).get("path_with_namespace", "?")
    branch = (payload.get("ref") or "").removeprefix("refs/heads/")
    await notify(
        db,
        group,
        f"🚀 {payload.get('user_name', '?')} 推送 {n} 个提交到 {pname}（{branch}）",
        reason="push",
        mention=[person] if person else None,
    )


async def _on_mr(db: AsyncSession, group: DevGroup, payload: dict, background: BackgroundTasks) -> None:
    attrs = payload.get("object_attributes") or {}
    action = attrs.get("action")
    if action in ("open", "reopen"):
        author = payload.get("author") or {}
        person = await _person_by(db, email=author.get("email"), username=author.get("username"))
        project = await db.scalar(
            select(Project).where(Project.group_id == group.id, Project.gitlab_project_id == _project_id(payload))
        )
        pname = project.path if project else "?"
        owners = await project_owners(db, project.id) if project else []
        await notify(
            db,
            group,
            f"📋 新 MR !{attrs.get('iid')}《{attrs.get('title')}》\n"
            f"{attrs.get('source_branch')} → {attrs.get('target_branch')}（{pname}，by {author.get('name', '?')}）",
            reason="mr_opened",
            mention=merge_mentions(person, *owners),
        )
    elif action in ("merge", "close"):
        # 人工在 GitLab 关闭/合并 MR：当场收尾并播报。只靠 merge_poll 兜底时，
        # 任务创建人和项目负责人最多要等 10 分钟，且 close 事件过去完全没处理。
        await _mark_task_final(
            db, group, _project_id(payload), attrs.get("iid"), merged=action == "merge"
        )
    if action in ("open", "reopen", "update"):
        _maybe_enqueue_mr_review(background, group.id, _project_id(payload), payload)


def _maybe_enqueue_mr_review(background: BackgroundTasks, group_id: int, gitlab_project_id, payload: dict) -> None:
    """项目配了 audit_prefixes 才审（fail-closed）；实际判定放后台，不阻塞 webhook 响应。"""
    background.add_task(_enqueue_mr_review_if_needed, group_id, gitlab_project_id, payload)


async def _enqueue_mr_review_if_needed(group_id: int, gitlab_project_id, payload: dict) -> None:
    try:
        async with sessionmaker()() as s:
            group = await s.get(DevGroup, group_id)
            project = await s.scalar(
                select(Project).where(
                    Project.group_id == group_id, Project.gitlab_project_id == gitlab_project_id
                )
            )
            if not group or not project or not project.audit_prefixes:
                return
            gl = _gitlab(group)
            if not gl:
                return
            attrs = payload.get("object_attributes") or {}
            iid = attrs.get("iid")
            if not iid:
                return
            sha = (attrs.get("last_commit") or {}).get("id") or ""
            async with client_ctx() as client:
                changes = await gl.mr_changes(client, project.gitlab_project_id, iid)
                if not sha:
                    sha = (await gl.mr_detail(client, project.gitlab_project_id, iid)).get("sha", "")
            if not sha:
                return
            # 熔断：同一 MR 24h 内最多审 5 次，高频推分支不再持续烧初审 token
            # （列是 timestamptz：PG 侧 asyncpg 要求 aware，SQLite 落库时会丢掉 tz，两种库都可比）
            day_ago = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=24)
            n_recent = len(
                (
                    await s.scalars(
                        select(MrReview.id).where(
                            MrReview.project_id == project.id,
                            MrReview.mr_iid == iid,
                            MrReview.created_at >= day_ago,
                        )
                    )
                ).all()
            )
            if n_recent >= 5:
                log.warning("mr !%s 初审熔断：24h 内已审 %d 次", iid, n_recent)
                return
            hits = [
                c["new_path"]
                for c in changes
                if c.get("new_path") and any(c["new_path"].startswith(p) for p in project.audit_prefixes)
            ]
            if not hits:
                return
            # 一行 = 一个 (project, iid, sha)：在审/已审不重复入队（并发事件靠唯一约束挡）；
            # 失败可重试——runner 偶发挂（LLM 抖动、claude 退出）不该让这个 commit 永远没人审
            existing = await s.scalar(
                select(MrReview).where(
                    MrReview.project_id == project.id,
                    MrReview.mr_iid == iid,
                    MrReview.head_sha == sha,
                )
            )
            if existing:
                if existing.verdict != "failed":
                    return
                existing.verdict, existing.error, existing.summary = "pending", None, None
                existing.issues, existing.gate, existing.flow_state = [], "", ""
                existing.changed_files = hits
            else:
                # 入队即建占位行：(project, iid, sha) 唯一约束挡住并发事件重复入队
                s.add(
                    MrReview(
                        project_id=project.id,
                        group_id=group.id,
                        mr_iid=iid,
                        head_sha=sha,
                        title=str(attrs.get("title") or "")[:256],
                        source_branch=attrs.get("source_branch"),
                        target_branch=attrs.get("target_branch"),
                        changed_files=hits,
                        verdict="pending",
                    )
                )
            try:
                await s.commit()
            except IntegrityError:
                return
            # 评审用的 proxy user_key 必须落到一个有 key 的人：平台公共 key 可为空，
            # 空 key 会被代理拒绝（claude 侧表现为 unrecognized_model）。
            row = (
                await s.execute(
                    select(DevTask, Requirement, Person)
                    .join(Requirement, DevTask.requirement_id == Requirement.id)
                    .join(Person, DevTask.creator_id == Person.id)
                    .where(Requirement.project_id == project.id, DevTask.mr_iid == iid)
                )
            ).first()
            reviewer = row.Person if row else None
            if not reviewer:
                author = (payload.get("author") or {}).get("username")
                if author:
                    reviewer = await s.scalar(select(Person).where(Person.gitlab_username == author))
            owners = await project_owners(s, project.id)
            token = decrypt(get_settings().secret_key, group.gitlab_token_enc)
            target = resolve_llm(None, group, person_first=False)
            # 项目级 team/agent 绑定直选注入团队资产；稳定 conv-id 让 proxy 的
            # 「是否关联资产」提问至多发生在该 MR 的首次评审
            env = build_claude_env(
                target,
                team_id=project.am_team_id or group.am_team_id,
                agent_id=project.am_agent_id,
                user_key=am_user_key(reviewer),
                conversation_id=f"vega-mr-{project.id}-{iid}",
            )
            await enqueue(
                s,
                type="mr_review",
                payload={
                    "project_id": project.id,
                    "mr": {
                        "iid": iid,
                        "title": attrs.get("title") or "",
                        "sha": sha,
                        "source_branch": attrs.get("source_branch"),
                        "target_branch": attrs.get("target_branch"),
                        "author": (payload.get("author") or {}).get("name", ""),
                    },
                    "git_http_url": f"{group.gitlab_base_url.rstrip('/')}/{project.path}.git",
                    "default_branch": project.default_branch,
                    "changed_files": hits,
                },
                secrets={"git_token": token, "claude_env": env},
            )
            # 评审开始即播报：任务创建人 + 项目负责人一起 @（外部 MR 退回按作者匹配）
            await notify(
                s,
                group,
                f"🔍 代码评审开始：{project.path} !{iid}《{attrs.get('title') or ''}》\n"
                f"AI 初审已排队（{len(hits)} 个文件命中审计前缀）",
                reason="mr_review_started",
                mention=merge_mentions(reviewer, *owners),
            )
    except Exception:  # noqa: BLE001 初审入队失败不影响 webhook；下个 update 事件还会再试
        log.exception("enqueue mr_review failed")


async def _on_pipeline(
    db: AsyncSession, group: DevGroup, payload: dict, background: BackgroundTasks, event_id: int
) -> None:
    attrs = payload.get("object_attributes") or {}
    status = attrs.get("status")
    project = await db.scalar(
        select(Project).where(
            Project.group_id == group.id, Project.gitlab_project_id == _project_id(payload)
        )
    )
    if status == "success":
        # 成功静默（设计要求），只更新关联任务状态
        await _mark_task_cicd_ok(db, attrs.get("ref"))
        return
    if status != "failed" or not project:
        return
    # LLM 分析放后台，不阻塞 webhook 响应
    async def _run() -> None:
        async with sessionmaker()() as s:
            g = await s.get(DevGroup, group.id)
            p = await s.get(Project, project.id)
            await ci_failure_analysis(s, g, p, payload)
            ev = await s.get(GitlabEvent, event_id)
            if ev:
                ev.handled_at = utcnow()
                await s.commit()

    background.add_task(_run)


async def _mark_task_final(
    db: AsyncSession,
    group: DevGroup,
    gitlab_project_id: int | None,
    mr_iid: int | None,
    *,
    merged: bool,
) -> None:
    """MR 关了/合了：按「项目 + MR iid」找到任务收尾，并立刻播报（与 merge_poll 同文案）。

    iid 只在项目内唯一，必须连项目一起匹配；任务已 done 说明轮询/另一个事件已经处理过，
    直接返回，避免重复通知。"""
    if not mr_iid or not gitlab_project_id:
        return
    project = await db.scalar(
        select(Project).where(
            Project.group_id == group.id, Project.gitlab_project_id == gitlab_project_id
        )
    )
    if not project:
        return
    row = (
        await db.execute(
            select(DevTask, Requirement)
            .join(Requirement, DevTask.requirement_id == Requirement.id)
            .where(Requirement.project_id == project.id, DevTask.mr_iid == mr_iid)
        )
    ).first()
    if not row:
        return
    task, req = row
    if task.status == "done":
        return
    task.status = "done"
    if merged:
        await mark_requirement_online(db, req)
        await _notify_task_final(db, group, req, task, "merged")
    else:
        task.error = task.error or MR_CLOSED_MARK
        await _notify_task_final(db, group, req, task, "closed")


async def _mark_task_cicd_ok(db: AsyncSession, ref: str | None) -> None:
    if not ref:
        return
    task = await db.scalar(select(DevTask).where(DevTask.branch == ref.removeprefix("refs/heads/")))
    if task and task.status == "cicd":
        task.status = "reviewing"
