"""plan 会话全链路：用户发言 → 入队 → runner 领取（粘性固化）→ claude stub 执行
→ 事件回传 → SSE 收到流式文本 → result 落 PlanMessage。"""

from __future__ import annotations

import asyncio
import json

import httpx
import uvicorn

from vega.runner import Runner


async def _create_node(admin_client, name: str = "node-a") -> tuple[int, str]:
    r = await admin_client.post("/api/runner-nodes", json={"name": name})
    assert r.status_code == 200, r.text
    return r.json()["id"], r.json()["token"]


def _runner_client(app, token: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://api.test",
        headers={"Authorization": f"Bearer {token}"},
    )


async def _start_server(app) -> tuple[uvicorn.Server, str]:
    """同进程同事件循环起一个真 uvicorn：ASGITransport 会缓冲整个响应体，SSE 必须走真 socket。"""
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started and server.servers:
            break
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, f"http://127.0.0.1:{port}"


async def test_plan_turn_loop(admin_client, client, db, seeded, claude_stub):
    from vega.main import app

    node_id, node_token = await _create_node(admin_client)
    server, base_url = await _start_server(app)

    # 张三认领需求
    from conftest import login_as

    _, zhangsan = await login_as(client, db, seeded["person"].gitlab_username)
    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    r = await client.post("/api/requirements", json={"system_id": seeded["project"].id, "title": "需求A"})
    req_id = r.json()["id"]
    await client.post(f"/api/requirements/{req_id}/claim")
    # 需求文档定稿后才允许方案讨论（首版上传即定稿）
    r = await client.post(
        f"/api/requirements/{req_id}/docs", files={"file": ("需求.md", "# 需求".encode(), "text/markdown")}
    )
    assert r.status_code == 200, r.text

    # 先订阅 SSE（后台消费）
    sse_chunks: list[dict] = []
    sse_done = asyncio.Event()

    async def consume():
        async with httpx.AsyncClient(base_url=base_url, headers=dict(client.headers)) as sse:
            async with sse.stream("GET", f"/api/requirements/{req_id}/plan/stream?mode=plan") as resp:
                async for line in resp.aiter_lines():
                    if line.startswith("data: "):
                        evt = json.loads(line[6:])
                        sse_chunks.append(evt)
                        if evt.get("type") == "plan_done":
                            sse_done.set()

    consumer = asyncio.create_task(consume())
    await asyncio.sleep(0.3)  # 等 history + ready 推完

    # 用户发言 → 入队
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "帮我定个方案"})
    assert r.status_code == 200, r.text
    task_id = r.json()["task_id"]

    # runner（走真实 Runner 类 + ASGI transport）领取并执行
    runner = Runner()
    runner.client = _runner_client(app, node_token)
    async with _runner_client(app, node_token) as rc:
        r = await rc.get("/api/runner/next", params={"wait": 3})
        task = r.json()["task"]
        assert task and task["type"] == "plan_turn"
        assert task["payload"]["resume"] is False
        # 方案标记规则每轮都要带（不封在首轮上下文里）
        assert "vega:plan:begin" in task["payload"]["prompt"]
        # 认领即固化粘性 runner
        from vega.models import Requirement

        req = await db.get(Requirement, req_id)
        await db.refresh(req)
        assert req.plan_runner_id == node_id

    await runner.execute(task)

    # result 落库为 assistant 消息
    from vega.models import PlanMessage

    msgs = (
        (await db.scalars(__import__("sqlalchemy").select(PlanMessage).where(PlanMessage.requirement_id == req_id)))
        .all()
    )
    assert [m.role for m in msgs] == ["user", "assistant"]
    assert msgs[1].content == "最终执行计划全文"

    # SSE 收到了流式文本与 done
    await asyncio.wait_for(sse_done.wait(), timeout=3)
    texts = [c for c in sse_chunks if c.get("type") == "plan_text"]
    assert any("计划第一段" in t["text"] for t in texts)
    done = [c for c in sse_chunks if c.get("type") == "plan_done"]
    assert done and done[0]["message"]["content"] == "最终执行计划全文"
    consumer.cancel()
    server.should_exit = True  # 收尾：关真服务

    # 定稿方案：回合本身不成版；人点「采用此方案」后才建 v1，再定稿入池
    from vega.models import PlanVersion

    vers = (await client.get(f"/api/requirements/{req_id}/plan-versions")).json()
    assert vers == []
    adopted = await client.post(
        f"/api/requirements/{req_id}/plan-versions", json={"content": "最终执行计划全文"}
    )
    assert adopted.status_code == 200 and adopted.json()["created"] is True
    r = await client.post(f"/api/requirements/{req_id}/plan-versions/{adopted.json()['id']}/finalize")
    assert r.status_code == 200
    assert r.json()["status"] == "confirmed"
    assert r.json()["plan_md"] == "最终执行计划全文"
    final = (await db.scalars(__import__("sqlalchemy").select(PlanVersion).where(PlanVersion.requirement_id == req_id))).all()
    assert [v.is_final for v in final] == [True]

    # 第二轮消息带 resume + 粘性
    r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "再补充下"})
    assert r.status_code == 400  # confirmed 后不允许继续聊


async def test_plan_turn_session_rebuild(admin_client, client, db, seeded, claude_stub, tmp_path):
    """resume 会话在 claude 侧丢失（换机/清配置）时，runner 应重建会话重试，而不是把需求对话永久卡死。"""
    from vega.config import get_settings
    from vega.main import app
    from vega.models import PlanMessage, Requirement
    from sqlalchemy import select

    node_id, node_token = await _create_node(admin_client, "node-rebuild")

    # 定制 stub：--resume 模拟会话丢失退出 1；--session-id 正常产出
    stub = tmp_path / "claude-rebuild-stub"
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "if '--resume' in sys.argv:\n"
        "    sys.stderr.write('No conversation found with session ID: gone')\n"
        "    sys.exit(1)\n"
        "print(json.dumps({'type': 'result', 'result': '重建后的回复', 'is_error': False}))\n",
    )
    stub.chmod(0o755)
    old_bin = get_settings().claude_bin
    get_settings().claude_bin = str(stub)
    try:
        from conftest import login_as

        token, _ = await login_as(client, db, seeded["person"].gitlab_username)
        client.headers["Authorization"] = f"Bearer {token}"
        r = await client.post("/api/requirements", json={"system_id": seeded["project"].id, "title": "会话重建"})
        req_id = r.json()["id"]
        await client.post(f"/api/requirements/{req_id}/claim")
        # 方案讨论要求需求文档已定稿（首版上传即定稿）
        await client.post(
            f"/api/requirements/{req_id}/docs",
            files={"file": ("需求.md", "# 需求".encode(), "text/markdown")},
        )
        # 手工造一个已存在 session_id（等价于第二轮发言 → resume 任务）
        req = await db.get(Requirement, req_id)
        req.plan_session_id = "11111111-2222-3333-4444-555555555555"
        await db.commit()  # db fixture 与 API 各持 session，提交后才可见
        r = await client.post(f"/api/requirements/{req_id}/plan/messages", json={"content": "第二轮"})
        assert r.status_code == 200
        runner = Runner()
        runner.client = _runner_client(app, node_token)
        async with _runner_client(app, node_token) as rc:
            t = (await rc.get("/api/runner/next", params={"wait": 3})).json()["task"]
            assert t["payload"]["resume"] is True
            await runner.execute(t)

        msgs = (
            await db.scalars(select(PlanMessage).where(PlanMessage.requirement_id == req_id).order_by(PlanMessage.id))
        ).all()
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert "重建后的回复" in msgs[-1].content
    finally:
        get_settings().claude_bin = old_bin


async def test_plan_events_persist_for_replay(admin_client, client, db, seeded):
    """刷新后思考链要能回放：plan 过程事件落库并挂到该轮 assistant 消息上。"""
    from sqlalchemy import select

    from vega.main import app
    from vega.models import PlanEvent, PlanMessage, RunnerTask

    node_id, node_token = await _create_node(admin_client, "node-proc")
    from conftest import login_as

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    req_id = (
        await client.post("/api/requirements", json={"system_id": seeded["project"].id, "title": "过程回放"})
    ).json()["id"]
    await client.post(f"/api/requirements/{req_id}/claim")

    # 直接造一个已被该节点认领的 plan 任务，避开任务池里其他用例的排队任务
    task = RunnerTask(type="plan_turn", payload={}, requirement_id=req_id, runner_id=node_id, status="running")
    db.add(task)
    await db.flush()
    task_id = task.id
    await db.commit()

    events = [
        {"kind": "thinking", "text": "先看目录"},
        {"kind": "raw", "event": {"subtype": "thinking_tokens", "estimated_tokens": 42}},
        {"kind": "raw", "event": {"type": "system", "subtype": "init", "cwd": "/tmp"}},
        {"kind": "tool_use", "id": "tu-1", "name": "Bash", "input": '{"command": "ls"}'},
        {"kind": "tool_result", "content": "README.md", "is_error": False},
        {
            "kind": "raw",
            "event": {
                "subtype": "task_progress",
                "tool_use_id": "tu-9",
                "description": "子代理干活",
                "usage": {"tool_uses": 1, "total_tokens": 10},
            },
        },
        # 子任务/压缩这类 system 事件也要能回放，不只 task_progress
        {"kind": "raw", "event": {"type": "system", "subtype": "task_started", "task_id": "t1", "description": "调研"}},
        {
            "kind": "raw",
            "event": {"type": "system", "subtype": "compact_boundary", "compact_metadata": {"trigger": "auto", "pre_tokens": 90000}},
        },
        {
            "kind": "usage",
            "usage": {"input": 100, "cache_read": 2000, "cache_create": 30, "output": 456},
            "window": 128144,
            "parent": "",
        },
        {"kind": "result", "text": "最终执行计划全文", "is_error": False},
    ]
    # 上下文环的直播帧：SSE 之外先直接盯 bus，确认 usage 会转成 plan_usage
    from vega.bus import bus

    q = bus.subscribe(req_id)
    async with _runner_client(app, node_token) as rc:
        r = await rc.post(f"/api/runner/tasks/{task_id}/events", json={"events": events})
        assert r.status_code == 200, r.text
        r = await rc.post(
            f"/api/runner/tasks/{task_id}/result",
            json={"status": "done", "data": {"text": "最终执行计划全文"}},
        )
        assert r.status_code == 200, r.text

    rows = (
        await db.scalars(select(PlanEvent).where(PlanEvent.runner_task_id == task_id).order_by(PlanEvent.seq))
    ).all()
    # result 不入库；thinking_tokens 是直播计量噪声，也不入库
    assert [e.kind for e in rows] == ["thinking", "tool_use", "tool_result", "raw", "raw", "raw", "usage"]
    live = []
    while not q.empty():
        live.append(q.get_nowait())
    bus.unsubscribe(req_id, q)
    usage_pub = [e for e in live if e.get("type") == "plan_usage"]
    assert usage_pub and usage_pub[0]["usage"]["cache_read"] == 2000
    assert usage_pub[0]["window"] == 128144 and usage_pub[0]["mode"] == "plan"
    assert rows[3].payload["subtype"] == "task_progress"  # raw 存内层事件，回放形状与直播一致
    assert all(e.message_id is not None for e in rows)
    msg = await db.get(PlanMessage, rows[0].message_id)
    assert msg and msg.role == "assistant" and all(e.message_id == msg.id for e in rows)

    # 重放接口：事件随 assistant 消息返回
    msgs = (await client.get(f"/api/requirements/{req_id}/plan/messages")).json()
    assert [m["role"] for m in msgs] == ["assistant"]
    evs = msgs[-1]["events"]
    # 回放与直播同构：system 事件带 kind=raw，前端才知道该走原始事件分支
    assert [(e.get("kind"), e.get("subtype") or e.get("kind")) for e in evs] == [
        ("thinking", "thinking"),
        ("tool_use", "tool_use"),
        ("tool_result", "tool_result"),
        ("raw", "task_progress"),
        ("raw", "task_started"),
        ("raw", "compact_boundary"),
        ("usage", "usage"),
    ]
    assert evs[1]["name"] == "Bash"


async def test_doc_raw_chinese_filename(admin_client, client, db, seeded):
    """回归：中文名文档的 raw 下载曾因 content-disposition 不能 latin-1 编码 500。"""
    from vega.main import app

    _, node_token = await _create_node(admin_client, name="node-doc-raw")
    from conftest import login_as

    token, _ = await login_as(client, db, seeded["person"].gitlab_username)
    client.headers["Authorization"] = f"Bearer {token}"
    req_id = (
        await client.post("/api/requirements", json={"system_id": seeded["project"].id, "title": "带文档"})
    ).json()["id"]

    content = "接入暗号：北极星-42".encode()
    r = await client.post(
        f"/api/requirements/{req_id}/docs",
        files={"file": ("验收标准.md", content, "text/markdown")},
    )
    assert r.status_code == 200, r.text
    doc_id = (await client.get(f"/api/requirements/{req_id}/docs")).json()[0]["id"]

    async with _runner_client(app, node_token) as rc:
        resp = await rc.get(f"/api/runner/requirements/{req_id}/docs/{doc_id}/raw")
        assert resp.status_code == 200, resp.text
        assert resp.content == content
        assert resp.headers["content-disposition"].isascii()
