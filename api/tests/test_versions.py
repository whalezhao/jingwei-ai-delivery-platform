"""需求文档多版本+定稿、plan 方案 md 版本化（人点采用才成版，confirm 标定稿）。"""

from __future__ import annotations

from sqlalchemy import select

from conftest import login_as

from vega.models import DevTask, PlanMessage, PlanVersion, Requirement, RequirementDoc, RunnerTask
from vega.routers.runner_api import _finish_plan_turn
from vega.schemas import TaskResultIn


async def _mk_req(client, db, seeded, title: str = "版本需求") -> int:
    return (
        await client.post(
            "/api/requirements", json={"system_id": seeded["project"].id, "title": title, "description": "d"}
        )
    ).json()["id"]


async def _upload(client, req_id: int, name: str) -> dict:
    r = await client.post(
        f"/api/requirements/{req_id}/docs", files={"file": (name, b"content-" + name.encode(), "text/plain")}
    )
    assert r.status_code == 200
    return r.json()


async def test_doc_versions_and_finalize(client, db, seeded):
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    rid = await _mk_req(client, db, seeded)
    # 定稿要求认领人或 admin（与其他文档操作一致）
    assert (await client.post(f"/api/requirements/{rid}/claim")).status_code == 200

    v1 = await _upload(client, rid, "需求 v1.md")
    v2 = await _upload(client, rid, "需求 v2.md")
    assert v1["version"] == 1 and v1["is_final"] is True  # 第一版自动定稿
    assert v2["version"] == 2 and v2["is_final"] is False

    docs = (await client.get(f"/api/requirements/{rid}/docs")).json()
    assert [(d["version"], d["is_final"]) for d in docs] == [(1, True), (2, False)]

    # 定稿 v2 → 唯一 final
    did2 = docs[1]["id"]
    r = await client.post(f"/api/requirements/{rid}/docs/{did2}/finalize")
    assert r.status_code == 200 and r.json()["final_version"] == 2
    docs = (await client.get(f"/api/requirements/{rid}/docs")).json()
    assert [(d["version"], d["is_final"]) for d in docs] == [(1, False), (2, True)]
    # 下载走原始字节
    dl = await client.get(f"/api/requirements/{rid}/docs/{did2}/download")
    assert dl.status_code == 200 and dl.content.startswith(b"content-")


async def test_plan_versions_adopt_per_turn_and_confirm(client, db, seeded):
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    rid = await _mk_req(client, db, seeded, "方案版本")

    # 两轮 plan_turn 只落对话消息，不再自动成版
    task = RunnerTask(type="plan_turn", payload={}, requirement_id=rid, status="done")  # 直接驱动收尾，不进队列
    db.add(task)
    await db.flush()
    for text in ("# 方案 V1\n草稿", "# 方案 V2\n改了一版"):
        await _finish_plan_turn(db, task, TaskResultIn(status="done", data={"text": text}))
    await db.commit()
    assert (
        await db.scalars(select(PlanVersion).where(PlanVersion.requirement_id == rid))
    ).all() == []

    # 认领 → 人点「采用此方案」才成版；同内容连点复用同一版
    await client.post(f"/api/requirements/{rid}/claim")
    r1 = await client.post(f"/api/requirements/{rid}/plan-versions", json={"content": "# 方案 V1\n草稿"})
    assert r1.status_code == 200 and r1.json()["version"] == 1 and r1.json()["created"] is True
    dup = await client.post(f"/api/requirements/{rid}/plan-versions", json={"content": "# 方案 V1\n草稿"})
    assert dup.status_code == 200 and dup.json()["created"] is False
    assert (dup.json()["id"], dup.json()["version"]) == (r1.json()["id"], 1)

    r = await client.post(f"/api/requirements/{rid}/plan-versions/{r1.json()['id']}/finalize")
    assert r.status_code == 200
    rows = (
        await db.execute(
            select(PlanVersion.version, PlanVersion.is_final)
            .where(PlanVersion.requirement_id == rid)
            .order_by(PlanVersion.id)
        )
    ).all()
    assert rows == [(1, True)]

    # 挑非最新版定稿：final 唯一切换到所选版
    rid2 = await _mk_req(client, db, seeded, "挑版定稿")
    await client.post(f"/api/requirements/{rid2}/claim")
    a = await client.post(f"/api/requirements/{rid2}/plan-versions", json={"content": "# 版 A"})
    b = await client.post(f"/api/requirements/{rid2}/plan-versions", json={"content": "# 版 B"})
    assert (a.json()["version"], b.json()["version"]) == (1, 2)
    r2 = await client.post(f"/api/requirements/{rid2}/plan-versions/{a.json()['id']}/finalize")
    assert r2.status_code == 200
    rows2 = (
        await db.execute(
            select(PlanVersion.version, PlanVersion.is_final)
            .where(PlanVersion.requirement_id == rid2)
            .order_by(PlanVersion.id)
        )
    ).all()
    assert rows2 == [(1, True), (2, False)]

    # 版本列表接口
    lst = (await client.get(f"/api/requirements/{rid}/plan-versions")).json()
    assert [v["version"] for v in lst] == [1] and lst[0]["is_final"] is True


async def test_plan_version_adopt_guards(client, db, seeded):
    """采用方案的三道门：内容非空、认领人、未定稿。"""
    owner, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {owner}"
    rid = await _mk_req(client, db, seeded, "采用校验")
    await client.post(f"/api/requirements/{rid}/claim", headers={"Authorization": f"Bearer {owner}"})

    r = await client.post(
        f"/api/requirements/{rid}/plan-versions",
        json={"content": "   "},
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert r.status_code == 400

    outsider, _ = await login_as(client, db, "adopt-outsider")
    r = await client.post(
        f"/api/requirements/{rid}/plan-versions",
        json={"content": "# 别人的方案"},
        headers={"Authorization": f"Bearer {outsider}"},
    )
    assert r.status_code == 403

    r = await client.post(
        f"/api/requirements/{rid}/plan-versions",
        json={"content": "# 采用后定稿"},
        headers={"Authorization": f"Bearer {owner}"},
    )
    vid = r.json()["id"]
    r = await client.post(
        f"/api/requirements/{rid}/plan-versions/{vid}/finalize",
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert r.status_code == 200
    r = await client.post(
        f"/api/requirements/{rid}/plan-versions",
        json={"content": "# 定稿后的新方案"},
        headers={"Authorization": f"Bearer {owner}"},
    )
    assert r.status_code == 400


async def test_plan_turn_failure_persists_message(client, db, seeded):
    """失败回合也落 assistant 消息：否则重放里最后一条永远是 user，前端 busy 卡死。"""
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    rid = await _mk_req(client, db, seeded, "失败回话")
    task = RunnerTask(type="plan_turn", payload={}, requirement_id=rid, status="done")
    db.add(task)
    await db.flush()
    await _finish_plan_turn(db, task, TaskResultIn(status="failed", error="claude 退出码 1"))
    await db.commit()
    rows = (
        await db.scalars(select(PlanMessage).where(PlanMessage.requirement_id == rid).order_by(PlanMessage.id))
    ).all()
    assert len(rows) == 1 and rows[0].role == "assistant" and "claude 退出码 1" in rows[0].content


async def test_task_detail_aggregate(client, db, seeded):
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    pid = seeded["person"].id
    rid = await _mk_req(client, db, seeded, "详情聚合")
    await _upload(client, rid, "定稿文档.md")
    req = await db.get(Requirement, rid)
    db.add(PlanVersion(requirement_id=rid, version=1, content="# 定稿方案", is_final=True))
    t1 = DevTask(requirement_id=rid, code="TASK-DET-1", status="running", creator_id=pid, title="任务一")
    t2 = DevTask(requirement_id=rid, code="TASK-DET-2", status="queued", creator_id=pid, title="任务二")
    db.add_all([t1, t2])
    db.add(RunnerTask(type="dev_task", payload={}, requirement_id=rid, dev_task_id=None))
    await db.flush()
    db.add(RunnerTask(type="dev_task", payload={}, requirement_id=rid, dev_task_id=t1.id))
    await db.commit()

    d = (await client.get(f"/api/dev-tasks/{t1.id}/detail")).json()
    assert d["task"]["code"] == "TASK-DET-1"
    assert d["requirement"]["code"] == req.code
    assert d["final_doc"]["filename"] == "定稿文档.md" and d["final_doc"]["version"] == 1
    assert d["final_plan"]["content"] == "# 定稿方案"
    assert [t["code"] for t in d["tasks"]] == ["TASK-DET-1", "TASK-DET-2"]
    assert len(d["runner_task_ids"]) == 1  # SSE dev_raw 过滤键：只含本任务的 runner 任务

    # 同需求的另一任务看到同一份定稿方案与文档
    d2 = (await client.get(f"/api/dev-tasks/{t2.id}/detail")).json()
    assert d2["final_plan"]["content"] == "# 定稿方案" and d2["final_doc"]["filename"] == "定稿文档.md"


async def test_task_detail_denies_non_claimant(client, db, seeded):
    """任务详情带需求文档与方案正文，必须和需求详情同权：非认领人 403。"""
    token, person = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    rid = await _mk_req(client, db, seeded, "他人认领")
    task = DevTask(requirement_id=rid, code="TASK-DENY-1", status="running", creator_id=person.id)
    db.add(task)
    await db.flush()
    task_id = task.id
    # 换一个没有认领需求的开发当访问者
    other_token, _ = await login_as(client, db, "lisi-deny")
    client.headers["Authorization"] = f"Bearer {other_token}"

    # 未认领时不锁（池中谁都能看）
    assert (await client.get(f"/api/dev-tasks/{task_id}/detail")).status_code == 200
    assert (await client.get(f"/api/dev-tasks/{task_id}/events")).status_code == 200

    req = await db.get(Requirement, rid)
    req.claimant_id = person.id
    await db.commit()

    assert (await client.get(f"/api/dev-tasks/{task_id}/detail")).status_code == 403
    assert (await client.get(f"/api/dev-tasks/{task_id}/events")).status_code == 403
    # 列表仍在（任务池公开），只是 can_open=false
    rows = (await client.get(f"/api/dev-tasks?requirement_id={rid}")).json()
    assert [(r["code"], r["can_open"]) for r in rows] == [("TASK-DENY-1", False)]
