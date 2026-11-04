"""测试环境：所有 vega 导入前先定 env（SQLite + 固定密钥 + 关调度器）。"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

TMP = Path(tempfile.mkdtemp(prefix="vega-test-"))
os.environ.update(
    {
        "VEGA_DATABASE_URL": f"sqlite+aiosqlite:///{TMP}/test.db",
        "VEGA_SECRET_KEY": "test-secret",
        "VEGA_DISABLE_SCHEDULER": "1",
        "VEGA_API_URL": "http://api.test",
        "VEGA_ADMIN_USERNAME": "admin",
        "VEGA_ADMIN_PASSWORD": "admin-pass",
        # 自动评审/自动漂移默认关：既有用例只验证流程本身，别被自动投递的任务干扰
        "VEGA_EGM_AUTO_REVIEW": "0",
        "VEGA_EGM_AUTO_DRIFT": "0",
    }
)
# 本机代理环境里的 NO_PROXY 含 [::1]，httpx 0.28 的 URLPattern 解析会抛 InvalidURL
os.environ.pop("NO_PROXY", None)
os.environ.pop("no_proxy", None)

from vega.auth import bootstrap_admin  # noqa: E402
from vega.db import init_db, sessionmaker  # noqa: E402
from vega.main import app  # noqa: E402
from vega.config import get_settings  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
async def _init_db():
    await init_db()
    async with sessionmaker()() as db:
        await bootstrap_admin(db)
    yield


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api.test") as c:
        yield c


@pytest_asyncio.fixture
async def admin_client(client):
    """已登录管理员的独立 client（不复用 client：测试里常会覆盖 client 的登录头）。"""
    async with httpx.AsyncClient(transport=client._transport, base_url="http://api.test") as c:
        r = await c.post("/api/auth/login", json={"username": "admin", "password": "admin-pass"})
        assert r.status_code == 200, r.text
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        yield c


@pytest_asyncio.fixture
async def db():
    async with sessionmaker()() as s:
        yield s


@pytest.fixture(autouse=True)
def _no_real_llm(monkeypatch):
    """报表里的 LLM 研判是可选增强：测试里直接失败，避免真实网络调用；需要时由用例自己覆盖。"""

    async def boom(*a, **k):
        raise RuntimeError("llm disabled in tests")

    monkeypatch.setattr("vega.reports.ask_llm", boom)


def make_claude_stub(tmp: Path, *, mode: str) -> Path:
    """生成 claude -p 的 stub：plan 模式吐三行 stream-json；dev 模式在 cwd 里落文件并 commit。"""
    stub = tmp / f"claude-{mode}"
    script = f"""#!{sys.executable}
import json, subprocess, sys, pathlib
args = sys.argv[1:]
def emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)

if "--permission-mode" in args:
    emit({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "计划第一段。"}}]}}}})
    emit({{"type": "assistant", "message": {{"content": [{{"type": "text", "text": "计划第二段。"}}]}}}})
    emit({{"type": "result", "result": "最终执行计划全文", "is_error": False}})
else:
    cwd = pathlib.Path.cwd()
    (cwd / "IMPLEMENTED.txt").write_text("done by stub", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], check=True)
    subprocess.run(["git", "commit", "-m", "feat(REQ-TEST): stub 实现需求"], check=True)
    emit({{"type": "result", "result": "开发完成", "is_error": False}})
"""
    stub.write_text(script, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    return stub


@pytest.fixture
def claude_stub():
    """默认给 plan/dev 双模式 stub；测试可按需改 settings.claude_bin。"""
    stub = make_claude_stub(TMP, mode="dual")
    get_settings().claude_bin = str(stub)
    yield stub
    get_settings().claude_bin = "claude"


_seed_seq = 0


@pytest_asyncio.fixture
async def seeded(db):
    """一组完整种子数据：组（含 token/octo/am 配置）+ 项目 + 成员人员。

    库是整个 session 共享的，本 fixture 每次调用生成独立一套（username 带序号），
    否则 person.gitlab_username 唯一约束会在第二个用例就炸。
    """
    global _seed_seq
    _seed_seq += 1
    username = f"zhangsan{_seed_seq}"

    from vega.crypto import encrypt
    from vega.models import DevGroup, GroupMember, Person, PersonGitlabAccount, System, SystemPM

    # 产品+开发双角色：现有用例都是「一个人从写需求一路走到定稿方案」，
    # 单角色会卡在两段式交接上；真实环境里兼职开发的技术经理正是这种配置
    person = Person(
        name="张三",
        username=username,
        gitlab_username=username,
        gitlab_email=f"{username}@test.cn",
        password_hash=None,
        roles=["product", "dev"],
    )
    db.add(person)
    await db.flush()
    group = DevGroup(
        name=f"g-{person.id}",
        gitlab_base_url="http://gitlab.test",
        gitlab_token_enc=encrypt(get_settings().secret_key, "gl-token"),
        octo_group_no="88888",
        am_team_id="team-1",
        webhook_secret=f"whs-{person.id}",  # 单址 webhook 靠 secret 识别组，必须唯一
    )
    db.add(group)
    await db.flush()
    account = PersonGitlabAccount(
        person_id=person.id,
        base_url="http://gitlab.test",
        username=username,
        token_enc=encrypt(get_settings().secret_key, "gl-token"),
    )
    db.add(account)
    await db.flush()
    gm = GroupMember(
        group_id=group.id, person_id=person.id, am_agent_id="agent-zhangsan", gitlab_account_id=account.id
    )
    db.add(gm)
    project = System(
        group_id=group.id,
        gitlab_project_id=100 + group.id,
        path="team/repo",
        name="repo",
        code_prefix="TEST",
        default_branch="master",
    )
    db.add(project)
    await db.flush()
    db.add(SystemPM(system_id=project.id, person_id=person.id))
    await db.commit()
    return {"person": person, "group": group, "project": project, "gm": gm}


async def login_as(client: httpx.AsyncClient, db, username: str, password: str = "pw"):
    from sqlalchemy import select
    from vega.auth import hash_password
    from vega.models import Person

    p = await db.scalar(select(Person).where(Person.username == username))
    if not p:
        p = Person(name=username, username=username, gitlab_username=username)
        db.add(p)
    p.password_hash = hash_password(password)  # 测试里统一给账号可登录态
    await db.commit()
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"], p
