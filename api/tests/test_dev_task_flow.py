"""开发任务全链路：派发 → runner 本地 worktree（真 git 裸仓）→ claude stub 提交
→ push → api 建 MR（mock GitLab）→ octo @下发人（未配置则留痕）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from conftest import TMP, login_as

from vega.gitlab import GitlabClient
from vega.models import DevTask, NotifyLog, Person, PlanVersion, ProjectOwner, Requirement
from vega.runner import Runner


@pytest.fixture(autouse=True)
def _no_crg_side_effects(monkeypatch):
    """测试只验 git 布局，别在本机真起 CRG 构建 / 改 daemon 配置。"""
    monkeypatch.setattr(Runner, "_crg_sync_bg", lambda self, repo: None)
    monkeypatch.setattr(Runner, "_daemon_register", lambda self, repo: None)


async def _finalize_plan(client, db, req_id: int, content: str) -> None:
    """落一版方案并定稿（生产里版本由 AI 每轮输出自动成版，人只挑版定稿）。"""
    from sqlalchemy import func, select

    nxt = (await db.scalar(select(func.max(PlanVersion.version)).where(PlanVersion.requirement_id == req_id)) or 0) + 1
    db.add(PlanVersion(requirement_id=req_id, version=nxt, content=content))
    await db.commit()
    vid = (await db.scalar(select(PlanVersion.id).where(PlanVersion.requirement_id == req_id)))
    r = await client.post(f"/api/requirements/{req_id}/plan-versions/{vid}/finalize")
    assert r.status_code == 200, r.text


def _bare_repo() -> Path:
    """file:// 裸仓：master 上一个初始提交。目录布局 group.gitlab_base_url + project.path。"""
    base = TMP / "gitroot" / "team"
    base.mkdir(parents=True, exist_ok=True)
    bare = base / "repo.git"
    if bare.exists():
        return bare
    work = TMP / "gitroot" / "seed"
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "master", str(work)], check=True)
    (work / "README.md").write_text("# repo", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "-c", "user.name=seed", "-c", "user.email=seed@t", "commit", "-qm", "init"], cwd=work, check=True)
    subprocess.run(["git", "clone", "-q", "--bare", str(work), str(bare)], check=True)
    return bare


async def test_dev_task_loop(admin_client, client, db, seeded, claude_stub, monkeypatch):
    from vega.main import app
    import httpx

    bare = _bare_repo()

    # 组指向 file:// gitroot；token 对 file:// 无效但不影响（runner 会透传）
    seeded["group"].gitlab_base_url = f"file://{TMP}/gitroot"
    await db.commit()

    # 项目负责人（绑定了 GitLab 账号）→ MR 审核人是他们，而不是任务创建人；多个负责人都要带上
    owner_username = f"owner-gl-{seeded['person'].id}"
    owner2_username = f"owner2-gl-{seeded['person'].id}"
    owner = Person(name="项目负责人丙", gitlab_username=owner_username)
    owner2 = Person(name="项目负责人戊", gitlab_username=owner2_username)
    db.add_all([owner, owner2])
    await db.flush()
    db.add_all([
        ProjectOwner(project_id=seeded["project"].id, person_id=owner.id),
        ProjectOwner(project_id=seeded["project"].id, person_id=owner2.id),
    ])
    await db.commit()
    seen_assignee: list[list[str]] = []

    # MR 创建 mock（不真打 GitLab）
    async def fake_create_mr(self, client, project_id, *, source, target, title, description, assignee_usernames, labels="ai-gen"):
        seen_assignee.append(assignee_usernames)
        return {"iid": 7, "web_url": f"http://gitlab.test/team/repo/-/merge_requests/7", "state": "opened"}

    monkeypatch.setattr(GitlabClient, "create_mr", fake_create_mr)
    # 测试不真挂 crg-daemon（否则 tmp 仓库会注册进本机 daemon）
    monkeypatch.setattr(Runner, "_daemon_register", lambda self, repo: None)

    # 张三：认领 → plan 确认（无 tasks 块 → 兜底单任务自动建 + 自动入池）
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    r = await client.post("/api/requirements", json={
        "project_id": seeded["project"].id, "title": "需求B", "description": "原始需求描述：做一个很厉害的功能",
    })
    req_id = r.json()["id"]
    await client.post(f"/api/requirements/{req_id}/claim")
    await _finalize_plan(client, db, req_id, "1. 建模块 2. 写测试")

    req_code = (await client.get(f"/api/requirements/{req_id}")).json()["code"]
    tasks = (await client.get(f"/api/dev-tasks", params={"requirement_id": req_id})).json()
    assert len(tasks) == 1
    task_code = tasks[0]["code"]
    assert task_code.startswith(f"TASK-{req_code}-1")
    assert tasks[0]["status"] == "queued"  # 定稿已自动入池
    branch = tasks[0]["branch"]
    assert branch.startswith(f"feature/test/{seeded['person'].gitlab_username}/REQ-TEST-") and branch.endswith("/t1")

    # runner 节点领取并执行（真 git 操作，claude 走 stub commit）
    rt = await admin_client.post("/api/runner-nodes", json={"name": "node-dev"})
    node_token = rt.json()["token"]
    runner = Runner(workdir=TMP / "runner-home")
    runner.client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test",
        headers={"Authorization": f"Bearer {node_token}"},
    )
    # 领取用独立 client：async with 退出会关掉 client，不能拿 runner.client 进 with
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test",
        headers={"Authorization": f"Bearer {node_token}"},
    ) as rc:
        r = await rc.get("/api/runner/next", params={"wait": 3})
        task = r.json()["task"]
        assert task["type"] == "dev_task"
        assert task["payload"]["git_http_url"] == f"file://{TMP}/gitroot/team/repo.git"
    await runner.execute(task)

    # 任务收尾：MR 已建、状态 mr_created、通知已留痕
    db.expire_all()
    dev = (await db.scalars(__import__("sqlalchemy").select(DevTask).where(DevTask.code == task_code))).first()
    assert dev.status == "mr_created"
    assert dev.mr_iid == 7
    assert dev.mr_url and "merge_requests/7" in dev.mr_url
    assert dev.branch == branch
    # 审核人取全部项目负责人的 GitLab 账号（顺序稳定：先挂第一个，余下描述里 @）
    assert seen_assignee == [[owner_username, owner2_username]]

    # 裸仓上确实有这条分支和一个新提交
    out = subprocess.run(["git", "--git-dir", str(bare), "log", "--oneline", f"master..{branch}"], capture_output=True, text=True)
    assert out.returncode == 0 and "stub" in out.stdout

    # octo 未配置 → 通知只留痕
    logs = (await db.scalars(__import__("sqlalchemy").select(NotifyLog).where(NotifyLog.reason == "mr_created"))).all()
    assert logs and "merge_requests/7" in logs[-1].content and task_code in logs[-1].content

    # 需求上下文只带定稿计划：plan_md 在、需求描述原文不在、Git 门禁在
    payload_prompt = task["payload"]["prompt"]
    assert "建模块" in payload_prompt
    assert "原始需求描述" not in payload_prompt
    assert "需求描述" not in payload_prompt
    assert "不要 push" in payload_prompt


async def test_project_repo_shared_per_project(monkeypatch):
    """同一 git_http_url 的两次 _ensure_project_repo 落在同一常驻仓库（首次 clone、二次 fetch）。"""
    bare = _bare_repo()
    monkeypatch.setattr(Runner, "_daemon_register", lambda self, repo: None)

    runner = Runner(workdir=TMP / "runner-home-slug")
    payload = {
        "git_http_url": f"file://{TMP}/gitroot/team/repo.git",
        "git_token": "gl-token",
        "default_branch": "master",
    }
    repo1 = await runner._ensure_project_repo(payload)
    repo2 = await runner._ensure_project_repo(payload)
    assert repo1 == repo2 and (repo1 / ".git").is_dir()
    assert repo1.name == "repo" and repo1.parent.name == "vega-workspace"

    # detach 到默认分支尖端；远端 URL 不落 token（file:// 本就透传，核对 set-url 生效）
    tip = subprocess.run(["git", "--git-dir", str(bare), "rev-parse", "master"], capture_output=True, text=True).stdout.strip()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo1, capture_output=True, text=True).stdout.strip()
    assert head == tip
    remote = subprocess.run(["git", "remote", "get-url", "origin"], cwd=repo1, capture_output=True, text=True).stdout.strip()
    assert remote == payload["git_http_url"]

    # slug：16 位 hex，不同 url 不同目录
    slug = Runner._repo_slug("http://gitlab.test/team/repo.git")
    assert len(slug) == 16 and all(c in "0123456789abcdef" for c in slug)
    assert slug != Runner._repo_slug("http://gitlab.test/team/other.git")


class _StubClient:
    """runner 测试用 api client：不真上报，只吃请求。"""

    def __init__(self) -> None:
        self.posts: list[str] = []

    async def post(self, url, json=None):
        self.posts.append(url)

    async def get(self, url, params=None):
        raise AssertionError("本测试不应有 API 下载")


async def test_dev_task_rerun_with_leftover_worktree(claude_stub, monkeypatch):
    """崩溃残留：worktree 检出在任务分支上时重跑，必须先清残留再 fetch（否则拒更 ref 死循环）。"""
    _bare_repo()
    monkeypatch.setattr(Runner, "_daemon_register", lambda self, repo: None)
    home = TMP / "runner-home-rerun"
    runner = Runner(workdir=home)
    runner.client = _StubClient()
    payload = {
        "dev_task_id": 999,
        "requirement_id": 1,
        "prompt": "实现需求",
        "docs": [],
        "session_id": "s-rerun-test",
        "git_http_url": f"file://{TMP}/gitroot/team/repo.git",
        "git_token": "gl-token",
        "default_branch": "master",
        "branch": "feature/test/rerun/dev",
        "claude_env": {},
    }
    await runner._exec_dev_task(1, payload)

    wt = home / "worktrees" / "task-999"
    repo = home / "vega-workspace" / "repo"
    subprocess.run(
        ["git", "--git-dir", str(repo / ".git"), "worktree", "add", "-q", str(wt), payload["branch"]],
        check=True, capture_output=True, text=True,
    )
    assert wt.exists()

    # 不抛错（fetch 未被残留分支卡死）且收尾清掉 worktree
    await runner._exec_dev_task(1, payload)
    assert not wt.exists()


async def test_dev_task_events_persisted(admin_client, client, db, seeded):
    """runner 过程事件落库留痕：跨批次 seq 续增、回放接口按序返回、节点计数含执行中任务。"""
    import httpx
    from sqlalchemy import select
    from vega.main import app
    from vega.models import RunnerNode, RunnerTask

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    req_id = (
        await client.post(
            "/api/requirements",
            json={"project_id": seeded["project"].id, "title": "留痕", "description": "d"},
        )
    ).json()["id"]
    await client.post(f"/api/requirements/{req_id}/claim")
    await _finalize_plan(client, db, req_id, "1. 干活")
    dev_id = (await client.post(f"/api/requirements/{req_id}/tasks")).json()["id"]

    nt = (await admin_client.post("/api/runner-nodes", json={"name": "node-evt"})).json()["token"]
    node = (await db.scalars(select(RunnerNode).where(RunnerNode.name == "node-evt"))).first()
    rt = RunnerTask(
        type="dev_task", payload={}, status="running",
        runner_id=node.id, dev_task_id=dev_id, requirement_id=req_id,
    )
    db.add(rt)
    await db.commit()

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://api.test",
        headers={"Authorization": f"Bearer {nt}"},
    ) as rc:
        await rc.post(f"/api/runner/tasks/{rt.id}/events", json={"events": [
            {"kind": "thinking", "text": "想一下怎么做"},
            # 逐 token 计量：一个任务能刷上万条，落库会把回放页灌爆，必须丢
            {"kind": "raw", "event": {"subtype": "thinking_tokens", "estimated_tokens": 42}},
            {"kind": "text", "text": "开始实现"},
            {"kind": "tool_use", "name": "Read", "input": '{"file_path": "a.py"}'},
            {"kind": "tool_result", "content": "文件内容", "is_error": False},
        ]})
        # 第二批：seq 必须接着 4 往下走
        await rc.post(f"/api/runner/tasks/{rt.id}/events", json={"events": [
            {"kind": "result", "text": "开发完成", "is_error": False},
        ]})

    # 节点执行中计数：claimed/running 各算一个 claude 进程
    nodes = (await admin_client.get("/api/runner-nodes")).json()
    assert [n for n in nodes if n["name"] == "node-evt"][0]["running_tasks"] == 1

    # 回放按序返回且字段完整
    evs = (await client.get(f"/api/dev-tasks/{dev_id}/events")).json()
    assert [e["kind"] for e in evs] == ["thinking", "text", "tool_use", "tool_result", "result"]
    assert [e["seq"] for e in evs] == [1, 2, 3, 4, 5]
    assert evs[2]["name"] == "Read" and "a.py" in evs[2]["input"]
