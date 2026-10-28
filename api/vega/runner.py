"""runner 节点进程（可部署任意多台）：长轮询领任务 + 本地执行 claude -p。

启动：VEGA_API_URL=… VEGA_RUNNER_TOKEN=… python -m vega.runner
环境自举：python -m vega.runner bootstrap
本地目录布局：~/vega-workspace/<仓库名>（项目常驻仓库）+ ~/vega-runner/{docs,worktrees}/
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

import httpx

from .claude import ClaudeError, dev_args, plan_args, review_args, run_claude
from .config import get_settings
from .numbering import gen_session_id

log = logging.getLogger("vega.runner")

HEARTBEAT_S = 30
EVENT_FLUSH_S = 0.3
EVENT_FLUSH_MAX = 20

# MR 初审口径（照 hermes 从严）：默认可批准，modify 必须是硬伤；输出契约只有一个 JSON
MR_REVIEW_RULES = (
    "你是 MR 初审员，只依据下方给定事实判定代码质量，不管 CI 状态与可合并性（系统另行处理）。"
    '默认结论 approve；只有发现硬伤（逻辑错误、安全问题、明显破坏兼容性）才 modify。'
    '最终只输出一个 JSON 对象（可带 ```json 围栏），格式：'
    '{"verdict": "approve"|"modify", "summary": "一句话结论", '
    '"issues": [{"file": "路径", "line": 0, "severity": "high|mid|low", "note": "问题说明"}]}（approve 时 issues 为空数组）'
)


def _parse_verdict(text: str) -> dict:
    """从 claude 输出提取判定 JSON（裸 JSON / 围栏 / 带解释文字都兼容）。不合法抛 ValueError。"""
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        raise ValueError("输出中没有 JSON 对象")
    d = json.loads(m.group(0))
    if d.get("verdict") not in ("approve", "modify"):
        raise ValueError(f"verdict 非法: {d.get('verdict')!r}")
    return {
        "verdict": d["verdict"],
        "summary": str(d.get("summary") or "")[:2000],
        "issues": d.get("issues") or [],
    }


def _base() -> str:
    return get_settings().api_url.rstrip("/")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {get_settings().runner_token}"}


def _workdir() -> Path:
    from .config import get_settings

    d = Path(get_settings().runner_home or Path.home() / "vega-runner")
    (d / "repos").mkdir(parents=True, exist_ok=True)
    (d / "worktrees").mkdir(parents=True, exist_ok=True)
    return d


def _workspace() -> Path:
    from .config import get_settings

    d = Path(get_settings().workspace_home or Path.home() / "vega-workspace")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _crg_env() -> dict[str, str]:
    """CRG 子进程的嵌入配置：OpenAI 兼容内网服务（CRG 要求 key 非空）。"""
    s = get_settings()
    env = dict(os.environ)
    env.update(
        {
            "CRG_OPENAI_BASE_URL": s.crg_embedding_base_url,
            "CRG_OPENAI_API_KEY": s.crg_embedding_api_key or "local",
            "CRG_OPENAI_MODEL": s.crg_embedding_model,
            # 内网地址不是 localhost，CRG 默认会打「云外发」警告
            "CRG_ACCEPT_CLOUD_EMBEDDINGS": "1",
        }
    )
    return env


def _git(*args: str, cwd: str | Path | None = None, check: bool = True) -> str:
    r = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True)
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args[:3])} 失败: {r.stderr[-300:]}")
    return r.stdout


def _claude_config_dir() -> str:
    # 与 llm.py build_claude_env 保持同一来源：runner 侧装插件也必须落在同一个配置目录
    return os.environ.get("VEGA_CLAUDE_CONFIG_DIR", os.path.expanduser("~/.claude-vega"))


class Runner:
    def __init__(self, workdir: Path | None = None, workspace: Path | None = None) -> None:
        self.client = httpx.AsyncClient(base_url=_base(), headers=_headers(), timeout=httpx.Timeout(60.0, read=60.0))
        self.pending_events: dict[int, list[dict]] = {}
        self.last_flush: dict[int, float] = {}
        self.workdir = workdir  # None → 默认 ~/vega-runner；测试注入临时目录
        # 项目常驻仓库根目录：生产 ~/vega-workspace；测试随 workdir 隔离
        self.workspace = workspace or (workdir / "vega-workspace" if workdir else None)
        self._crg_syncing: set[str] = set()
        # 在跑的任务协程：不设并发上限，但要持引用防止被 GC 掉
        self._running: set[asyncio.Task] = set()
        # 共享资源的互斥：同一仓库共用 clone/fetch/worktree，同一需求共用 claude session
        self._repo_locks: dict[str, asyncio.Lock] = {}
        self._req_locks: dict[int, asyncio.Lock] = {}

    def _repo_lock(self, url: str) -> asyncio.Lock:
        """同一仓库的 git 操作串行（不同仓库互不影响）。"""
        lock = self._repo_locks.get(url)
        if lock is None:
            lock = self._repo_locks[url] = asyncio.Lock()
        return lock

    def _req_lock(self, requirement_id: int | None) -> asyncio.Lock:
        """同一需求的 plan 轮次串行：多轮共用一条 claude session。"""
        key = requirement_id or 0
        lock = self._req_locks.get(key)
        if lock is None:
            lock = self._req_locks[key] = asyncio.Lock()
        return lock

    # ---------- 与 api 的通信 ----------

    async def heartbeat_loop(self) -> None:
        while True:
            try:
                await self.client.post("/api/runner/heartbeat")
            except Exception:  # noqa: BLE001
                log.warning("heartbeat failed", exc_info=True)
            await asyncio.sleep(HEARTBEAT_S)

    async def worker_loop(self) -> None:
        # 并发数不设上限：一个任务就是一个 claude 子进程，能同时跑几个由上游模型
        # 的并发决定，这里只管领。领取后立刻开后台协程，自己继续长轮询下一个
        while True:
            try:
                r = await self.client.get("/api/runner/next", params={"wait": 25})
                r.raise_for_status()
                task = r.json().get("task")
                if task:
                    log.info("claimed task %s (%s)", task["id"], task["type"])
                    t = asyncio.create_task(self.execute(task))
                    self._running.add(t)
                    t.add_done_callback(self._task_done)
            except Exception:  # noqa: BLE001
                log.exception("worker loop error")
                await asyncio.sleep(3)

    def _task_done(self, t: asyncio.Task) -> None:
        self._running.discard(t)
        # 后台任务没人 await，异常不取出就只剩一条 GC 警告
        if not t.cancelled() and t.exception() is not None:
            log.error("task crashed", exc_info=t.exception())

    async def _flush_events(self, task_id: int, force: bool = False) -> None:
        evts = self.pending_events.get(task_id)
        if not evts:
            return
        now = asyncio.get_event_loop().time()
        # 攒批只针对高频计量帧；其余事件由 _emit 立即 flush。时间兜底保证
        # 长时间静默前最后几帧不会一直压在本地等下一批
        if not force and len(evts) < EVENT_FLUSH_MAX and now - self.last_flush.get(task_id, 0.0) < EVENT_FLUSH_S:
            return
        try:
            await self.client.post(f"/api/runner/tasks/{task_id}/events", json={"events": evts})
            self.pending_events[task_id] = []
            self.last_flush[task_id] = now
        except Exception:  # noqa: BLE001 事件丢了前端还有 DB 兜底
            log.warning("flush events failed for %s", task_id, exc_info=True)

    async def _emit(self, task_id: int, event: dict) -> None:
        self.pending_events.setdefault(task_id, []).append(event)
        # 思考/工具/正文都立即下发（前端打字机与思考链要跟手）；只有逐 token 的
        # thinking_tokens 是高频计量噪声，按条数 + 时间攒批
        tick = event.get("kind") == "raw" and (event.get("event") or {}).get("subtype") == "thinking_tokens"
        await self._flush_events(task_id, force=not tick)

    async def _result(self, task_id: int, status: str, error: str | None = None, data: dict | None = None) -> None:
        await self._flush_events(task_id, force=True)
        body = {"status": status, "error": error, "data": data or {}}
        # 结果上报失败会让任务永远卡在 running（活节点不会被 requeue 兜底），重试一次
        for attempt in range(2):
            try:
                await self.client.post(f"/api/runner/tasks/{task_id}/result", json=body)
                return
            except Exception:  # noqa: BLE001
                if attempt:
                    raise

    # ---------- 任务执行 ----------

    async def execute(self, task: dict) -> None:
        tid = task["id"]
        run = asyncio.create_task(self._run_task(task))
        watch = asyncio.create_task(self._watch_cancel(tid))
        done, _ = await asyncio.wait({run, watch}, return_when=asyncio.FIRST_COMPLETED)
        if watch in done and watch.result():
            # 用户点了停止：取消执行协程，run_claude 的 finally 会杀掉 claude 子进程
            run.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await run
            self.pending_events.pop(tid, None)
            self.last_flush.pop(tid, None)
            log.info("task %s cancelled by user", tid)
            return
        watch.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await watch
        await run

    async def _run_task(self, task: dict) -> None:
        tid = task["id"]
        try:
            if task["type"] == "plan_turn":
                # 同一需求的轮次串行：多轮 --resume 同一个 session，并发会互相踩会话文件
                async with self._req_lock(task["payload"].get("requirement_id")):
                    await self._exec_plan_turn(tid, task["payload"])
            elif task["type"] == "dev_task":
                await self._exec_dev_task(tid, task["payload"])
            elif task["type"] == "mr_review":
                await self._exec_mr_review(tid, task["payload"])
            else:
                await self._result(tid, "failed", f"未知任务类型 {task['type']}")
        except ClaudeError as e:
            await self._result(tid, "failed", str(e))
        except Exception as e:  # noqa: BLE001
            log.exception("task %s crashed", tid)
            await self._result(tid, "failed", f"{type(e).__name__}: {e}")

    async def _watch_cancel(self, task_id: int) -> bool:
        """执行期间每秒问一次任务状态；用户停止（status=cancelled）就返回 True。"""
        while True:
            await asyncio.sleep(1.0)
            try:
                r = await self.client.get(f"/api/runner/tasks/{task_id}/state")
                if r.json().get("status") == "cancelled":
                    return True
            except Exception:  # noqa: BLE001 轮询失败（网络抖动）不打断执行，下一轮再试
                log.debug("cancel poll failed for %s", task_id, exc_info=True)

    async def _docs_paths(self, payload: dict) -> list[str]:
        """把需求文档原始文件下载到本节点（按需求分目录），返回本地路径清单。

        平台只做传递与下载，解析交给 claude。每次执行都重新下载：
        文档很小，且同名重传后内容可能变了。
        """
        docs = payload.get("docs") or []
        if not docs:
            return []
        base = (self.workdir or _workdir()) / "docs" / f"req-{payload['requirement_id']}"
        base.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for d in docs:
            name = d["filename"].replace("\\", "/").rsplit("/", 1)[-1]  # 只留文件名，防路径穿越
            if not name:
                continue
            r = await self.client.get(f"/api/runner/requirements/{payload['requirement_id']}/docs/{d['id']}/raw")
            r.raise_for_status()
            (base / name).write_bytes(r.content)
            paths.append(str(base / name))
        return paths

    @staticmethod
    def _with_docs(prompt: str, paths: list[str]) -> str:
        if not paths:
            return prompt
        listing = "\n".join(f"- {p}" for p in paths)
        return f"{prompt}\n\n[需求文档已下载到本机（原始文件），请自行读取解析：]\n{listing}"

    async def _query_dir(self, payload: dict) -> str:
        """只读问答的工作目录：有仓库就进常驻基线（图按绝对路径索引，worktree 借不过来），
        拉不下来退到需求文档目录——绝不继承 runner 的启动目录（那里有 .env）。"""
        if payload.get("git_http_url"):
            try:
                async with self._repo_lock(payload["git_http_url"]):
                    return str(await self._ensure_project_repo(payload))
            except Exception:  # noqa: BLE001 仓库不可达不该拖垮问答，文档照常下发
                log.warning("问答基线不可用，退到需求文档目录", exc_info=True)
        d = (self.workdir or _workdir()) / "docs" / f"req-{payload.get('requirement_id')}"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    async def _exec_plan_turn(self, task_id: int, payload: dict) -> None:
        payload["prompt"] = self._with_docs(payload["prompt"], await self._docs_paths(payload))
        payload["cwd"] = await self._query_dir(payload)
        resume = payload.get("resume", False)
        try:
            await self._run_plan_claude(task_id, plan_args(payload["session_id"], resume), payload)
        except ClaudeError as e:
            # 会话在 claude 侧丢失（换机器/清配置会让 --resume 永远失败，需求对话被卡死）：
            # 用同一 session id 重建会话重试。resume 轮的 prompt 不带历史上下文，重建后仅缺前文
            if not resume or "No conversation found" not in str(e):
                raise
            await self._emit(task_id, {"kind": "text", "text": "（会话已失效，重建会话后重试）"})
            await self._run_plan_claude(task_id, plan_args(payload["session_id"], False), payload)

    async def _run_plan_claude(self, task_id: int, args: list[str], payload: dict) -> None:
        chunks: list[str] = []
        final_text = ""
        async for evt in run_claude(args, payload["claude_env"], cwd=payload.get("cwd"), prompt=payload["prompt"]):
            # 只累积主会话文本：子 agent（parent 非空）的输出不进 plan 定稿内容
            if evt["kind"] == "text" and not evt.get("parent"):
                chunks.append(evt["text"])
            elif evt["kind"] == "result":
                final_text = evt["text"] or final_text
            await self._emit(task_id, evt)
        await self._result(task_id, "done", data={"text": final_text or "".join(chunks)})

    @staticmethod
    def _repo_slug(url: str) -> str:
        return hashlib.sha1(url.encode()).hexdigest()[:16]

    def _repo_dir(self, url: str) -> Path:
        """项目常驻仓库：~/vega-workspace/<仓库名>；同名不同源时退回 hash 目录，避免串仓。"""
        root = self.workspace or _workspace()
        root.mkdir(parents=True, exist_ok=True)
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        name = name or self._repo_slug(url)
        dest = root / name
        if dest.exists():
            origin = ""
            if (dest / ".git").exists():
                origin = _git("remote", "get-url", "origin", cwd=dest, check=False).strip()
            if origin != url:
                dest = root / f"{name}-{self._repo_slug(url)[:8]}"
            return dest
        # 旧布局 ~/vega-runner/repos/<sha1> 就地迁移，避免首次整仓重克隆
        legacy = (self.workdir or _workdir()) / "repos" / self._repo_slug(url)
        if (legacy / ".git").exists():
            shutil.move(str(legacy), str(dest))
        return dest

    def _crg_sync(self, repo: Path) -> None:
        """补齐/更新 CRG 图与向量：无图 build；有图按 commit 漂移 update；向量按节点增量补。"""
        if not shutil.which("code-review-graph"):
            return
        env = _crg_env()
        model = get_settings().crg_embedding_model
        steps: list[list[str]] = []
        if not (repo / ".code-review-graph" / "graph.db").exists():
            steps.append(["build", "--repo", str(repo), "-q"])
        else:
            r = subprocess.run(
                ["code-review-graph", "status", "--repo", str(repo), "--json"],
                capture_output=True, text=True, env=env,
            )
            try:
                st = json.loads(r.stdout)
            except ValueError:
                st = {}
            if st.get("built_at_commit") != st.get("current_sha") or st.get("build_incomplete"):
                steps.append(["update", "--repo", str(repo), "-q"])
        # embed 按 text_hash+provider 跳过未变节点，重复调用不会重复请求嵌入服务
        steps.append(["embed", "--repo", str(repo), "--provider", "openai", "--model", model])
        for cmd in steps:
            try:
                r = subprocess.run(["code-review-graph", *cmd], capture_output=True, text=True, env=env, timeout=3600)
                if r.returncode != 0:
                    log.warning("crg %s %s 失败: %s", cmd[0], repo, r.stderr[-300:])
            except (OSError, subprocess.TimeoutExpired):
                log.warning("crg %s %s 失败", cmd[0], repo, exc_info=True)

    def _crg_sync_bg(self, repo: Path) -> None:
        """后台补齐图与向量（大仓 build 可能几分钟），同一仓库不重复启动。"""
        key = str(repo)
        if key in self._crg_syncing:
            return
        self._crg_syncing.add(key)

        async def run() -> None:
            try:
                await asyncio.to_thread(self._crg_sync, repo)
            finally:
                self._crg_syncing.discard(key)

        asyncio.create_task(run())

    def _daemon_register(self, repo: Path) -> None:
        """把常驻仓库挂进 crg-daemon（watcher 盯文件变化做增量索引），已挂/无 daemon 则跳过。"""
        if not shutil.which("crg-daemon"):
            return
        try:
            r = subprocess.run(["crg-daemon", "status"], capture_output=True, text=True)
            if str(repo) in r.stdout + r.stderr:
                return
            subprocess.run(["crg-daemon", "add", str(repo)], capture_output=True, text=True)
        except OSError:
            log.warning("crg-daemon 注册 %s 失败", repo, exc_info=True)

    async def _ensure_project_repo(self, payload: dict) -> Path:
        """per-project 常驻仓库：首次 clone，之后显式 URL fetch（token 只进命令行不落盘）。

        HEAD 恒 --detach 在默认分支：这是多个任务共用的只读基线（只读问答直接在里面跑），
        任何想离开默认分支的任务一律用自己的 worktree。
        checkout 还有一个作用：daemon watcher 只认文件变化，光 fetch 不动文件不触发增量索引。
        """
        url = payload["git_http_url"]
        repo = self._repo_dir(url)
        authed = self._with_token(url, payload["git_token"])
        if (repo / ".git").exists():
            _git("fetch", "--prune", authed, "+refs/heads/*:refs/heads/*", cwd=repo)
        else:
            _git("clone", authed, str(repo))
            _git("remote", "set-url", "origin", url, cwd=repo)
            self._daemon_register(repo)
            # clone 只把远端 HEAD 落成本地分支：先离开该分支再补齐本地 refs。
            # 否则 fetch 会拒绝更新「已检出」的分支，远端 HEAD 不是默认分支时
            # 默认分支根本不在本地（首轮评审/开发任务会直接找不到 ref）
            _git("checkout", "-q", "--detach", "HEAD", cwd=repo)
            _git("fetch", "--prune", authed, "+refs/heads/*:refs/heads/*", cwd=repo)
        _git("checkout", "-q", "--detach", f"refs/heads/{payload['default_branch']}", cwd=repo)
        # 迁移到 vega-workspace 后的仓库不在 daemon 配置里，这里兜住（已挂则跳过）
        self._daemon_register(repo)
        self._crg_sync_bg(repo)
        return repo

    async def _exec_dev_task(self, task_id: int, payload: dict) -> None:
        payload["prompt"] = self._with_docs(payload["prompt"], await self._docs_paths(payload))

        wd = self.workdir or _workdir()
        if self.workdir:
            self.workdir.mkdir(parents=True, exist_ok=True)
        wt = wd / "worktrees" / f"task-{payload['dev_task_id']}"
        branch = payload["branch"]
        base = payload["default_branch"]

        # 同一仓库的 clone/fetch/worktree 串行（git 自己会抢 ref/index 锁）；
        # claude 在各自 worktree 里跑，不受这把锁约束
        async with self._repo_lock(payload["git_http_url"]):
            repo_dir = self._repo_dir(payload["git_http_url"])
            git_dir = str(repo_dir / ".git")
            # ① 先清上次失败残留的 worktree：它检出的分支会让常驻仓库 fetch 拒更 ref
            if wt.exists():
                if (repo_dir / ".git").exists():
                    _git("--git-dir", git_dir, "worktree", "remove", "--force", str(wt))
                else:
                    shutil.rmtree(wt)  # 旧布局（per-task mirror 时代）残留，无主直接删

            # ② 常驻仓库 clone/fetch + detach 默认分支
            await self._ensure_project_repo(payload)

            # ③ worktree（分支从默认分支切出；-B：重跑时分支已存在则重置到基线）
            _git("--git-dir", git_dir, "worktree", "add", "-B", branch, str(wt), f"refs/heads/{base}")

        _git("config", "user.name", "Vega Agent", cwd=str(wt))
        _git("config", "user.email", "vega@runner.local", cwd=str(wt))

        # ④ claude 在 worktree 里干活（只见自己的目录，碰不到别的分支）
        async for evt in run_claude(dev_args(payload["session_id"]), payload["claude_env"], cwd=str(wt), prompt=payload["prompt"]):
            await self._emit(task_id, evt)

        # ⑤ 校验产出 + push（确定性动作，AI 只负责 commit）
        commits = _git("log", "--oneline", f"refs/heads/{base}..HEAD", cwd=str(wt)).strip()
        if not commits:
            raise RuntimeError("claude 执行完但分支上没有新提交")
        # 分支为任务独占，重跑时 -B 已重置历史，须强推才是确定性的
        _git("push", self._with_token(payload["git_http_url"], payload["git_token"]), f"HEAD:refs/heads/{branch}", "--force", cwd=str(wt))

        async with self._repo_lock(payload["git_http_url"]):
            _git("--git-dir", git_dir, "worktree", "remove", "--force", str(wt))
        await self._result(task_id, "done", data={"status": "pushed", "branch": branch, "commits": commits})

    @staticmethod
    def _with_token(url: str, token: str) -> str:
        """https://host/group/repo.git → https://oauth2:token@host/…；非 http(s)（如 file:// 测试远端）原样返回。"""
        if not url.startswith(("http://", "https://")):
            return url
        scheme, rest = url.split("://", 1)
        return f"{scheme}://oauth2:{token}@{rest}"

    @staticmethod
    def _crg_report(repo: Path, base: str, files: list[str], graph_repo: Path | None = None) -> str:
        """CRG 两条 CLI 各截 12KB；不可用/输出为空时 git diff --stat 兜底。

        repo 是评审用的检出目录（detect-changes 按它的 HEAD 算 diff）；graph_repo
        是图所在的常驻仓库——图的文件路径是绝对的，worktree 借不到它的图，
        所以 impact 单独指向常驻仓库（文件清单是仓库相对路径，两边通用）。
        """
        graph = graph_repo or repo
        out: list[str] = []
        for name, cmd in (
            (
                "detect-changes",
                ["code-review-graph", "detect-changes", "--base", base, "--repo", str(repo), "--brief"],
            ),
            (
                "impact",
                [
                    "code-review-graph", "impact", "--files", *files[:50],  # argv 长度保护
                    "--base", base, "--repo", str(graph),
                ],
            ),
        ):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
                if r.returncode == 0 and r.stdout.strip():
                    out.append(f"## CRG {name}\n{r.stdout[:12000]}")
            except (OSError, subprocess.TimeoutExpired):
                continue
        if not out:
            try:
                diff = _git("diff", "--stat", f"{base}...HEAD", cwd=str(repo))
            except RuntimeError:
                diff = f"（git diff 不可用，base={base}）"
            out.append(f"## git diff --stat（CRG 不可用兜底）\n{diff[:12000]}")
        return "\n\n".join(out)

    async def _exec_mr_review(self, task_id: int, payload: dict) -> None:
        """评审在自己的 detached worktree 里跑：共享检出目录的 HEAD 不动，
        锁只包 fetch 与 worktree 增删，CRG + claude 在锁外执行。

        worktree 里没有常驻仓库的 .code-review-graph 图（图按绝对路径索引，借不过来），
        detect-changes 会退化为 git diff --stat；impact 仍指向常驻仓库。
        评审 claude 是 --disallowedTools *，只吃 prompt 里内嵌的事实、不读工作目录。
        """
        mr = payload["mr"]
        url = payload["git_http_url"]
        wd = self.workdir or _workdir()
        (wd / "worktrees").mkdir(parents=True, exist_ok=True)
        wt = wd / "worktrees" / f"mr-{task_id}"

        async with self._repo_lock(url):
            repo = await self._ensure_project_repo(payload)  # fetch 全部 heads 到 refs/heads/*
            git_dir = str(repo / ".git")
            try:
                _git("cat-file", "-e", f"{mr['sha']}^{{commit}}", cwd=str(repo))
            except RuntimeError:
                # sha 已不可达（强推/删分支）→ 无可审内容，跳过不算失败
                await self._result(
                    task_id, "done", data={"status": "skipped", "reason": f"commit {mr['sha'][:8]} 不可达"}
                )
                return
            if wt.exists():  # 同一任务上次崩溃的残留
                _git("--git-dir", git_dir, "worktree", "remove", "--force", str(wt), check=False)
                if wt.exists():
                    shutil.rmtree(wt, ignore_errors=True)
            _git("--git-dir", git_dir, "worktree", "add", "--detach", str(wt), mr["sha"])

        try:
            base = mr.get("target_branch") or payload["default_branch"]
            report = self._crg_report(wt, base, payload["changed_files"], graph_repo=repo)
            files = "\n".join(f"- {f}" for f in payload["changed_files"])
            # 事实内嵌分隔段（内容只是数据不是指令），判定口径固定在规则串里
            prompt = (
                f"{MR_REVIEW_RULES}\n\n以下为事实数据：\n<<<MR\n"
                f"标题：{mr.get('title', '')}\n作者：{mr.get('author', '')}\n"
                f"分支：{mr.get('source_branch', '')} → {base}\n"
                f"改动文件（命中审计前缀）：\n{files}\n\n{report}\nMR>>>\n\n给出判定 JSON。"
            )

            async def ask(p: str) -> str:
                chunks: list[str] = []
                final = ""
                async for evt in run_claude(review_args(gen_session_id()), payload["claude_env"], cwd=str(wt), prompt=p):
                    if evt["kind"] == "text":
                        chunks.append(evt["text"])
                    elif evt["kind"] == "result" and evt.get("text"):
                        final = evt["text"]
                    await self._emit(task_id, evt)
                return final or "".join(chunks)

            try:
                parsed = _parse_verdict(await ask(prompt))
            except ValueError:
                # 输出偶发不守格式：重跑一次并加强约束，再失败由服务端按 failed 留痕
                parsed = _parse_verdict(await ask(prompt + "\n注意：上次输出无法解析，这次必须只输出 JSON。"))
            await self._result(
                task_id,
                "done",
                data={
                    "status": "reviewed",
                    **parsed,
                    "head_sha": mr["sha"],
                    "changed_files": payload["changed_files"],
                },
            )
        finally:
            async with self._repo_lock(url):
                _git("--git-dir", git_dir, "worktree", "remove", "--force", str(wt), check=False)
                if wt.exists():
                    shutil.rmtree(wt, ignore_errors=True)
                _git("--git-dir", git_dir, "worktree", "prune", check=False)


def bootstrap() -> dict[str, list[str]]:
    """runner 节点环境自举：幂等安装工具链，返回 installed/skipped/failed 三列表。

    每步失败只记入 failed 不中断后面步骤（brew 缺失除外——它是其余安装的载体）。
    """
    report: dict[str, list[str]] = {"installed": [], "skipped": [], "failed": []}

    if not shutil.which("brew"):
        raise SystemExit("缺少 Homebrew（brew），请先手动安装 https://brew.sh 再跑 bootstrap")

    def step(name: str, present: bool, install: list[list[str]]) -> None:
        if present:
            report["skipped"].append(name)
            return
        ok = True
        for cmd in install:
            r = subprocess.run(cmd, capture_output=True, text=True)
            if r.returncode != 0:
                log.warning("安装 %s 失败: %s", name, (r.stderr or r.stdout)[-300:])
                ok = False
        report["installed" if ok else "failed"].append(name)

    step("node", bool(shutil.which("node")), [["brew", "install", "node"]])
    step("jq", bool(shutil.which("jq")), [["brew", "install", "jq"]])
    step("ast-grep", bool(shutil.which("ast-grep")), [["brew", "install", "ast-grep"]])
    step("uv", bool(shutil.which("uv")), [["brew", "install", "uv"]])
    step("code-review-graph", bool(shutil.which("code-review-graph")), [["uv", "tool", "install", "code-review-graph"]])
    step("serena", bool(shutil.which("serena")), [["uv", "tool", "install", "serena-agent"]])
    step("jdtls", bool(shutil.which("jdtls")), [["brew", "install", "jdtls"]])
    step("typescript-language-server", bool(shutil.which("typescript-language-server")),
         [["npm", "install", "-g", "typescript", "typescript-language-server"]])
    step("pyright", bool(shutil.which("pyright")), [["npm", "install", "-g", "pyright"]])
    step("claude-cli", bool(shutil.which("claude")), [["npm", "install", "-g", "@anthropic-ai/claude-code"]])

    # crg-daemon：装完 code-review-graph 才有；没起来就拉起来
    try:
        r = subprocess.run(["crg-daemon", "status"], capture_output=True, text=True)
        if "running" in (r.stdout + r.stderr).lower():
            report["skipped"].append("crg-daemon")
        else:
            subprocess.run(["crg-daemon", "start"], capture_output=True, text=True)
            report["installed"].append("crg-daemon")
    except FileNotFoundError:
        # uv tool 装完要新 shell 才在 PATH 上，重跑一次 bootstrap 即可
        report["skipped"].append("crg-daemon")

    _bootstrap_plugins(report)
    _merge_mcp_servers(_claude_config_dir())
    return report


def _bootstrap_plugins(report: dict[str, list[str]]) -> None:
    """LSP 插件装进 CLAUDE_CONFIG_DIR（claude CLI 幂等：重复 install rc=0 输出 already installed）。"""
    env = {**os.environ, "CLAUDE_CONFIG_DIR": _claude_config_dir()}

    def run(cmd: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(cmd, capture_output=True, text=True, env=env)

    try:
        r = run(["claude", "plugin", "marketplace", "list"])
        if "claude-plugins-official" in r.stdout + r.stderr:
            report["skipped"].append("plugin-marketplace")
        else:
            r = run(["claude", "plugin", "marketplace", "add", "anthropics/claude-plugins-official"])
            report["installed" if r.returncode == 0 else "failed"].append("plugin-marketplace")

        have = run(["claude", "plugin", "list"]).stdout
        for plugin in ("typescript-lsp", "pyright-lsp", "jdtls-lsp"):
            if f"{plugin}@claude-plugins-official" in have:
                report["skipped"].append(plugin)
                continue
            r = run(["claude", "plugin", "install", f"{plugin}@claude-plugins-official"])
            out = r.stdout + r.stderr
            if r.returncode != 0 and "already installed" not in out:
                report["failed"].append(plugin)
            else:
                report["skipped" if "already installed" in out else "installed"].append(plugin)
    except FileNotFoundError:
        report["failed"].append("lsp-plugins")


def _merge_mcp_servers(config_dir: str) -> None:
    """把 CRG/serena 两个 MCP server 合并进 CLAUDE_CONFIG_DIR/.claude.json（不动其他 key）。

    用户级 MCP 的真正落点是 .claude.json 的 mcpServers：settings.json 里的同名键 claude 不读。
    """
    path = Path(config_dir) / ".claude.json"
    data: dict = {}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.warning(".claude.json 解析失败，跳过 MCP 合并: %s", path)
            return
    s = get_settings()
    servers = data.setdefault("mcpServers", {})
    servers["code-review-graph"] = {
        "type": "stdio",
        "command": "code-review-graph",
        "args": ["mcp"],
        # MCP 子进程只继承 claude 的 env（VEGA_* 前缀在这里没用），嵌入配置随 entry 一起写死
        "env": {
            "CRG_OPENAI_BASE_URL": s.crg_embedding_base_url,
            "CRG_OPENAI_API_KEY": s.crg_embedding_api_key or "local",
            "CRG_OPENAI_MODEL": s.crg_embedding_model,
            "CRG_ACCEPT_CLOUD_EMBEDDINGS": "1",
        },
    }
    servers["serena"] = {
        "type": "stdio",
        "command": "serena",
        "args": ["start-mcp-server", "--context", "claude-code", "--project-from-cwd"],
        "env": {},
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def _worker_main() -> None:
    s = get_settings()
    if not s.runner_token:
        raise SystemExit("需要 VEGA_RUNNER_TOKEN（管理员在界面创建节点后发放）")
    name = s.runner_name or socket.gethostname()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # 每次启动按当前 .env 重写 MCP 条目（镜像里只装 LSP 插件与工具链，嵌入地址等随配置变）
    try:
        _merge_mcp_servers(_claude_config_dir())
    except OSError:
        log.warning("MCP 配置合并失败，claude 将没有 code-review-graph/serena", exc_info=True)
    runner = Runner()
    log.info("runner %s 启动，api=%s", name, s.api_url)
    # 一个领取循环 + 一条节点心跳；执行不阻塞领取，并发由上游模型决定
    await asyncio.gather(runner.heartbeat_loop(), runner.worker_loop())


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(prog="python -m vega.runner", description="vega runner 节点")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("bootstrap", help="安装节点工具链（幂等，可重复执行）")
    args = parser.parse_args()
    if args.cmd == "bootstrap":
        report = bootstrap()
        for key in ("installed", "skipped", "failed"):
            print(f"{key}: {', '.join(report[key]) or '（无）'}")
        return
    asyncio.run(_worker_main())


if __name__ == "__main__":
    main()
