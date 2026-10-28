"""P5 自动放行：fail-closed · 幂等终态 · CI gate 复查 · sha/draft/target 门禁 · 冲突分类 · sweep · 直通。"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from vega.autoflow import _prefix_ok, mr_autoflow_sweep, try_autoflow
from vega.models import DevTask, MrReview, NotifyLog, Person, ProjectOwner, Requirement, RunnerTask

SHA = "b" * 40


def _http_error(status: int, text: str):
    import httpx

    req = httpx.Request("PUT", "http://gitlab.test/api/v4/merge")
    resp = httpx.Response(status, request=req, text=text)
    return httpx.HTTPStatusError(f"Server error '{status} {text}'", request=req, response=resp)


class FakeGL:
    """记录调用的最小 Gitlab 替身：mr 状态/pipeline/merge 行为全部可注入。"""

    def __init__(self, *, sha: str = SHA, state: str = "opened", draft=False, target="nightly",
                 merge_status="can_be_merged", pipelines=None, merge_raises=None, author=None):
        self.sha, self.state, self.draft, self.target = sha, state, draft, target
        self.merge_status = merge_status
        self.pipelines = pipelines if pipelines is not None else [{"id": 1, "sha": SHA, "status": "success"}]
        self.merge_raises = merge_raises
        self.author = author or {"username": "zhang", "email": "z@t.cn"}
        self.calls: list[str] = []

    async def mr_detail(self, client, project_id, mr_iid):
        self.calls.append("detail")
        return {
            "iid": mr_iid, "state": self.state, "sha": self.sha, "target_branch": self.target,
            "draft": self.draft, "merge_status": self.merge_status,
            "author": self.author,
        }

    async def mr_pipelines(self, client, project_id, mr_iid):
        self.calls.append("pipelines")
        return self.pipelines

    async def approve_mr(self, client, project_id, mr_iid):
        self.calls.append("approve")
        return True

    async def merge_mr(self, client, project_id, mr_iid, sha):
        self.calls.append("merge")
        if self.merge_raises:
            raise self.merge_raises
        return {"state": "merged"}

    async def add_mr_note(self, client, project_id, mr_iid, body):
        self.calls.append(f"note:{body[:12]}")
        return {}


_UNSET = object()


async def _mk_review(db, seeded, *, iid=7, verdict="approve", gate="", flow="", files=None, prefixes=_UNSET):
    seeded["project"].auto_approve_prefixes = ["api/"] if prefixes is _UNSET else prefixes
    await db.commit()
    review = MrReview(
        project_id=seeded["project"].id,
        group_id=seeded["group"].id,
        mr_iid=iid,
        head_sha=SHA,
        title="feat: x",
        changed_files=files if files is not None else ["api/vega/x.py"],
        verdict=verdict,
        gate=gate,
        commented=True,
        flow_state=flow,
    )
    db.add(review)
    await db.flush()
    return review


@pytest.fixture(autouse=True)
async def _cleanup(db):
    yield
    for r in (await db.scalars(select(MrReview))).all():
        await db.delete(r)
    for t in (await db.scalars(select(RunnerTask))).all():
        if t.type in ("mr_review", "noop"):
            await db.delete(t)
    for t in (await db.scalars(select(DevTask).where(DevTask.code == "TASK-AF-OWNER"))).all():
        await db.delete(t)
    for r in (await db.scalars(select(Requirement).where(Requirement.code == "REQ-AF-OWNER"))).all():
        await db.delete(r)
    await db.commit()


def test_prefix_ok_fail_closed():
    assert _prefix_ok(["api/"], ["api/a.py"]) is True
    assert _prefix_ok(["api/", "web/"], ["api/a.py", "web/b.ts"]) is True
    assert _prefix_ok(None, ["api/a.py"]) is False  # 未配置不放行
    assert _prefix_ok(["api/"], ["api/a.py", "docs/x.md"]) is False  # 部分命中不放行
    assert _prefix_ok(["api/"], []) is False  # 无改动文件不放行


async def test_autoflow_merges_when_all_green(client, db, seeded):
    gl = FakeGL()
    review = await _mk_review(db, seeded)
    await try_autoflow(db, seeded["group"], seeded["project"], review, gl, None)
    await db.commit()
    assert "merge" in gl.calls and review.flow_state == "merged" and review.gate == ""

    # 幂等：merged 终态不再碰 GitLab
    gl.calls.clear()
    await try_autoflow(db, seeded["group"], seeded["project"], review, gl, None)
    assert gl.calls == []


async def test_autoflow_merge_notify_mentions_project_owner(client, db, seeded):
    """毕业通知除任务创建人外也要 @项目负责人（MR 相关通知的统一口径）。"""
    owner = Person(name="负责人丁", octo_uid="uid-owner-autoflow")
    db.add(owner)
    await db.flush()
    db.add(ProjectOwner(project_id=seeded["project"].id, person_id=owner.id))
    req = Requirement(project_id=seeded["project"].id, title="放行通知", code="REQ-AF-OWNER")
    db.add(req)
    await db.flush()
    db.add(
        DevTask(
            requirement_id=req.id,
            code="TASK-AF-OWNER",
            status="mr_created",
            creator_id=seeded["person"].id,
            mr_iid=77,
        )
    )
    await db.commit()

    review = await _mk_review(db, seeded, iid=77)
    await try_autoflow(db, seeded["group"], seeded["project"], review, FakeGL(), None)
    logs = (
        await db.scalars(select(NotifyLog).where(NotifyLog.reason == "mr_merged"))
    ).all()
    mine = [l for l in logs if "TASK-AF-OWNER" in l.content]
    assert len(mine) == 1 and "uid-owner-autoflow" in mine[0].mentioned_uids
    assert req.status == "online"  # 需求下任务全合入 → 已上线


async def test_autoflow_gate_recheck_needs_green(client, db, seeded):
    review = await _mk_review(db, seeded, gate="ci:running")
    gl = FakeGL(pipelines=[{"id": 2, "sha": SHA, "status": "running"}])
    await try_autoflow(db, seeded["group"], seeded["project"], review, gl, None)
    assert "merge" not in gl.calls and review.flow_state == ""  # 未绿不记死，等下轮
    assert review.gate == "ci:running"

    # CI 转绿 → 同一 review 放行（gate 清空）
    gl2 = FakeGL(pipelines=[{"id": 3, "sha": SHA, "status": "success"}])
    await try_autoflow(db, seeded["group"], seeded["project"], review, gl2, None)
    assert "merge" in gl2.calls and review.flow_state == "merged" and review.gate == ""


async def test_autoflow_sha_draft_target_blocked(client, db, seeded):
    r1 = await _mk_review(db, seeded, iid=11)  # sha 变了（初审后新推送）→ 不合并
    await try_autoflow(db, seeded["group"], seeded["project"], r1, FakeGL(sha="c" * 40), None)
    r2 = await _mk_review(db, seeded, iid=12)  # draft → 不合并
    await try_autoflow(db, seeded["group"], seeded["project"], r2, FakeGL(draft=True), None)
    r3 = await _mk_review(db, seeded, iid=13)  # 目标分支不匹配 → 不合并
    await try_autoflow(db, seeded["group"], seeded["project"], r3, FakeGL(target="master"), None)
    rows = {r.mr_iid: r.flow_state for r in (await db.scalars(select(MrReview))).all()}
    assert rows == {11: "", 12: "", 13: ""}  # 都没动


async def test_autoflow_fail_closed_prefixes(client, db, seeded):
    r1 = await _mk_review(db, seeded, iid=14, prefixes=None)  # 项目未配置 → 连 mr_detail 都不查
    gl = FakeGL()
    await try_autoflow(db, seeded["group"], seeded["project"], r1, gl, None)
    assert gl.calls == []

    r2 = await _mk_review(db, seeded, iid=15, files=["api/a.py", "docs/b.md"], prefixes=["api/"])
    await try_autoflow(db, seeded["group"], seeded["project"], r2, FakeGL(), None)
    assert r2.flow_state == ""  # 改动不全命中 → 不放行


async def test_autoflow_conflict_terminal_and_notify(client, db, seeded, monkeypatch):
    review = await _mk_review(db, seeded, iid=16)
    sent: list[dict] = []

    async def fake_notify(db, group, content, *, reason, mention=None):
        sent.append({"reason": reason, "mention": mention})

    monkeypatch.setattr("vega.autoflow.notify", fake_notify)
    author = {
        "username": seeded["person"].gitlab_username,
        "email": seeded["person"].gitlab_email,
    }
    gl = FakeGL(merge_raises=_http_error(405, "Method Not Allowed fast-forward"), author=author)
    await try_autoflow(db, seeded["group"], seeded["project"], review, gl, None)
    assert review.flow_state == "conflict_notified"  # 终态
    assert sent and sent[0]["reason"] == "autoflow_conflict"
    assert sent[0]["mention"]  # @了 MR 作者（按 username/email 建过档）

    gl.calls.clear()
    await try_autoflow(db, seeded["group"], seeded["project"], review, gl, None)
    assert gl.calls == []  # 终态幂等


async def test_autoflow_hold_retry_then_merge_via_sweep(client, db, seeded, monkeypatch):
    review = await _mk_review(db, seeded, iid=17)
    gl = FakeGL(merge_raises=_http_error(500, "Internal Server Error"))
    await try_autoflow(db, seeded["group"], seeded["project"], review, gl, None)
    assert review.flow_state == "hold_not_mergeable"  # 非终态

    monkeypatch.setattr("vega.autoflow._gitlab", lambda g: FakeGL())  # sweep 不打真实 GitLab
    stats = await mr_autoflow_sweep(db, seeded["group"])
    assert stats["merged"] == 1 and review.flow_state == "merged"


async def test_sweep_candidates_only(client, db, seeded, monkeypatch):
    await _mk_review(db, seeded, iid=18)  # 候选
    await _mk_review(db, seeded, iid=19, verdict="modify")  # 非候选：初审未 approve
    monkeypatch.setattr("vega.autoflow._gitlab", lambda g: FakeGL())
    called: list[int] = []

    import vega.autoflow as af

    orig = af.try_autoflow

    async def spy(db, group, project, review, gl, client):
        called.append(review.mr_iid)

    af.try_autoflow = spy
    try:
        await mr_autoflow_sweep(db, seeded["group"])
    finally:
        af.try_autoflow = orig
    assert called == [18]


async def test_finish_review_direct_triggers_autoflow(client, db, seeded, monkeypatch):
    """初审 approve 且评论成功 → _finish_mr_review 直通 try_autoflow。"""
    from vega.gitlab import GitlabClient
    from vega.queue import enqueue
    from vega.routers import runner_api
    from vega.schemas import TaskResultIn

    seeded["project"].audit_prefixes = ["api/"]
    seeded["project"].auto_approve_prefixes = ["api/"]
    await db.commit()

    calls: list[int] = []

    async def fake_autoflow(db, group, project, review, gl, client):
        calls.append(review.mr_iid)

    async def fake_note(self, client, project_id, mr_iid, body):
        return {}

    async def fake_pipes(self, client, project_id, mr_iid):
        return []

    monkeypatch.setattr("vega.routers.runner_api.try_autoflow", fake_autoflow)
    monkeypatch.setattr(GitlabClient, "add_mr_note", fake_note)
    monkeypatch.setattr(GitlabClient, "mr_pipelines", fake_pipes)

    task = await enqueue(
        db,
        type="mr_review",
        payload={
            "project_id": seeded["project"].id,
            "mr": {"iid": 20, "title": "t", "sha": SHA},
            "changed_files": ["api/x.py"],
        },
    )
    await runner_api._finish_mr_review(
        db,
        task,
        TaskResultIn(status="done", data={"status": "reviewed", "verdict": "approve", "summary": "ok", "issues": []}),
    )
    await db.commit()
    assert calls == [20]
