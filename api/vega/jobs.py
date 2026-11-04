"""定时任务与事件处理的业务实现（APScheduler 与 webhook 共用）。"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import octo
from .config import get_settings
from .crypto import decrypt
from .gitlab import GitlabClient, client_ctx
from .llm import resolve_llm
from .llm_client import LLMError, ask_llm
from .matching import run_matching
from .models import (
    CommitFile,
    DailyStat,
    DevGroup,
    DevTask,
    FileStat,
    GitlabEvent,
    GitlabMember,
    GroupMember,
    GroupTechLead,
    System,
    SystemOwner,
    SystemPM,
    Person,
    PersonGitlabAccount,
    Project,
    Requirement,
    utcnow,
)
from .notify import merge_mentions, notify, notify_project, project_owners
from .reports import build_code_report

log = logging.getLogger(__name__)

MR_BLOCK_DAYS = 3
MR_BLOCK_ESCALATE_DAYS = 7
MR_BLOCK_FINAL_DAYS = 10  # 项目负责人也没动静：升到技术负责人
MR_ESCALATE_HOURS = 12  # 任务 MR 无人处理：每 12h 升一级，3 级封顶
HOT_FILE_MAX_COMMITS = 200  # 每项目每天最多拉多少个提交的 diff（控制 GitLab 调用量）


def _gitlab(group: DevGroup) -> GitlabClient | None:
    token = decrypt(get_settings().secret_key, group.gitlab_token_enc)
    if not (group.gitlab_base_url and token):
        return None
    return GitlabClient(group.gitlab_base_url, token)


async def _link_persons(db: AsyncSession, model, **owner_filter) -> list[Person]:
    stmt = select(Person).join(model, model.person_id == Person.id)
    for k, v in owner_filter.items():
        stmt = stmt.where(getattr(model, k) == v)
    return list((await db.scalars(stmt)).all())


async def _group_tech_leads(db: AsyncSession, group: DevGroup) -> list[Person]:
    """研发组负责人：代码日报的 @ 对象，也是项目群汇报人。"""
    return await _link_persons(db, GroupTechLead, group_id=group.id)


async def _gitlab_identity_maps(
    db: AsyncSession, group: DevGroup
) -> tuple[dict[str, Person], dict[str, Person]]:
    """本组的 GitLab 身份 → 人：账号表优先，旧 person.gitlab_* 兜底。"""
    rows = (
        await db.execute(
            select(PersonGitlabAccount, Person)
            .join(Person, Person.id == PersonGitlabAccount.person_id)
            .join(GroupMember, GroupMember.person_id == Person.id)
            .where(GroupMember.group_id == group.id)
        )
    ).all()
    by_user = {a.username: p for a, p in rows}
    by_email = {a.email: p for a, p in rows if a.email}
    for p in (await db.scalars(select(Person))).all():
        if p.gitlab_username:
            by_user.setdefault(p.gitlab_username, p)
        if p.gitlab_email:
            by_email.setdefault(p.gitlab_email, p)
    return by_user, by_email


async def _tech_leads(db: AsyncSession, project: System, group: DevGroup) -> list[Person]:
    """技术负责人：项目级优先，没配落组级。"""
    return await _link_persons(db, SystemPM, system_id=project.id) or await _group_tech_leads(db, group)


async def _owner_targets(db: AsyncSession, project: System, group: DevGroup) -> list[Person]:
    """卡 MR 一级升级对象：系统负责人；没配就直接升到技术负责人。"""
    return await _link_persons(db, SystemOwner, system_id=project.id) or await _tech_leads(
        db, project, group
    )


async def scan_projects(db: AsyncSession, group: DevGroup) -> dict:
    """扫描有管理权的项目入库 + 自动配置 webhook（幂等）。"""
    gl = _gitlab(group)
    if not gl:
        return {"error": "研发组未配置 GitLab"}
    base = get_settings().api_url.rstrip("/")
    webhook_url = f"{base}/webhooks/gitlab"  # 全局单一地址，靠每组 secret 识别
    old_prefix = f"{webhook_url}/"  # 历史单组地址（/webhooks/gitlab/{id}），扫到就清掉
    added, hooks = 0, 0
    async with client_ctx() as client:
        projects = await gl.list_admin_projects(client)
        for p in projects:
            proj = await db.scalar(
                select(System).where(
                    System.group_id == group.id, System.gitlab_project_id == p["id"]
                )
            )
            if not proj:
                path = p.get("path_with_namespace") or p["path"]
                prefix = (p.get("path") or "PRJ")[:12].upper().replace("-", "_")
                db.add(
                    System(
                        group_id=group.id,
                        gitlab_project_id=p["id"],
                        path=path,
                        name=p.get("name") or path,
                        code_prefix=prefix,
                        default_branch=p.get("default_branch") or "master",
                    )
                )
                added += 1
        await db.commit()
        if group.auto_webhook and group.webhook_secret:
            events = ["push", "merge_request", "pipeline"]
            for p in projects:
                if await gl.ensure_webhook(client, p["id"], webhook_url, group.webhook_secret, events):
                    hooks += 1
                for hook in await gl.list_webhooks(client, p["id"]):
                    if hook.get("url", "").startswith(old_prefix):
                        await gl.delete_webhook(client, p["id"], hook["id"])
    return {"projects_added": added, "webhooks_added": hooks}


async def _gitlab_project_members(db: AsyncSession, group: DevGroup) -> tuple[dict[str, dict], bool]:
    """本组所有项目的 GitLab 成员并集 {username: member}。

    第二个值表示「这次名单是否完整」：没配 GitLab / 有项目拉取失败时为 False，
    此时不能拿结果覆盖快照（否则一次网络抖动就把名单清空）。
    """
    gl = _gitlab(group)
    if not gl:
        return {}, False
    project_ids = list(
        (await db.scalars(select(System.gitlab_project_id).where(System.group_id == group.id))).all()
    )
    if not project_ids:
        return {}, True  # 本组确实没有项目 = 空名单，可以覆盖
    out: dict[str, dict] = {}
    ok = True
    try:
        async with client_ctx() as client:
            for pid in project_ids:
                try:
                    for m in await gl.list_project_members(client, pid):
                        if m.get("username"):
                            out.setdefault(m["username"], m)
                except Exception:  # noqa: BLE001 一个项目 403/404 不能把整组名单清空
                    ok = False
                    log.warning("gitlab members fetch failed for project %s", pid, exc_info=True)
    except Exception:  # noqa: BLE001 GitLab 拉不到不阻塞 octo 对配
        log.warning("gitlab members fetch failed for group %s", group.id, exc_info=True)
        return out, False
    return out, ok


async def _store_gitlab_members(db: AsyncSession, group: DevGroup, members: dict[str, dict]) -> None:
    """整组覆盖成员快照（不 commit，调用方统一提交）。"""
    await db.execute(delete(GitlabMember).where(GitlabMember.group_id == group.id))
    now = utcnow()
    for username, m in members.items():
        db.add(
            GitlabMember(
                group_id=group.id,
                username=username,
                name=m.get("name") or "",
                email=m.get("email") or "",
                synced_at=now,
            )
        )


async def refresh_gitlab_members(db: AsyncSession, group: DevGroup) -> tuple[dict[str, dict], bool]:
    """拉一次 GitLab 名单并覆盖快照；ok=False 时保留旧快照。"""
    members, ok = await _gitlab_project_members(db, group)
    if ok:
        await _store_gitlab_members(db, group, members)
        await db.commit()
    return members, ok


async def _sync_gitlab_people(db: AsyncSession, group: DevGroup) -> tuple[int, int]:
    """GitLab 成员名单是研发身份的权威来源：按 (GitLab 实例, username) 建档并挂本组。

    同一个人的多个 GitLab 账号落在 person_gitlab_account；Person 本身不再存唯一
    gitlab_username。账号先按 (base_url, username) 找，找不到再看旧字段；
    都找不到才新建 Person。成员加入本组时若还没选定账号，自动绑上本组这份身份。
    """
    members, ok = await _gitlab_project_members(db, group)
    if ok:
        await _store_gitlab_members(db, group, members)  # 每次成功的拉取都整组覆盖快照
    if not members:
        if ok:
            await db.commit()
        return 0, 0
    base = (group.gitlab_base_url or "").rstrip("/")
    accounts = {
        a.username: a
        for a in (
            await db.scalars(
                select(PersonGitlabAccount).where(
                    PersonGitlabAccount.base_url == base,
                    PersonGitlabAccount.username.in_(list(members)),
                )
            )
        ).all()
    } if base else {}
    legacy = {
        p.gitlab_username: p
        for p in (
            await db.scalars(select(Person).where(Person.gitlab_username.in_(list(members))))
        ).all()
    }
    in_group = set(
        (await db.scalars(select(GroupMember.person_id).where(GroupMember.group_id == group.id))).all()
    )
    created = merged = 0
    for username, m in members.items():
        acc = accounts.get(username)
        person = await db.get(Person, acc.person_id) if acc else None
        if not person and username in legacy:
            person = legacy[username]
            merged += 1
        if not person and m.get("name"):
            # 同人合并：仅「唯一精确同名且既无账号也无旧 gitlab_username」才复用；
            # 同名歧义/昵称改名的的不自动猜，留给成员抽屉的人工绑定
            person_ids = set(
                (await db.scalars(select(PersonGitlabAccount.person_id))).all()
            )
            cands = [
                p
                for p in (
                    await db.scalars(
                        select(Person).where(
                            Person.gitlab_username.is_(None), Person.name == m["name"]
                        )
                    )
                ).all()
                if p.id not in person_ids
            ]
            if len(cands) == 1:
                person = cands[0]
                merged += 1
        if not person:
            person = Person(name=m.get("name") or username)
            db.add(person)
            await db.flush()
            created += 1
        if not acc and base:
            acc = PersonGitlabAccount(
                person_id=person.id,
                base_url=base,
                username=username,
                name=m.get("name") or "",
                email=m.get("email") or "",
            )
            db.add(acc)
            await db.flush()
            accounts[username] = acc
        if person.id not in in_group:
            # 同上：GitLab 用户名不是 agent-memory 的专家 id，存进去只会让
            # proxy 判 mismatch 弹表单。专家由人员在 Vega 里显式选。
            db.add(GroupMember(group_id=group.id, person_id=person.id, gitlab_account_id=acc.id if acc else None))
            in_group.add(person.id)
        else:
            gm = await db.scalar(
                select(GroupMember).where(
                    GroupMember.group_id == group.id, GroupMember.person_id == person.id
                )
            )
            if gm and not gm.gitlab_account_id and acc:
                gm.gitlab_account_id = acc.id
    if created or merged or ok:
        await db.commit()
    return created, merged


async def sync_members_and_match(db: AsyncSession, group: DevGroup) -> dict:
    """三方对配：Vega 用户（手工 + GitLab 建档）↔ GitLab 账号（权威身份）↔ octo 群成员（仅人类，bot 已滤）。
    对配后群里仍未被认领的人自动建档兜底。"""
    members, m_err = octo.group_members_ex(
        group.octo_group_no or "",
        token=decrypt(get_settings().secret_key, group.octo_bot_token_enc),
    )
    if not members:
        return {"error": m_err or "群成员为空"}

    gitlab_created, gitlab_merged = await _sync_gitlab_people(db, group)

    async def load_gm_rows() -> list[GroupMember]:
        return list(
            (
                await db.scalars(
                    select(GroupMember)
                    .where(GroupMember.group_id == group.id)
                    .options(selectinload(GroupMember.person))
                )
            ).all()
        )

    gm_rows = await load_gm_rows()
    # 昵称回显（无论是否匹配上）
    by_uid = {gm.octo_uid: gm for gm in gm_rows if gm.octo_uid}
    for uid, nickname in members.items():
        if uid in by_uid:
            by_uid[uid].octo_nickname = nickname

    stats = await run_matching(db, group, gm_rows, members)

    # octo 兜底建档：对配跑完后群里还没被任何成员认领的人（bot 已在 octo 解析层滤掉），
    # 直接建 Person 并锁定绑定。放在对配之后，避免和 GitLab 建档/自动对配抢同一个人。
    gm_rows = await load_gm_rows()
    by_uid = {gm.octo_uid for gm in gm_rows if gm.octo_uid}
    known = {
        p.octo_uid: p
        for p in (await db.scalars(select(Person).where(Person.octo_uid.in_(list(members))))).all()
    }
    in_group = {gm.person_id for gm in gm_rows}
    octo_created = 0
    for uid, nickname in members.items():
        if uid in by_uid:
            continue
        person = known.get(uid)
        if not person:
            person = Person(name=nickname, octo_uid=uid)
            db.add(person)
            await db.flush()
            octo_created += 1
        if person.id in in_group:
            continue
        db.add(
            GroupMember(
                group_id=group.id,
                person_id=person.id,
                octo_uid=uid,
                octo_nickname=nickname,
                match_status="manual",
                match_confidence=1.0,
                match_reason="群成员自动建档",
                manual_lock=True,
            )
        )
        in_group.add(person.id)
    if octo_created:
        await db.commit()

    stats["gitlab_created"] = gitlab_created
    stats["gitlab_merged"] = gitlab_merged
    stats["octo_created"] = octo_created
    return stats


def _diff_lines(diff: str) -> tuple[int, int]:
    """13.12 的 commit diff 不带每文件增删行，自己数 +/-（跳过文件头三行标记）。"""
    add = sum(1 for ln in diff.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
    dele = sum(1 for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("---"))
    return add, dele


async def collect_day(
    db: AsyncSession, group: DevGroup, day: date, system_id: int | None = None
) -> dict:
    """采集某天 项目×提交人 / 项目×文件 统计入库，不推送报表（每日任务与补数据共用）。"""
    gl = _gitlab(group)
    if not gl:
        return {"error": "研发组未配置 GitLab"}
    since = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    until = since + timedelta(days=1)
    q = select(System).where(System.group_id == group.id)
    if system_id:
        q = q.where(System.id == system_id)
    projects = list((await db.scalars(q)).all())
    agg: dict[tuple[int, str], dict] = {}
    files: dict[tuple[int, str], dict] = {}
    async with client_ctx() as client:
        from .gitlab import gather_concurrently

        results = await gather_concurrently(
            [
                lambda pid=p.gitlab_project_id: gl.project_commits(client, pid, since=since, until=until)
                for p in projects
            ]
        )
        # 热点文件：逐提交拉 diff，每项目每天封顶 HOT_FILE_MAX_COMMITS 个提交
        targets = [
            (proj, c)
            for proj, commits in zip(projects, results)
            for c in (commits or [])[:HOT_FILE_MAX_COMMITS]
        ]
        diffs = await gather_concurrently(
            [lambda p=proj, c=c: gl.commit_diff(client, p.gitlab_project_id, c["id"]) for proj, c in targets]
        )
    names: dict[str, str] = {}  # author_email → 提交人姓名（git 身份邮箱五花八门，姓名才靠得住）
    for proj, commits in zip(projects, results):
        for c in commits or []:
            author = c.get("author_email") or c.get("author_name") or "unknown"
            names.setdefault(author, c.get("author_name") or "")
            key = (proj.id, author)
            slot = agg.setdefault(key, {"commits": 0, "additions": 0, "deletions": 0})
            slot["commits"] += 1
            stats = c.get("stats") or {}
            slot["additions"] += stats.get("additions") or 0
            slot["deletions"] += stats.get("deletions") or 0
    commit_rows: dict[tuple[int, str, str], CommitFile] = {}
    for (proj, c), diff in zip(targets, diffs):
        raw = (c.get("message") or "").strip()
        msg = raw.splitlines()[0][:200] if raw else ""  # 只留首行：风险里当一句话用
        author = c.get("author_email") or c.get("author_name") or ""
        for f in diff or []:
            path = f.get("new_path") or f.get("old_path")
            if not path:
                continue
            add, dele = _diff_lines(f.get("diff") or "")
            slot = files.setdefault((proj.id, path), {"commits": 0, "additions": 0, "deletions": 0})
            slot["commits"] += 1
            slot["additions"] += add
            slot["deletions"] += dele
            commit_rows[(proj.id, c["id"], path)] = CommitFile(
                group_id=group.id,
                stat_date=day,
                system_id=proj.id,
                path=path,
                sha=c["id"],
                author=author,
                message=msg,
                additions=add,
                deletions=dele,
            )

    # 人员匹配：邮箱整串 → GitLab 账号 → 邮箱前缀（工号@域名 最常见）→ 提交人姓名（重名不猜）
    people = list((await db.scalars(select(Person))).all())
    # 本人名下的 GitLab 身份按账号表匹配；旧字段只作兜底
    acct_rows = (
        await db.execute(
            select(PersonGitlabAccount, Person)
            .join(Person, Person.id == PersonGitlabAccount.person_id)
            .join(GroupMember, GroupMember.person_id == Person.id)
            .where(GroupMember.group_id == group.id)
        )
    ).all()
    by_email = {a.email.lower(): p for a, p in acct_rows if a.email}
    by_username = {a.username.lower(): p for a, p in acct_rows}
    for p in people:
        if p.gitlab_email:
            by_email.setdefault(p.gitlab_email.lower(), p)
        if p.gitlab_username:
            by_username.setdefault(p.gitlab_username.lower(), p)
    name_count = Counter(p.name for p in people)
    by_name = {p.name: p for p in people if name_count[p.name] == 1}
    # 重跑同一天（补数据/重发日报）先清旧行，唯一约束才不会炸
    for model in (DailyStat, FileStat, CommitFile):
        await db.execute(
            model.__table__.delete().where(model.group_id == group.id, model.stat_date == day)
        )
    for (system_id, author), s in agg.items():
        key = author.strip().lower()
        person = (
            by_email.get(key)
            or by_username.get(key)
            or by_username.get(key.split("@")[0])
            or by_name.get((names.get(author) or "").strip())
        )
        db.add(
            DailyStat(
                group_id=group.id,
                stat_date=day,
                system_id=system_id,
                person_id=person.id if person else None,
                gitlab_author=author,
                commits=s["commits"],
                additions=s["additions"],
                deletions=s["deletions"],
            )
        )
    for (system_id, path), s in files.items():
        db.add(FileStat(group_id=group.id, stat_date=day, system_id=system_id, path=path, **s))
    db.add_all(commit_rows.values())
    await db.commit()
    return {"rows": len(agg), "files": len(files), "projects": len(projects)}


async def daily_commit_stats(db: AsyncSession, group: DevGroup, day: date | None = None) -> dict:
    """昨日 项目×提交人 / 项目×文件 统计入库 + 群内代码日报（@ 组技术负责人）。"""
    day = day or (datetime.now(timezone.utc) - timedelta(days=1)).date()
    res = await collect_day(db, group, day)
    if "error" in res:
        return res
    text = await build_code_report(db, group, day, day, "daily")
    leads = await _group_tech_leads(db, group)
    await notify(db, group, text, reason="code_report_daily", mention=leads)
    return {**res, "leads": len(leads)}


async def blocked_mr_scan(db: AsyncSession, group: DevGroup) -> dict:
    """open 超 3 天 @assignee，超 7 天 @系统负责人，超 10 天兜底 @技术负责人。"""
    gl = _gitlab(group)
    if not gl:
        return {"error": "研发组未配置 GitLab"}
    projects = list((await db.scalars(select(System).where(System.group_id == group.id))).all())
    cutoff = utcnow() - timedelta(days=MR_BLOCK_DAYS)
    escalate_cutoff = utcnow() - timedelta(days=MR_BLOCK_ESCALATE_DAYS)
    final_cutoff = utcnow() - timedelta(days=MR_BLOCK_FINAL_DAYS)
    notified = 0
    by_user, by_email = await _gitlab_identity_maps(db, group)
    async with client_ctx() as client:
        for proj in projects:
            mrs = await gl.list_merge_requests(client, proj.gitlab_project_id, state="opened", wip="no")
            for mr in mrs:
                updated = datetime.fromisoformat(mr["updated_at"].replace("Z", "+00:00"))
                if updated > cutoff:
                    continue
                assignee = mr.get("assignee") or {}
                person = by_user.get(assignee.get("username")) or by_email.get(assignee.get("email"))
                author = mr.get("author") or {}
                author_p = by_user.get(author.get("username")) or by_email.get(author.get("email"))
                text = (
                    f"⏳ MR 阻塞提醒：{proj.path} !{mr['iid']}《{mr['title']}》"
                    f"已 {int((utcnow() - updated).total_seconds() // 86400)} 天无动态"
                )
                mention = [person or author_p] if (person or author_p) else []
                await notify(db, group, text, reason="mr_blocked", mention=mention)
                notified += 1
                if updated >= escalate_cutoff:
                    continue
                owners = await _link_persons(db, SystemOwner, system_id=proj.id)
                if not owners:
                    # 没配项目负责人：直接升到技术负责人，不再重复发第二条
                    leads = await _tech_leads(db, proj, group)
                    if leads:
                        await notify(
                            db, group, f"🔔 技术负责人兜底：{text}", reason="mr_blocked_escalate", mention=leads
                        )
                    continue
                await notify(db, group, f"⚠️ 系统负责人升级：{text}", reason="mr_blocked_escalate", mention=owners)
                if updated < final_cutoff:
                    leads = await _tech_leads(db, proj, group)
                    if leads:
                        await notify(
                            db, group, f"🔔 技术负责人兜底：{text}", reason="mr_blocked_final", mention=leads
                        )
                elif group.project_id:
                    # 满 10 天仍未推进：升到项目群（群里是各研发组负责人，一级一级汇报）
                    project = await db.get(Project, group.project_id)
                    if project:
                        await notify_project(
                            db,
                            project,
                            f"📣 项目级提醒：研发组「{group.name}」的 {proj.path} MR 已 10 天以上无动态\n{text}",
                            reason="mr_blocked_project",
                            mention=await _group_tech_leads(db, group),
                            source_group=group,
                        )
    return {"notified": notified}


async def merge_poll(db: AsyncSession) -> dict:
    """13.12 无 merge webhook → 轮询 reviewing 状态任务的 MR。"""
    tasks = list(
        (
            await db.scalars(
                select(DevTask)
                .where(DevTask.status.in_(["mr_created", "reviewing", "cicd"]))
                .options(selectinload(DevTask.requirement))
            )
        ).all()
    )
    merged = closed = 0
    for task in tasks:
        if not task.mr_iid:
            continue
        req = task.requirement
        proj = await db.get(System, req.system_id)
        group = await db.get(DevGroup, proj.group_id)
        gl = _gitlab(group)
        if not gl:
            continue
        async with client_ctx() as client:
            mr = await gl.mr_detail(client, proj.gitlab_project_id, task.mr_iid)
        state = mr.get("state")
        if state == "merged" and task.status != "done":
            task.status = "done"
            merged += 1
            await mark_requirement_online(db, req)
            await _notify_task_final(db, group, req, task, "merged")
        elif state == "closed" and task.status != "done":
            task.status = "done"
            task.error = task.error or MR_CLOSED_MARK
            closed += 1
            await _notify_task_final(db, group, req, task, "closed")
        elif state == "opened":
            await _maybe_escalate(db, group, proj, req, task, mr)
    await db.commit()
    return {"merged": merged, "closed": closed}


# merge_poll 标记 MR 被关闭的哨兵文案；工作台据此区分「MR 关闭」与「已合入」
MR_CLOSED_MARK = "MR 被关闭"


async def mark_requirement_online(db: AsyncSession, req: Requirement) -> None:
    """需求下所有任务都已合入 → 需求置「已上线」；还有在跑/失败/关闭的任务就不动。"""
    tasks = (await db.scalars(select(DevTask).where(DevTask.requirement_id == req.id))).all()
    if tasks and all(t.status == "done" and not (t.error or "").startswith(MR_CLOSED_MARK) for t in tasks):
        req.status = "online"


async def _notify_task_final(
    db: AsyncSession, group: DevGroup, req: Requirement, task: DevTask, kind: str
) -> None:
    """MR 到终态（合并/关闭）→ octo @plan 负责人（=任务创建人=需求认领人）。"""
    creator = await db.get(Person, task.creator_id)
    project = await db.get(System, req.system_id)
    owners = await project_owners(db, project.id) if project else []
    if kind == "merged":
        text = (
            f"✅ 任务 {task.code} 已上线：MR !{task.mr_iid} 已合并到 {project.mr_target_branch}\n"
            f"需求 {req.code}《{req.title}》{task.mr_url or ''}"
        )
    else:
        text = f"🚪 任务 {task.code} 结束（MR 被关闭）：!{task.mr_iid} {task.mr_url or ''}"
    await notify(
        db,
        group,
        text,
        reason="mr_merged" if kind == "merged" else "mr_closed",
        mention=merge_mentions(creator, *owners),
    )


async def _maybe_escalate(
    db: AsyncSession, group: DevGroup, project: System, req: Requirement, task: DevTask, mr: dict
) -> None:
    """MR 一直开放无人管：每 12h 升一级（1~3），每级 @审核人；第 3 级连带 @项目负责人（无则技术负责人）。

    级别单调递增、毕业即止 —— 不随 updated_at 回落重置：三级都已喊过人就不再重复打扰。"""
    updated = mr.get("updated_at")
    if not updated:
        return
    idle_h = (utcnow() - datetime.fromisoformat(updated.replace("Z", "+00:00"))).total_seconds() / 3600
    level = min(3, int(idle_h // MR_ESCALATE_HOURS))
    if level < 1 or level <= task.escalated_level:
        return
    task.escalated_level = level
    creator = await db.get(Person, task.creator_id)
    mention = [creator] if creator else []
    text = (
        f"⏰ MR 无人处理 · 等级 {level}/3：任务 {task.code} 的 MR !{task.mr_iid} "
        f"已 {int(idle_h)} 小时无动态\n需求 {req.code}《{req.title}》{task.mr_url or ''}"
    )
    if level >= 3:
        targets = await _owner_targets(db, project, group)
        if targets:
            mention += targets
            text = f"🔔 MR 无人处理已达最高级，请系统负责人介入\n{text}"
    await notify(db, group, text, reason=f"mr_escalate_l{level}", mention=mention)


async def ci_failure_analysis(db: AsyncSession, group: DevGroup, project: System, payload: dict) -> None:
    """pipeline 失败 → 拉 job 日志 → 组 LLM 分析 → @提交人。"""
    gl = _gitlab(group)
    if not gl:
        return
    user = payload.get("user") or {}
    attrs = payload.get("object_attributes") or {}
    by_user, by_email = await _gitlab_identity_maps(db, group)
    person = by_user.get(user.get("username")) or by_email.get(user.get("email"))
    log_snippet = ""
    try:
        async with client_ctx() as client:
            jobs = await gl.pipeline_jobs(client, project.gitlab_project_id, attrs.get("id"))
            for job in jobs:
                if job.get("status") == "failed":
                    log_snippet += (
                        f"## job {job.get('name')} ({job.get('stage')})\n"
                        + (await gl.job_trace(client, project.gitlab_project_id, job["id"]))[-4000:]
                        + "\n"
                    )
    except Exception:  # noqa: BLE001 日志拉不到也要通知
        log.warning("ci trace fetch failed", exc_info=True)

    analysis = ""
    if log_snippet:
        try:
            target = resolve_llm(None, group, person_first=False)
            analysis = await ask_llm(
                f"GitLab CI 失败了。项目 {project.path}，分支 {attrs.get('ref')}，"
                f"提交 {attrs.get('sha', '')[:8]}。\n失败 job 日志（截尾）：\n{log_snippet}\n"
                "请给出：1) 失败根因一句话 2) 修复建议（要点式，不超过 5 条）。",
                system="你是 CI 排障助手，回答精炼，中文。",
                target=target,
                group=group,
            )
        except LLMError as e:
            analysis = f"（LLM 分析失败：{e}）"

    text = (
        f"❌ CI 失败：{project.path} 分支 {attrs.get('ref')}\n"
        f"{attrs.get('sha', '')[:8]}（{user.get('name') or user.get('username', '')}）\n"
        + (f"\n{analysis}" if analysis else "")
    )
    await notify(db, group, text, reason="ci_failed", mention=[person] if person else [])


async def mark_event_handled(db: AsyncSession, event: GitlabEvent) -> None:
    event.handled_at = utcnow()
    await db.commit()
