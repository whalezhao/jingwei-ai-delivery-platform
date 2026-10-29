"""runner 侧 API：心跳 / 长轮询领任务 / 事件回传 / 结果回传。

这是任务闭环的心脏：
- /next 认领 plan_turn 时立刻把粘性 runner 写回需求（关掉「第二轮落到别的节点丢会话」的竞态）
- /events 把 claude 归一化事件总线广播给 SSE 订阅者
- /result 收尾：plan_turn 落 PlanMessage；dev_task 建 MR + octo @下发人
"""

from __future__ import annotations

import hashlib
import logging
import re
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..bus import bus
from ..autoflow import try_autoflow
from ..db import get_db
from ..gitlab import GitlabClient, client_ctx
from ..jobs import _gitlab
from ..models import (
    DevGroup,
    DevTask,
    DevTaskEvent,
    MrReview,
    Person,
    PlanEvent,
    Project,
    Requirement,
    RequirementDoc,
    RunnerNode,
    RunnerTask,
    PlanMessage,
    utcnow,
)
from ..notify import merge_mentions, notify, project_owners
from ..queue import claim_next
from ..schemas import TaskEventIn, TaskResultIn

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/runner", tags=["runner-protocol"])

# git clone/push 失败信息里带 oauth2:<token>@ 形式的完整远端 URL，入库/通知前必须抹掉
_TOKEN_IN_URL_RE = re.compile(r"(oauth2:)[^@\s]+@")


def scrub_token(s: str | None) -> str | None:
    return _TOKEN_IN_URL_RE.sub(r"\1***@", s) if s else s


def _is_token_tick(evt: dict) -> bool:
    """逐 token 的用量计量事件：一次开发任务能刷出上万条，对开发任务全程留痕是纯噪声。"""
    return evt.get("kind") == "raw" and (evt.get("event") or {}).get("subtype") == "thinking_tokens"


# plan 会话回放要跳过的 system 事件：逐 token 计量会刷爆表，init 只是会话元信息
# （工具清单/模型/工作目录），每次开新会话都来一条，对回看执行过程没有价值。
_PLAN_NOISE = {"thinking_tokens", "init"}


async def _auth_node(
    authorization: str = Header(default=""), db: AsyncSession = Depends(get_db)
) -> RunnerNode:
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "缺少 runner token")
    h = hashlib.sha256(token.encode()).hexdigest()
    node = await db.scalar(select(RunnerNode).where(RunnerNode.token_hash == h))
    if not node:
        raise HTTPException(401, "runner token 无效")
    return node


@router.post("/heartbeat")
async def heartbeat(
    node: RunnerNode = Depends(_auth_node), db: AsyncSession = Depends(get_db)
) -> dict:
    node.last_heartbeat = utcnow()
    node.status = "online"
    await db.commit()
    return {"ok": True}


@router.get("/next")
async def next_task(
    request: Request,
    wait: float = 25.0,
    node: RunnerNode = Depends(_auth_node),
    db: AsyncSession = Depends(get_db),
) -> dict:
    async def alive() -> bool:
        return not await request.is_disconnected()

    claimed = await claim_next(node.id, wait_s=min(wait, 50.0), alive=alive)
    if not claimed:
        return {"task": None}
    task_id = claimed["id"]
    # plan_turn 粘性：认领即固化 runner，后续轮次只会派到本节点
    if claimed["type"] == "plan_turn":
        t = await db.get(RunnerTask, task_id)
        if t and t.requirement_id:
            req = await db.get(Requirement, t.requirement_id)
            if req and not req.plan_runner_id:
                req.plan_runner_id = node.id
        if t:
            t.status = "running"
        await db.commit()
    elif claimed["type"] == "dev_task" and claimed["payload"].get("dev_task_id"):
        # 任务池抢单：谁认领谁干活，DevTask 状态随之可见
        dev = await db.get(DevTask, claimed["payload"]["dev_task_id"])
        if dev and dev.status == "queued":
            dev.status = "running"
            await db.commit()
    return {"task": {"id": task_id, "type": claimed["type"], "payload": claimed["payload"]}}


def _pub(req_id: int, event: dict) -> None:
    bus.publish(req_id, event)


@router.get("/requirements/{req_id}/docs/{doc_id}/raw")
async def get_doc_raw(
    req_id: int,
    doc_id: int,
    node: RunnerNode = Depends(_auth_node),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """runner 下载需求文档原始字节（平台只传递，解析在节点上由 claude 完成）。"""
    doc = await db.get(RequirementDoc, doc_id)
    if not doc or doc.requirement_id != req_id:
        raise HTTPException(404, "文档不存在")
    # 文件名常含中文，HTTP 头只允许 latin-1 → RFC 5987 编码，否则 500
    fn = quote(doc.filename)
    return Response(
        doc.content,
        media_type="application/octet-stream",
        headers={"content-disposition": f"attachment; filename*=UTF-8''{fn}"},
    )


@router.post("/tasks/{task_id}/events")
async def post_events(
    task_id: int,
    body: TaskEventIn,
    node: RunnerNode = Depends(_auth_node),
    db: AsyncSession = Depends(get_db),
) -> dict:
    task = await db.get(RunnerTask, task_id)
    if not task or task.runner_id != node.id:
        raise HTTPException(404, "任务不存在或不属于该节点")
    # plan 直播帧带模式，SSE 据此只推给同模式的订阅者
    plan_mode = (task.payload or {}).get("mode", "plan")
    # dev_task 全过程落库留痕（SSE 只在直播时可见，回看靠这张表）。
    # 逐 token 的 thinking_tokens 是计量噪声，一个任务能刷出上万条，落库会把
    # 进展页灌爆（事件上限内全是它，真正的工具调用反而被截掉），直接丢弃。
    if task.dev_task_id:
        base = (
            await db.scalar(
                select(func.max(DevTaskEvent.seq)).where(DevTaskEvent.dev_task_id == task.dev_task_id)
            )
            or 0
        )
        for evt in body.events:
            if _is_token_tick(evt):
                continue
            base += 1
            db.add(
                DevTaskEvent(
                    dev_task_id=task.dev_task_id,
                    seq=base,
                    kind=str(evt.get("kind", "raw"))[:16],
                    payload=evt,
                )
            )
        await db.commit()
    if not task.requirement_id:
        return {"ok": True}
    # plan 会话的过程（思考/工具链/子代理进度）也要落库：SSE 只在直播时可见，
    # 刷新后思考链得靠回放。只存前端要用的事件：逐 token 计量、system 摘要
    # 这类直播噪声/一次性事件不入库。
    if not task.dev_task_id:
        seq = (
            await db.scalar(select(func.max(PlanEvent.seq)).where(PlanEvent.runner_task_id == task.id))
            or 0
        )
        for evt in body.events:
            kind = evt.get("kind")
            if kind in ("thinking", "tool_use", "tool_result") and not evt.get("parent"):
                payload = evt
            elif kind == "raw" and (evt.get("event") or {}).get("subtype") not in _PLAN_NOISE:
                # 原始事件按前端 plan_raw 的形状存内层 event，回放与直播同构。
                # 除逐 token 计量/会话元信息这类噪声外都留：子任务、压缩、重试、
                # 拒答都是用户想回看的执行痕迹，只存 task_progress 会让刷新后凭空消失
                payload = evt.get("event") or {}
                payload["kind"] = "raw"  # 回放要能分清归一化块与 system 事件
            else:
                continue
            seq += 1
            db.add(
                PlanEvent(
                    requirement_id=task.requirement_id,
                    runner_task_id=task.id,
                    seq=seq,
                    kind=str(kind)[:16],
                    payload=payload,
                )
            )
        await db.commit()
    for evt in body.events:
        if task.dev_task_id and _is_token_tick(evt):
            continue
        kind = evt.get("kind")
        if kind == "text" and not evt.get("parent"):
            # plan 直播气泡只吃主会话文本；子 agent 输出走 dev_raw 泳道
            _pub(task.requirement_id, {"type": "plan_text", "text": evt.get("text", ""), "mode": plan_mode})
        elif kind == "raw":
            # 原始流事件：开发任务走 live 频道；plan 任务（thinking_tokens 计量、
            # task_progress 子代理进度）走 plan 频道，前端渲染思考块+子代理进展
            if task.dev_task_id:
                _pub(task.requirement_id, {"type": "dev_raw", "task_id": task_id, "event": evt.get("event", {})})
            else:
                _pub(task.requirement_id, {"type": "plan_raw", "event": evt.get("event", {}), "mode": plan_mode})
        elif kind != "result" and task.dev_task_id:
            # thinking / tool_use / tool_result：开发任务的过程细节也走 live 频道
            _pub(task.requirement_id, {"type": "dev_raw", "task_id": task_id, "event": evt})
        elif kind in ("thinking", "tool_use", "tool_result") and not evt.get("parent"):
            # plan 会话的主会话过程（思考/工具链）也直播，前端渲染思考块+思考链
            _pub(task.requirement_id, {"type": "plan_raw", "event": evt, "mode": plan_mode})
    return {"ok": True}


@router.get("/tasks/{task_id}/state")
async def task_state(
    task_id: int, node: RunnerNode = Depends(_auth_node), db: AsyncSession = Depends(get_db)
) -> dict:
    """runner 执行期间轮询：看到 cancelled 就自己杀掉 claude 子进程。"""
    task = await db.get(RunnerTask, task_id)
    if not task or task.runner_id != node.id:
        raise HTTPException(404, "任务不存在或不属于该节点")
    return {"status": task.status}


@router.post("/tasks/{task_id}/result")
async def post_result(
    task_id: int,
    body: TaskResultIn,
    node: RunnerNode = Depends(_auth_node),
    db: AsyncSession = Depends(get_db),
) -> dict:
    task = await db.get(RunnerTask, task_id)
    if not task or task.runner_id != node.id:
        raise HTTPException(404, "任务不存在或不属于该节点")
    # 已被用户停止：停止接口已落好消息，迟到的结果不再覆盖
    if task.status == "cancelled":
        return {"ok": True}
    task.status = "done" if body.status == "done" else "failed"
    task.error = body.error
    task.finished_at = utcnow()

    if task.type == "plan_turn":
        await _finish_plan_turn(db, task, body)
    elif task.type == "dev_task":
        await _finish_dev_task(db, task, body)
    elif task.type == "mr_review":
        await _finish_mr_review(db, task, body)
    await db.commit()
    return {"ok": True}


async def _bind_plan_events(db: AsyncSession, task: RunnerTask, msg: PlanMessage) -> list[dict]:
    """把本回合的过程事件挂到 assistant 消息上，返回给前端随消息回放。"""
    await db.execute(
        update(PlanEvent)
        .where(PlanEvent.runner_task_id == task.id, PlanEvent.message_id.is_(None))
        .values(message_id=msg.id)
    )
    rows = (
        await db.scalars(select(PlanEvent).where(PlanEvent.message_id == msg.id).order_by(PlanEvent.seq))
    ).all()
    return [r.payload for r in rows]


async def _finish_plan_turn(db: AsyncSession, task: RunnerTask, body: TaskResultIn) -> None:
    req = await db.get(Requirement, task.requirement_id) if task.requirement_id else None
    if not req:
        return
    mode = (task.payload or {}).get("mode", "plan")
    if body.status == "done":
        text = body.data.get("text", "")
        msg = PlanMessage(requirement_id=req.id, role="assistant", content=text, mode=mode)
        db.add(msg)
        # 不自动成版：AI 只在交出方案时用标记包住正文，人点「采用此方案」才建 PlanVersion
        await db.flush()
        events = await _bind_plan_events(db, task, msg)
        _pub(
            req.id,
            {
                "type": "plan_done",
                "message": {"id": msg.id, "role": "assistant", "content": text, "mode": mode, "events": events},
            },
        )
    else:
        # 失败也落 assistant 消息：SSE 只在失败瞬间在线才收得到 plan_error，
        # 不落库的话重放里最后一条永远是 user，前端 busy 永不清（页面像卡死）
        text = f"⚠️ 本轮生成失败：{body.error or '执行失败'}"
        msg = PlanMessage(requirement_id=req.id, role="assistant", content=text, mode=mode)
        db.add(msg)
        await db.flush()
        events = await _bind_plan_events(db, task, msg)
        _pub(
            req.id,
            {
                "type": "plan_done",
                "message": {"id": msg.id, "role": "assistant", "content": text, "mode": mode, "events": events},
            },
        )


async def _finish_dev_task(db: AsyncSession, task: RunnerTask, body: TaskResultIn) -> None:
    dev = await db.get(DevTask, task.dev_task_id) if task.dev_task_id else None
    req = await db.get(Requirement, dev.requirement_id) if dev else None
    if not dev or not req:
        return
    creator = await db.get(Person, dev.creator_id)
    project = await db.get(Project, req.project_id)
    group = await db.get(DevGroup, project.group_id)
    # 任务创建人之外，项目负责人也要收到失败/MR 通知
    owners = await project_owners(db, project.id)
    # MR 审核人 = 项目负责人（用其绑定的 GitLab 账号），多个负责人都算上；
    # 一个都没有可用的账号再退回任务创建人
    reviewers = [p.gitlab_username for p in owners if p.gitlab_username]
    if not reviewers and creator and creator.gitlab_username:
        reviewers = [creator.gitlab_username]

    if body.status != "done" or body.data.get("status") != "pushed":
        dev.status = "failed"
        dev.error = scrub_token(body.error or body.data.get("error") or "执行失败")
        await notify(
            db,
            group,
            f"❌ 任务 {dev.code} 执行失败：{dev.error[:200]}\n需求 {req.code}《{req.title}》",
            reason="task_failed",
            mention=merge_mentions(creator, *owners),
        )
        return

    # runner 已 push 分支 → 系统确定性建 MR（git 门禁：feature → nightly，人审）
    gl = _gitlab(group)
    if not gl:
        dev.status = "failed"
        dev.error = "研发组 GitLab 配置缺失，无法建 MR（分支已推送）"
        return
    try:
        async with client_ctx() as client:
            mr = await gl.create_mr(
                client,
                project.gitlab_project_id,
                source=dev.branch or body.data.get("branch", ""),
                target=project.mr_target_branch,
                title=f"{dev.code}：{req.title}",
                description=(
                    f"任务 {dev.code}（需求 {req.code}）由 AI 完成。\n\n"
                    f"## 执行计划\n{req.plan_md or '（无）'}\n\n"
                    "🤖 Generated with Vega"
                ),
                assignee_usernames=reviewers,
            )
    except Exception as e:  # noqa: BLE001
        dev.status = "failed"
        dev.error = f"MR 创建失败：{e}"
        await notify(
            db,
            group,
            f"❌ 任务 {dev.code} 的 MR 创建失败：{e}",
            reason="mr_failed",
            mention=merge_mentions(creator, *owners),
        )
        return

    dev.mr_iid = mr["iid"]
    dev.mr_url = mr.get("web_url")
    dev.status = "mr_created"
    await notify(
        db,
        group,
        f"✅ 任务 {dev.code} 完成，MR 待审核：{mr.get('web_url', '')}\n"
        f"需求 {req.code}《{req.title}》（分支 {dev.branch} → {project.mr_target_branch}）",
        reason="mr_created",
        mention=merge_mentions(creator, *owners),
    )


def _review_comment(review: MrReview, gate: str) -> str:
    head = f"🤖 Vega AI 初审 · `{review.head_sha[:8]}` · 结论："
    if review.verdict == "approve":
        ci = f"待 CI 转绿（{gate.removeprefix('ci:')}）后自动放行" if gate else "已通过或无 CI"
        return f"{head}✅ 可批准\n{review.summary or ''}\n（CI 门禁：{ci}）"
    if review.verdict == "modify":
        issues = "\n".join(
            f"- `{i.get('file', '?')}`:{i.get('line', '?')} [{i.get('severity', '?')}] {i.get('note', '')}"
            for i in (review.issues or [])
        )
        return f"{head}⚠️ 需修改\n{review.summary or ''}\n{issues}"
    return f"{head}❌ 初审失败\n{review.error or 'runner 执行失败'}"


_VERDICT_TEXT: dict[str, str] = {"approve": "✅ 可批准", "modify": "⚠️ 需修改", "failed": "❌ 初审失败"}


async def _notify_review_result(
    db: AsyncSession, group: DevGroup, project: Project, review: MrReview
) -> None:
    """评审结论双通道之一：GitLab 评论之外，octo 同步 @审核人（=任务创建人=需求认领人）。"""
    row = (
        await db.execute(
            select(DevTask, Requirement, Person)
            .join(Requirement, DevTask.requirement_id == Requirement.id)
            .join(Person, DevTask.creator_id == Person.id)
            .where(Requirement.project_id == project.id, DevTask.mr_iid == review.mr_iid)
        )
    ).first()
    text = (
        f"📋 代码评审结论：{project.path} !{review.mr_iid} "
        f"{_VERDICT_TEXT.get(review.verdict, review.verdict)}"
    )
    if review.verdict == "approve" and review.gate:
        text += f"（CI 未绿：{review.gate.removeprefix('ci:')}，转绿后自动放行）"
    detail = (review.summary or review.error or "").strip()
    if detail:
        text += f"\n{detail}"
    if row:
        text += f"\n任务 {row.DevTask.code} · 需求 {row.Requirement.code}《{row.Requirement.title}》{row.DevTask.mr_url or ''}"
    owners = await project_owners(db, project.id)
    await notify(
        db, group, text, reason="mr_reviewed", mention=merge_mentions(row.Person if row else None, *owners)
    )


async def _finish_mr_review(db: AsyncSession, task: RunnerTask, body: TaskResultIn) -> None:
    """runner 初审结论落 MrReview（幂等：占位行入队时已建）→ CI 门禁判定 → MR 审计评论。"""
    p = task.payload or {}
    mr = p.get("mr") or {}
    project = await db.get(Project, p.get("project_id"))
    if not project or not mr.get("iid"):
        return
    group = await db.get(DevGroup, project.group_id)

    review = await db.scalar(
        select(MrReview).where(
            MrReview.project_id == project.id,
            MrReview.mr_iid == mr["iid"],
            MrReview.head_sha == mr.get("sha", ""),
        )
    )
    if not review:  # 占位行缺失（异常路径）补建，保证 (project,iid,sha) 必有留痕
        review = MrReview(
            project_id=project.id,
            group_id=group.id,
            mr_iid=mr["iid"],
            head_sha=mr.get("sha", ""),
            title=str(mr.get("title") or "")[:256],
            changed_files=p.get("changed_files") or [],
            verdict="pending",
        )
        db.add(review)
        await db.flush()

    data = body.data or {}
    if body.status == "done" and data.get("status") == "reviewed":
        review.verdict = data.get("verdict", "failed")
        review.summary = (data.get("summary") or "")[:2000]
        review.issues = data.get("issues") or []
        review.changed_files = data.get("changed_files") or review.changed_files
    elif body.status == "done" and data.get("status") == "skipped":
        review.verdict, review.error = "failed", data.get("reason") or "跳过"
        return  # sha 已不可达，无可评论的 MR 内容
    else:
        review.verdict, review.error = "failed", body.error or "runner 执行失败"

    gl = _gitlab(group)
    if not gl:
        review.error = (review.error or "") + "；GitLab 配置缺失，审计评论未发"
        return
    try:
        async with client_ctx() as client:
            gate = ""
            pipes = await gl.mr_pipelines(client, project.gitlab_project_id, review.mr_iid)
            mine = [x for x in pipes if str(x.get("sha") or "").startswith(review.head_sha[:12])]
            if mine:
                latest = max(mine, key=lambda x: x.get("id") or 0)
                review.pipeline_url = latest.get("web_url")
                if latest.get("status") != "success":  # 宽松口径：非 success 一律视为未绿
                    gate = f"ci:{latest.get('status', 'unknown')}"
            review.gate = gate
            await gl.add_mr_note(
                client, project.gitlab_project_id, review.mr_iid, _review_comment(review, gate)
            )
            review.commented = True
            await _notify_review_result(db, group, project, review)
            if review.verdict == "approve":
                # 直通自动放行（全链硬门禁在 try_autoflow 里，不可放行不记死，sweep 兜底重试）
                await try_autoflow(db, group, project, review, gl, client)
    except Exception:  # noqa: BLE001 评论/gate 失败不吞初审结论
        log.warning("mr_review gate/comment failed for !%s", review.mr_iid, exc_info=True)
