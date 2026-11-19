"""方案返工回路：轮次上限、修订稿自动成版、进入复审状态。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from vega import rework
from vega.models import PlanReview, PlanVersion, Requirement, RunnerTask

PLAN_TEXT = "说明\n<!-- vega:plan:begin -->\n# 修订方案\n<!-- vega:plan:end -->\n"


@pytest.fixture
async def req(db, seeded):
    row = Requirement(
        system_id=seeded["project"].id,
        code=f"REQ-REWORK-{uuid.uuid4().hex[:8]}",
        title="返工验证",
        status="confirmed",
        claimant_id=seeded["person"].id,
    )
    db.add(row)
    await db.commit()
    yield row
    # 本用例会真入队评测任务，清掉避免影响其他用例
    for t in (
        await db.scalars(select(RunnerTask).where(RunnerTask.requirement_id == row.id))
    ).all():
        await db.delete(t)
    for r in (
        await db.scalars(select(PlanReview).where(PlanReview.requirement_id == row.id))
    ).all():
        await db.delete(r)
    await db.commit()


async def test_start_rework_stops_at_round_cap(db, req):
    req.rework_round = rework.MAX_ROUNDS
    await db.commit()

    assert await rework.start_rework(db, req.id, "findings") is False
    assert req.rework_state is None


async def test_start_rework_is_idempotent_while_running(db, req):
    req.rework_state = "planning"
    await db.commit()

    assert await rework.start_rework(db, req.id, "findings") is False
    assert req.rework_round == 0


async def test_plan_turn_done_builds_version_and_queues_review(db, req):
    req.rework_state = "planning"
    await db.commit()

    assert await rework.on_plan_turn_done(db, req, PLAN_TEXT, {"rework": True})
    versions = (
        await db.scalars(select(PlanVersion).where(PlanVersion.requirement_id == req.id))
    ).all()
    assert [v.content for v in versions] == ["# 修订方案"]
    assert req.rework_state == "reviewing"
    review = await db.scalar(select(PlanReview).where(PlanReview.requirement_id == req.id))
    assert review is not None and review.status == "running"
    task = await db.get(RunnerTask, review.runner_task_id)
    assert task is not None and task.type == "egm_review" and task.payload["trigger"] == "rework"


async def test_plan_turn_without_block_stops_the_loop(db, req):
    req.rework_state = "planning"
    await db.commit()

    assert not await rework.on_plan_turn_done(db, req, "只有说明，没有方案块", {"rework": True})
    assert req.rework_state is None
    assert (
        await db.scalars(select(PlanVersion).where(PlanVersion.requirement_id == req.id))
    ).all() == []


async def test_non_rework_turn_is_ignored(db, req):
    await db.commit()
    assert not await rework.on_plan_turn_done(db, req, PLAN_TEXT, {})