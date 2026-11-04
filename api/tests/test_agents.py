"""多 agent 协作链路：子 agent 事件带 parent 归属 → 事件回放 / 工作台协作统计 / 定稿节点。"""

from __future__ import annotations

import httpx
from sqlalchemy import func, select

from conftest import login_as

from vega.main import app
from vega.models import RunnerNode, RunnerTask
from vega.models import DevTask, PlanVersion


async def _mk_running_task(client, admin_client, db, seeded) -> tuple[int, int, str]:
    """方案定稿（单任务入池）→ 注册节点 → 直接认领，返回 (dev_task_id, runner_task_id, node_token)。"""
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    req_id = (
        await client.post(
            "/api/requirements",
            json={"system_id": seeded["project"].id, "title": "协作", "description": "d"},
        )
    ).json()["id"]
    await client.post(f"/api/requirements/{req_id}/claim")
    nxt = (await db.scalar(select(func.max(PlanVersion.version)).where(PlanVersion.requirement_id == req_id)) or 0) + 1
    db.add(PlanVersion(requirement_id=req_id, version=nxt, content="1. 干活"))
    await db.commit()
    vid = (await db.scalar(select(PlanVersion.id).where(PlanVersion.requirement_id == req_id)))  # type: ignore[assignment]
    await client.post(f"/api/requirements/{req_id}/plan-versions/{vid}/finalize")
    dev_id = (await db.scalar(select(DevTask.id).where(DevTask.requirement_id == req_id)))  # type: ignore[assignment]

    nt = (await admin_client.post("/api/runner-nodes", json={"name": "node-ag"})).json()["token"]
    node = (await db.scalars(select(RunnerNode).where(RunnerNode.name == "node-ag"))).first()
    rt = RunnerTask(
        type="dev_task", payload={}, status="running",
        runner_id=node.id, dev_task_id=dev_id, requirement_id=req_id,
    )
    db.add(rt)
    await db.commit()
    return dev_id, rt.id, nt


async def test_subagent_events_lanes_and_workbench(client, admin_client, db, seeded):
    dev_id, rt_id, nt = await _mk_running_task(client, admin_client, db, seeded)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test",
        headers={"Authorization": f"Bearer {nt}"},
    ) as rc:
        await rc.post(f"/api/runner/tasks/{rt_id}/events", json={"events": [
            {"kind": "thinking", "text": "主 agent 思考", "parent": ""},
            {"kind": "tool_use", "id": "toolu_01", "name": "Task", "input": '{"description": "查登录模块"}', "parent": ""},
            {"kind": "tool_use", "name": "Grep", "input": "{}", "parent": "toolu_01"},
            {"kind": "text", "text": "子 agent 结论", "parent": "toolu_01"},
            {"kind": "tool_result", "content": "ok", "is_error": False, "parent": "toolu_01"},
        ]})

    # 回放接口原样带出 parent / tool_use id —— 前端按其分主/子泳道
    evs = (await client.get(f"/api/dev-tasks/{dev_id}/events")).json()
    assert [e.get("parent") for e in evs] == ["", "", "toolu_01", "toolu_01", "toolu_01"]
    assert evs[1]["id"] == "toolu_01"

    # 工作台跟踪初态是需求定稿：还没上传定稿文档的需求不进场
    req_id = (await db.scalar(select(DevTask.requirement_id).where(DevTask.id == dev_id)))  # type: ignore[assignment]
    wb = (await client.get("/api/workbench", params={"system_id": seeded["project"].id})).json()
    assert all(r["id"] != req_id for r in wb["requirements"])

    # 上传首个文档即自动定稿 → 进流程图，协作统计（1 主 + 1 子）带出
    import io

    r = await client.post(
        f"/api/requirements/{req_id}/docs",
        files={"file": ("需求.md", io.BytesIO("# 需求".encode()), "text/markdown")},
    )
    assert r.status_code == 200, r.text
    wb = (await client.get("/api/workbench", params={"system_id": seeded["project"].id})).json()
    row = next(r for r in wb["requirements"] if r["id"] == req_id)
    assert row["doc_final"] is True and row["plan_final"] is True
    assert row["tasks"][0]["agents"] == {"sub": 1, "events": 5}

    # 清场：confirm 自动入队的 queued 任务不能漏给后续抢单测试
    from sqlalchemy import update

    await db.execute(
        update(RunnerTask)
        .where(RunnerTask.requirement_id == req_id, RunnerTask.status == "queued")
        .values(status="failed")
    )
    await db.commit()
