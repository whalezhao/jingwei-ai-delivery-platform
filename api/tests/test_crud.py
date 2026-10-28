"""CRUD API 流：登录 → 人员 → 研发组 → 项目 → 需求 → 认领 → 上传文档。"""

from __future__ import annotations

import io

from conftest import login_as


async def test_full_crud_flow(admin_client, client, db, seeded):
    c = admin_client
    # 人员
    r = await c.post(
        "/api/people",
        json={"name": "李四", "gitlab_username": "lisi", "gitlab_email": "lisi@test.cn", "password": "pw2"},
    )
    assert r.status_code == 200, r.text
    lisi = r.json()
    assert lisi["has_llm_key"] is False

    # 人员 LLM 配置：写不读（key 不回显）
    r = await c.put(f"/api/people/{lisi['id']}", json={"llm_base_url": "http://x/v1", "llm_api_key": "sk-x", "llm_model": "m1"})
    assert r.status_code == 200
    assert "llm_api_key" not in r.json()
    assert r.json()["has_llm_key"] is True

    # 研发组
    r = await c.post(
        "/api/groups",
        json={"name": "平台组", "gitlab_base_url": "http://gitlab.test", "gitlab_token": "tok", "octo_group_no": "G1", "am_team_id": "T1"},
    )
    assert r.status_code == 200, r.text
    group = r.json()
    assert group["has_gitlab_token"] is True

    # webhook 信息已收敛到集成中心（单址 + secret 识别组）
    r = await c.get(f"/api/groups/{group['id']}/webhook-info")
    assert r.status_code == 404
    r = await c.get("/api/integrations/summary")
    assert r.status_code == 200
    assert r.json()["webhook_url"] == "http://api.test/webhooks/gitlab"

    # 项目（挂 PM）
    r = await c.post(
        "/api/projects",
        json={
            "group_id": group["id"],
            "gitlab_project_id": 42,
            "path": "team/new-repo",
            "name": "new-repo",
            "code_prefix": "nr",
            "tech_lead_person_ids": [lisi["id"]],
            # 前缀在新建时就要落库：只放在 PUT 会导致新建表单里填的规则被静默丢弃
            "audit_prefixes": ["api/", "src/"],
            "auto_approve_prefixes": [""],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["code_prefix"] == "NR"
    assert r.json()["tech_lead_person_ids"] == [lisi["id"]]
    assert r.json()["mr_target_branch"] == "nightly"
    assert r.json()["audit_prefixes"] == ["api/", "src/"]
    assert r.json()["auto_approve_prefixes"] == [""]

    # 需求（seeded 项目的成员才可认领）
    _, zhangsan = await login_as(client, db, seeded["person"].gitlab_username)
    r = await c.post("/api/requirements", json={"project_id": seeded["project"].id, "title": "登录功能", "description": "支持 SSO"})
    assert r.status_code == 200, r.text
    req = r.json()
    assert req["code"].startswith("REQ-TEST-")

    # 张三登录并认领
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    r = await client.post(f"/api/requirements/{req['id']}/claim")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "claimed"
    assert r.json()["claimant_name"] == "张三"

    # 重复认领被拒
    r = await client.post(f"/api/requirements/{req['id']}/claim")
    assert r.status_code == 400

    # 上传 md 文档
    files = {"file": ("需求说明.md", io.BytesIO("# 需求\n支持手机号登录".encode()), "text/markdown")}
    r = await client.post(f"/api/requirements/{req['id']}/docs", files=files)
    assert r.status_code == 200, r.text
    # 共享测试库：doc id 不从 1 开始，从列表取本需求刚传的 md
    doc_id = next(d["id"] for d in (await client.get(f"/api/requirements/{req['id']}/docs")).json() if d["filename"] == "需求说明.md")
    r = await client.get(f"/api/requirements/{req['id']}/docs/{doc_id}")
    assert "手机号登录" in r.json()["content"]

    # docx 走 mammoth
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:  # 极简 docx（mammoth 容忍缺样式）
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>docx内容ABC</w:t></w:r></w:p></w:body></w:document>')
    files = {"file": ("a.docx", io.BytesIO(buf.getvalue()), "application/vnd.openxmlformats-officedocument.wordprocessingml.document")}
    r = await client.post(f"/api/requirements/{req['id']}/docs", files=files)
    assert r.status_code == 200, r.text

    # 未登录访问被拒
    import httpx as _h

    anon = _h.AsyncClient(transport=client._transport, base_url="http://api.test")
    r = await anon.get("/api/people")
    assert r.status_code == 401
    await anon.aclose()


async def test_requirement_tags_and_iteration_status(client, db, seeded):
    """迭代版本标签去重归一；已上线的需求迭代状态自动是「正式」，标签仍可改。"""
    from vega.models import Requirement

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    req = (
        await client.post("/api/requirements", json={"project_id": seeded["project"].id, "title": "迭代标签"})
    ).json()
    assert req["iteration_status"] == "iterating" and req["tags"] == []

    r = await client.put(f"/api/requirements/{req['id']}/tags", json={"tags": [" V2.3 ", "V2.3", "V2.4", "  "]})
    assert r.status_code == 200, r.text
    assert r.json()["tags"] == ["V2.3", "V2.4"]  # 去空白、去重、丢空值

    row = await db.get(Requirement, req["id"])
    row.status = "online"  # 等价于任务全部合入后的状态
    await db.commit()
    r = await client.get(f"/api/requirements/{req['id']}")
    assert r.json()["iteration_status"] == "formal"
    r = await client.put(f"/api/requirements/{req['id']}/tags", json={"tags": ["V3.0"]})
    assert r.status_code == 200 and r.json()["tags"] == ["V3.0"]  # 已上线后仍可打版本标签


async def test_avatar_preset_roundtrip(admin_client):
    """头像只收内置预设 key：写入、清空、非法值拒绝（含路径穿越）。"""
    r = await admin_client.post("/api/people", json={"name": "头像测试", "avatar": "a3"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert r.json()["avatar"] == "a3"

    r = await admin_client.put(f"/api/people/{pid}", json={"avatar": ""})
    assert r.status_code == 200 and r.json()["avatar"] is None

    r = await admin_client.put(f"/api/people/{pid}", json={"avatar": "nope"})
    assert r.status_code == 400
    r = await admin_client.put(f"/api/people/{pid}", json={"avatar": "../a1"})
    assert r.status_code == 422


async def test_list_filters_group_project_status_keyword(admin_client, db, seeded):
    """列表组过滤 + 任务项目/状态/关键字过滤；关键字大小写不敏感且 % 按字面匹配。"""
    from vega.models import DevTask

    c = admin_client

    g1, p1 = seeded["group"].id, seeded["project"].id
    r = await c.post("/api/groups", json={"name": "筛选组", "gitlab_base_url": "http://gitlab.test"})
    assert r.status_code == 200, r.text
    g2 = r.json()["id"]
    r = await c.post(
        "/api/projects",
        json={"group_id": g2, "gitlab_project_id": 9001, "path": "team/other", "name": "other", "code_prefix": "OTH"},
    )
    assert r.status_code == 200, r.text
    p2 = r.json()["id"]

    req1 = (await c.post("/api/requirements", json={"project_id": p1, "title": "本组需求"})).json()["id"]
    req2 = (await c.post("/api/requirements", json={"project_id": p2, "title": "外组需求"})).json()["id"]

    # 需求：按组过滤
    ids = {r["id"] for r in (await c.get("/api/requirements", params={"group_id": g1})).json()}
    assert req1 in ids and req2 not in ids

    # 任务：组 / 项目 / 状态 / 关键字
    db.add(DevTask(requirement_id=req1, code="ALPHA-1", title="登录功能", status="done", creator_id=seeded["person"].id))
    db.add(DevTask(requirement_id=req2, code="BETA-2", title="支付功能", status="running", creator_id=seeded["person"].id))
    await db.commit()

    def codes(rows):
        return {t["code"] for t in rows}

    assert codes((await c.get("/api/dev-tasks", params={"group_id": g1})).json()) >= {"ALPHA-1"}
    assert "BETA-2" not in codes((await c.get("/api/dev-tasks", params={"group_id": g1})).json())
    assert codes((await c.get("/api/dev-tasks", params={"project_id": p2})).json()) == {"BETA-2"}
    assert codes((await c.get("/api/dev-tasks", params={"project_id": p1, "status": "done"})).json()) >= {"ALPHA-1"}
    assert codes((await c.get("/api/dev-tasks", params={"q": "alpha"})).json()) == {"ALPHA-1"}
    assert (await c.get("/api/dev-tasks", params={"q": "%"})).json() == []

    # 人员：按组成员过滤（刚建的人不在任何组）
    r = await c.post("/api/people", json={"name": "组外人员"})
    outsider = r.json()["id"]
    pids = {p["id"] for p in (await c.get("/api/people", params={"group_id": g1})).json()}
    assert seeded["person"].id in pids and outsider not in pids


async def test_llm_effort_rejects_unknown_level(admin_client):
    """思考等级只收 claude --effort 全量档位，乱值 422 挡在写库前。"""
    r = await admin_client.post(
        "/api/people",
        json={"name": "档位测试", "llm_effort": "ultra"},
    )
    assert r.status_code == 422
    # 全量档位都要能过（high/max 是补全的档）
    for lv in ("low", "medium", "high", "xhigh", "max"):
        r = await admin_client.post(
            "/api/people", json={"name": f"档位-{lv}", "llm_effort": lv}
        )
        assert r.status_code == 200, (lv, r.text)
    # 前端「未设置」传空串：等价 null，不能 422
    r = await admin_client.post("/api/people", json={"name": "档位-空", "llm_effort": ""})
    assert r.status_code == 200, r.text
    assert r.json()["llm_effort"] is None
    r = await admin_client.post("/api/groups", json={"name": "档位组-空", "llm_effort": ""})
    assert r.status_code == 200, r.text
    assert r.json()["llm_effort"] is None


async def test_gitlab_member_snapshot(admin_client, db):
    """成员抽屉读快照：GET 直接返回库里的名单（不再实时拉项目）；成功拉取整组覆盖。"""
    from vega.jobs import _store_gitlab_members
    from vega.models import DevGroup, GitlabMember

    g = (await admin_client.post("/api/groups", json={"name": "快照组"})).json()
    db.add_all(
        [
            GitlabMember(group_id=g["id"], username="snap_a", name="甲", email="a@test.cn"),
            GitlabMember(group_id=g["id"], username="snap_b", name="乙", email=""),
        ]
    )
    await db.commit()

    r = await admin_client.get(f"/api/groups/{g['id']}/gitlab-users")
    assert r.status_code == 200, r.text
    assert [i["username"] for i in r.json()["items"]] == ["snap_a", "snap_b"]
    assert r.json()["error"] is None

    # 每次成功拉取整组覆盖：没出现在新名单里的人要被删掉
    grp = await db.get(DevGroup, g["id"])
    await _store_gitlab_members(db, grp, {"snap_b": {"name": "乙改", "email": "b@test.cn"}})
    await db.commit()
    r = await admin_client.get(f"/api/groups/{g['id']}/gitlab-users")
    assert r.json()["items"] == [{"username": "snap_b", "name": "乙改", "email": "b@test.cn"}]


async def test_octo_global_bot_token_roundtrip(admin_client):
    """集成中心只收一个全局 bot token：写入来源 db、留空保存即清除、没 token 时群列表给原因。"""
    r = await admin_client.get("/api/integrations/octo")
    assert r.status_code == 200, r.text
    assert r.json()["has_token"] is False

    r = await admin_client.put("/api/integrations/octo", json={"bot_token": " bot-abc "})
    assert r.status_code == 200, r.text
    assert r.json() == {"has_token": True, "source": "db"}
    assert (await admin_client.get("/api/integrations/octo")).json()["source"] == "db"

    r = await admin_client.put("/api/integrations/octo", json={"bot_token": ""})
    assert r.status_code == 200
    assert r.json()["has_token"] is False
    assert (await admin_client.get("/api/integrations/octo")).json()["source"] is None

    # 群列表：没 token 时直接给原因，不发网络请求
    r = await admin_client.get("/api/integrations/octo-groups")
    assert r.status_code == 200, r.text
    assert r.json()["items"] == [] and "token" in (r.json()["error"] or "")
