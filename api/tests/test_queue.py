"""队列：认领互斥 + plan_turn 粘性路由。"""

from __future__ import annotations

import asyncio

from sqlalchemy import delete, select

from vega.db import sessionmaker
from vega.models import Requirement, RunnerNode, RunnerTask
from vega.queue import claim_next, enqueue, requeue_dead_nodes


async def _clear_queue(db):
    # 库是整个 session 共享的：先清掉别的用例漏下的 queued 任务，claim 才可断言
    await db.execute(delete(RunnerTask).where(RunnerTask.status == "queued"))
    await db.commit()


async def _mk_task(db, **payload):
    return await enqueue(
        db,
        type=payload.pop("type", "plan_turn"),
        payload=payload,
        requirement_id=payload.pop("requirement_id", None),
    )


async def test_claim_exclusive_and_fifo(db, seeded):
    await _clear_queue(db)
    r1 = await _mk_task(db, type="dev_task", prompt="t1")
    r2 = await _mk_task(db, type="dev_task", prompt="t2")

    # 两个 runner 并发认领 → 各拿一条、不重复
    a, b = await asyncio.gather(claim_next(seeded["group"].id + 1000, wait_s=1), claim_next(seeded["group"].id + 2000, wait_s=1))
    ids = {a["id"], b["id"]}
    assert ids == {r1.id, r2.id}

    # 队列空 → 长轮询到点返回 None
    assert await claim_next(999, wait_s=0.3) is None


async def test_sticky_routing(db, seeded):
    """带 sticky_runner_id 的任务只有指定 runner 能领。"""
    await _clear_queue(db)
    sticky_runner = seeded["group"].id + 5000
    t = await _mk_task(db, type="plan_turn", sticky_runner_id=sticky_runner, prompt="plan")

    # 其他 runner 领不到（等 0.5s）
    assert await claim_next(sticky_runner + 1, wait_s=0.5) is None
    # 指定 runner 立刻领到
    got = await claim_next(sticky_runner, wait_s=1)
    assert got["id"] == t.id
    assert got["payload"]["sticky_runner_id"] == sticky_runner


async def test_claim_aborts_when_client_dead(db, seeded):
    """客户端断开（alive=False）时长轮询必须放弃，不能把任务抢给死连接（runner 重启孤儿认领）。"""
    await _clear_queue(db)
    t = await _mk_task(db, type="dev_task", prompt="orphan")
    rid = seeded["group"].id + 6000

    async def dead() -> bool:
        return False

    assert await claim_next(rid, wait_s=1, alive=dead) is None
    # 任务必须还在队列里，能被活连接领走
    got = await claim_next(rid, wait_s=1)
    assert got["id"] == t.id


async def test_claim_priority_mr_review_first(db, seeded):
    """mr_review 有时效（MR 开着等结论）：即使后入队也先于先入队的 dev_task 被领。"""
    await _clear_queue(db)
    dev = await _mk_task(db, type="dev_task", prompt="slow-dev")
    plan = await _mk_task(db, type="plan_turn", prompt="plan")
    review = await _mk_task(db, type="mr_review", system_id=1)

    rid = seeded["group"].id + 7000
    first = await claim_next(rid, wait_s=1)
    second = await claim_next(rid + 1, wait_s=1)
    third = await claim_next(rid + 2, wait_s=1)
    assert [first["id"], second["id"], third["id"]] == [review.id, plan.id, dev.id]


async def test_requeue_dead_nodes(db, seeded):
    """认领节点心跳停了的任务要放回队列；活节点的任务不动。"""
    await _clear_queue(db)
    dead = RunnerNode(name="dead-node", token_hash="x", status="online", last_heartbeat=None)
    db.add(dead)
    await db.flush()
    t = await _mk_task(db, type="dev_task", prompt="zombie")
    # 人为置成已被死节点认领
    await db.execute(
        RunnerTask.__table__.update().where(RunnerTask.id == t.id).values(status="claimed", runner_id=dead.id)
    )
    await db.commit()

    n = await requeue_dead_nodes()
    assert n >= 1
    row = (await db.execute(RunnerTask.__table__.select().where(RunnerTask.id == t.id))).one()
    assert row.status == "queued" and row.runner_id is None


async def test_delete_node_unbinds_sticky(admin_client, db, seeded):
    """删节点必须解绑粘性：sticky 只认已不存在的节点，会话任务会永远卡在 queued。"""
    r = await admin_client.post("/api/runner-nodes", json={"name": "node-del"})
    assert r.status_code == 200, r.text
    node_id = r.json()["id"]

    req = Requirement(
        system_id=seeded["project"].id,
        code="REQ-DEL-1",
        title="删节点",
        status="claimed",
        claimant_id=seeded["person"].id,
        plan_runner_id=node_id,
        req_session_id="sess-req",
        plan_session_id="sess-plan",
    )
    db.add(req)
    await db.commit()

    queued = await _mk_task(db, sticky_runner_id=node_id, prompt="粘住的消息")
    running = await _mk_task(db, sticky_runner_id=node_id, prompt="在跑的任务")
    # commit 会 expire 所有属性；id 先取出来，后面 expire_all 后就不会触发同步懒加载
    qid, rid, req_id = queued.id, running.id, req.id
    await db.execute(
        RunnerTask.__table__.update()
        .where(RunnerTask.id == running.id)
        .values(status="running", runner_id=node_id)
    )
    await db.commit()

    assert (await admin_client.delete(f"/api/runner-nodes/{node_id}")).status_code == 200

    db.expire_all()
    rows = (await db.scalars(select(RunnerTask).where(RunnerTask.id.in_((qid, rid))))).all()
    assert len(rows) == 2
    for t in rows:
        assert "sticky_runner_id" not in t.payload
        assert t.runner_id is None and t.status == "queued"
    fresh = await db.get(Requirement, req_id)
    assert fresh is not None and fresh.plan_runner_id is None
    # 会话在节点本机，节点没了就作废：下一条消息新开会话并带历史
    assert fresh.req_session_id is None and fresh.plan_session_id is None
