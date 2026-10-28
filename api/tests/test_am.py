"""agent-memory 下拉数据源：转发 hub meta RPC 的薄代理。"""

from vega.routers import am as am_router


async def test_am_teams_and_agents(client, admin_client, monkeypatch):
    # 未登录 401
    r = await client.get("/api/am/teams")
    assert r.status_code == 401

    calls = []

    async def fake_rpc(method, body, key):
        calls.append((method, body))
        if method == "team/list":
            return {
                "items": [
                    {"team_id": "team-a", "name": "团队A", "description": "d1"},
                    {"team_id": "team-b", "name": "团队B", "description": ""},
                ],
                "total": 2,
            }
        return {"items": [{"agent_id": "agt-1", "name": "专家1", "description": "x"}], "total": 1}

    monkeypatch.setattr(am_router, "_rpc", fake_rpc)
    r = await admin_client.get("/api/am/teams")
    assert r.status_code == 200
    assert r.json() == [
        {"id": "team-a", "name": "团队A", "description": "d1"},
        {"id": "team-b", "name": "团队B", "description": ""},
    ]

    # team_id 必填
    assert (await admin_client.get("/api/am/agents")).status_code == 422

    r = await admin_client.get("/api/am/agents", params={"team_id": "team-a"})
    assert r.status_code == 200
    assert r.json() == [{"id": "agt-1", "name": "专家1", "description": "x"}]
    assert calls[1][1]["team_id"] == "team-a"


async def test_am_rpc_error_maps_502(admin_client, monkeypatch):
    async def bad_rpc(method, body, key):
        from fastapi import HTTPException

        raise HTTPException(502, "agent-memory team/list 失败：upstream down")

    monkeypatch.setattr(am_router, "_rpc", bad_rpc)
    r = await admin_client.get("/api/am/teams")
    assert r.status_code == 502
