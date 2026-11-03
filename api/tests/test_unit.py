"""单元测试：加密 / LLM 解析链 / claude env / 编号 / token / octo @。"""

from __future__ import annotations

from urllib.parse import quote

from vega.auth import hash_password, parse_token, sign_token, verify_password
from vega.config import get_settings
from vega.crypto import decrypt, encrypt
from vega.llm import LLMTarget, build_claude_env, resolve_llm
from vega.models import DevGroup, Person
from vega.octo import build_mentions


async def test_list_project_members_paginates():
    """GitLab 默认每页 20 人：不翻页的话大项目里后排成员整个从名单里消失。"""
    import httpx

    from vega.gitlab import GitlabClient

    rows = [{"id": i, "username": f"u{i}", "state": "active"} for i in range(1, 121)]
    rows.append({"id": 999, "username": "left", "state": "blocked"})
    seen: list[tuple[int, int]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        per = int(req.url.params.get("per_page", 20))
        page = int(req.url.params.get("page", 1))
        seen.append((per, page))
        return httpx.Response(200, json=rows[(page - 1) * per : page * per])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
        got = await GitlabClient("https://gl.test", "t").list_project_members(c, 1)
    assert [m["username"] for m in got][-1] == "u120" and len(got) == 120  # blocked 不算人
    assert seen == [(100, 1), (100, 2)]


def test_crypto_roundtrip():
    s = get_settings().secret_key
    assert decrypt(s, encrypt(s, "sk-abc-123")) == "sk-abc-123"
    assert encrypt(s, "") == ""
    assert encrypt(s, None) is None
    assert decrypt(s, None) is None


def test_password_hash():
    h = hash_password("secret")
    assert verify_password("secret", h)
    assert not verify_password("wrong", h)
    assert not verify_password("secret", None)


def test_token_sign_parse():
    t = sign_token(42, ttl_s=60)
    assert parse_token(t) == 42
    assert parse_token("garbage") is None
    assert parse_token(sign_token(42, ttl_s=-1)) is None  # 过期


def test_resolve_llm_chain():
    g_with = DevGroup(name="g", llm_base_url="http://group.llm/v1", llm_model="g-model", llm_effort="medium")
    p_with = Person(name="p", llm_base_url="http://person.llm/v1", llm_model="p-model", llm_effort="low")
    p_without = Person(name="p2")

    # 人员 > 组 > 默认
    assert resolve_llm(p_with, g_with).source == "person"
    assert resolve_llm(p_without, g_with).source == "group"
    assert resolve_llm(None, g_with).source == "group"
    assert resolve_llm(p_without, None).source == "default"
    # CICD/组级：person_first=False → 只看组
    assert resolve_llm(p_with, g_with, person_first=False).source == "group"

    # effort 独立按 人员 → 组 → 兜底 解析（可与 model 来源不同级）
    assert resolve_llm(p_with, g_with).effort == "low"
    assert resolve_llm(p_without, g_with).effort == "medium"
    assert resolve_llm(p_without, None).effort == get_settings().am_default_effort
    # 人员只选思考等级、不配 LLM：model 走组，effort 用人员的
    p_effort_only = Person(name="p3", llm_effort="low")
    t = resolve_llm(p_effort_only, g_with)
    assert t.source == "group" and t.effort == "low"
    # 组级分析不吃人员 effort
    assert resolve_llm(p_effort_only, g_with, person_first=False).effort == "medium"


def test_build_claude_env():
    target = LLMTarget("http://up.llm/v1", "sk-up", "deepseek-x", "low", "person")
    env = build_claude_env(
        target, team_id="team-9", agent_id="agent-1", user_key="sk-mem-xxx"
    )
    assert env["ANTHROPIC_BASE_URL"].endswith("/claude-code/default")
    assert env["ANTHROPIC_API_KEY"] == "sk-mem-xxx"
    headers = env["ANTHROPIC_CUSTOM_HEADERS"].splitlines()
    assert "x-team-id: team-9" in headers
    assert "x-agent-id: agent-1" in headers
    assert "p-url: http://up.llm/v1" in headers
    assert "p-api-key: sk-up" in headers
    assert env["ANTHROPIC_MODEL"] == "deepseek-x"
    assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "deepseek-x"  # 后台调用映射也要对齐，否则 404
    assert env["VEGA_CLAUDE_EFFORT"] == "low"

    # 中文 team id 不能裸进 HTTP 头（claude CLI 会拒掉非 ASCII 头直接退出 1）
    env_cn = build_claude_env(LLMTarget(), team_id="示例团队", agent_id="agt-中国", user_key="k")
    hdr = [h for h in env_cn["ANTHROPIC_CUSTOM_HEADERS"].splitlines() if h.startswith("x-team-id")][0]
    assert hdr.isascii() and f"x-team-id: {quote('示例团队')}" in hdr
    assert "CLAUDE_CODE_MAX_OUTPUT_TOKENS" in env

    # team 单独存在时不下发：proxy 会为"选专家"发 AskUserQuestion，claude -p 答不了
    env_team_only = build_claude_env(LLMTarget(), team_id="team-9", agent_id=None, user_key="k")
    assert all(
        not h.startswith(("x-team-id", "x-agent-id")) for h in env_team_only["ANTHROPIC_CUSTOM_HEADERS"].splitlines()
    )

    # 无自定义 LLM：不带 p-* 头；model/effort 落到 am 兜底（proxy 不认 claude 原生名）
    env2 = build_claude_env(LLMTarget(), team_id=None, agent_id=None, user_key="k")
    assert "p-url" not in env2["ANTHROPIC_CUSTOM_HEADERS"]
    assert env2["ANTHROPIC_MODEL"] == get_settings().am_default_model
    assert env2["VEGA_CLAUDE_EFFORT"] == get_settings().am_default_effort

    # proxy 剥 content-encoding 却转发压缩 body：必须显式要 identity，否则自定义上游
    # 一压缩 claude 就 JSON Parse error（用户侧表现为 unrecognized_model）
    assert "accept-encoding: identity" in env["ANTHROPIC_CUSTOM_HEADERS"].splitlines()


def test_build_claude_env_conversation_headers():
    """conversation_id 触发 proxy 全局会话直选四头；不传则完全不带（现网行为不变）。"""
    env = build_claude_env(
        LLMTarget(), team_id="team-9", agent_id="agt-1", user_key="k", conversation_id="vega-mr-1-2"
    )
    headers = env["ANTHROPIC_CUSTOM_HEADERS"].splitlines()
    assert "x-team-id: team-9" in headers
    assert "x-task-id: no-task" in headers
    assert "x-conversation-id: vega-mr-1-2" in headers

    env_none = build_claude_env(LLMTarget(), team_id=None, agent_id=None, user_key="k")
    assert all("x-conversation-id" not in h and "x-task-id" not in h for h in env_none["ANTHROPIC_CUSTOM_HEADERS"].splitlines())


async def test_numbering(db):
    from vega.models import Project, Requirement
    from vega.numbering import next_requirement_code, next_task_code

    proj = Project(group_id=1, gitlab_project_id=1, path="a/b", name="b", code_prefix="TEST")
    db.add(proj)
    await db.flush()
    c1 = await next_requirement_code(db, "TEST")
    req = Requirement(project_id=proj.id, code=c1, title="t")
    db.add(req)
    await db.flush()
    assert c1.startswith("REQ-TEST-")
    t1 = await next_task_code(db, req)
    db.add(__import__("vega.models", fromlist=["DevTask"]).DevTask(requirement_id=req.id, code=t1, creator_id=1))
    await db.flush()
    assert t1 == f"TASK-{c1}-1"
    assert (await next_requirement_code(db, "TEST")) != c1


async def test_runner_docs_sync(tmp_path):
    """runner 文档同步：下载原始字节落盘、防路径穿越、prompt 注入本地路径。"""
    from vega.runner import Runner

    class FakeResp:
        content = b"# doc \xe5\x8e\x9f\xe5\xa7\x8b\xe5\xad\x97\xe8\x8a\x82"

        def raise_for_status(self):
            pass

    class FakeClient:
        calls: list[str] = []

        async def get(self, path):
            self.calls.append(path)
            return FakeResp()

    r = Runner(workdir=tmp_path)
    r.client = FakeClient()
    payload = {
        "requirement_id": 9,
        "prompt": "看文档",
        "docs": [{"id": 1, "filename": "../evil/a.md"}, {"id": 2, "filename": "说明书.docx"}],
    }
    paths = await r._docs_paths(payload)
    assert len(paths) == 2 and all("req-9" in p for p in paths)
    assert ".." not in str(paths)  # 穿越被剥掉，只剩文件名
    saved = (tmp_path / "docs" / "req-9" / "a.md").read_bytes()
    assert saved.startswith(b"# doc")  # 原始字节原样落盘
    out = Runner._with_docs(payload["prompt"], paths)
    assert out.startswith("看文档\n") and "a.md" in out and "说明书.docx" in out
    assert Runner._with_docs("p", []) == "p"


def test_repo_dir_and_crg_sync(tmp_path, monkeypatch):
    """项目目录固定 vega-workspace；CRG 无图 build、有图仅在 commit 漂移时 update。"""
    import json
    from types import SimpleNamespace

    import vega.runner as runner_mod
    from vega.runner import Runner

    r = Runner(workdir=tmp_path / "home")
    assert r._repo_dir("https://git.example.cn/team/svc.git") == tmp_path / "home" / "vega-workspace" / "svc"

    repo = tmp_path / "ws" / "proj"
    repo.mkdir(parents=True)
    calls: list[str] = []
    status = {"built_at_commit": "a", "current_sha": "a", "build_incomplete": False}

    def fake_run(cmd, **kw):
        calls.append(cmd[1])
        return SimpleNamespace(returncode=0, stdout=json.dumps(status) if cmd[1] == "status" else "", stderr="")

    monkeypatch.setattr(runner_mod.shutil, "which", lambda _: "/usr/bin/code-review-graph")
    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    r._crg_sync(repo)  # 无 graph.db → build + embed
    assert calls == ["build", "embed"]

    calls.clear()
    (repo / ".code-review-graph").mkdir()
    (repo / ".code-review-graph" / "graph.db").touch()
    r._crg_sync(repo)  # commit 一致 → 只补向量
    assert calls == ["status", "embed"]

    calls.clear()
    status["current_sha"] = "b"
    r._crg_sync(repo)  # commit 漂移 → update + embed
    assert calls == ["status", "update", "embed"]


def test_octo_mention_offsets():
    members = {"u1": "张三", "u2": "李四"}
    text, entities = build_mentions("MR 已就绪", members, ["u1"])
    assert text == "@张三 MR 已就绪"
    assert entities[0]["uid"] == "u1"
    assert entities[0]["offset"] == 0
    # 正文已有 @昵称 不重复补，offset 按出现位置算
    text2, e2 = build_mentions("先看 @李四 的评审", members, ["u2"])
    assert text2 == "先看 @李四 的评审"
    assert e2[0]["offset"] == _utf16("先看 ")
    # 查不到昵称的宁可不 @（不出现 uid 天书）
    text3, e3 = build_mentions("hi", {}, ["u1"])
    assert text3 == "hi" and e3 == []


def test_octo_send_wire_format(monkeypatch):
    """实测端点是 /v1/bot/sendMessage，消息体嵌 payload、content 是裸字符串（hermes 对齐）。"""
    from vega import octo as o

    monkeypatch.setattr(o, "_api_url", lambda: "https://im.test")
    monkeypatch.setattr(o, "_token", lambda: "tok")
    monkeypatch.setattr(o, "group_members", lambda g, token=None: {"u1": "张三"})
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(o, "_post", lambda path, payload, token: sent.append((path, payload)))

    assert o.send_text("g1", "MR 已就绪", mention_uids=["u1"]) is True
    path, payload = sent[0]
    assert path == "/v1/bot/sendMessage"
    assert payload["channel_id"] == "g1" and payload["channel_type"] == 2
    inner = payload["payload"]
    assert inner["type"] == 1 and inner["content"] == "@张三 MR 已就绪"
    assert inner["mention"]["entities"][0]["uid"] == "u1"
    assert payload["client_msg_no"]


def _utf16(s: str) -> int:
    return sum(2 if ord(c) > 0xFFFF else 1 for c in s)


async def test_backup_sqlite_db_rotation():
    """备份可用且轮换：keep=1 时两次备份只剩最新一份，且是可查询的完整库。"""
    import sqlite3
    from pathlib import Path

    from vega.db import backup_sqlite_db, get_db  # noqa: F401  get_db 只为引用同模块

    p1 = await backup_sqlite_db(keep=1)
    p2 = await backup_sqlite_db(keep=1)
    assert p1 and p2 and p1 != p2
    d = Path(p2).parent
    assert not Path(p1).exists()  # 旧的被轮换删掉
    # 备份是完整可读库（person 表在、admin 在）
    with sqlite3.connect(p2) as c:
        n = c.execute("select count(*) from person").fetchone()[0]
    assert n >= 1


async def test_change_self_password(client, db):
    from conftest import login_as

    tok, person = await login_as(client, db, "pw-user", "old-pw")
    auth = {"Authorization": f"Bearer {tok}"}

    r = await client.post("/api/auth/password", json={"old": "wrong", "new": "new-pw123"}, headers=auth)
    assert r.status_code == 400  # 旧密码错 → 拒

    r = await client.post("/api/auth/password", json={"old": "old-pw", "new": "new-pw123"}, headers=auth)
    assert r.status_code == 200

    assert (
        await client.post("/api/auth/login", json={"username": "pw-user", "password": "old-pw"})
    ).status_code == 401
    r = await client.post("/api/auth/login", json={"username": "pw-user", "password": "new-pw123"})
    assert r.status_code == 200


async def test_list_nodes_naive_heartbeat(admin_client, db):
    """回归：SQLite 读回的 naive 心跳与 aware utcnow 相减曾抛 TypeError（GET /api/runner-nodes 500）。"""
    from datetime import datetime, timedelta

    from vega.models import RunnerNode

    db.add(
        RunnerNode(
            name="hb-naive-node",
            token_hash="x",
            status="online",
            # 模拟 SQLite 读回的 naive datetime（无 tzinfo），且早已超时
            last_heartbeat=datetime.utcnow() - timedelta(seconds=600),
        )
    )
    await db.commit()

    r = await admin_client.get("/api/runner-nodes")
    assert r.status_code == 200, r.text
    row = next(n for n in r.json() if n["name"] == "hb-naive-node")
    assert row["status"] == "offline"
