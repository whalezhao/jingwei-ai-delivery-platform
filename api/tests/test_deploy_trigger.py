"""部署事件 → 待测需求匹配 → 触发 QA → 汇总通知。

不依赖 GitLab/QA 服务：call_qa_run 与 notify 都由用例替换掉，
只验证匹配口径、幂等与收件人。
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from vega import deploy
from vega.models import (
    DeployEvent,
    DevTask,
    Requirement,
    SystemOwner,
    SystemQaOwner,
    utcnow,
)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _req_with_task(db, project, person, *, merged_ago_minutes: int = 60) -> Requirement:
    req = Requirement(
        system_id=project.id,
        code=f"REQ-TEST-{_uid()}",
        title="部署触发测试",
        status="online",
        req_type="requirement",
        claimant_id=person.id,
    )
    db.add(req)
    await db.flush()
    db.add(
        DevTask(
            requirement_id=req.id,
            code=f"TASK-{req.code}-1",
            status="done",
            creator_id=person.id,
            merged_at=utcnow() - timedelta(minutes=merged_ago_minutes),
        )
    )
    await db.commit()
    return req


async def _event(db, project, *, environment="test", sha=None) -> DeployEvent:
    ev = DeployEvent(
        system_id=project.id,
        gitlab_project_id=project.gitlab_project_id,
        environment=environment,
        ref=project.mr_target_branch,
        sha=sha or _uid(),
        status="success",
        deployed_at=utcnow(),
    )
    db.add(ev)
    await db.commit()
    await db.refresh(ev)
    return ev


@pytest.fixture
def captured(monkeypatch):
    """拦掉对外调用：QA 发起与 octo 通知。"""
    calls = {"qa": [], "notify": []}

    async def fake_qa(requirement_id, environment, deploy_event_id):
        calls["qa"].append((requirement_id, environment, deploy_event_id))
        return "ok", None

    async def fake_notify(db, group, content, *, reason, mention=None):
        calls["notify"].append({"content": content, "reason": reason, "mention": mention})
        return True

    monkeypatch.setattr(deploy, "call_qa_run", fake_qa)
    monkeypatch.setattr(deploy, "notify", fake_notify)
    return calls


async def test_env_not_mapped_does_not_trigger(db, seeded, captured):
    project = seeded["project"]
    project.deploy_env_map = [{"gitlab": "prod", "qa": "生产"}]
    await db.commit()
    await _req_with_task(db, project, seeded["person"])
    ev = await _event(db, project, environment="test")

    result = await deploy.handle_deploy_event(db, ev.id)

    assert result["skipped"] == "env_not_mapped"
    assert captured["qa"] == []
    await db.refresh(ev)
    assert ev.handled_at is not None


async def test_merged_after_deploy_is_skipped(db, seeded, captured):
    project = seeded["project"]
    project.deploy_env_map = [{"gitlab": "test", "qa": "测试环境"}]
    await db.commit()
    await _req_with_task(db, project, seeded["person"], merged_ago_minutes=-10)  # 部署之后才合入
    ev = await _event(db, project)

    result = await deploy.handle_deploy_event(db, ev.id)

    assert "merged_after_deploy" in result["skipped"]
    assert captured["qa"] == []


async def test_pending_requirement_triggers_once_with_owners(db, seeded, captured):
    project = seeded["project"]
    project.deploy_env_map = [{"gitlab": "test", "qa": "测试环境"}]
    person = seeded["person"]
    db.add(SystemOwner(system_id=project.id, person_id=person.id))
    db.add(SystemQaOwner(system_id=project.id, person_id=person.id))
    await db.commit()
    req = await _req_with_task(db, project, person)
    ev = await _event(db, project)

    result = await deploy.handle_deploy_event(db, ev.id)

    assert result["triggered"] == [req.code]
    assert len(captured["qa"]) == 1
    assert captured["qa"][0][1] == "测试环境"
    # 汇总只发一条，收件人是测试负责人 + 系统负责人（同一个人去重）
    assert len(captured["notify"]) == 1
    assert captured["notify"][0]["reason"] == "deploy_autotest"
    assert [p.id for p in captured["notify"][0]["mention"]] == [person.id]

    # 同一事件再处理一次：已 handled，不再触发
    again = await deploy.handle_deploy_event(db, ev.id)
    assert again == {"skipped": "handled"}
    assert len(captured["qa"]) == 1


async def test_qa_error_is_reported_to_both_owners(db, seeded, monkeypatch):
    project = seeded["project"]
    project.deploy_env_map = [{"gitlab": "test", "qa": "测试环境"}]
    person = seeded["person"]
    db.add(SystemOwner(system_id=project.id, person_id=person.id))
    db.add(SystemQaOwner(system_id=project.id, person_id=person.id))
    await db.commit()
    await _req_with_task(db, project, person)
    ev = await _event(db, project)

    notes = []

    async def fake_qa(*a):
        return "error", "QA 不可达：connect refused"

    async def fake_notify(db, group, content, *, reason, mention=None):
        notes.append({"content": content, "reason": reason, "mention": mention})
        return True

    monkeypatch.setattr(deploy, "call_qa_run", fake_qa)
    monkeypatch.setattr(deploy, "notify", fake_notify)

    result = await deploy.handle_deploy_event(db, ev.id)

    assert result["errors"]
    assert [n["reason"] for n in notes] == ["deploy_autotest_failed"]
    assert [p.id for p in notes[0]["mention"]] == [person.id]