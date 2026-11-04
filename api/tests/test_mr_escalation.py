"""MR 无人处理三级升级：12h/级、3 级封顶、级别单调不重发；第 3 级连带 @系统负责人。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from vega.gitlab import GitlabClient
from vega.jobs import merge_poll
from vega.models import DevTask, NotifyLog, Person, SystemOwner, Requirement, utcnow


def _hours_ago(h: float) -> str:
    return (utcnow() - timedelta(hours=h)).isoformat().replace("+00:00", "Z")


async def _seed_task(db, system_id: int, person_id: int, *, iid: int) -> tuple[Requirement, DevTask]:
    req = Requirement(system_id=system_id, title="升级验证", code=f"REQ-ESC-{iid}")
    db.add(req)
    await db.flush()
    task = DevTask(
        requirement_id=req.id,
        code=f"TASK-ESC-{iid}",
        status="reviewing",
        creator_id=person_id,
        mr_iid=iid,
        mr_url=f"https://git.example.com/!{iid}",
    )
    db.add(task)
    await db.commit()
    return req, task


def _patch_mr_detail(monkeypatch, *, state: str = "opened", updated_at: str | None = None):
    async def fake(self, client, system_id, mr_iid):
        return {"state": state, "updated_at": updated_at or _hours_ago(0)}

    monkeypatch.setattr(GitlabClient, "mr_detail", fake)


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    for t in (await db.scalars(select(DevTask).where(DevTask.code.like("TASK-ESC-%")))).all():
        await db.delete(t)
    for r in (await db.scalars(select(Requirement).where(Requirement.code.like("REQ-ESC-%")))).all():
        await db.delete(r)
    await db.commit()


async def _escalate_logs(db, task: DevTask) -> list[NotifyLog]:
    rows = (await db.scalars(select(NotifyLog).where(NotifyLog.reason.like("mr_escalate_%")))).all()
    return [l for l in rows if task.code in l.content]


async def test_no_escalation_within_half_day(client, db, seeded, monkeypatch):
    pid = seeded["person"].id
    _, task = await _seed_task(db, seeded["project"].id, pid, iid=31)
    _patch_mr_detail(monkeypatch, updated_at=_hours_ago(5))
    await merge_poll(db)
    await db.refresh(task)
    assert task.escalated_level == 0
    assert await _escalate_logs(db, task) == []


async def test_level1_after_12h_then_level2_only(client, db, seeded, monkeypatch):
    pid = seeded["person"].id
    _, task = await _seed_task(db, seeded["project"].id, pid, iid=32)
    _patch_mr_detail(monkeypatch, updated_at=_hours_ago(13))
    await merge_poll(db)
    await db.refresh(task)
    assert task.escalated_level == 1
    assert [l.reason for l in await _escalate_logs(db, task)] == ["mr_escalate_l1"]

    # 再跑一轮同 idle：级别没涨 → 不重发
    await merge_poll(db)
    assert [l.reason for l in await _escalate_logs(db, task)] == ["mr_escalate_l1"]

    # idle 到 25h：只发 l2，不重发 l1
    _patch_mr_detail(monkeypatch, updated_at=_hours_ago(25))
    await merge_poll(db)
    await db.refresh(task)
    assert task.escalated_level == 2
    assert [l.reason for l in await _escalate_logs(db, task)] == ["mr_escalate_l1", "mr_escalate_l2"]


async def test_level3_mentions_owner(client, db, seeded, monkeypatch):
    pid = seeded["person"].id
    owner = Person(name="项目负责人甲", gitlab_username="owner-esc", octo_uid="uid-owner-esc")
    db.add(owner)
    await db.flush()
    db.add(SystemOwner(system_id=seeded["project"].id, person_id=owner.id))
    _, task = await _seed_task(db, seeded["project"].id, pid, iid=33)
    _patch_mr_detail(monkeypatch, updated_at=_hours_ago(37))
    await merge_poll(db)
    await db.refresh(task)
    assert task.escalated_level == 3  # 封顶：idle 已 37h，首次扫描直接落在 3 级
    logs = await _escalate_logs(db, task)
    assert [l.reason for l in logs] == ["mr_escalate_l3"]  # 跳级只发当前级，不倒补 l1/l2
    top = logs[-1]
    assert "系统负责人介入" in top.content
    assert "uid-owner-esc" in top.mentioned_uids  # 审核人之外连带 @系统负责人


async def test_merged_or_closed_no_escalation(client, db, seeded, monkeypatch):
    """毕业路径优先：MR 已合并/关闭时不做升级判定。"""
    pid = seeded["person"].id
    req, task = await _seed_task(db, seeded["project"].id, pid, iid=34)
    _patch_mr_detail(monkeypatch, state="merged", updated_at=_hours_ago(48))
    await merge_poll(db)
    await db.refresh(task)
    await db.refresh(req)
    assert task.status == "done" and task.escalated_level == 0
    assert req.status == "online"  # 唯一任务已合入 → 需求置已上线
    assert await _escalate_logs(db, task) == []
