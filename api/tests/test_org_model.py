"""组织模型：项目（顶层）↔ 研发组 ↔ 系统；个人多 GitLab 账号与组内账号选择。"""

from __future__ import annotations

from vega.gitlab import GitlabClient
from vega.models import GroupMember, Person, PersonGitlabAccount


async def test_project_crud_and_group_binding(admin_client, db, seeded):
    name = f"项目-{seeded['group'].id}"
    r = await admin_client.post(
        "/api/projects",
        json={"name": name, "code": "PRJ", "leader_person_ids": [seeded["person"].id]},
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert r.json()["leader_person_ids"] == [seeded["person"].id]

    # 同名拒绝
    r = await admin_client.post("/api/projects", json={"name": name})
    assert r.status_code == 400

    # 研发组挂项目后，组输出带项目名；项目此时不能删
    r = await admin_client.put(
        f"/api/groups/{seeded['group'].id}",
        json={"project_id": pid, "gitlab_bot_username": "vega-bot"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["project_id"] == pid and r.json()["project_name"] == name
    assert r.json()["gitlab_bot_username"] == "vega-bot"
    r = await admin_client.delete(f"/api/projects/{pid}")
    assert r.status_code == 400

    # 解绑后可删
    r = await admin_client.put(f"/api/groups/{seeded['group'].id}", json={"project_id": None})
    assert r.status_code == 200 and r.json()["project_id"] is None
    assert (await admin_client.delete(f"/api/projects/{pid}")).status_code == 200


async def test_person_accounts_and_member_selection(admin_client, db, seeded, monkeypatch):
    pid = seeded["person"].id
    gid = seeded["group"].id

    async def fake_whoami(self, client):
        return {"id": 7001, "username": "zhangsan-2", "name": "张三2", "email": "zs2@test.cn"}

    monkeypatch.setattr(GitlabClient, "whoami", fake_whoami)

    r = await admin_client.post(
        f"/api/people/{pid}/gitlab-accounts",
        json={"base_url": "http://gitlab.test", "token": "gl-personal"},
    )
    assert r.status_code == 200, r.text
    acc_id = r.json()["id"]
    assert r.json()["username"] == "zhangsan-2" and r.json()["has_token"] is True

    # 同一实例同一 GitLab 用户重复添加是幂等更新，不新建
    r = await admin_client.post(
        f"/api/people/{pid}/gitlab-accounts",
        json={"base_url": "http://gitlab.test/", "token": "gl-personal-2"},
    )
    assert r.status_code == 200 and r.json()["id"] == acc_id
    from sqlalchemy import select

    rows = (await db.scalars(select(PersonGitlabAccount))).all()
    assert len([x for x in rows if x.person_id == pid and x.username == "zhangsan-2"]) == 1

    # 组内选择该账号
    r = await admin_client.put(
        f"/api/groups/{gid}/members/{pid}/account", json={"gitlab_account_id": acc_id}
    )
    assert r.status_code == 200 and r.json()["gitlab_account_id"] == acc_id
    members = (await admin_client.get(f"/api/groups/{gid}/members")).json()
    me = next(m for m in members if m["person_id"] == pid)
    assert acc_id in [a["id"] for a in me["gitlab_accounts"]]
    assert me["gitlab_account_username"] == "zhangsan-2"

    # 别的成员的账号不能选
    other = Person(name="李四", username=f"lisi-org-{pid}")
    db.add(other)
    await db.flush()
    db.add(GroupMember(group_id=gid, person_id=other.id))
    await db.commit()
    r = await admin_client.put(
        f"/api/groups/{gid}/members/{other.id}/account", json={"gitlab_account_id": acc_id}
    )
    assert r.status_code == 404

    # 非本组 GitLab 实例的账号不能选
    acc = await db.get(PersonGitlabAccount, acc_id)
    acc.base_url = "http://other.git"
    await db.commit()
    r = await admin_client.put(
        f"/api/groups/{gid}/members/{pid}/account", json={"gitlab_account_id": acc_id}
    )
    assert r.status_code == 400
    acc.base_url = "http://gitlab.test"
    await db.commit()

    # 删除账号时自动解除组内绑定
    r = await admin_client.delete(f"/api/people/{pid}/gitlab-accounts/{acc_id}")
    assert r.status_code == 200
    gm = await db.get(GroupMember, me["id"])
    await db.refresh(gm)
    assert gm.gitlab_account_id is None