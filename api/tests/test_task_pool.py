"""任务池：定稿单任务入池（拆解交由主 agent 运行期自开子 agent）· 抢单互斥 · 独立分支/prompt。"""

from __future__ import annotations

import httpx
from sqlalchemy import func, select

from conftest import login_as

from vega.main import app
from vega.models import DevTask, Person, PlanVersion, RunnerTask
from vega.routers.runner_api import scrub_token


def test_scrub_token():
    """clone 报错里的 oauth2 token 不能落库。"""
    msg = "git clone https://oauth2:***@gitlab.test/repo.git 失败"
    assert "glpat-SECRET" not in scrub_token(msg)
    assert scrub_token(msg).startswith("git clone https://oauth2:***@")
    assert scrub_token(None) is None


PLAN_MD = """# 方案

1. 前端加组件 2. 后端加接口 3. 联调"""


async def _add_version(db, req_id: int, content: str) -> int:
    """直接落一版方案（生产路径是 runner 回传时自动成版，这里只造数据）。"""
    nxt = (await db.scalar(select(func.max(PlanVersion.version)).where(PlanVersion.requirement_id == req_id)) or 0) + 1
    v = PlanVersion(requirement_id=req_id, version=nxt, content=content)
    db.add(v)
    await db.commit()
    await db.refresh(v)
    return v.id


async def _mk_confirmed_req(client, db, person_id: int, system_id: int, plan_md: str | None = None) -> int:
    # 用 int 而非实例属性：本文件有的测试 db.expire_all() 过，过期实例任何属性访问都会触发同步 refresh
    username = await db.scalar(select(Person.gitlab_username).where(Person.id == person_id))
    token, _ = await login_as(client, db, username)
    client.headers["Authorization"] = f"Bearer {token}"
    req_id = (
        await client.post(
            "/api/requirements",
            json={"system_id": system_id, "title": "单任务需求", "description": "d"},
        )
    ).json()["id"]
    await client.post(f"/api/requirements/{req_id}/claim")
    vid = await _add_version(db, req_id, plan_md if plan_md is not None else PLAN_MD)
    r = await client.post(f"/api/requirements/{req_id}/plan-versions/{vid}/finalize")
    assert r.status_code == 200, r.text
    return req_id


async def test_finalize_single_task_into_pool(client, db, seeded):
    """方案版本定稿 → 恰好 1 个任务（=整个需求）入池；方案里的 tasks 围栏不再被解析拆分。"""
    req_id = await _mk_confirmed_req(client, db, seeded["person"].id, seeded["project"].id)
    db.expire_all()

    tasks = (
        await db.scalars(select(DevTask).where(DevTask.requirement_id == req_id).order_by(DevTask.id))
    ).all()
    assert len(tasks) == 1
    t = tasks[0]
    assert t.title == "单任务需求" and t.scope is None
    assert t.status == "queued"  # 已自动入池
    assert t.branch.rsplit("/", 1)[-1] == "t1"

    rts = (
        await db.scalars(
            select(RunnerTask)
            .where(RunnerTask.type == "dev_task", RunnerTask.requirement_id == req_id)
            .order_by(RunnerTask.id)
        )
    ).all()
    assert len(rts) == 1
    prompt = rts[0].payload["prompt"]
    assert "前端加组件" in prompt  # 定稿方案全文作为上下文
    assert "不要实现它们的范围" not in prompt  # 无并行任务 → 无边界约束


async def test_finalize_picks_chosen_version(client, db, seeded):
    """定稿取指定版本（预览区挑版），且同需求唯一 final。"""
    pid, proj_id = seeded["person"].id, seeded["project"].id
    req_id = await _mk_confirmed_req(client, db, pid, proj_id, plan_md="1. 干活 2. 收尾")
    db.expire_all()
    tasks = (await db.scalars(select(DevTask).where(DevTask.requirement_id == req_id))).all()
    assert len(tasks) == 1 and tasks[0].title == "单任务需求"

    # 换一个需求：两版并存，定稿第二版 → plan_md 取第二版内容、仅它 final
    username = await db.scalar(select(Person.gitlab_username).where(Person.id == pid))
    token, _ = await login_as(client, db, username)
    client.headers["Authorization"] = f"Bearer {token}"
    req2 = (
        await client.post("/api/requirements", json={"system_id": proj_id, "title": "挑版需求", "description": "d"})
    ).json()["id"]
    await client.post(f"/api/requirements/{req2}/claim")
    v1 = await _add_version(db, req2, "旧方案")
    v2 = await _add_version(db, req2, "新方案")
    r = await client.post(f"/api/requirements/{req2}/plan-versions/{v2}/finalize")
    assert r.status_code == 200
    rows = {v.id: v for v in (await db.scalars(select(PlanVersion).where(PlanVersion.requirement_id == req2))).all()}
    assert rows[v2].is_final is True and rows[v1].is_final is False
    assert rows[v2].content == "新方案"


async def test_pool_claim_exclusive(client, admin_client, db, seeded):
    """两个节点抢 2 个任务（两个需求各 confirm 一个）：各拿各的、不重复；认领后状态可见。"""
    tokens = {}
    for name in ("pool-a", "pool-b", "pool-c"):
        tokens[name] = (await admin_client.post("/api/runner-nodes", json={"name": name})).json()["token"]

    async def claim(name: str) -> dict | None:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://api.test",
            headers={"Authorization": f"Bearer {tokens[name]}"},
        ) as rc:
            return (await rc.get("/api/runner/next", params={"wait": 0.05})).json()["task"]

    async def drain(name: str) -> int:
        """连抢到空：清掉本文件早前测试留下的排队任务，后面两个新任务才好断言归属。"""
        n = 0
        while await claim(name):
            n += 1
        return n

    await drain("pool-c")  # janitor 先清场，再入池本轮的两个任务

    req1 = await _mk_confirmed_req(client, db, seeded["person"].id, seeded["project"].id)
    req2 = await _mk_confirmed_req(client, db, seeded["person"].id, seeded["project"].id)
    t1, t2 = await claim("pool-a"), await claim("pool-b")
    assert t1 and t2 and t1["id"] != t2["id"]
    assert {t["payload"]["requirement_id"] for t in (t1, t2)} == {req1, req2}
    assert t1["payload"]["dev_task_id"] != t2["payload"]["dev_task_id"]
    assert await claim("pool-c") is None  # 池已抢空，不加塞不重复

    db.expire_all()
    devs = (await db.scalars(select(DevTask).where(DevTask.requirement_id.in_([req1, req2])))).all()
    assert {d.status for d in devs} == {"running"}  # 抢单即 running
    for rid in (req1, req2):
        out = (await client.get("/api/dev-tasks", params={"requirement_id": rid})).json()
        assert all(t["title"] for t in out)
    assert {t["runner_name"] for t in (await client.get("/api/dev-tasks")).json() if t["runner_name"]} >= {"pool-a", "pool-b"}
