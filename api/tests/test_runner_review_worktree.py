"""MR 评审的隔离：在独立 detached worktree 里按目标 sha 检出，共享常驻仓库的
HEAD 不动，评审结束 worktree 清理干净（含残留兜底）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import TMP

from vega import runner as runner_mod
from vega.runner import Runner


def _rev(cwd: Path, ref: str) -> str:
    return subprocess.run(["git", "rev-parse", ref], cwd=str(cwd), capture_output=True, text=True).stdout.strip()


def _seed_repo(root: Path) -> tuple[Path, str, str]:
    """master 一个提交 + feature/x 一个提交；返回（裸仓, master sha, feature sha）。"""
    seed = root / "seed"
    seed.mkdir(parents=True)
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", "-b", "master", str(seed)], check=True)
    (seed / "README.md").write_text("# repo\n", encoding="utf-8")
    subprocess.run([*git, "add", "-A"], cwd=seed, check=True)
    subprocess.run([*git, "commit", "-qm", "init"], cwd=seed, check=True)
    master = _rev(seed, "HEAD")
    subprocess.run([*git, "checkout", "-qb", "feature/x"], cwd=seed, check=True)
    (seed / "README.md").write_text("# repo\nfeature\n", encoding="utf-8")
    subprocess.run([*git, "commit", "-qam", "feat"], cwd=seed, check=True)
    feat = _rev(seed, "HEAD")
    bare = root / "repo.git"
    subprocess.run(["git", "clone", "-q", "--bare", str(seed), str(bare)], check=True)
    return bare, master, feat


async def test_mr_review_runs_in_own_worktree(tmp_path, monkeypatch):
    bare, master, feat = _seed_repo(tmp_path)
    url = f"file://{bare}"
    monkeypatch.setattr(Runner, "_crg_sync_bg", lambda self, repo: None)
    monkeypatch.setattr(Runner, "_daemon_register", lambda self, repo: None)
    monkeypatch.setattr(Runner, "_crg_report", lambda self, repo, base, files, graph_repo=None: "CRG-STUB")

    seen: dict = {}

    async def fake_run_claude(args, env, *, cwd=None, prompt=None, partial=False):
        # 评审期间：目录存在，且就在被审的那个提交上
        seen["cwd"] = cwd
        seen["head"] = _rev(Path(cwd), "HEAD")
        yield {"kind": "result", "text": '{"verdict": "approve", "summary": "ok"}'}

    monkeypatch.setattr(runner_mod, "run_claude", fake_run_claude)

    runner = Runner(workdir=TMP / "review-runner-home")

    async def noop_emit(*_a, **_k):
        return None

    async def fake_result(task_id, status, error=None, data=None):
        seen["result"] = (status, error, data)

    monkeypatch.setattr(runner, "_emit", noop_emit)
    monkeypatch.setattr(runner, "_result", fake_result)

    payload = {
        "git_http_url": url,
        "git_token": "",
        "default_branch": "master",
        "changed_files": ["README.md"],
        "claude_env": {},
        "mr": {
            "sha": feat,
            "title": "评审隔离",
            "author": "zhangsan",
            "source_branch": "feature/x",
            "target_branch": "master",
        },
    }
    await runner._exec_mr_review(1, payload)

    status, _err, data = seen["result"]
    assert status == "done" and data["status"] == "reviewed" and data["head_sha"] == feat
    assert Path(seen["cwd"]).name == "mr-1" and seen["head"] == feat

    # 共享常驻仓库留在默认分支尖端，评审的 sha 没把它带跑
    repo = runner._repo_dir(url)
    assert _rev(repo, "HEAD") == master
    # 评审 worktree 已清理（目录 + git 的工作树登记）
    assert not (TMP / "review-runner-home" / "worktrees" / "mr-1").exists()
    listing = subprocess.run(
        ["git", "--git-dir", str(repo / ".git"), "worktree", "list"], capture_output=True, text=True
    ).stdout.strip()
    assert len(listing.splitlines()) == 1
