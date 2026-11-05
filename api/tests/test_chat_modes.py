"""需求对话双模式：文档定稿前聊需求（AI 产出文档），定稿后聊方案；
两种模式各用一条 session，并分别用需求级 / 项目级 agent-memory 绑定。"""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy import select

from vega.config import get_settings
from vega.crypto import decrypt
from vega.models import Requirement, RunnerNode, RunnerTask, utcnow


async def _login(client, db, seeded):
    from conftest import login_as

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"


async def _new_req(client, db, seeded, **extra) -> int:
    r = await client.post(
        "/api/requirements", json={"system_id": seeded["project"].id, "title": "双模式", **extra}
    )
    assert r.status_code == 200, r.text
    req_id = r.json()["id"]
    r = await client.post(f"/api/requirements/{req_id}/claim")
    assert r.status_code == 200, r.text
    return req_id


async def _last_task(db, req_id: int) -> tuple[dict, dict]:
    """该需求最新 plan_turn 的 payload 与解密后的 claude_env。"""
    await db.commit()  # 丢掉本 session 的快照，看到 API 侧刚提交的行
    task = await db.scalar(
        select(RunnerTask).where(RunnerTask.requirement_id == req_id).order_by(RunnerTask.id.desc()).limit(1)
    )
    assert task is not None
    return task.payload, json.loads(decrypt(get_settings().secret_key, task.secrets))["claude_env"]


async def _drain(db, req_id: int) -> None:
    """收尾：本用例留下的排队任务置 done，避免被后面的 /runner/next 用例抢去。"""
    rows = (await db.scalars(select(RunnerTask).where(RunnerTask.requirement_id == req_id))).all()
    for t in rows:
        t.status = "done"
    await db.commit()


async def test_chat_mode_follows_doc_finalization(admin_client, client, db, seeded):
    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)

    # 文档未定稿：不允许聊方案；缺省进入需求讨论
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊需求", "mode": "plan"})
    assert r.status_code == 400 and "未定稿" in r.json()["detail"]
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊需求"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "requirement"
    payload, _env = await _last_task(db, req_id)
    assert "vega:doc:begin" in payload["prompt"] and "vega:plan:begin" not in payload["prompt"]
    assert payload["resume"] is False
    req = await db.get(Requirement, req_id)
    await db.refresh(req)
    assert req.req_session_id and not req.plan_session_id
    req_session = req.req_session_id

    # 采用 AI 文档：v1 非定稿；同内容不重复成版；空白 400
    r = await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求\n目标…"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] is True and body["version"] == 1 and body["is_final"] is False
    doc_id = body["id"]
    r = await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求\n目标…"})
    assert r.json()["created"] is False and r.json()["id"] == doc_id
    assert (await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "   "})).status_code == 400

    # 定稿文档 → 切到方案讨论，需求讨论被拒
    r = await client.post(f"/api/requirements/{req_id}/docs/{doc_id}/finalize")
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages", json={"content": "再聊需求", "mode": "requirement"}
    )
    assert r.status_code == 400 and "已定稿" in r.json()["detail"]
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊方案", "mode": "plan"})
    assert r.status_code == 200, r.text
    assert r.json()["mode"] == "plan"
    payload, _env = await _last_task(db, req_id)
    assert "vega:plan:begin" in payload["prompt"] and "vega:doc:begin" not in payload["prompt"]
    req = await db.get(Requirement, req_id)
    await db.refresh(req)
    assert req.plan_session_id and req.plan_session_id != req_session

    # 方案定稿后：开发阶段，两种模式都不允许
    req.status = "confirmed"
    await db.commit()
    for mode in ("requirement", "plan"):
        r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "继续", "mode": mode})
        assert r.status_code == 400
    await _drain(db, req_id)


async def test_chat_headers_follow_picker(admin_client, client, db, seeded):
    """输入框选的团队/专家逐轮生效：两种讨论各记各的选择，换专家下一轮头部就换。"""
    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)

    # 需求讨论首轮就带 team+agent：proxy 命中预设直接注册，不弹选择表单
    await client.post(
        f"/api/requirements/{req_id}/plan/messages",
        json={"content": "聊需求", "team_id": "team-req", "agent_id": "agent-req"},
    )
    _payload, env = await _last_task(db, req_id)
    headers = env["ANTHROPIC_CUSTOM_HEADERS"]
    assert "x-team-id: team-req" in headers and "x-agent-id: agent-req" in headers

    # 会话已初始化，第二轮起带上 agent
    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages",
        json={"content": "继续聊需求", "team_id": "team-req", "agent_id": "agent-req"},
    )
    old_session = r.json()["session_id"]
    _payload, env = await _last_task(db, req_id)
    headers = env["ANTHROPIC_CUSTOM_HEADERS"]
    assert "x-team-id: team-req" in headers and "x-agent-id: agent-req" in headers

    # 中途换专家：新开一条会话，并把之前的聊天记录整体带过去
    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages",
        json={"content": "换个专家聊", "team_id": "team-req", "agent_id": "agent-other"},
    )
    new_session = r.json()["session_id"]
    assert new_session != old_session
    payload, env = await _last_task(db, req_id)
    assert payload["resume"] is False
    assert "用户：聊需求" in payload["prompt"] and "用户：继续聊需求" in payload["prompt"]
    # 换专家后的新会话首轮同样带新 team+agent
    headers = env["ANTHROPIC_CUSTOM_HEADERS"]
    assert "x-team-id: team-req" in headers and "x-agent-id: agent-other" in headers

    # 下一轮沿用新会话，并带上新专家；消息里留痕供刷新后恢复输入框
    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages",
        json={"content": "新专家继续", "team_id": "team-req", "agent_id": "agent-other"},
    )
    assert r.json()["session_id"] == new_session
    payload, env = await _last_task(db, req_id)
    assert payload["resume"] is True
    headers = env["ANTHROPIC_CUSTOM_HEADERS"]
    assert "x-team-id: team-req" in headers and "x-agent-id: agent-other" in headers
    req_msgs = (
        await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "requirement"})
    ).json()
    assert req_msgs[-1]["team_id"] == "team-req" and req_msgs[-1]["agent_id"] == "agent-other"

    # 方案讨论不传选择 → 回退项目/研发组（seeded 项目未配 am → team-1 + 组成员 agent）
    doc_id = (
        await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求"})
    ).json()["id"]
    await client.post(f"/api/requirements/{req_id}/docs/{doc_id}/finalize")
    await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊方案", "mode": "plan"})
    _payload, env = await _last_task(db, req_id)
    headers = env["ANTHROPIC_CUSTOM_HEADERS"]
    assert "x-team-id: team-1" in headers and "x-agent-id: agent-zhangsan" in headers
    assert "team-req" not in headers and "agent-req" not in headers

    await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "继续聊方案", "mode": "plan"})
    _payload, env = await _last_task(db, req_id)
    assert "x-agent-id: agent-zhangsan" in env["ANTHROPIC_CUSTOM_HEADERS"]
    await _drain(db, req_id)


async def test_update_requirement_edits_fields(admin_client, client, db, seeded):
    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)

    # 先开一条需求讨论会话
    await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊需求"})
    await db.commit()
    req = await db.get(Requirement, req_id)
    await db.refresh(req)
    session = req.req_session_id
    assert session

    # 改标题/描述：团队/专家已改为输入框逐轮选，改需求不再动会话
    r = await client.put(
        f"/api/requirements/{req_id}",
        json={"title": "新标题", "description": "新描述"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "新标题"
    await db.commit()
    req = await db.get(Requirement, req_id)
    await db.refresh(req)
    assert req.description == "新描述" and req.req_session_id == session

    # 非认领人不能改
    from conftest import login_as

    token, _ = await login_as(client, db, "other-user")
    client.headers["Authorization"] = f"Bearer {token}"
    r = await client.put(f"/api/requirements/{req_id}", json={"title": "越权"})
    assert r.status_code == 403

    # 定稿后不允许改
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    req = await db.get(Requirement, req_id)
    req.status = "confirmed"
    await db.commit()
    r = await client.put(f"/api/requirements/{req_id}", json={"title": "定稿后"})
    assert r.status_code == 400
    await _drain(db, req_id)


async def test_from_ai_requires_claimant(admin_client, client, db, seeded):
    from conftest import login_as

    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)
    token, _ = await login_as(client, db, "other-user")
    client.headers["Authorization"] = f"Bearer {token}"
    r = await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求"})
    assert r.status_code == 403


async def test_mode_history_isolation(admin_client, client, db, seeded):
    """两种讨论的历史互相不可见。"""
    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)
    doc = (
        await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求 v1"})
    ).json()

    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊需求"})
    assert r.status_code == 200 and r.json()["mode"] == "requirement"

    req_msgs = (
        await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "requirement"})
    ).json()
    assert [m["content"] for m in req_msgs] == ["聊需求"]
    assert req_msgs[0]["mode"] == "requirement"
    assert (
        await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "plan"})
    ).json() == []

    await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/finalize")
    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages", json={"content": "聊方案", "mode": "plan"}
    )
    assert r.status_code == 200, r.text

    req_msgs = (
        await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "requirement"})
    ).json()
    plan_msgs = (
        await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "plan"})
    ).json()
    assert [m["content"] for m in req_msgs] == ["聊需求"]
    assert [m["content"] for m in plan_msgs] == ["聊方案"]
    # 不带 mode：全量返回，兼容旧客户端
    assert len((await client.get(f"/api/requirements/{req_id}/plan/messages")).json()) == 2
    await _drain(db, req_id)


async def test_unfinalize_doc_rolls_back_to_requirement_mode(admin_client, client, db, seeded):
    """方案未定稿前可撤回需求文档定稿，回到需求讨论。"""
    from conftest import login_as

    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)
    doc = (
        await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求"})
    ).json()
    assert (await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/finalize")).status_code == 200

    r = await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/unfinalize")
    assert r.status_code == 200, r.text
    docs = (await client.get(f"/api/requirements/{req_id}/docs")).json()
    assert docs and all(not d["is_final"] for d in docs)

    # 回到需求讨论：方案模式被拒
    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages", json={"content": "再聊需求", "mode": "requirement"}
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/requirements/{req_id}/plan/messages", json={"content": "聊方案", "mode": "plan"}
    )
    assert r.status_code == 400 and "未定稿" in r.json()["detail"]

    # 非认领人不能撤回
    token, _ = await login_as(client, db, "other-user")
    client.headers["Authorization"] = f"Bearer {token}"
    assert (
        await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/unfinalize")
    ).status_code == 403

    # 方案定稿（confirmed）后不可撤回
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    req = await db.get(Requirement, req_id)
    req.status = "confirmed"
    await db.commit()
    r = await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/unfinalize")
    assert r.status_code == 400
    await _drain(db, req_id)


async def test_lost_runner_node_self_heals(admin_client, client, db, seeded):
    """粘住的节点被删（库里还留着旧 id）：自动解绑 → 任何节点可接，首轮带全量历史；
    认领后重新固化绑定，之后照常只发输入框里的单条消息。"""
    import httpx

    from vega.main import app

    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)

    # 两轮需求讨论（同一条会话）
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "第一问"})
    old_session = r.json()["session_id"]
    await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "第二问"})

    # 模拟 runner 认领时固化的节点已不存在（删节点走了别的路径/库漂移）
    req = await db.get(Requirement, req_id)
    req.plan_runner_id = 999999
    await db.commit()

    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "换节点后第一句"})
    assert r.status_code == 200, r.text
    assert r.json()["session_id"] != old_session
    payload, _env = await _last_task(db, req_id)
    assert payload["resume"] is False
    assert "sticky_runner_id" not in payload
    assert "切换运行节点前的聊天记录，供接续" in payload["prompt"]
    assert "用户：第一问" in payload["prompt"] and "用户：第二问" in payload["prompt"]
    await db.commit()
    req = await db.get(Requirement, req_id)
    await db.refresh(req)
    assert req.plan_runner_id is None

    # 新节点认领 → 重新固化绑定
    node = await admin_client.post("/api/runner-nodes", json={"name": "node-heal"})
    assert node.status_code == 200, node.text
    node_id, node_token = node.json()["id"], node.json()["token"]
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {node_token}"},
    ) as rc:
        got = (await rc.get("/api/runner/next", params={"wait": 1})).json()["task"]
    assert got and got["payload"]["requirement_id"] == req_id
    await db.commit()
    req = await db.get(Requirement, req_id)
    await db.refresh(req)
    assert req.plan_runner_id == node_id

    # 后续轮次：沿用新会话，仅发单条消息
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "第三问"})
    assert r.json()["session_id"] != old_session
    payload, _env = await _last_task(db, req_id)
    assert payload["resume"] is True
    assert payload["sticky_runner_id"] == node_id
    assert payload["prompt"].startswith("第三问")
    assert "第一问" not in payload["prompt"]
    await _drain(db, req_id)


async def test_stop_plan_turn_lands_stopped_message(admin_client, client, db, seeded):
    """停止本轮：任务置 cancelled，历史落「已停止」消息，重复停止 400。"""
    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊需求"})
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/requirements/{req_id}/plan/stop")
    assert r.status_code == 200, r.text
    await db.commit()
    task = await db.scalar(
        select(RunnerTask).where(RunnerTask.requirement_id == req_id).order_by(RunnerTask.id.desc()).limit(1)
    )
    await db.refresh(task)
    assert task.status == "cancelled"

    msgs = (
        await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "requirement"})
    ).json()
    assert msgs[-1]["role"] == "assistant" and "已停止" in msgs[-1]["content"]

    # 没有在跑的任务了
    assert (await client.post(f"/api/requirements/{req_id}/plan/stop")).status_code == 400
    await _drain(db, req_id)


async def test_clear_session_resets_mode_history(admin_client, client, db, seeded):
    """清除会话（/clear）：只清当前模式的记录并换新 session，另一模式与文档不受影响。"""
    from conftest import login_as

    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)
    assert (await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊需求"})).status_code == 200
    req = await db.get(Requirement, req_id)
    await db.refresh(req)
    old_session = req.req_session_id
    assert old_session

    # 正在生成时不允许清除；非认领人 403
    r = await client.post(f"/api/requirements/{req_id}/plan/clear", json={"mode": "requirement"})
    assert r.status_code == 400 and "先停止" in r.json()["detail"]
    await _drain(db, req_id)
    token, _ = await login_as(client, db, "other-user")
    client.headers["Authorization"] = f"Bearer {token}"
    assert (
        await client.post(f"/api/requirements/{req_id}/plan/clear", json={"mode": "requirement"})
    ).status_code == 403
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"

    r = await client.post(f"/api/requirements/{req_id}/plan/clear", json={"mode": "requirement"})
    assert r.status_code == 200 and r.json()["cleared"] == 1
    assert (
        await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "requirement"})
    ).json() == []
    req = await db.get(Requirement, req_id)
    await db.refresh(req)
    assert req.req_session_id is None

    # 下一轮重新开一条会话：首轮（非 resume）且重新带上下文文档
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "重新开始"})
    assert r.status_code == 200 and r.json()["session_id"] != old_session
    payload, _env = await _last_task(db, req_id)
    assert payload["resume"] is False and "vega:doc:begin" in payload["prompt"]
    await _drain(db, req_id)

    # 另一模式互不影响：清方案讨论后，需求讨论的记录还在
    doc = (
        await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求 v1"})
    ).json()
    await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/finalize")
    await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "聊方案", "mode": "plan"})
    await _drain(db, req_id)
    assert (await client.post(f"/api/requirements/{req_id}/plan/clear", json={"mode": "plan"})).status_code == 200
    req_msgs = (
        await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "requirement"})
    ).json()
    assert [m["content"] for m in req_msgs] == ["重新开始"]
    assert (await client.get(f"/api/requirements/{req_id}/plan/messages", params={"mode": "plan"})).json() == []


async def test_finalize_doc_requires_claimant_and_claimed(admin_client, client, db, seeded):
    from conftest import login_as

    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)
    doc = (
        await client.post(f"/api/requirements/{req_id}/docs/from-ai", json={"content": "# 需求"})
    ).json()

    token, _ = await login_as(client, db, "other-user")
    client.headers["Authorization"] = f"Bearer {token}"
    assert (
        await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/finalize")
    ).status_code == 403

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    req = await db.get(Requirement, req_id)
    req.status = "confirmed"
    await db.commit()
    assert (
        await client.post(f"/api/requirements/{req_id}/docs/{doc['id']}/finalize")
    ).status_code == 400


async def test_plan_message_rejected_without_online_runner(admin_client, client, db, seeded):
    """没有在线 Runner 时直接拒发：否则消息永远 queued，前端一直显示「已等待」。"""
    await _login(client, db, seeded)
    req_id = await _new_req(client, db, seeded)

    for n in (await db.scalars(select(RunnerNode))).all():
        n.last_heartbeat = utcnow() - timedelta(minutes=5)
    await db.commit()
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "在吗"})
    assert r.status_code == 409 and "没有在线" in r.json()["detail"]

    # 粘住的节点离线：别的节点在线也领不走，同样不能收
    dead = RunnerNode(
        name="node-dead", token_hash="dead", status="online", last_heartbeat=utcnow() - timedelta(minutes=5)
    )
    db.add(dead)
    await db.flush()
    req = await db.get(Requirement, req_id)
    req.plan_runner_id = dead.id
    live = await db.scalar(select(RunnerNode).where(RunnerNode.name == "node-default"))
    live.last_heartbeat = utcnow()
    await db.commit()
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "在吗"})
    assert r.status_code == 409 and "绑定" in r.json()["detail"]
