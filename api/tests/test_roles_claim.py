"""研发体系角色：认领门禁（未定稿只能产品、定稿后只能开发）、定稿交接、认领后详情仅认领人可见。"""

from __future__ import annotations

import httpx

from conftest import login_as

from vega.main import app
from vega.models import Person, Requirement


async def _mk_req(client, db, seeded, title: str = "角色需求") -> int:
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    r = await client.post("/api/requirements", json={"project_id": seeded["project"].id, "title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def _add_member(db, group_id: int, name: str, roles: list[str], password: str = "pw"):
    """造一个该研发组成员（认领还要求组成员），带指定角色。"""
    from vega.auth import hash_password
    from vega.models import GroupMember

    p = Person(name=name, gitlab_username=name, roles=roles, password_hash=hash_password(password))
    db.add(p)
    await db.flush()
    db.add(GroupMember(group_id=group_id, person_id=p.id))
    await db.commit()
    return p


async def _as(client, db, username: str):
    token, person = await login_as(client, db, username)
    client.headers["Authorization"] = f"Bearer {token}"
    return person


async def test_claim_requires_product_before_doc(client, db, seeded):
    """需求文档未定稿：产品能认领，开发不能。"""
    req_id = await _mk_req(client, db, seeded)
    gid = seeded["group"].id

    await _add_member(db, gid, "dev-only", ["dev"])
    await _as(client, db, "dev-only")
    r = await client.post(f"/api/requirements/{req_id}/claim")
    assert r.status_code == 403 and "产品" in r.json()["detail"]

    await _add_member(db, gid, "pm-only", ["pm"])
    await _as(client, db, "pm-only")
    assert (await client.post(f"/api/requirements/{req_id}/claim")).status_code == 403

    await _add_member(db, gid, "prod-only", ["product"])
    await _as(client, db, "prod-only")
    r = await client.post(f"/api/requirements/{req_id}/claim")
    assert r.status_code == 200 and r.json()["status"] == "claimed"


async def test_finalize_hands_over_and_dev_reclaims(client, db, seeded):
    """产品定稿文档 → 认领权交还池子、留痕提出人 → 开发重新认领；产品此后打不开详情。"""
    req_id = await _mk_req(client, db, seeded)
    gid = seeded["group"].id
    product = await _add_member(db, gid, "handover-pm", ["product"])
    product_id = product.id
    await _add_member(db, gid, "handover-dev", ["dev"])

    await _as(client, db, "handover-pm")
    assert (await client.post(f"/api/requirements/{req_id}/claim")).status_code == 200
    doc = (await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求"})).json()
    r = await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/finalize")
    assert r.status_code == 200, r.text

    db.expire_all()
    row = await db.get(Requirement, req_id)
    assert row.status == "pool" and row.claimant_id is None and row.proposer_id == product_id

    # 产品失去详情：本体、文档、对话都 403；列表仍给只读摘要
    assert (await client.get(f"/api/requirements/{req_id}")).status_code == 403
    assert (await client.get(f"/api/requirements/{req_id}/docs")).status_code == 403
    assert (await client.get(f"/api/requirements/{req_id}/plan/messages")).status_code == 403
    listed = {r["id"]: r for r in (await client.get("/api/requirements")).json()}
    assert listed[req_id]["can_open"] is False
    assert listed[req_id]["proposer_name"] == "handover-pm" and listed[req_id]["claimant_name"] is None

    # 产品（单角色）不能再认领已定稿的需求
    r = await client.post(f"/api/requirements/{req_id}/claim")
    assert r.status_code == 403 and "开发" in r.json()["detail"]

    # 开发能认领，随后产品仍打不开，开发能打开
    await _as(client, db, "handover-dev")
    r = await client.post(f"/api/requirements/{req_id}/claim")
    assert r.status_code == 200 and r.json()["status"] == "claimed"
    assert (await client.get(f"/api/requirements/{req_id}")).status_code == 200
    listed = {r["id"]: r for r in (await client.get("/api/requirements")).json()}
    assert listed[req_id]["claimant_name"] == "handover-dev"

    await _as(client, db, "handover-pm")
    assert (await client.get(f"/api/requirements/{req_id}")).status_code == 403


async def test_first_upload_finalizes_and_hands_over(client, db, seeded):
    """第一版上传即定稿：同一次请求里就完成交接，不留「有定稿文档但认领人是产品」的中间态。"""
    import io

    req_id = await _mk_req(client, db, seeded)
    product = await _add_member(db, seeded["group"].id, "upload-pm", ["product"])
    product_id = product.id
    await _as(client, db, "upload-pm")
    assert (await client.post(f"/api/requirements/{req_id}/claim")).status_code == 200

    r = await client.post(
        f"/api/requirements/{req_id}/docs",
        files={"file": ("原型.md", io.BytesIO(b"# \xe9\x9c\x80\xe6\xb1\x82"), "text/markdown")},
    )
    assert r.status_code == 200 and r.json()["is_final"] is True

    db.expire_all()
    row = await db.get(Requirement, req_id)
    assert row.status == "pool" and row.claimant_id is None and row.proposer_id == product_id


async def test_only_claimant_can_open_detail(client, db, seeded):
    """被认领之后只有认领人自己能打开详情；管理员不受限。"""
    req_id = await _mk_req(client, db, seeded)
    await _add_member(db, seeded["group"].id, "owner-pm", ["product"])
    await _add_member(db, seeded["group"].id, "peeker", ["product", "dev"])

    await _as(client, db, "owner-pm")
    assert (await client.post(f"/api/requirements/{req_id}/claim")).status_code == 200
    doc = (await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求"})).json()
    assert (await client.get(f"/api/requirements/{req_id}")).status_code == 200
    assert (await client.get(f"/api/requirements/{req_id}/docs/{doc['id']}")).status_code == 200

    await _as(client, db, "peeker")
    for path in (
        f"/api/requirements/{req_id}",
        f"/api/requirements/{req_id}/docs",
        f"/api/requirements/{req_id}/docs/{doc['id']}",
        f"/api/requirements/{req_id}/docs/{doc['id']}/download",
        f"/api/requirements/{req_id}/plan/messages",
        f"/api/requirements/{req_id}/plan-versions",
    ):
        r = await client.get(path)
        assert r.status_code == 403, (path, r.status_code)
    # SSE 在建流前就拒，前端才能立刻给出提示而不是干等空流
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers=client.headers,
    ) as rc:
        assert (await rc.get(f"/api/requirements/{req_id}/plan/stream")).status_code == 403

    # 池中未认领的需求谁都能看（不看就没法认领）
    free = await _mk_req(client, db, seeded, "池中可看")
    assert (await client.get(f"/api/requirements/{free}")).status_code == 200

    # 管理员不受限
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://api.test") as ac:
        r = await ac.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
        ac.headers["Authorization"] = f"Bearer {r.json()['token']}"
        assert (await ac.get(f"/api/requirements/{req_id}")).status_code == 200


async def test_upload_doc_needs_claimant_once_claimed(client, db, seeded):
    """池中可直接上传（录需求的用法），被认领或已移交之后只能认领人传。"""
    import io

    req_id = await _mk_req(client, db, seeded)
    await _add_member(db, seeded["group"].id, "upload-owner", ["product"])
    await _add_member(db, seeded["group"].id, "upload-peeker", ["product", "dev"])

    files = {"file": ("原型.md", io.BytesIO(b"# x"), "text/markdown")}
    await _as(client, db, "upload-peeker")
    assert (await client.post(f"/api/requirements/{req_id}/docs", files=files)).status_code == 200

    # 已被他人认领：拒绝
    req_id2 = await _mk_req(client, db, seeded, "认领后不许旁人传")
    await _as(client, db, "upload-owner")
    assert (await client.post(f"/api/requirements/{req_id2}/claim")).status_code == 200
    await _as(client, db, "upload-peeker")
    r = await client.post(f"/api/requirements/{req_id2}/docs", files=files)
    assert r.status_code == 403 and "认领人" in r.json()["detail"]


async def test_unfinalize_returns_claim_to_proposer(client, db, seeded):
    """撤回定稿把认领权还给提出人：需求讨论本来就是产品那一段。"""
    req_id = await _mk_req(client, db, seeded)
    await _add_member(db, seeded["group"].id, "rollback-pm", ["product"])

    product = await _as(client, db, "rollback-pm")
    product_id = product.id
    assert (await client.post(f"/api/requirements/{req_id}/claim")).status_code == 200
    doc = (await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求"})).json()
    assert (await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/finalize")).status_code == 200

    r = await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/unfinalize")
    assert r.status_code == 200, r.text
    db.expire_all()
    row = await db.get(Requirement, req_id)
    assert row.claimant_id == product_id and row.proposer_id is None and row.status == "claimed"
    assert (await client.get(f"/api/requirements/{req_id}")).status_code == 200


async def test_roles_admin_only_and_login_carries_them(admin_client, client, db):
    """角色只能管理员改（自己改等于给自己提权），登录返回角色供前端判断认领资格。"""
    r = await admin_client.post(
        "/api/people", json={"name": "角色甲", "gitlab_username": "role-a", "password": "pw", "roles": ["product", "dev", "dev"]}
    )
    assert r.status_code == 200, r.text
    assert r.json()["roles"] == ["product", "dev"]  # 去重保序

    token, _ = await login_as(client, db, "role-a", "pw")
    client.headers["Authorization"] = f"Bearer {token}"
    r = await client.post("/api/auth/login", json={"username": "role-a", "password": "pw"})
    assert r.json()["roles"] == ["product", "dev"]

    pid = (await client.get("/api/people")).json()
    me = next(p for p in pid if p["gitlab_username"] == "role-a")
    assert (await client.put(f"/api/people/{me['id']}", json={"roles": ["pm", "dev"]})).status_code == 403
    # 回传原样不算修改（前端提交整份表单）
    assert (await client.put(f"/api/people/{me['id']}", json={"name": "角色甲", "roles": ["product", "dev"]})).status_code == 200

    # 非法角色值挡在写库前
    assert (await admin_client.put(f"/api/people/{me['id']}", json={"roles": ["boss"]})).status_code == 422