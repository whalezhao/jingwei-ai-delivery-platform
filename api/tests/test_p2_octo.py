"""P2：按组 bot token 透传 · GitLab 成员自动建档 · 组保存自动触发 · octo-test。"""

from __future__ import annotations

from sqlalchemy import select

from vega import octo
from vega.crypto import encrypt
from vega.jobs import sync_members_and_match
from vega.models import GroupMember, NotifyLog, Person
from vega.notify import notify


def test_octo_token_fallback(monkeypatch):
    """token=None 落全局 settings，组 token 原样透传。"""
    monkeypatch.setattr(octo, "_token", lambda: "global-tok")
    assert octo._tok(None) == "global-tok"
    assert octo._tok("grp-tok") == "grp-tok"


async def test_notify_uses_group_token(client, db, seeded, monkeypatch):
    seeded["group"].octo_bot_token_enc = encrypt("test-secret", "grp-tok")
    await db.commit()
    captured: dict = {}

    def fake_send(chat_id, body, mention_uids=None, token=None):
        captured.update(chat=chat_id, token=token)
        return True

    monkeypatch.setattr(octo, "send_text", fake_send)
    ok = await notify(db, seeded["group"], "hi", reason="t")
    assert ok is True
    assert captured == {"chat": "88888", "token": "grp-tok"}


async def test_notify_global_token_when_group_unset(client, db, seeded, monkeypatch):
    captured: dict = {}

    def fake_send(chat_id, body, mention_uids=None, token=None):
        captured["token"] = token
        return False

    monkeypatch.setattr(octo, "send_text", fake_send)
    ok = await notify(db, seeded["group"], "hi", reason="t")
    assert ok is False and captured["token"] is None  # None → octo 层落全局


async def test_sync_gitlab_enroll(client, db, seeded, monkeypatch):
    """三方对配：GitLab 项目成员自动建档挂组；octo 群成员不再直接建 Person（防 bot/外人污染）。"""
    existing = Person(name="老王", gitlab_username="laowang", octo_uid=None)
    db.add(existing)
    await db.flush()

    async def fake_gl_members(db_, group):
        return {
            "gl-new": {"username": "gl-new", "name": "新同事", "email": "gl-new@test.cn"},
            "laowang": {"username": "laowang", "name": "老王", "email": "lw@test.cn"},
        }, True

    monkeypatch.setattr("vega.jobs._gitlab_project_members", fake_gl_members)
    # 群成员：u-new 昵称=新同事（与 GitLab 建档的 name 相等 → 规则精确）；
    #         u-only 无任何 GitLab/手工 Person 对应（不允许直接建档）
    monkeypatch.setattr(
        octo,
        "group_members_ex",
        lambda group_no, token=None: ({"u-new": "新同事", "u-only": "路人甲"}, None),
    )

    async def fake_ai(group, people, octo_members):
        return {}

    monkeypatch.setattr("vega.matching.ai_match", fake_ai)
    stats = await sync_members_and_match(db, seeded["group"])

    # GitLab 新账号自动建档；已有 username 不重建
    gl_new = await db.scalar(select(Person).where(Person.gitlab_username == "gl-new"))
    assert gl_new and gl_new.name == "新同事" and gl_new.gitlab_email == "gl-new@test.cn"
    assert (await db.scalar(select(Person).where(Person.gitlab_username == "laowang"))).id == existing.id
    assert stats["gitlab_created"] == 1

    # octo 昵称=姓名 → 规则精确绑定，person.octo_uid 回填（三方串起来）
    assert gl_new.octo_uid == "u-new"
    gm_new = await db.scalar(select(GroupMember).where(GroupMember.person_id == gl_new.id))
    assert gm_new.match_status == "auto_exact"

    # 无人认领的群成员兜底建档（锁定），不是留给对配流程
    passerby = await db.scalar(select(Person).where(Person.name == "路人甲"))
    assert passerby and passerby.octo_uid == "u-only"
    assert stats["octo_created"] == 1
    assert "error" not in stats

    # 幂等：再跑一遍不重复建档/挂组
    await sync_members_and_match(db, seeded["group"])
    rows2 = (
        await db.scalars(select(GroupMember).where(GroupMember.group_id == seeded["group"].id))
    ).all()
    assert len([r for r in rows2 if r.person_id == gl_new.id]) == 1


async def test_sync_octo_enroll_leftover_humans(client, db, seeded, monkeypatch):
    """对配后群里没人认领的人兜底建档（锁定）；已被 GitLab 对配占用的 uid 不重复建。"""
    async def fake_gl_members(db_, group):
        return {"gl-a": {"username": "gl-a", "name": "同事A", "email": "a@test.cn"}}, True

    monkeypatch.setattr("vega.jobs._gitlab_project_members", fake_gl_members)
    # u-a 昵称=同事A → 被规则配给 GitLab 建档的 gl-a；u-left 无人认领 → 兜底建档
    monkeypatch.setattr(
        octo,
        "group_members_ex",
        lambda group_no, token=None: ({"u-a": "同事A", "u-left": "纯群友"}, None),
    )

    async def fake_ai(group, people, octo_members):
        return {}

    monkeypatch.setattr("vega.matching.ai_match", fake_ai)
    stats = await sync_members_and_match(db, seeded["group"])

    left = await db.scalar(select(Person).where(Person.octo_uid == "u-left"))
    assert left and left.name == "纯群友"
    gm_left = await db.scalar(select(GroupMember).where(GroupMember.person_id == left.id))
    assert gm_left.manual_lock and gm_left.match_reason == "群成员自动建档"
    assert stats["octo_created"] == 1

    gl_a = await db.scalar(select(Person).where(Person.gitlab_username == "gl-a"))
    assert gl_a.octo_uid == "u-a"  # GitLab 对配优先占了 u-a，没给 u-a 重复建档
    assert (await db.scalar(select(Person).where(Person.octo_uid == "u-a"))).id == gl_a.id

    # 幂等：再跑不重复建
    await sync_members_and_match(db, seeded["group"])
    rows = (
        await db.scalars(select(GroupMember).where(GroupMember.group_id == seeded["group"].id))
    ).all()
    assert len([r for r in rows if r.octo_uid == "u-left"]) == 1


async def test_gitlab_users_and_match(client, admin_client, db, seeded, monkeypatch):
    """GitLab 账号下拉数据源 + 人工绑定（回填邮箱、账号改绑、解绑）。"""

    async def fake_gl(db_, group):
        return {"zhousan": {"username": "zhousan", "name": "张三", "email": "zs@test.cn"}}, True

    # 快照为空 → GET 现场拉一次并落库；之后读的都是快照
    monkeypatch.setattr("vega.jobs._gitlab_project_members", fake_gl)

    gid = seeded["group"].id
    pid = seeded["person"].id
    r = await admin_client.get(f"/api/groups/{gid}/gitlab-users")
    assert r.json()["items"] == [{"username": "zhousan", "name": "张三", "email": "zs@test.cn"}]

    r = await admin_client.put(
        f"/api/groups/{gid}/gitlab-match/{pid}", json={"gitlab_username": "zhousan"}
    )
    assert r.status_code == 200
    db.expire_all()  # sessionmaker expire_on_commit=False，identity map 里是 seed 旧对象，须显式过期重读
    person = await db.get(Person, pid)
    assert person.gitlab_username == "zhousan" and person.gitlab_email == "zs@test.cn"

    # username 唯一：第二个成员绑同一账号 → 旧持有者（张三）自动解绑，而不是报错
    other = Person(name="李四", gitlab_username=f"lisi-{pid}")
    db.add(other)
    await db.flush()
    other_id = other.id
    db.add(GroupMember(group_id=gid, person_id=other_id, am_agent_id="lisi"))
    await db.commit()
    r = await admin_client.put(
        f"/api/groups/{gid}/gitlab-match/{other_id}", json={"gitlab_username": "zhousan"}
    )
    assert r.status_code == 200 and r.json()["released"] == "张三"
    db.expire_all()
    assert (await db.get(Person, pid)).gitlab_username is None
    assert (await db.get(Person, other_id)).gitlab_username == "zhousan"

    # 解绑
    r = await admin_client.put(f"/api/groups/{gid}/gitlab-match/{other_id}", json={"gitlab_username": None})
    assert r.status_code == 200
    db.expire_all()
    person = await db.get(Person, other_id)
    assert person.gitlab_username is None and person.gitlab_email is None


async def test_gitlab_enroll_merges_octo_person(client, db, seeded, monkeypatch):
    """octo 兜底建档的人后来被 GitLab 扫到：唯一精确同名（无 gitlab_username）复用回填，不建第二条。"""
    async def fake_ai(group, people, octo_members):
        return {}

    monkeypatch.setattr("vega.matching.ai_match", fake_ai)

    # 第一轮：GitLab 无成员，群里只有王二 → 兜底建档（锁定，无 gitlab_username）
    async def gl_empty(db_, group):
        return {}, True

    monkeypatch.setattr("vega.jobs._gitlab_project_members", gl_empty)
    monkeypatch.setattr(octo, "group_members_ex", lambda group_no, token=None: ({"u-x": "王二"}, None))
    await sync_members_and_match(db, seeded["group"])
    wang = await db.scalar(select(Person).where(Person.name == "王二"))
    assert wang and wang.octo_uid == "u-x" and wang.gitlab_username is None

    # 第二轮：GitLab 扫到 username=wanger name=王二 → 复用回填，octo 绑定不动
    async def gl_wanger(db_, group):
        return {"wanger": {"username": "wanger", "name": "王二", "email": "we@test.cn"}}, True

    monkeypatch.setattr("vega.jobs._gitlab_project_members", gl_wanger)
    stats = await sync_members_and_match(db, seeded["group"])
    assert stats["gitlab_created"] == 0 and stats["gitlab_merged"] == 1
    rows = (await db.scalars(select(Person).where(Person.name == "王二"))).all()
    assert len(rows) == 1 and rows[0].id == wang.id
    assert rows[0].gitlab_username == "wanger" and rows[0].gitlab_email == "we@test.cn"
    assert rows[0].octo_uid == "u-x"


async def test_daily_commit_stats_no_double_count(client, db, seeded, monkeypatch):
    """同人跨项目累加，首条不翻倍（曾因 setdefault 放拷贝再恒真累加导致每条目双计）。"""
    from vega.jobs import daily_commit_stats
    from vega.models import Project

    email = seeded["person"].gitlab_email

    async def fake_commits(self, client_, pid, since=None, until=None):
        if pid == seeded["project"].gitlab_project_id:
            return [
                {"author_email": email, "stats": {"additions": 10, "deletions": 2}},
                {"author_email": email, "stats": {"additions": 5, "deletions": 1}},
                {"author_email": "ghost@x.cn", "stats": {"additions": 1, "deletions": 0}},  # 匹配不到人，不进排行
            ]
        return [{"author_email": email, "stats": {"additions": 15, "deletions": 3}}]

    async def fake_diff(self, client_, pid, sha):
        return []

    monkeypatch.setattr("vega.gitlab.GitlabClient.project_commits", fake_commits)
    monkeypatch.setattr("vega.gitlab.GitlabClient.commit_diff", fake_diff)
    p2 = Project(
        group_id=seeded["group"].id, gitlab_project_id=990, path="team/repo2",
        name="repo2", code_prefix="R2", default_branch="master",
    )
    db.add(p2)
    await db.commit()

    sent: dict = {}

    async def fake_notify(db_, group_, content, *, reason, mention=None):
        sent["content"] = content

    monkeypatch.setattr("vega.jobs.notify", fake_notify)

    stats = await daily_commit_stats(db, seeded["group"])
    assert stats["rows"] == 3
    assert "张三" in sent["content"] and "3 次（+30/-6）" in sent["content"]  # 旧 bug 会算成 5 提交 +45/-9


async def test_group_save_triggers(client, admin_client, db, seeded, monkeypatch):
    calls: list[tuple[str, int]] = []

    async def fake_scan(db, group):
        calls.append(("scan", group.id))

    async def fake_sync(db, group):
        calls.append(("sync", group.id))

    monkeypatch.setattr("vega.routers.groups.scan_projects", fake_scan)
    monkeypatch.setattr("vega.routers.groups.sync_members_and_match", fake_sync)

    gid = seeded["group"].id
    # 改 gitlab 地址 → 触发扫描；octo 不变 → 不触发同步
    r = await admin_client.put(f"/api/groups/{gid}", json={"gitlab_base_url": "http://gitlab2.test"})
    assert r.status_code == 200, r.text
    assert ("scan", gid) in calls and ("sync", gid) not in calls

    calls.clear()
    # 只改组名 → 什么都不触发
    r = await admin_client.put(f"/api/groups/{gid}", json={"name": "renamed"})
    assert r.status_code == 200
    assert calls == []

    calls.clear()
    # 新建带全配置的组 → 两个都触发
    r = await admin_client.post(
        "/api/groups",
        json={"name": "ng", "gitlab_base_url": "http://gl.test", "gitlab_token": "t", "octo_group_no": "999"},
    )
    assert r.status_code == 200, r.text
    kinds = {c[0] for c in calls}
    assert kinds == {"scan", "sync"}


async def test_octo_test_endpoint(client, admin_client, db, seeded, monkeypatch):
    seeded["group"].octo_bot_token_enc = encrypt("test-secret", "grp-tok")
    await db.commit()
    monkeypatch.setattr(octo, "send_text", lambda *a, **kw: True)
    r = await admin_client.post(f"/api/groups/{seeded['group'].id}/octo-test")
    assert r.status_code == 200 and r.json() == {"ok": True, "dedicated": True}
    log_row = await db.scalar(select(NotifyLog).where(NotifyLog.reason == "octo_test"))
    assert log_row and log_row.ok is True

    # 非管理员不可用
    from conftest import login_as

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    r = await client.post(
        f"/api/groups/{seeded['group'].id}/octo-test", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 403


async def test_group_out_has_bot_flag(client, admin_client, seeded):
    gid = seeded["group"].id
    r = await admin_client.put(f"/api/groups/{gid}", json={"octo_bot_token": "tok-1"})
    assert r.json()["has_octo_bot_token"] is True
    r = await admin_client.put(f"/api/groups/{gid}", json={"octo_bot_token": ""})
    assert r.json()["has_octo_bot_token"] is False  # 空串清除 → 回退全局


async def test_webhook_info_gone(client, admin_client, seeded):
    r = await admin_client.get(f"/api/groups/{seeded['group'].id}/webhook-info")
    assert r.status_code in (404, 405)
