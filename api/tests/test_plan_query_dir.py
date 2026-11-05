"""只读问答：跑在常驻基线目录（不占 worktree）、仓库不可达退到需求文档目录、
基线 HEAD 只钉默认分支、工具白名单不再整体放行 Bash。"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from sqlalchemy import select

from vega import runner as runner_mod
from vega.claude import plan_args
from vega.config import get_settings
from vega.crypto import decrypt
from vega.models import RunnerTask
from vega.runner import Runner


def _rev(cwd: Path, ref: str) -> str:
    return subprocess.run(["git", "rev-parse", ref], cwd=str(cwd), capture_output=True, text=True).stdout.strip()


def _seed_bare(root: Path) -> tuple[Path, str]:
    """裸仓的 HEAD 故意停在 feature/x：默认分支 master 只能靠 fetch 补出来。"""
    seed = root / "seed"
    seed.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "master", str(seed)], check=True)
    (seed / "README.md").write_text("# repo\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], cwd=seed, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=seed, check=True)
    master = _rev(seed, "HEAD")
    subprocess.run(["git", "checkout", "-qb", "feature/x"], cwd=seed, check=True)
    (seed / "README.md").write_text("# repo\nfeature\n", encoding="utf-8")
    subprocess.run([*git, "commit", "-qam", "feat"], cwd=seed, check=True)
    bare = root / "repo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    return bare, master


def _payload(req_id: int, url: str | None = None) -> dict:
    p = {
        "requirement_id": req_id,
        "session_id": "sid",
        "resume": False,
        "prompt": "入口在哪",
        "mode": "requirement",
        "docs": [],
        "claude_env": {},
    }
    if url:
        p["git_http_url"] = url
        p["git_token"] = ""
        p["default_branch"] = "master"
    return p


async def _run_turn(monkeypatch, runner: Runner, payload: dict) -> dict:
    seen: dict = {}

    async def fake_run_claude(args, env, *, cwd=None, prompt=None, partial=False):
        seen["cwd"] = cwd
        yield {"kind": "result", "text": "答"}

    async def noop_emit(*_a, **_k):
        return None

    async def fake_result(task_id, status, error=None, data=None):
        seen["result"] = (status, error, data)

    monkeypatch.setattr(runner_mod, "run_claude", fake_run_claude)
    monkeypatch.setattr(runner, "_emit", noop_emit)
    monkeypatch.setattr(runner, "_result", fake_result)
    await runner._exec_plan_turn(1, payload)
    return seen


def test_plan_args_have_no_bare_bash():
    args = plan_args("sid", False)
    tools = args[args.index("--allowedTools") + 1].split(",")
    assert "Bash" not in tools
    assert {"Read", "Grep", "Glob", "LS"} <= set(tools)
    assert any("memory-bridge" in t for t in tools)


async def test_plan_turn_runs_in_baseline(tmp_path, monkeypatch):
    bare, master = _seed_bare(tmp_path)
    url = f"file://{bare}"
    monkeypatch.setattr(Runner, "_crg_sync_bg", lambda self, repo: None)
    monkeypatch.setattr(Runner, "_daemon_register", lambda self, repo: None)

    runner = Runner(workdir=tmp_path / "home")
    seen = await _run_turn(monkeypatch, runner, _payload(9001, url))

    assert seen["result"][0] == "done"
    cwd = Path(seen["cwd"])
    assert cwd == runner._repo_dir(url)
    # 基线钉默认分支：裸仓 HEAD 在 feature/x，问答看到的必须是 master 的内容
    assert _rev(cwd, "HEAD") == master
    assert (cwd / "README.md").read_text(encoding="utf-8") == "# repo\n"
    # 不占 worktree
    assert not (tmp_path / "home" / "worktrees").exists()


async def test_plan_turn_falls_back_when_repo_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(Runner, "_crg_sync_bg", lambda self, repo: None)
    monkeypatch.setattr(Runner, "_daemon_register", lambda self, repo: None)

    runner = Runner(workdir=tmp_path / "home")
    seen = await _run_turn(monkeypatch, runner, _payload(9002, f"file://{tmp_path}/missing.git"))

    assert seen["result"][0] == "done"
    assert Path(seen["cwd"]) == tmp_path / "home" / "docs" / "req-9002"
    assert Path(seen["cwd"]).is_dir()


async def test_plan_payload_carries_repo(client, db, seeded):
    from conftest import login_as

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"

    r = await client.post("/api/requirements", json={"system_id": seeded["project"].id, "title": "问答基线"})
    assert r.status_code == 200, r.text
    req_id = r.json()["id"]
    assert (await client.post(f"/api/requirements/{req_id}/claim")).status_code == 200
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "入口在哪"})
    assert r.status_code == 200, r.text

    await db.commit()
    task = await db.scalar(
        select(RunnerTask).where(RunnerTask.requirement_id == req_id).order_by(RunnerTask.id.desc()).limit(1)
    )
    assert task is not None
    assert task.payload["git_http_url"] == "http://gitlab.test/team/repo.git"
    assert task.payload["default_branch"] == "master"
    raw = decrypt(get_settings().secret_key, task.secrets)
    assert raw is not None
    assert json.loads(raw)["git_token"] == "gl-token"

    # 收尾：别把排队任务留给后面的 /runner/next 用例
    task.status = "done"
    await db.commit()
