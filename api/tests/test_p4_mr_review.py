"""P4 MR 审计链：前缀 fail-closed · 入队幂等 · _parse_verdict · _finish_mr_review 落库+门禁+评论。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from vega.gitlab import GitlabClient
from vega.models import MrReview, NotifyLog, RunnerTask
from vega.queue import enqueue
from vega.runner import _parse_verdict
from vega.routers.runner_api import _finish_mr_review, _review_comment
from vega.routers.webhooks import _enqueue_mr_review_if_needed
from vega.schemas import TaskResultIn

SHA = "a" * 40


def _mr_payload(action: str = "update", sha: str = SHA, system_id: int | None = None) -> dict:
    return {
        "project_id": system_id,  # GitLab hook 的真实字段名，_project_id 只认这个
        "author": {"name": "张三"},
        "object_attributes": {
            "iid": 7,
            "title": "feat: x",
            "action": action,
            "source_branch": "feature/x",
            "target_branch": "nightly",
            "last_commit": {"id": sha},
        },
    }


async def _patch_gl(monkeypatch, *, changes: list[dict], pipelines: list[dict], notes: list[str]):
    async def fake_changes(self, client, system_id, mr_iid):
        return changes

    async def fake_detail(self, client, system_id, mr_iid):
        return {"sha": SHA}

    async def fake_pipelines(self, client, system_id, mr_iid):
        return pipelines

    async def fake_note(self, client, system_id, mr_iid, body):
        notes.append(body)
        return {}

    for name, fake in (
        ("mr_changes", fake_changes),
        ("mr_detail", fake_detail),
        ("mr_pipelines", fake_pipelines),
        ("add_mr_note", fake_note),
    ):
        monkeypatch.setattr(GitlabClient, name, fake)


CHANGES = [{"new_path": "api/vega/x.py"}, {"new_path": "web/src/y.ts"}]


@pytest.fixture(autouse=True)
async def _cleanup_queue(db):
    """本文件会真入队 mr_review 任务；不清掉会被别的测试的 runner 长轮询抢走（共享库）。"""
    yield
    for t in (await db.scalars(select(RunnerTask).where(RunnerTask.type == "mr_review"))).all():
        await db.delete(t)
    for r in (await db.scalars(select(MrReview))).all():
        await db.delete(r)
    await db.commit()


# ---------- _parse_verdict（纯函数） ----------


def test_parse_verdict_bare_json():
    assert _parse_verdict('{"verdict":"approve","summary":"ok"}')["verdict"] == "approve"


def test_parse_verdict_fenced_with_prose():
    text = "我先分析一下……\n```json\n{\"verdict\": \"modify\", \"summary\": \"有问题\", \"issues\": [{\"file\": \"a.py\"}]}\n```\n以上。"
    d = _parse_verdict(text)
    assert d["verdict"] == "modify" and d["issues"][0]["file"] == "a.py"


def test_parse_verdict_garbage():
    with pytest.raises(ValueError):
        _parse_verdict("我认为这个 MR 没问题，可以合并。")
    with pytest.raises(ValueError):
        _parse_verdict('{"verdict":"maybe"}')


# ---------- 入队：fail-closed / 部分命中 / 幂等 ----------


async def test_enqueue_fail_closed_without_prefixes(client, db, seeded, monkeypatch):
    called = []
    await _patch_gl(monkeypatch, changes=CHANGES, pipelines=[], notes=called)
    await _enqueue_mr_review_if_needed(seeded["group"].id, seeded["project"].gitlab_project_id, _mr_payload())
    tasks = (await db.scalars(select(RunnerTask).where(RunnerTask.type == "mr_review"))).all()
    assert tasks == [] and called == []  # 未配置前缀 → 连 GitLab 都不查


async def test_enqueue_partial_hit_only_matching_files(client, db, seeded, monkeypatch):
    notes: list[str] = []
    await _patch_gl(monkeypatch, changes=CHANGES, pipelines=[], notes=notes)
    seeded["project"].audit_prefixes = ["api/"]
    await db.commit()

    await _enqueue_mr_review_if_needed(seeded["group"].id, seeded["project"].gitlab_project_id, _mr_payload())
    task = await db.scalar(select(RunnerTask).where(RunnerTask.type == "mr_review"))
    review = await db.scalar(select(MrReview))
    assert task and review
    assert task.payload["changed_files"] == ["api/vega/x.py"]  # 只传命中文件
    assert review.verdict == "pending" and review.head_sha == SHA
    # token 加密单存：payload 明文里没有，认领时才解密并入（内存态）
    assert "git_token" not in task.payload and "claude_env" not in task.payload
    from vega.config import get_settings as _gs
    from vega.crypto import decrypt as _dec

    sec = json.loads(_dec(_gs().secret_key, task.secrets))
    assert sec["git_token"] == "gl-token" and "claude_env" in sec

    # 同 sha 再来一次（并发事件）→ 唯一约束挡住，不重复入队
    n_before = len((await db.scalars(select(RunnerTask).where(RunnerTask.type == "mr_review"))).all())
    await _enqueue_mr_review_if_needed(seeded["group"].id, seeded["project"].gitlab_project_id, _mr_payload())
    n_after = len((await db.scalars(select(RunnerTask).where(RunnerTask.type == "mr_review"))).all())
    assert n_before == n_after == 1
    # 评审开始播报：首次入队有留痕；同 sha 第二次被唯一约束挡下，不重复播报
    started = (await db.scalars(select(NotifyLog).where(NotifyLog.reason == "mr_review_started"))).all()
    assert len(started) == 1 and "代码评审开始" in started[0].content and "!7" in started[0].content


async def test_enqueue_retries_failed_review(client, db, seeded, monkeypatch):
    """初审失败 → 同 sha 的 update 事件要能重试，不能被唯一约束永久挡住。"""
    notes: list[str] = []
    await _patch_gl(monkeypatch, changes=CHANGES, pipelines=[], notes=notes)
    seeded["project"].audit_prefixes = ["api/"]
    await db.commit()

    await _enqueue_mr_review_if_needed(seeded["group"].id, seeded["project"].gitlab_project_id, _mr_payload())
    review = await db.scalar(select(MrReview))
    review.verdict, review.error = "failed", "claude -p 退出码 1"
    await db.commit()

    await _enqueue_mr_review_if_needed(seeded["group"].id, seeded["project"].gitlab_project_id, _mr_payload())
    db.expire_all()
    tasks = (await db.scalars(select(RunnerTask).where(RunnerTask.type == "mr_review"))).all()
    assert len(tasks) == 2  # 第二次确实重新入队
    review = await db.scalar(select(MrReview))
    assert review.verdict == "pending" and review.error is None


async def test_enqueue_circuit_breaker_after_5_per_day(client, db, seeded, monkeypatch):
    """熔断：同一 MR 24h 内已审 5 次 → 再来的 update 事件不入队（防高频推送烧 token）。"""
    notes: list[str] = []
    await _patch_gl(monkeypatch, changes=CHANGES, pipelines=[], notes=notes)
    seeded["project"].audit_prefixes = ["api/"]
    await db.commit()

    for i in range(5):
        db.add(
            MrReview(
                system_id=seeded["project"].id,
                group_id=seeded["group"].id,
                mr_iid=7,
                head_sha=f"{i}" + "b" * 39,
                verdict="approve",
            )
        )
    await db.commit()
    n_before = len((await db.scalars(select(RunnerTask))).all())
    await _enqueue_mr_review_if_needed(seeded["group"].id, seeded["project"].gitlab_project_id, _mr_payload())
    n_after = len((await db.scalars(select(RunnerTask))).all())
    assert n_before == n_after


async def test_enqueue_no_hit_no_task(client, db, seeded, monkeypatch):
    notes: list[str] = []
    await _patch_gl(monkeypatch, changes=CHANGES, pipelines=[], notes=notes)
    seeded["project"].audit_prefixes = ["docs/"]
    await db.commit()
    await _enqueue_mr_review_if_needed(seeded["group"].id, seeded["project"].gitlab_project_id, _mr_payload())
    assert (await db.scalars(select(RunnerTask).where(RunnerTask.type == "mr_review"))).all() == []


async def test_webhook_mr_update_triggers_review(client, db, seeded, monkeypatch):
    """端到端：merge_request update 事件 → 后台入队 + 占位行。"""
    notes: list[str] = []
    await _patch_gl(monkeypatch, changes=CHANGES, pipelines=[], notes=notes)
    seeded["project"].audit_prefixes = ["api/"]
    await db.commit()

    r = await client.post(
        "/webhooks/gitlab",
        json=_mr_payload(system_id=seeded["project"].gitlab_project_id),
        headers={
            "X-Gitlab-Event": "Merge Request Hook",
            "X-Gitlab-Token": seeded["group"].webhook_secret,
        },
    )
    assert r.status_code == 200
    task = await db.scalar(select(RunnerTask).where(RunnerTask.type == "mr_review"))
    assert task and task.payload["mr"]["iid"] == 7


# ---------- _finish_mr_review ----------


async def _make_review_task(db, seeded) -> RunnerTask:
    review = MrReview(
        system_id=seeded["project"].id,
        group_id=seeded["group"].id,
        mr_iid=7,
        head_sha=SHA,
        title="feat: x",
        changed_files=["api/vega/x.py"],
        verdict="pending",
    )
    db.add(review)
    await db.flush()
    return await enqueue(
        db,
        type="mr_review",
        payload={
            "system_id": seeded["project"].id,
            "mr": {"iid": 7, "title": "feat: x", "sha": SHA},
            "changed_files": ["api/vega/x.py"],
        },
    )


async def test_finish_mr_review_idempotent_row_gate_comment(client, db, seeded, monkeypatch):
    notes: list[str] = []
    await _patch_gl(
        monkeypatch,
        changes=CHANGES,
        pipelines=[{"id": 3, "sha": SHA, "status": "running"}, {"id": 2, "sha": SHA, "status": "failed"}],
        notes=notes,
    )
    task = await _make_review_task(db, seeded)
    body = TaskResultIn(
        status="done",
        data={"status": "reviewed", "verdict": "approve", "summary": "没问题", "issues": [], "head_sha": SHA},
    )
    await _finish_mr_review(db, task, body)
    await db.commit()

    reviews = (await db.scalars(select(MrReview))).all()
    assert len(reviews) == 1  # 幂等：更新占位行，不新建
    rv = reviews[0]
    assert rv.verdict == "approve" and rv.commented is True
    assert rv.gate == "ci:running"  # 取 id 最大的 pipeline，非 success → 未绿
    assert len(notes) == 1 and "✅ 可批准" in notes[0] and "待 CI 转绿" in notes[0]
    # 评审结论 octo 双通道：留痕含结论与 CI 门禁（本用例无 dev 任务 → 不 @ 人）
    reviewed = (await db.scalars(select(NotifyLog).where(NotifyLog.reason == "mr_reviewed"))).all()
    assert any("代码评审结论" in l.content and "!7" in l.content and "转绿后自动放行" in l.content for l in reviewed)


async def test_finish_mr_review_ci_green_no_gate(client, db, seeded, monkeypatch):
    notes: list[str] = []
    await _patch_gl(monkeypatch, changes=[], pipelines=[{"id": 9, "sha": SHA, "status": "success"}], notes=notes)
    task = await _make_review_task(db, seeded)
    body = TaskResultIn(status="done", data={"status": "reviewed", "verdict": "modify", "summary": "有硬伤",
                                             "issues": [{"file": "a.py", "line": 3, "severity": "high", "note": "空指针"}]})
    await _finish_mr_review(db, task, body)
    await db.commit()
    rv = await db.scalar(select(MrReview))
    assert rv.gate == "" and "⚠️ 需修改" in notes[0] and "a.py" in notes[0]


async def test_finish_mr_review_runner_failed(client, db, seeded, monkeypatch):
    notes: list[str] = []
    await _patch_gl(monkeypatch, changes=[], pipelines=[], notes=notes)
    task = await _make_review_task(db, seeded)
    await _finish_mr_review(db, task, TaskResultIn(status="failed", error="claude 崩了"))
    await db.commit()
    rv = await db.scalar(select(MrReview))
    assert rv.verdict == "failed" and "claude 崩了" in rv.error
    assert len(notes) == 1 and "❌" in notes[0]


async def test_finish_mr_review_skipped_no_comment(client, db, seeded, monkeypatch):
    notes: list[str] = []
    await _patch_gl(monkeypatch, changes=[], pipelines=[], notes=notes)
    task = await _make_review_task(db, seeded)
    await _finish_mr_review(db, task, TaskResultIn(status="done", data={"status": "skipped", "reason": "commit 不可达"}))
    await db.commit()
    rv = await db.scalar(select(MrReview))
    assert rv.verdict == "failed" and "不可达" in rv.error
    assert notes == []  # sha 都没了，没 MR 可评论
