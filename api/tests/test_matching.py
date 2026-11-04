"""三段式对配：规则精确 / AI（阈值）/ 人工锁定不被覆盖。"""

from __future__ import annotations

from vega.matching import AI_CONFIDENCE_THRESHOLD, run_matching
from vega.models import GroupMember, Person


async def test_matching_pipeline(db, seeded, monkeypatch):
    # 群成员：u1 昵称=张三 username（归一化相等）→ 规则命中
    #         u2 昵称=李四_后端（规则不中，李四需 AI）
    #         u3 昵称=小王五（与王五姓名不同 → 规则不中，走 AI 拿低置信度）
    zhangsan_u = seeded["person"].gitlab_username
    members = {"u1": zhangsan_u, "u2": "李四_后端", "u3": "小王五"}
    monkeypatch.setattr("vega.octo.group_members", lambda group_no, token=None: dict(members))

    # 加两个人：李四（需 AI 才能配 u2）、王五（AI 低置信度 → pending）
    # username 加后缀防与 test_crud 建的 lisi 撞唯一约束（库是 session 共享的）
    seq = seeded["person"].id
    lisi = Person(name="李四", gitlab_username=f"lisi-{seq}", gitlab_email=f"lisi-{seq}@test.cn")
    wangwu = Person(name="王五", gitlab_username=f"wangwu-{seq}", gitlab_email=f"wangwu-{seq}@test.cn")
    db.add_all([lisi, wangwu])
    await db.flush()
    db.add_all(
        [
            GroupMember(group_id=seeded["group"].id, person_id=lisi.id, am_agent_id="lisi"),
            GroupMember(group_id=seeded["group"].id, person_id=wangwu.id, am_agent_id="wangwu"),
        ]
    )
    await db.commit()

    from sqlalchemy import select

    gm_rows = (
        await db.scalars(
            select(GroupMember)
            .where(GroupMember.group_id == seeded["group"].id)
            .options(__import__("sqlalchemy.orm", fromlist=["selectinload"]).selectinload(GroupMember.person))
        )
    ).all()

    async def fake_ai(group, people, octo_members, aliases=None):
        out = {}
        for p in people:
            if p.name == "李四":
                out[p.id] = ("u2", 0.95, "昵称含姓名，置信度高")
            elif p.name == "王五":
                out[p.id] = ("u3", 0.4, "重名风险")
        return out

    monkeypatch.setattr("vega.matching.ai_match", fake_ai)

    stats = await run_matching(db, seeded["group"], list(gm_rows), members)

    assert stats["auto_exact"] == 1  # 张三 ← u1（zhangsan）
    assert stats["auto_ai"] == 1  # 李四 ← u2
    assert stats["pending"] == 1  # 王五 → u3 低置信度待确认

    zhangsan_gm = next(g for g in gm_rows if g.person_id == seeded["person"].id)
    assert zhangsan_gm.match_status == "auto_exact"
    assert zhangsan_gm.octo_uid == "u1"
    lisi_gm = next(g for g in gm_rows if g.person_id == lisi.id)
    assert lisi_gm.match_status == "auto_ai" and lisi_gm.octo_uid == "u2"
    assert lisi_gm.match_confidence == 0.95
    wangwu_gm = next(g for g in gm_rows if g.person_id == wangwu.id)
    assert wangwu_gm.match_status == "pending"
    # person.octo_uid 已回填（张三、李四）
    assert (await db.get(Person, seeded["person"].id)).octo_uid == "u1"
    assert (await db.get(Person, lisi.id)).octo_uid == "u2"
    # pending 不回填 person
    assert (await db.get(Person, wangwu.id)).octo_uid is None


async def test_manual_lock_never_overwritten(db, seeded, monkeypatch):
    members = {"u9": "张三本人"}
    zhangsan_gm = seeded["gm"]
    zhangsan_gm.octo_uid = "u9"
    zhangsan_gm.octo_nickname = "张三本人"
    zhangsan_gm.match_status = "manual"
    zhangsan_gm.manual_lock = True
    await db.commit()

    # 别的 uid 明明规则可配，但 manual 锁优先
    members2 = {"u1": seeded["person"].gitlab_username, "u9": "张三本人"}

    async def fake_ai(group, people, octo_members, aliases=None):
        return {}

    monkeypatch.setattr("vega.matching.ai_match", fake_ai)

    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    gm_rows = (
        await db.scalars(
            select(GroupMember)
            .where(GroupMember.group_id == seeded["group"].id)
            .options(selectinload(GroupMember.person))
        )
    ).all()
    stats = await run_matching(db, seeded["group"], list(gm_rows), members2)

    assert stats["kept_manual"] == 1
    zhangsan_gm = await db.get(GroupMember, zhangsan_gm.id)
    assert zhangsan_gm.octo_uid == "u9" and zhangsan_gm.match_status == "manual"


# ---------- 一对一：改绑时旧持有者自动解绑 ----------


async def test_octo_uid_rebind_releases_previous(admin_client, db, seeded, monkeypatch):
    """一个 octo 群成员只能挂一个人：手动改绑后，之前绑的那个人自动解绑。"""
    from sqlalchemy import select

    from vega import octo

    monkeypatch.setattr(
        octo, "group_members_ex", lambda group_no, token=None: ({"rebind-1": "张三", "rebind-2": "李四"}, None)
    )
    gid, p1 = seeded["group"].id, seeded["person"]
    p2 = Person(name="李四", gitlab_username=f"lisi-rebind-{gid}")
    db.add(p2)
    await db.flush()
    db.add(GroupMember(group_id=gid, person_id=p2.id))
    await db.commit()

    p1_id, p2_id = p1.id, p2.id  # expire_all 后取属性会触发异步懒加载，先留 id
    r = await admin_client.put(f"/api/groups/{gid}/match/{p1_id}", json={"octo_uid": "rebind-1"})
    assert r.status_code == 200 and r.json()["released"] is None
    r = await admin_client.put(f"/api/groups/{gid}/match/{p2_id}", json={"octo_uid": "rebind-1"})
    assert r.status_code == 200 and r.json()["released"] == "张三"

    db.expire_all()
    gm1 = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == gid, GroupMember.person_id == p1_id)
    )
    assert gm1.octo_uid is None and gm1.match_status == "unmatched" and not gm1.manual_lock
    assert "改绑到 李四" in gm1.match_reason
    assert (await db.get(Person, p1_id)).octo_uid is None
    gm2 = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == gid, GroupMember.person_id == p2_id)
    )
    assert gm2.octo_uid == "rebind-1" and gm2.match_status == "manual"
    assert (await db.get(Person, p2_id)).octo_uid == "rebind-1"


async def test_gitlab_username_rebind_releases_previous(admin_client, db, seeded):
    """一个 GitLab 账号只能绑一个人：改绑时旧持有者被清掉，而不是报「已被绑定」。"""
    gid, p1 = seeded["group"].id, seeded["person"]
    p2 = Person(name="王五", gitlab_username=f"wangwu-rebind-{gid}")
    db.add(p2)
    await db.flush()
    db.add(GroupMember(group_id=gid, person_id=p2.id))
    await db.commit()

    p1_id, p2_id = p1.id, p2.id
    r = await admin_client.put(f"/api/groups/{gid}/gitlab-match/{p1_id}", json={"gitlab_username": "shared-acct"})
    assert r.status_code == 200
    r = await admin_client.put(f"/api/groups/{gid}/gitlab-match/{p2_id}", json={"gitlab_username": "shared-acct"})
    assert r.status_code == 200 and r.json()["released"] == "张三"

    db.expire_all()
    from sqlalchemy import select

    from vega.models import PersonGitlabAccount

    acc = await db.scalar(
        select(PersonGitlabAccount).where(
            PersonGitlabAccount.base_url == "http://gitlab.test",
            PersonGitlabAccount.username == "shared-acct",
        )
    )
    assert acc and acc.person_id == p2_id
    gm1 = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == gid, GroupMember.person_id == p1_id)
    )
    assert gm1.gitlab_account_id is None
    gm2 = await db.scalar(
        select(GroupMember).where(GroupMember.group_id == gid, GroupMember.person_id == p2_id)
    )
    assert gm2.gitlab_account_id == acc.id


# ---------- octo 客户端：群列表 + 成员失败原因（成员配对页不再静默 0 人） ----------


def test_octo_list_groups_shapes(monkeypatch):
    import urllib.error

    from vega import octo

    monkeypatch.setattr(octo, "_api_url", lambda: "https://im.test")
    monkeypatch.setattr(octo, "_token", lambda: "tok")

    # 实测形态：顶层数组
    monkeypatch.setattr(octo, "_get", lambda p, t: [{"group_no": "g1", "name": "一群"}])
    items, err = octo.list_groups()
    assert err is None and items == [{"group_no": "g1", "name": "一群"}]

    # bot 不在任何群时 API 返回字面 null
    monkeypatch.setattr(octo, "_get", lambda p, t: None)
    items, err = octo.list_groups()
    assert items == [] and "不在任何群" in err

    # {"groups": [...]} 兼容形态
    monkeypatch.setattr(octo, "_get", lambda p, t: {"groups": [{"group_no": "g2", "name": "二群"}]})
    items, err = octo.list_groups()
    assert err is None and items[0]["group_no"] == "g2"

    # 未配置
    monkeypatch.setattr(octo, "_api_url", lambda: "")
    assert "VEGA_OCTO_API_URL" in octo.list_groups()[1]

    # HTTP 失败（群号错/权限）
    monkeypatch.setattr(octo, "_api_url", lambda: "https://im.test")

    def boom(p, t):
        raise urllib.error.HTTPError(p, 404, "nope", {}, None)

    monkeypatch.setattr(octo, "_get", boom)
    assert "HTTP 404" in octo.group_members_ex("g1")[1]


def test_octo_members_filter_bots(monkeypatch):
    """bot 是 AI 不是人类：robot 标志与 _bot 后缀都不进成员名单。"""
    from vega import octo

    monkeypatch.setattr(octo, "_api_url", lambda: "https://im.test")
    monkeypatch.setattr(octo, "_token", lambda: "tok")
    monkeypatch.setattr(
        octo,
        "_get",
        lambda p, t: [
            {"uid": "u1", "name": "张三", "robot": 0},
            {"uid": "u2", "name": "锦鲤", "robot": 1},
            {"uid": "u3_bot", "name": "群助手", "robot": 0},
        ],
    )
    members, err = octo.group_members_ex("g1")
    assert err is None and members == {"u1": "张三"}


async def test_octo_members_endpoint_reports_error(admin_client, seeded, monkeypatch):
    from vega import octo

    def fake_ex(group_no, token=None):
        return ({"u1": "张三"}, None) if group_no == seeded["group"].octo_group_no else ({}, "群号不对")

    monkeypatch.setattr(octo, "group_members_ex", fake_ex)
    r = await admin_client.get(f"/api/groups/{seeded['group'].id}/octo-members")
    assert r.status_code == 200
    assert r.json() == {"items": [{"uid": "u1", "nickname": "张三"}], "error": None}

    # 失败时 error 带原因，前端据此亮 Alert
    monkeypatch.setattr(octo, "group_members_ex", lambda group_no, token=None: ({}, "bot token 未配置（本组未填且全局未配）"))
    r = await admin_client.get(f"/api/groups/{seeded['group'].id}/octo-members")
    assert r.json()["items"] == [] and "token" in r.json()["error"]


async def test_octo_groups_endpoint(admin_client, seeded, monkeypatch):
    from vega import octo

    monkeypatch.setattr(
        octo,
        "list_groups",
        lambda token=None: ([{"group_no": "g1", "name": "一群"}], None),
    )
    r = await admin_client.get(f"/api/groups/{seeded['group'].id}/octo-groups")
    assert r.status_code == 200
    d = r.json()
    assert d["items"] == [{"group_no": "g1", "name": "一群"}] and d["error"] is None
    assert d["dedicated"] is False  # 种子组未配本组 bot token
