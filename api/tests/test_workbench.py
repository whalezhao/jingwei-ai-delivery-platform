"""工作台只显示实时工作的需求：任务全终态即退场，不再返回已毕业汇总。"""

from __future__ import annotations

from conftest import login_as

from vega.jobs import MR_CLOSED_MARK
from vega.models import DevTask, MrReview, PlanMessage, PlanVersion, ProjectOwner, RequirementDoc
from vega.routers.workbench import requirement_graduation, task_outcome


def test_graduation_rules():
    """merged 看 status=done（closed 靠哨兵文案区分）；failed 直接收尾。"""
    t = DevTask(status="failed")
    assert task_outcome(t, None) == "failed"
    t = DevTask(status="done")
    assert task_outcome(t, None) == "merged"
    t = DevTask(status="done", error=MR_CLOSED_MARK)
    assert task_outcome(t, None) == "closed"
    t = DevTask(status="running")
    assert task_outcome(t, None) is None
    t = DevTask(status="done", error=f"{MR_CLOSED_MARK}：标题变了也能识别")
    assert task_outcome(t, None) == "closed"

    assert requirement_graduation([]) is None  # 没有任务不毕业
    assert requirement_graduation(["merged", None]) is None  # 还有人没到终点
    assert requirement_graduation(["merged", "merged"]) == "done"
    assert requirement_graduation(["merged", "failed"]) == "failed"
    assert requirement_graduation(["merged", "closed"]) == "closed"


async def test_workbench_graduation_filter(client, db, seeded):
    """两个需求：一个全终态（退场不返回）、一个进行中（留在流程图）。"""
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    pid, proj = seeded["project"].id, seeded["project"].id
    r1 = (
        await client.post("/api/requirements", json={"project_id": pid, "title": "毕业需求", "description": "d"})
    ).json()["id"]
    r2 = (
        await client.post("/api/requirements", json={"project_id": proj, "title": "在读需求", "description": "d"})
    ).json()["id"]
    # r3：没有定稿文档 → 还没进入跟踪流程
    await client.post("/api/requirements", json={"project_id": proj, "title": "未定稿需求", "description": "d"})

    # r1：两任务全 done（一个 merged 一个关闭）→ 收尾
    for err in (None, MR_CLOSED_MARK):
        db.add(DevTask(requirement_id=r1, code=f"TASK-A-{err}", status="done", creator_id=1, error=err))
    # r2：一个 done 一个 running → 在读
    db.add(DevTask(requirement_id=r2, code="TASK-B-1", status="done", creator_id=1))
    db.add(DevTask(requirement_id=r2, code="TASK-B-2", status="running", creator_id=1))
    # 工作台跟踪初态是需求定稿：r1/r2 有定稿文档，r3 没有
    for rid in (r1, r2):
        db.add(RequirementDoc(requirement_id=rid, filename="需求.md", version=1, is_final=True, content=b"x"))
    await db.commit()

    base = f"/api/workbench?project_id={proj}"
    d = (await client.get(base)).json()
    assert [r["id"] for r in d["requirements"]] == [r2]  # 收尾的退场，未定稿的不进场
    assert "graduated_summary" not in d  # 已毕业名册已下线
    assert "default_group_id" in d  # 登录人默认研发组（未入组为 None）


async def test_workbench_expert_attribution(client, db, seeded):
    """需求/方案专家从对话记录带出：显式选择优先，空则回落到认领人在该组的默认 agent。"""
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    pid = seeded["project"].id
    rid = (
        await client.post("/api/requirements", json={"project_id": pid, "title": "专家", "description": "d"})
    ).json()["id"]
    await client.post(f"/api/requirements/{rid}/claim")
    db.add(RequirementDoc(requirement_id=rid, filename="需求.md", version=1, is_final=True, content=b"x"))
    await db.commit()

    base = {"project_id": pid}

    # 还没聊过 → 两个专家都为空
    row = next(r for r in (await client.get("/api/workbench", params=base)).json()["requirements"] if r["id"] == rid)
    assert row["doc_agent"] is None and row["plan_agent"] is None

    # 需求讨论没选专家 → 认领人在组内的默认 agent（agent-zhangsan → 张三）
    db.add(PlanMessage(requirement_id=rid, role="user", mode="requirement", content="写需求"))
    await db.commit()
    row = next(r for r in (await client.get("/api/workbench", params=base)).json()["requirements"] if r["id"] == rid)
    assert row["doc_agent"] == {"id": "agent-zhangsan", "name": "张三"}
    assert row["plan_agent"] is None

    # 方案讨论显式选了没映射的 agent → 原样显示 id；后一条覆盖前一条
    for aid in ("agent-old", "agent-custom"):
        db.add(PlanMessage(requirement_id=rid, role="user", mode="plan", agent_id=aid, content="写方案"))
    await db.commit()
    row = next(r for r in (await client.get("/api/workbench", params=base)).json()["requirements"] if r["id"] == rid)
    assert row["plan_agent"] == {"id": "agent-custom", "name": "agent-custom"}
    assert row["doc_agent"] == {"id": "agent-zhangsan", "name": "张三"}


async def test_workbench_project_gates(client, db, seeded):
    """卡片必要信息 + 轨道裁剪依据：项目配置决定有无评审/自动合并，负责人名单给通知卡。"""
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    proj = seeded["project"]
    proj.audit_prefixes = ["api/"]
    proj.auto_approve_prefixes = ["api/"]
    db.add(ProjectOwner(project_id=proj.id, person_id=seeded["person"].id))
    rid = (
        await client.post("/api/requirements", json={"project_id": proj.id, "title": "配置", "description": "d"})
    ).json()["id"]
    db.add(RequirementDoc(requirement_id=rid, filename="需求.md", version=2, is_final=True, content=b"x"))
    db.add(PlanVersion(requirement_id=rid, version=3, content="方案", is_final=True))
    task = DevTask(requirement_id=rid, code="TASK-GATE-1", status="mr_created", creator_id=seeded["person"].id, mr_iid=9)
    db.add(task)
    await db.flush()
    db.add(
        MrReview(project_id=proj.id, group_id=proj.group_id, mr_iid=9, head_sha="a" * 40, verdict="approve")
    )
    await db.commit()

    row = next(
        r
        for r in (await client.get("/api/workbench", params={"project_id": proj.id})).json()["requirements"]
        if r["id"] == rid
    )
    assert row["doc"]["version"] == 2 and row["doc"]["filename"] == "需求.md"
    assert row["plan"]["version"] == 3
    # 每张卡都要有时间：SQLite 读回 naive UTC，_iso 补上 +00:00 供前端转本地时区
    assert row["created_at"] and row["doc"]["time"] and row["plan"]["time"]
    assert row["tasks"][0]["created_at"].endswith("+00:00")
    assert row["tasks"][0]["mr"]["created_at"].endswith("+00:00")
    assert row["review_on"] is True and row["merge_on"] is True
    assert row["owners"] == [seeded["person"].name]
