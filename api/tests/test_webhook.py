"""webhook：secret 校验 / push 播报 / pipeline 失败走 LLM 分析（mock）。"""

from __future__ import annotations

from vega.gitlab import GitlabClient
from vega.llm_client import ask_llm
from vega.models import NotifyLog, Person


async def _secret(client, seeded) -> str:
    from sqlalchemy import select

    from vega.db import sessionmaker

    async with sessionmaker()() as s:
        from vega.models import DevGroup

        g = await s.get(DevGroup, seeded["group"].id)
        return g.webhook_secret


async def test_webhook_secret_rejected(client, seeded):
    r = await client.post(
        "/webhooks/gitlab",
        json={},
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": "wrong"},
    )
    assert r.status_code == 403


async def test_push_event_notifies(client, db, seeded):
    secret = await _secret(client, seeded)
    # 张三的 octo_uid 已知 → 通知会带上他
    person = await db.get(Person, seeded["person"].id)
    person.octo_uid = "u-zhangsan"
    await db.commit()

    u, e = seeded["person"].gitlab_username, seeded["person"].gitlab_email
    r = await client.post(
        "/webhooks/gitlab",
        json={
            "project_id": seeded["project"].gitlab_project_id,
            "user_name": u,
            "user_email": e,
            "ref": "refs/heads/feature/x",
            "total_commits_count": 2,
            "after": "abc123",
        },
        headers={"X-Gitlab-Event": "Push Hook", "X-Gitlab-Token": secret},
    )
    assert r.status_code == 200, r.text

    from sqlalchemy import select

    log = (
        await db.scalars(select(NotifyLog).where(NotifyLog.reason == "push").order_by(NotifyLog.id.desc()))
    ).first()
    assert log and "推送 2 个提交" in log.content and "u-zhangsan" in log.mentioned_uids


async def test_mr_open_notifies(client, db, seeded):
    secret = await _secret(client, seeded)
    from vega.models import SystemOwner

    owner = Person(name="项目负责人乙", octo_uid="uid-mr-owner")
    db.add(owner)
    await db.flush()
    db.add(SystemOwner(system_id=seeded["project"].id, person_id=owner.id))
    await db.commit()
    r = await client.post(
        "/webhooks/gitlab",
        json={
            # 真实 MR hook 没有顶层 project_id：项目 id 只在 project.id（13.12 实测）
            "project": {"id": seeded["project"].gitlab_project_id, "path_with_namespace": seeded["project"].path},
            "author": {"username": seeded["person"].gitlab_username, "name": "张三", "email": seeded["person"].gitlab_email},
            "object_attributes": {
                "iid": 5,
                "action": "open",
                "title": "新功能",
                "source_branch": "feature/x",
                "target_branch": "nightly",
            },
        },
        headers={"X-Gitlab-Event": "Merge Request Hook", "X-Gitlab-Token": secret},
    )
    assert r.status_code == 200
    from sqlalchemy import select

    log = (
        await db.scalars(select(NotifyLog).where(NotifyLog.reason == "mr_opened").order_by(NotifyLog.id.desc()))
    ).first()
    assert log and "新 MR !5" in log.content
    # 项目能按 project.id 反查到，播报才带得出项目名（认不出时是「?」）
    assert seeded["project"].path in log.content
    assert "uid-mr-owner" in log.mentioned_uids  # 新 MR 播报要 @项目负责人


async def test_mr_close_marks_task_and_notifies(client, db, seeded):
    """人工关闭 MR：当场收尾任务并播报（不再等 10 分钟轮询，也不会静默）。"""
    secret = await _secret(client, seeded)
    from sqlalchemy import select

    from vega.models import DevTask, NotifyLog, SystemOwner, Requirement

    owner = Person(name="项目负责人丙", octo_uid="uid-close-owner")
    db.add(owner)
    await db.flush()
    db.add(SystemOwner(system_id=seeded["project"].id, person_id=owner.id))
    req = Requirement(system_id=seeded["project"].id, code="REQ-CLOSE-1", title="关闭需求", status="claimed")
    db.add(req)
    await db.flush()
    task = DevTask(
        requirement_id=req.id,
        code="TASK-CLOSE-1",
        status="mr_created",
        creator_id=seeded["person"].id,
        mr_iid=7,
        mr_url="http://gitlab.test/team/repo/-/merge_requests/7",
    )
    db.add(task)
    await db.commit()

    r = await client.post(
        "/webhooks/gitlab",
        json={
            "project": {"id": seeded["project"].gitlab_project_id, "path_with_namespace": seeded["project"].path},
            "object_attributes": {"iid": 7, "action": "close", "title": "关闭"},
        },
        headers={"X-Gitlab-Event": "Merge Request Hook", "X-Gitlab-Token": secret},
    )
    assert r.status_code == 200, r.text

    log = (
        await db.scalars(select(NotifyLog).where(NotifyLog.reason == "mr_closed").order_by(NotifyLog.id.desc()))
    ).first()
    assert log and "MR 被关闭" in log.content and "uid-close-owner" in log.mentioned_uids
    await db.refresh(task)
    assert task.status == "done" and (task.error or "").startswith("MR 被关闭")

    # 重复的 close 事件不再打扰（幂等）
    await client.post(
        "/webhooks/gitlab",
        json={
            "project": {"id": seeded["project"].gitlab_project_id},
            "object_attributes": {"iid": 7, "action": "close"},
        },
        headers={"X-Gitlab-Event": "Merge Request Hook", "X-Gitlab-Token": secret},
    )
    n = len((await db.scalars(select(NotifyLog).where(NotifyLog.reason == "mr_closed"))).all())
    assert n == 1


async def test_pipeline_failed_triggers_llm_analysis(client, db, seeded, monkeypatch):
    secret = await _secret(client, seeded)

    async def fake_pipeline_jobs(self, client_, system_id, pipeline_id):
        return [{"id": 99, "name": "build", "stage": "build", "status": "failed"}]

    async def fake_trace(self, client_, system_id, job_id):
        return "npm ERR! missing script build\nexit 1"

    async def fake_ask_llm(prompt, **kw):
        assert "npm ERR" in prompt
        return "根因：package.json 缺少 build 脚本。建议：1. 补 scripts.build"

    monkeypatch.setattr(GitlabClient, "pipeline_jobs", fake_pipeline_jobs)
    monkeypatch.setattr(GitlabClient, "job_trace", fake_trace)
    monkeypatch.setattr("vega.jobs.ask_llm", fake_ask_llm)

    r = await client.post(
        "/webhooks/gitlab",
        json={
            # Pipeline hook 同样只有 project.id
            "project": {"id": seeded["project"].gitlab_project_id, "path_with_namespace": seeded["project"].path},
            "user": {"username": seeded["person"].gitlab_username, "name": "张三", "email": seeded["person"].gitlab_email},
            "object_attributes": {"id": 31, "status": "failed", "ref": "feature/x", "sha": "deadbeef99"},
        },
        headers={"X-Gitlab-Event": "Pipeline Hook", "X-Gitlab-Token": secret},
    )
    assert r.status_code == 200, r.text

    from sqlalchemy import select

    log = (
        await db.scalars(select(NotifyLog).where(NotifyLog.reason == "ci_failed").order_by(NotifyLog.id.desc()))
    ).first()
    assert log and "CI 失败" in log.content and "build 脚本" in log.content

    # success 静默
    r = await client.post(
        "/webhooks/gitlab",
        json={
            "project": {"id": seeded["project"].gitlab_project_id, "path_with_namespace": seeded["project"].path},
            "user": {"username": seeded["person"].gitlab_username},
            "object_attributes": {"id": 32, "status": "success", "ref": "feature/x", "sha": "ab12"},
        },
        headers={"X-Gitlab-Event": "Pipeline Hook", "X-Gitlab-Token": secret},
    )
    assert r.status_code == 200
    n_ok = len((await db.scalars(select(NotifyLog).where(NotifyLog.reason == "ci_failed"))).all())
    assert n_ok == 1
