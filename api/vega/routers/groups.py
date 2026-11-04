from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .. import octo
from ..auth import current_person, require_admin
from ..config import get_settings
from ..crypto import decrypt, encrypt
from ..db import get_db, sessionmaker
from ..jobs import (
    _group_tech_leads,
    daily_commit_stats,
    refresh_gitlab_members,
    scan_projects,
    sync_members_and_match,
)
from ..reports import build_code_report, report_range
from ..models import (
    DevGroup,
    GitlabMember,
    GroupMember,
    GroupTechLead,
    Person,
    PersonGitlabAccount,
    Project,
    System,
)
from ..notify import notify
from ..person_links import person_ids, set_persons
from ..schemas import (
    GitlabMatchIn,
    GroupIn,
    GroupOut,
    GroupUpd,
    ManualMatchIn,
    MatchRunOut,
    MemberAccountIn,
    MemberAdd,
    MemberOut,
    ScanOut,
)
from . import person_to_out

router = APIRouter(prefix="/api/groups", tags=["groups"])

log = logging.getLogger(__name__)


def _group_bot_token(group: DevGroup) -> str | None:
    return decrypt(get_settings().secret_key, group.octo_bot_token_enc)


def group_to_out(
    g: DevGroup, tech_lead_ids: list[int] | None = None, project_name: str | None = None
) -> dict:
    return {
        "id": g.id,
        "name": g.name,
        "project_id": g.project_id,
        "project_name": project_name,
        "gitlab_base_url": g.gitlab_base_url,
        "gitlab_bot_username": g.gitlab_bot_username,
        "octo_group_no": g.octo_group_no,
        "am_team_id": g.am_team_id,
        "scan_enabled": g.scan_enabled,
        "auto_webhook": g.auto_webhook,
        "tech_lead_person_ids": tech_lead_ids or [],
        "llm_base_url": g.llm_base_url,
        "llm_model": g.llm_model,
        "llm_effort": g.llm_effort,
        "has_gitlab_token": bool(g.gitlab_token_enc),
        "has_llm_key": bool(g.llm_api_key_enc),
        "has_octo_bot_token": bool(g.octo_bot_token_enc),
    }


def _apply(body: GroupIn | GroupUpd, group: DevGroup) -> None:
    s = get_settings().secret_key
    data = body.model_dump(exclude_unset=True, exclude={"regenerate_webhook_secret"})
    for k in (
        "name",
        "gitlab_base_url",
        "gitlab_bot_username",
        "octo_group_no",
        "am_team_id",
        "scan_enabled",
        "auto_webhook",
        "llm_base_url",
        "llm_model",
    ):
        if k in data and data[k] is not None:
            setattr(group, k, data[k])
    if "project_id" in data:  # 传 null = 解绑项目
        group.project_id = data["project_id"]
    if "gitlab_token" in data:
        group.gitlab_token_enc = encrypt(s, data["gitlab_token"]) if data["gitlab_token"] else None
    if "octo_bot_token" in data:  # 空 = 清除，回退全局 bot
        group.octo_bot_token_enc = encrypt(s, data["octo_bot_token"]) if data["octo_bot_token"] else None
    if "llm_api_key" in data:
        group.llm_api_key_enc = encrypt(s, data["llm_api_key"]) if data["llm_api_key"] else None
    if "llm_effort" in data:  # 空串/null = 清除（落到兜底）
        group.llm_effort = data["llm_effort"] or None


async def _get_group(db: AsyncSession, group_id: int) -> DevGroup:
    group = await db.get(DevGroup, group_id)
    if not group:
        raise HTTPException(404, "研发组不存在")
    return group


async def _check_project(db: AsyncSession, project_id: int | None) -> None:
    if project_id is not None and not await db.get(Project, project_id):
        raise HTTPException(400, "项目不存在")


async def _check_bot_account(db: AsyncSession, group: DevGroup) -> None:
    """同一个 GitLab 实例的同一个账号不能同时给两个组当机器账号（权限隔离的前提）。"""
    if not group.gitlab_bot_username or not group.gitlab_base_url:
        return
    dup = await db.scalar(
        select(DevGroup.id).where(
            DevGroup.gitlab_base_url == group.gitlab_base_url,
            DevGroup.gitlab_bot_username == group.gitlab_bot_username,
            DevGroup.id != group.id,
        )
    )
    if dup:
        raise HTTPException(400, "该 GitLab 账号已被其他研发组用作机器账号")


# ---------- 保存后自动触发（后台任务用自己的 session，请求 session 响应后即关闭） ----------


async def _bg_scan(group_id: int) -> None:
    try:
        async with sessionmaker()() as db:
            group = await db.get(DevGroup, group_id)
            if group:
                await scan_projects(db, group)
    except Exception:  # noqa: BLE001 自动触发失败不阻塞保存，下次定时扫描兜底
        log.warning("auto scan_projects for group %s failed", group_id, exc_info=True)


async def _bg_sync_members(group_id: int) -> None:
    try:
        async with sessionmaker()() as db:
            group = await db.get(DevGroup, group_id)
            if group:
                await sync_members_and_match(db, group)
    except Exception:  # noqa: BLE001
        log.warning("auto sync_members for group %s failed", group_id, exc_info=True)


def _auto_triggers(background: BackgroundTasks, before: tuple | None, group: DevGroup) -> None:
    """gitlab 侧配置变了 → 扫项目；octo 侧变了 → 同步群成员。before=None 表示新建。"""
    b = before or ("", None, "", None)
    if group.gitlab_base_url and group.gitlab_token_enc and (
        (group.gitlab_base_url, group.gitlab_token_enc) != (b[0], b[1])
    ):
        background.add_task(_bg_scan, group.id)
    if group.octo_group_no and (group.octo_group_no, group.octo_bot_token_enc) != (b[2], b[3]):
        background.add_task(_bg_sync_members, group.id)


@router.get("", response_model=list[GroupOut])
async def list_groups(
    project_id: int | None = None,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> list[GroupOut]:
    q = select(DevGroup).order_by(DevGroup.id)
    if project_id:
        q = q.where(DevGroup.project_id == project_id)
    rows = (await db.scalars(q)).all()
    projects = {p.id: p.name for p in (await db.scalars(select(Project))).all()}
    return [
        group_to_out(g, await person_ids(db, GroupTechLead, group_id=g.id), projects.get(g.project_id))
        for g in rows
    ]


@router.post("", response_model=GroupOut)
async def create_group(
    body: GroupIn,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> GroupOut:
    await _check_project(db, body.project_id)
    group = DevGroup(webhook_secret=secrets.token_hex(16))
    _apply(body, group)
    await _check_bot_account(db, group)
    db.add(group)
    await db.flush()
    await set_persons(db, GroupTechLead, body.tech_lead_person_ids, "技术负责人", group_id=group.id)
    await db.commit()
    await db.refresh(group)
    _auto_triggers(background, None, group)
    return group_to_out(
        group,
        body.tech_lead_person_ids,
        (await db.get(Project, group.project_id)).name if group.project_id else None,
    )


@router.put("/{group_id}", response_model=GroupOut)
async def update_group(
    group_id: int,
    body: GroupUpd,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> GroupOut:
    group = await _get_group(db, group_id)
    before = (group.gitlab_base_url, group.gitlab_token_enc, group.octo_group_no, group.octo_bot_token_enc)
    _apply(body, group)
    if "project_id" in body.model_fields_set:
        await _check_project(db, group.project_id)
    await _check_bot_account(db, group)
    if body.regenerate_webhook_secret:
        group.webhook_secret = secrets.token_hex(16)
    if "tech_lead_person_ids" in body.model_fields_set:
        await set_persons(db, GroupTechLead, body.tech_lead_person_ids, "技术负责人", group_id=group.id)
    await db.commit()
    await db.refresh(group)
    _auto_triggers(background, before, group)
    return group_to_out(
        group,
        await person_ids(db, GroupTechLead, group_id=group.id),
        (await db.get(Project, group.project_id)).name if group.project_id else None,
    )


@router.delete("/{group_id}")
async def delete_group(
    group_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> dict:
    group = await _get_group(db, group_id)
    await db.delete(group)
    await db.commit()
    return {"ok": True}


@router.post("/{group_id}/code-report")
async def code_report(
    group_id: int,
    kind: str = "daily",
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> dict:
    """手动触发一次代码报表（daily 会先重采昨日数据），用于验证与补发。"""
    group = await _get_group(db, group_id)
    if kind not in ("daily", "weekly", "monthly"):
        raise HTTPException(400, "kind 只支持 daily/weekly/monthly")
    if kind == "daily":
        return await daily_commit_stats(db, group)
    start, end = report_range(kind)
    ok = await notify(
        db,
        group,
        await build_code_report(db, group, start, end, kind),
        reason=f"code_report_{kind}",
        mention=await _group_tech_leads(db, group),
    )
    return {"ok": ok, "start": str(start), "end": str(end)}


@router.post("/{group_id}/octo-test")
async def octo_test(
    group_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> dict:
    """发一条测试消息验证本组 bot 通道（组 token 优先，未配则全局）。"""
    group = await _get_group(db, group_id)
    ok = await notify(db, group, f"✅ Vega 测试消息：研发组「{group.name}」bot 通道正常", reason="octo_test")
    return {"ok": ok, "dedicated": bool(group.octo_bot_token_enc)}


# ---------- 成员与对配 ----------


@router.get("/{group_id}/members", response_model=list[MemberOut])
async def list_members(
    group_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> list[MemberOut]:
    group = await _get_group(db, group_id)
    base = (group.gitlab_base_url or "").rstrip("/")
    rows = (
        await db.scalars(
            select(GroupMember)
            .where(GroupMember.group_id == group_id)
            .options(selectinload(GroupMember.person))
            .order_by(GroupMember.id)
        )
    ).all()
    # 一次取出全组成员名下的账号，避免逐成员查库
    by_person: dict[int, list[PersonGitlabAccount]] = {}
    if rows:
        for a in (
            await db.scalars(
                select(PersonGitlabAccount)
                .where(PersonGitlabAccount.person_id.in_([gm.person_id for gm in rows]))
                .order_by(PersonGitlabAccount.id)
            )
        ).all():
            # 只列出本组 GitLab 实例下的账号；选了别的实例后端也会拒绝
            if not base or a.base_url.rstrip("/") == base:
                by_person.setdefault(a.person_id, []).append(a)
    return [await _member_out(db, gm, by_person.get(gm.person_id, [])) for gm in rows]


async def _member_out(
    db: AsyncSession, gm: GroupMember, accounts: list[PersonGitlabAccount] | None = None
) -> dict:
    acc = await db.get(PersonGitlabAccount, gm.gitlab_account_id) if gm.gitlab_account_id else None
    if accounts is None:
        accounts = list(
            (
                await db.scalars(
                    select(PersonGitlabAccount).where(PersonGitlabAccount.person_id == gm.person_id)
                )
            ).all()
        )
    return MemberOut(
        id=gm.id,
        person_id=gm.person_id,
        person_name=gm.person.name if gm.person else "",
        gitlab_username=gm.person.gitlab_username if gm.person else None,
        gitlab_email=gm.person.gitlab_email if gm.person else None,
        gitlab_account_id=gm.gitlab_account_id,
        gitlab_account_username=acc.username if acc else None,
        gitlab_accounts=[
            {
                "id": a.id,
                "base_url": a.base_url,
                "gitlab_user_id": a.gitlab_user_id or 0,
                "username": a.username,
                "name": a.name,
                "email": a.email,
                "has_token": bool(a.token_enc),
            }
            for a in accounts
        ],
        octo_uid=gm.octo_uid,
        octo_nickname=gm.octo_nickname,
        match_status=gm.match_status,
        match_confidence=gm.match_confidence,
        match_reason=gm.match_reason,
        manual_lock=gm.manual_lock,
    ).model_dump()


@router.put("/{group_id}/members/{person_id}/account", response_model=MemberOut)
async def set_member_account(
    group_id: int,
    person_id: int,
    body: MemberAccountIn,
    db: AsyncSession = Depends(get_db),
    me: Person = Depends(current_person),
) -> MemberOut:
    """选择「我在本组用哪个 GitLab 账号」。本人或管理员可改；一组只许一个账号。"""
    if not me.is_admin and me.id != person_id:
        raise HTTPException(403, "只能设置自己的账号")
    group = await _get_group(db, group_id)
    gm = await db.scalar(
        select(GroupMember)
        .where(GroupMember.group_id == group_id, GroupMember.person_id == person_id)
        .options(selectinload(GroupMember.person))
    )
    if not gm:
        raise HTTPException(404, "成员不存在")
    acc = None
    if body.gitlab_account_id:
        acc = await db.get(PersonGitlabAccount, body.gitlab_account_id)
        if not acc or acc.person_id != person_id:
            raise HTTPException(404, "账号不存在或不属于该成员")
        if group.gitlab_base_url and acc.base_url.rstrip("/") != group.gitlab_base_url.rstrip("/"):
            raise HTTPException(400, "该账号不属于本组使用的 GitLab 实例")
        holder = await db.scalar(
            select(GroupMember.id).where(
                GroupMember.gitlab_account_id == acc.id, GroupMember.id != gm.id
            )
        )
        if holder:
            raise HTTPException(400, "该账号已绑定在其他研发组")
    gm.gitlab_account_id = acc.id if acc else None
    await db.commit()
    await db.refresh(gm)
    return await _member_out(db, gm)


@router.post("/{group_id}/members", response_model=MemberOut)
async def add_member(
    group_id: int,
    body: MemberAdd,
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> MemberOut:
    group = await _get_group(db, group_id)
    person = await db.get(Person, body.person_id)
    if not person:
        raise HTTPException(404, "人员不存在")
    if await db.scalar(select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.person_id == body.person_id)):
        raise HTTPException(400, "已是成员")
    gm = GroupMember(
        group_id=group_id,
        person_id=person.id,
        # 专家 id 只能由 agent-memory 发放（agt-…）；这里不猜，猜错的值会让 proxy
        # 判 header mismatch 去弹交互表单，claude -p 答不了这一轮就作废
        am_agent_id=None,
    )
    db.add(gm)
    await db.commit()
    await db.refresh(gm)
    return await _member_out(db, gm)


@router.delete("/{group_id}/members/{person_id}")
async def remove_member(
    group_id: int,
    person_id: int,
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> dict:
    gm = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.person_id == person_id)
    )
    if gm:
        await db.delete(gm)
        await db.commit()
    return {"ok": True}


@router.get("/{group_id}/octo-members")
async def octo_members(
    group_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> dict:
    """实时群成员（对配页 B 侧名单）。error 非空时前端亮出原因，不再静默显示 0 人。"""
    group = await _get_group(db, group_id)
    members, err = octo.group_members_ex(group.octo_group_no or "", token=_group_bot_token(group))
    return {"items": [{"uid": u, "nickname": n} for u, n in members.items()], "error": err}


@router.get("/{group_id}/octo-groups")
async def octo_groups(
    group_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> dict:
    """该组 bot 所在的群列表（编辑表单的群号下拉数据源）。"""
    group = await _get_group(db, group_id)
    items, err = octo.list_groups(token=_group_bot_token(group))
    return {"items": items, "error": err, "dedicated": bool(group.octo_bot_token_enc)}


async def _gitlab_snapshot(db: AsyncSession, group: DevGroup) -> tuple[list[GitlabMember], str | None]:
    rows = list(
        (
            await db.scalars(
                select(GitlabMember)
                .where(GitlabMember.group_id == group.id)
                .order_by(GitlabMember.username)
            )
        ).all()
    )
    synced = max((r.synced_at for r in rows), default=None)
    return rows, (str(synced) if synced else None)


@router.get("/{group_id}/gitlab-users")
async def gitlab_users(
    group_id: int, db: AsyncSession = Depends(get_db), me: Person = Depends(current_person)
) -> dict:
    """成员抽屉 GitLab 绑定下拉：读本组成员快照（DB），只有从没同步过才现场拉一次。"""
    group = await _get_group(db, group_id)
    rows, synced = await _gitlab_snapshot(db, group)
    if not rows:
        await refresh_gitlab_members(db, group)
        rows, synced = await _gitlab_snapshot(db, group)
    return {
        "items": [{"username": r.username, "name": r.name, "email": r.email} for r in rows],
        "error": None if rows else "暂无 GitLab 成员（未配置 token、没有项目，或拉取失败）",
        "synced_at": synced,
    }


@router.post("/{group_id}/gitlab-users/refresh")
async def gitlab_users_refresh(
    group_id: int, db: AsyncSession = Depends(get_db), _me: Person = Depends(require_admin)
) -> dict:
    """重拉本组所有项目成员并整组覆盖快照（抽屉上的「刷新 GitLab 名单」）。"""
    group = await _get_group(db, group_id)
    _members, ok = await refresh_gitlab_members(db, group)
    if not ok:
        raise HTTPException(502, "GitLab 成员拉取失败（检查组 token 与项目权限）")
    rows, synced = await _gitlab_snapshot(db, group)
    return {
        "items": [{"username": r.username, "name": r.name, "email": r.email} for r in rows],
        "synced_at": synced,
    }


@router.put("/{group_id}/gitlab-match/{person_id}")
async def gitlab_match(
    group_id: int,
    person_id: int,
    body: GitlabMatchIn,
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> dict:
    """人工把本组成员的 GitLab 身份绑到他的账号表记录上。

    账号以 (GitLab 实例, username) 为唯一键；改绑到别人时账号整体转手，
    并解除其在其他研发组的占用（一组只用一个账号）。
    """
    gm = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.person_id == person_id)
    )
    if not gm:
        raise HTTPException(404, "成员不存在")
    username = (body.gitlab_username or "").strip()
    if not username:
        gm.gitlab_account_id = None
        await db.commit()
        return {"ok": True, "released": None}
    group = await _get_group(db, group_id)
    base = (group.gitlab_base_url or "").rstrip("/")
    if not base:
        raise HTTPException(400, "研发组未配置 GitLab 地址")
    acc = await db.scalar(
        select(PersonGitlabAccount).where(
            PersonGitlabAccount.base_url == base, PersonGitlabAccount.username == username
        )
    )
    released = None
    if acc and acc.person_id != person_id:
        holder = await db.get(Person, acc.person_id)
        released = holder.name if holder else None
        acc.person_id = person_id
    if not acc:
        info = await db.scalar(
            select(GitlabMember).where(
                GitlabMember.group_id == group_id, GitlabMember.username == username
            )
        )
        acc = PersonGitlabAccount(
            person_id=person_id,
            base_url=base,
            username=username,
            name=info.name if info else "",
            email=info.email if info else "",
        )
        db.add(acc)
        await db.flush()
    # 一个账号在一个研发组只用一次；账号整体转手时，旧的组内绑定一并解除
    for other in (
        await db.scalars(select(GroupMember).where(GroupMember.gitlab_account_id == acc.id))
    ).all():
        if other.id != gm.id:
            other.gitlab_account_id = None
    gm.gitlab_account_id = acc.id
    await db.commit()
    return {"ok": True, "released": released}


@router.post("/{group_id}/match/run", response_model=MatchRunOut)
async def run_match(
    group_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> MatchRunOut:
    group = await _get_group(db, group_id)
    return MatchRunOut(stats=await sync_members_and_match(db, group))


async def _release_octo_uid(db: AsyncSession, uid: str, keeper: Person) -> str | None:
    """一个 octo 群成员只能挂一个人：改绑时把旧持有者解绑，返回其姓名。

    跨研发组生效——uid 是人的身份，同一个 uid 挂在两个人身上就是重复计数。
    """
    released = None
    rows = (
        await db.scalars(
            select(GroupMember).where(GroupMember.octo_uid == uid, GroupMember.person_id != keeper.id)
        )
    ).all()
    for gm in rows:
        gm.octo_uid, gm.octo_nickname = None, None
        gm.match_status, gm.match_confidence, gm.manual_lock = "unmatched", None, False
        gm.match_reason = f"改绑到 {keeper.name}"
        if released is None:
            old = await db.get(Person, gm.person_id)
            released = old.name if old else None
    for p in (
        await db.scalars(select(Person).where(Person.octo_uid == uid, Person.id != keeper.id))
    ).all():
        p.octo_uid = None
        released = released or p.name
    return released


@router.put("/{group_id}/match/{person_id}")
async def manual_match(
    group_id: int,
    person_id: int,
    body: ManualMatchIn,
    db: AsyncSession = Depends(get_db),
    admin: Person = Depends(require_admin),
) -> dict:
    """人工调整：绑定/解绑并锁定，自动对配永不覆盖。"""
    gm = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == group_id, GroupMember.person_id == person_id)
    )
    if not gm:
        raise HTTPException(404, "成员不存在")
    person = await db.get(Person, person_id)
    group = await _get_group(db, group_id)
    members, m_err = octo.group_members_ex(group.octo_group_no or "", token=_group_bot_token(group))
    if m_err:
        raise HTTPException(400, f"拉不到群成员：{m_err}")
    old_uid = gm.octo_uid
    released = None
    if body.octo_uid:
        if body.octo_uid not in members:
            raise HTTPException(400, "该 uid 不在群成员列表里")
        released = await _release_octo_uid(db, body.octo_uid, person)
        gm.octo_uid = body.octo_uid
        gm.octo_nickname = members[body.octo_uid]
        gm.match_status, gm.match_confidence, gm.match_reason = "manual", 1.0, "人工设定"
        gm.manual_lock = True
        person.octo_uid = body.octo_uid
    else:
        gm.octo_uid, gm.octo_nickname = None, None
        gm.match_status, gm.match_reason = "unmatched", "人工解绑"
        gm.manual_lock = False
        if old_uid and person.octo_uid == old_uid:
            person.octo_uid = None
    await db.commit()
    return {"ok": True, "released": released}


# ---------- 扫描 ----------


@router.post("/{group_id}/scan", response_model=ScanOut)
async def trigger_scan(
    group_id: int, db: AsyncSession = Depends(get_db), admin: Person = Depends(require_admin)
) -> ScanOut:
    group = await _get_group(db, group_id)
    result = await scan_projects(db, group)
    return ScanOut(**result)
