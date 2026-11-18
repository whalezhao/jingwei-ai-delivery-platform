"""Bug 类型：没有需求文档，开发认领后直接进入方案讨论。"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy import delete, select

from vega.models import (
    DevTask,
    DevTaskEvent,
    GroupMember,
    Person,
    PlanAttachment,
    PlanEvent,
    PlanMessage,
    PlanReview,
    PlanVersion,
    Requirement,
    RequirementDoc,
    RunnerTask,
)


@pytest_asyncio.fixture
async def req_cleanup(db):
    """测试库跨用例共享：清掉本用例入队的 plan_turn/dev_task，避免后续 runner 用例领错任务。"""
    ids: list[int] = []
    yield ids
    if not ids:
        return
    task_ids = (await db.scalars(select(DevTask.id).where(DevTask.requirement_id.in_(ids)))).all()
    if task_ids:
        await db.execute(delete(DevTaskEvent).where(DevTaskEvent.dev_task_id.in_(task_ids)))
    for model in (
        DevTask,
        PlanAttachment,
        PlanEvent,
        PlanReview,
        PlanMessage,
        PlanVersion,
        RunnerTask,
        RequirementDoc,
    ):
        await db.execute(delete(model).where(model.requirement_id.in_(ids)))
    await db.execute(delete(Requirement).where(Requirement.id.in_(ids)))
    await db.commit()


async def test_bug_skips_doc_stage(admin_client, client, db, seeded, req_cleanup):
    from conftest import login_as

    # 只有产品角色的人：普通需求未定稿时能认领，但 Bug 必须开发认领
    product = Person(name="产品", username=f"product-{seeded['person'].id}", roles=["product"])
    db.add(product)
    await db.flush()
    db.add(GroupMember(group_id=seeded["group"].id, person_id=product.id))
    await db.commit()

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"

    # 默认类型仍是普通需求
    r = await client.post("/api/requirements", json={"system_id": seeded["project"].id, "title": "普通需求"})
    assert r.status_code == 200, r.text
    assert r.json()["req_type"] == "requirement"
    req_cleanup.append(r.json()["id"])

    r = await client.post(
        "/api/requirements",
        json={"system_id": seeded["project"].id, "title": "登录崩溃", "req_type": "bug"},
    )
    assert r.status_code == 200, r.text
    req_id = r.json()["id"]
    req_cleanup.append(req_id)
    assert r.json()["req_type"] == "bug"

    # 产品认领 Bug → 403
    ptoken, _ = await login_as(client, db, product.username)
    r = await client.post(
        f"/api/requirements/{req_id}/claim", headers={"Authorization": f"Bearer {ptoken}"}
    )
    assert r.status_code == 403, r.text
    assert "开发" in r.json()["detail"]

    # 开发认领后没有需求文档也能直接聊方案
    r = await client.post(f"/api/requirements/{req_id}/claim")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "claimed"

    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages", json={"content": "给个修复方案", "mode": "plan"}
    )
    assert r.status_code == 200, r.text

    # 文档相关接口对 Bug 一律拒绝
    r = await client.post(
        f"/api/requirements/{req_id}/docs", files={"file": ("bug.md", b"# bug", "text/markdown")}
    )
    assert r.status_code == 400, r.text
    r = await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# bug"})
    assert r.status_code == 400, r.text

    # 方案成版 + 定稿 → confirmed，并生成开发任务
    v = await client.post(f"/api/requirements/{req_id}/plan-versions", json={"content": "修复方案正文"})
    assert v.status_code == 200, v.text
    r = await client.post(f"/api/requirements/{req_id}/plan-versions/{v.json()['id']}/finalize")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "confirmed"
    assert r.json()["plan_md"] == "修复方案正文"

    # 工作台不依赖需求文档，也能看到这个 Bug
    r = await admin_client.get("/api/workbench", params={"system_id": seeded["project"].id})
    assert r.status_code == 200, r.text
    rows = [x for x in r.json()["requirements"] if x["id"] == req_id]
    assert rows and rows[0]["req_type"] == "bug"


async def test_plan_message_carries_attachment(admin_client, db, seeded, req_cleanup):
    r = await admin_client.post(
        "/api/requirements", json={"system_id": seeded["project"].id, "title": "带附件的需求"}
    )
    assert r.status_code == 200, r.text
    req_id = r.json()["id"]
    req_cleanup.append(req_id)
    r = await admin_client.post(f"/api/requirements/{req_id}/claim")
    assert r.status_code == 200, r.text

    png = b"\x89PNG\r\n\x1a\n" + b"x" * 32
    r = await admin_client.post(
        f"/api/requirements/{req_id}/plan/attachments",
        files={"file": ("shot.png", png, "image/png")},
    )
    assert r.status_code == 200, r.text
    att = r.json()
    assert (att["filename"], att["mime"], att["size"]) == ("shot.png", "image/png", len(png))

    r = await admin_client.post(
        f"/api/requirements/{req_id}/plan/messages",
        json={"content": "看截图", "attachment_ids": [att["id"]]},
    )
    assert r.status_code == 200, r.text

    # 附件随任务下发 runner（runner 按 id 下载原始字节）
    task = await db.scalar(
        select(RunnerTask)
        .where(RunnerTask.requirement_id == req_id, RunnerTask.type == "plan_turn")
        .order_by(RunnerTask.id.desc())
    )
    assert task and task.payload["attachments"][0]["id"] == att["id"]
    assert task.payload["attachments"][0]["filename"] == "shot.png"

    # 历史回放同样带附件，刷新后气泡里的文件名不丢
    r = await admin_client.get(f"/api/requirements/{req_id}/plan/messages")
    assert r.status_code == 200, r.text
    user_msg = [m for m in r.json() if m["role"] == "user"][-1]
    assert user_msg["attachments"][0]["filename"] == "shot.png"

    # 已经被消息用掉的附件不能再绑第二轮
    r = await admin_client.post(
        f"/api/requirements/{req_id}/plan/messages",
        json={"content": "再用一次", "attachment_ids": [att["id"]]},
    )
    assert r.status_code == 400, r.text

async def test_switch_team_agent_rebuilds_session_with_history(admin_client, db, seeded, req_cleanup):
    """换团队/专家 = 新会话：首轮必须带上该模式的历史，并写明本轮绑定的团队/专家。

    模型看不到 HTTP 头，绑定信息只能写进上下文，否则被问「用的哪个团队/专家」只能回答不知道。
    """
    r = await admin_client.post(
        "/api/requirements",
        json={"system_id": seeded["project"].id, "title": "切换团队", "req_type": "bug"},
    )
    req_id = r.json()["id"]
    req_cleanup.append(req_id)

    r = await admin_client.post(
        f"/api/requirements/{req_id}/plan/messages", json={"content": "你好", "mode": "plan"}
    )
    assert r.status_code == 200, r.text
    first = await db.scalar(
        select(RunnerTask)
        .where(RunnerTask.requirement_id == req_id, RunnerTask.type == "plan_turn")
        .order_by(RunnerTask.id.desc())
    )
    # 项目/研发组里有团队时也会写进上下文（专家未选就是未选）
    assert "agent-memory 绑定：团队 team-1，专家 未选" in first.payload["prompt"]

    r = await admin_client.post(
        f"/api/requirements/{req_id}/plan/messages",
        json={
            "content": "继续",
            "mode": "plan",
            "team_id": "team-x",
            "agent_id": "agt-y",
        },
    )
    assert r.status_code == 200, r.text
    second = await db.scalar(
        select(RunnerTask)
        .where(RunnerTask.requirement_id == req_id, RunnerTask.type == "plan_turn")
        .order_by(RunnerTask.id.desc())
    )
    assert second.payload["resume"] is False  # 换人 = 新会话
    prompt = second.payload["prompt"]
    assert "换团队/专家前的聊天记录" in prompt and "你好" in prompt
    assert "团队 team-x，专家 agt-y" in prompt
