"""EGM 接入：argv 权限档、方案轮技能注入与产物回传、评审结论必须来自报告文件。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from vega import runner as runner_mod
from vega.claude import egm_job_args, egm_plan_args, egm_review_args
from vega.config import get_settings
from vega.runner import Runner


def _tools(args: list[str]) -> list[str]:
    return args[args.index("--allowedTools") + 1].split(",")


def test_egm_args_are_shaped_for_the_skills():
    plugin = get_settings().egm_plugin_dir
    for args in (
        egm_plan_args("sid", False, "/tmp/art"),
        egm_review_args("sid", "/tmp/art"),
        egm_job_args("sid", "/tmp/art", work_dir="/tmp/wt"),
    ):
        tools = _tools(args)
        assert "Agent" in tools
        assert "Bash" not in tools
        assert "--dangerously-skip-permissions" not in args
        assert args[args.index("--plugin-dir") + 1] == plugin
        # 技能要 Read 插件自己的 docs/resources，插件目录必须进工作区
        assert plugin in [args[i + 1] for i, a in enumerate(args) if a == "--add-dir"]
        assert any("EGM artifact directory:" in a for a in args)

    # 写权限只落在产物目录；bootstrap 另外只放任务独占 worktree
    assert any(t.startswith("Write(") and t.endswith("/art/**)") for t in _tools(egm_plan_args("sid", False, "/tmp/art")))
    job = _tools(egm_job_args("sid", "/tmp/art", work_dir="/tmp/wt"))
    assert any("/art/**)" in t for t in job)
    assert any(t.startswith("Write(") and "/wt/**)" in t for t in job)


def _egm_payload(req_id: int, **kw) -> dict:
    p = {
        "requirement_id": req_id,
        "session_id": "sid",
        "resume": False,
        "prompt": "先做一轮头脑风暴",
        "mode": "plan",
        "docs": [],
        "claude_env": {},
        "egm": "brainstorm",
        "egm_target": "需求A：做点东西",
    }
    p.update(kw)
    return p


async def _fake_claude(monkeypatch, runner: Runner, seen: dict, on_run=None):
    async def fake_run_claude(args, env, *, cwd=None, prompt=None):
        seen["args"] = args
        seen["cwd"] = cwd
        seen["prompt"] = prompt
        if on_run:
            on_run(Path(cwd) if cwd else None)
        yield {"kind": "result", "text": "好的"}

    async def noop_emit(*_a, **_k):
        return None

    async def fake_result(task_id, status, error=None, data=None):
        seen["result"] = (status, error, data)

    monkeypatch.setattr(runner_mod, "run_claude", fake_run_claude)
    monkeypatch.setattr(runner, "_emit", noop_emit)
    monkeypatch.setattr(runner, "_result", fake_result)


async def test_egm_plan_turn_injects_skill_and_returns_artifacts(tmp_path, monkeypatch):
    runner = Runner(workdir=tmp_path / "home")
    seen: dict = {}
    payload = _egm_payload(9101)
    art = runner._egm_dir(payload)

    def on_run(_cwd):
        (art / "concepts_brief.md").write_text("# 概念\n", encoding="utf-8")
        (art / "design_draft.md").write_text("# 方案\n", encoding="utf-8")

    await _fake_claude(monkeypatch, runner, seen, on_run)
    await runner._exec_plan_turn(1, payload)

    assert seen["prompt"].startswith("/egm:egm-brainstorm 需求A：做点东西")
    assert "--plugin-dir" in seen["args"]
    status, error, data = seen["result"]
    assert (status, error) == ("done", None)
    assert data["concepts_brief"] == "# 概念\n"
    assert data["design_draft"] == "# 方案\n"


async def test_egm_resolve_hands_skill_file_paths(tmp_path, monkeypatch):
    runner = Runner(workdir=tmp_path / "home")
    seen: dict = {}
    payload = _egm_payload(
        9102, egm="resolve", egm_review_id=77, egm_findings="缺陷一", egm_design_md="# 旧方案\n"
    )

    await _fake_claude(monkeypatch, runner, seen)
    await runner._exec_plan_turn(1, payload)

    art = runner._egm_dir(payload)
    assert "TargetFile: " in seen["prompt"] and str(art / "design_draft.md") in seen["prompt"]
    assert str(art / "findings-77.md") in seen["prompt"]
    assert (art / "findings-77.md").read_text(encoding="utf-8") == "缺陷一"
    assert (art / "design_draft.md").read_text(encoding="utf-8") == "# 旧方案\n"


def _seed_bare(root: Path) -> tuple[Path, str]:
    seed = root / "seed"
    seed.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "master", str(seed)], check=True)
    (seed / "README.md").write_text("# repo\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], cwd=seed, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=seed, check=True)
    bare = root / "repo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    return bare, "master"


async def test_egm_plan_turn_fails_on_dirty_baseline(tmp_path, monkeypatch):
    bare, branch = _seed_bare(tmp_path)
    url = f"file://{bare}"
    monkeypatch.setattr(Runner, "_crg_sync_bg", lambda self, repo: None)
    monkeypatch.setattr(Runner, "_daemon_register", lambda self, repo: None)

    runner = Runner(workdir=tmp_path / "home")
    seen: dict = {}

    def on_run(cwd: Path):
        (cwd / "README.md").write_text("越界写入\n", encoding="utf-8")

    await _fake_claude(monkeypatch, runner, seen, on_run)
    payload = _egm_payload(
        9103, prompt="写方案", git_http_url=url, git_token="", default_branch=branch
    )
    with pytest.raises(RuntimeError, match="写脏了基线仓库"):
        await runner._exec_plan_turn(1, payload)
    # 已跟踪文件被复原，留一个干净基线给后续任务
    assert (Path(seen["cwd"]) / "README.md").read_text(encoding="utf-8") == "# repo\n"


async def test_egm_review_without_report_is_incomplete(tmp_path, monkeypatch):
    runner = Runner(workdir=tmp_path / "home")
    seen: dict = {}
    payload = {
        "requirement_id": 9104,
        "egm_review_id": 1,
        "design_md": "# 方案\n",
        "claude_env": {},
    }

    await _fake_claude(monkeypatch, runner, seen)
    await runner._exec_egm_review(2, payload)

    assert seen["prompt"].startswith("/egm:egm-review ")
    status, _error, data = seen["result"]
    assert status == "done"
    # 没有报告文件 = EGM 自报未完成，绝不能当通过
    assert data["status"] == "incomplete"


async def test_egm_review_with_report_is_reviewed(tmp_path, monkeypatch):
    runner = Runner(workdir=tmp_path / "home")
    seen: dict = {}
    payload = {
        "requirement_id": 9105,
        "egm_review_id": 2,
        "design_md": "# 方案\n",
        "claude_env": {},
    }
    art = runner._egm_dir(payload, "review-3")

    def on_run(_cwd):
        (art / "consolidated_review_report.md").write_text("# 评审结论\n", encoding="utf-8")

    await _fake_claude(monkeypatch, runner, seen, on_run)
    await runner._exec_egm_review(3, payload)

    _status, _error, data = seen["result"]
    assert data["status"] == "reviewed"
    assert data["report_md"] == "# 评审结论\n"

# ---------- API：发起评审 / 阶段门禁 ----------


async def _login(client, db, seeded):
    from conftest import login_as

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"


async def _claimed_req(client, seeded, title: str) -> int:
    r = await client.post("/api/requirements", json={"project_id": seeded["project"].id, "title": title})
    assert r.status_code == 200, r.text
    req_id = r.json()["id"]
    assert (await client.post(f"/api/requirements/{req_id}/claim")).status_code == 200
    return req_id


async def test_egm_skill_rejected_before_plan_stage(client, db, seeded):
    await _login(client, db, seeded)
    req_id = await _claimed_req(client, seeded, "EGM 阶段门禁")

    # 需求文档还没定稿：聊的是需求，EGM 设计技能拒绝入队
    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages", json={"content": "头脑风暴", "egm": "brainstorm"}
    )
    assert r.status_code == 400 and "EGM" in r.json()["detail"]


async def test_plan_review_requires_version_and_enqueues_task(client, db, seeded):
    from sqlalchemy import select

    from vega.models import RunnerTask

    await _login(client, db, seeded)
    req_id = await _claimed_req(client, seeded, "Goldfish 评审")
    r = await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求\n"})
    doc_id = r.json()["id"]
    assert (await client.post(f"/api/requirements/{req_id}/docs/{doc_id}/finalize")).status_code == 200

    # 还没有方案版本：不许评审
    r = await client.post(f"/api/requirements/{req_id}/plan/review", json={})
    assert r.status_code == 400 and "方案版本" in r.json()["detail"]

    r = await client.post(f"/api/requirements/{req_id}/plan-versions", json={"content": "# 方案\n"})
    assert r.status_code == 200, r.text
    vid = r.json()["id"]

    r = await client.post(f"/api/requirements/{req_id}/plan/review", json={"plan_version_id": vid})
    assert r.status_code == 200, r.text
    rev = r.json()
    assert rev["status"] == "running" and rev["plan_version_id"] == vid
    # 同一需求只允许一轮在跑
    r = await client.post(f"/api/requirements/{req_id}/plan/review", json={"plan_version_id": vid})
    assert r.status_code == 400 and "在跑" in r.json()["detail"]

    rows = (await client.get(f"/api/requirements/{req_id}/plan/reviews")).json()
    assert [x["id"] for x in rows] == [rev["id"]]

    await db.commit()
    task = await db.scalar(
        select(RunnerTask).where(RunnerTask.requirement_id == req_id, RunnerTask.type == "egm_review")
    )
    assert task.payload["egm_review_id"] == rev["id"]
    assert task.payload["design_md"] == "# 方案"
    # 收尾：别把排队任务留给后面的 /runner/next 用例
    task.status = "done"
    await db.commit()


def test_parse_findings_reads_severity_headings():
    from vega.routers.runner_api import parse_findings

    report = """# Goldfish Review Report

## Prioritized Findings (Blockers -> Major -> Nits)

### 缺少回滚路径 - Severity: [Blocker]
* **Reviewer Lens:** `structural`
* **Affected Scope:** `Lines 12-18` (回滚设计)
* **Discrepancy Description**: 没有定义失败后的回滚路径。
* **Rationale & Recommendation**: 补一节回滚设计。

### 命名不一致 - Severity: **Major**
* **Affected Scope:** 命名约定
### 文案问题 - Severity: Nit
"""
    rows = parse_findings(report)
    assert [r["severity"] for r in rows] == ["Blocker", "Major", "Nit"]
    assert rows[0]["title"] == "缺少回滚路径"
    assert "12-18" in rows[0]["scope"] and "回滚设计" in rows[0]["scope"]
    assert rows[0]["description"] == "没有定义失败后的回滚路径。"
    assert rows[0]["recommendation"] == "补一节回滚设计。"
    assert rows[1]["scope"] == "命名约定"
    assert rows[2]["scope"] == ""


def test_parse_findings_falls_back_to_severity_marks():
    from vega.routers.runner_api import parse_findings

    rows = parse_findings("- 🔴 Blocker：没有回滚\n- 🟡 Major：命名\n- 🟢 Nit：错别字")
    assert [r["severity"] for r in rows] == ["Blocker", "Major", "Nit"]


async def test_review_with_blockers_notifies_and_counts(db, seeded, monkeypatch):
    from types import SimpleNamespace

    from vega.models import PlanReview, Requirement
    from vega.routers import runner_api

    sent: list[dict] = []

    async def fake_notify(_db, _group, content, *, reason, mention=None):
        sent.append({"content": content, "reason": reason, "mention": [p.id for p in mention or []]})
        return True

    monkeypatch.setattr(runner_api, "notify", fake_notify)

    req = Requirement(
        project_id=seeded["project"].id,
        code=f"TEST-B{seeded['person'].id}",
        title="阻塞项用例",
        claimant_id=seeded["person"].id,
        status="claimed",
    )
    db.add(req)
    await db.flush()
    review = PlanReview(requirement_id=req.id, status="running")
    db.add(review)
    await db.flush()
    task = SimpleNamespace(requirement_id=req.id, payload={"egm_review_id": review.id})
    report = "# 报告\n\n### 缺少回滚路径 - Severity: [Blocker]\n### 命名 - Severity: Major\n"

    await runner_api._finish_egm_review(
        db, task, SimpleNamespace(status="done", data={"status": "reviewed", "report_md": report}, error=None)
    )

    assert review.status == "done" and review.blocker_count == 1
    assert sent[-1]["reason"] == "egm_review_blockers"
    assert "1 个阻塞项" in sent[-1]["content"] and "缺少回滚路径" in sent[-1]["content"]
    assert seeded["person"].id in sent[-1]["mention"]


async def test_creating_project_can_bootstrap_egm(admin_client, db, seeded):
    from sqlalchemy import select

    from vega.models import RunnerTask

    r = await admin_client.post(
        "/api/projects",
        json={
            "group_id": seeded["group"].id,
            "gitlab_project_id": 99001,
            "path": "team/new-repo",
            "name": "new-repo",
            "code_prefix": "NEW",
            "run_egm_bootstrap": True,
        },
    )
    assert r.status_code == 200, r.text
    pid = r.json()["id"]

    await db.commit()
    tasks = (await db.scalars(select(RunnerTask).where(RunnerTask.type == "egm_job"))).all()
    task = next(t for t in tasks if t.payload.get("project_id") == pid)
    assert task.payload["kind"] == "bootstrap"
    task.status = "done"
    await db.commit()


async def test_project_egm_job_endpoint_enqueues_bootstrap(client, db, seeded):
    from sqlalchemy import select

    from vega.models import RunnerTask

    await _login(client, db, seeded)
    r = await client.post(f"/api/projects/{seeded['project'].id}/egm-jobs", json={"kind": "bootstrap"})
    assert r.status_code == 200, r.text
    rep = r.json()
    assert rep["kind"] == "bootstrap" and rep["status"] == "running"

    await db.commit()
    tasks = (await db.scalars(select(RunnerTask).where(RunnerTask.type == "egm_job"))).all()
    task = next(t for t in tasks if t.payload.get("egm_job_id") == rep["id"])
    assert task.payload["kind"] == "bootstrap" and task.payload["git_http_url"].endswith("/team/repo.git")

    # 同一项目已有 EGM 任务在跑：拒绝并发
    r = await client.post(f"/api/projects/{seeded['project'].id}/egm-jobs", json={"kind": "bootstrap"})
    assert r.status_code == 400 and "在跑" in r.json()["detail"]

    task.status = "done"
    await db.commit()


async def test_adopting_a_plan_version_auto_enqueues_review(client, db, seeded, monkeypatch):
    from sqlalchemy import select

    from vega.models import PlanReview, RunnerTask

    await _login(client, db, seeded)
    req_id = await _claimed_req(client, seeded, "采用即评审")
    r = await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求\n"})
    assert (await client.post(f"/api/requirements/{req_id}/docs/{r.json()['id']}/finalize")).status_code == 200
    monkeypatch.setattr(get_settings(), "egm_auto_review", True)

    async def adopt(text: str) -> dict:
        r = await client.post(f"/api/requirements/{req_id}/plan-versions", json={"content": text})
        assert r.status_code == 200, r.text
        await db.commit()  # 换一个 session 看见 API 的提交
        return r.json()

    async def reviews() -> list[PlanReview]:
        rows = await db.scalars(select(PlanReview).where(PlanReview.requirement_id == req_id))
        return list(rows)

    first = await adopt("# 方案A\n")
    rows = await reviews()
    assert len(rows) == 1 and rows[0].plan_version_id == first["id"]
    task = await db.scalar(
        select(RunnerTask).where(RunnerTask.requirement_id == req_id, RunnerTask.type == "egm_review")
    )
    assert task.payload["trigger"] == "auto" and task.payload["egm_review_id"] == rows[0].id

    # 同内容再点「采用」：复用同一版，不重复投
    assert (await adopt("# 方案A\n"))["created"] is False
    assert len(await reviews()) == 1

    # 上一轮还在跑：新版本也不并发投递
    await adopt("# 方案B\n")
    assert len(await reviews()) == 1

    # 上一轮结束后再采用新版本：自动再评审一次
    rows[0].status = "done"
    await db.commit()
    await adopt("# 方案C\n")
    assert len(await reviews()) == 2

    for t in (await db.scalars(select(RunnerTask).where(RunnerTask.requirement_id == req_id))).all():
        t.status = "done"
    await db.commit()


async def test_incomplete_or_failed_review_notifies_claimant(db, seeded, monkeypatch):
    from types import SimpleNamespace

    from vega.models import PlanReview, Requirement
    from vega.routers import runner_api

    sent: list[dict] = []

    async def fake_notify(_db, group, content, *, reason, mention=None):
        sent.append({"group": group.id, "content": content, "reason": reason, "mention": [p.id for p in mention or []]})
        return True

    monkeypatch.setattr(runner_api, "notify", fake_notify)

    req = Requirement(
        project_id=seeded["project"].id,
        code=f"TEST-N{seeded['person'].id}",
        title="通知用例",
        claimant_id=seeded["person"].id,
        status="claimed",
    )
    db.add(req)
    await db.flush()
    review = PlanReview(requirement_id=req.id, status="running")
    db.add(review)
    await db.flush()
    task = SimpleNamespace(requirement_id=req.id, payload={"egm_review_id": review.id})

    await runner_api._finish_egm_review(
        db, task, SimpleNamespace(status="done", data={"status": "incomplete", "report_md": "没读到评审标准"}, error=None)
    )
    assert review.status == "incomplete"
    assert sent[-1]["reason"] == "egm_review_incomplete"
    assert "通知用例" in sent[-1]["content"] and seeded["person"].id in sent[-1]["mention"]

    review.status = "running"
    await runner_api._finish_egm_review(db, task, SimpleNamespace(status="failed", data={}, error="boom"))
    assert sent[-1]["reason"] == "egm_review_failed"
    assert "boom" in sent[-1]["content"]


async def test_egm_drift_sweep_skips_projects_checked_this_week(db, seeded, monkeypatch):
    from vega.models import EgmReport, Requirement
    from vega.routers import projects as projects_mod

    monkeypatch.setattr(get_settings(), "egm_auto_drift", True)
    db.add(
        Requirement(
            project_id=seeded["project"].id,
            code=f"TEST-D{seeded['person'].id}",
            title="漂移用例",
            plan_md="# 方案",
        )
    )
    await db.commit()

    queued: list[tuple[int, str]] = []

    async def fake_enqueue(_db, project, kind):
        queued.append((project.id, kind))
        return EgmReport(project_id=project.id, kind=kind, status="running")

    monkeypatch.setattr(projects_mod, "enqueue_project_egm_job", fake_enqueue)

    assert await projects_mod.egm_drift_sweep(db) >= 1
    assert (seeded["project"].id, "drift") in queued

    # 7 天内已经跑过 drift：这周不再投
    queued.clear()
    db.add(EgmReport(project_id=seeded["project"].id, kind="drift", status="done"))
    await db.commit()
    await projects_mod.egm_drift_sweep(db)
    assert (seeded["project"].id, "drift") not in queued
